"""
Breakout detector — three resistance types + composite score.

Per Phase 1 office hours Item 5: HVN-only resistance is incomplete. Three types matter,
each with different psychology and follow-through:
  - HVN break:        crossed a high-volume node from the lookback profile
  - Swing high break: closed above prior N-day high (default N=20)
  - Cycle high break: closed above prior 52-week (252-day) high

For each detection: yesterday's close ≤ level AND today's close > level. This requires
a real upward cross today, not a continuation of an existing position above.

Composite score (0-100):
  base = sum(weight × flag) for the 3 resistance types  (default weights 0.4 / 0.3 / 0.3)
  vol_mult = clip(volume_ratio / 2.0, 0.5, 2.0)
  range_mult = clip(close_in_range_pct + 0.25, 0.5, 1.0)
  ma_mult = (1.0 if above_50dma else 0.7) × (1.0 if above_200dma else 0.85)
  score = min(100, base × 100 × vol_mult × range_mult × ma_mult)

Anti-look-ahead: all "history" computations use df rows STRICTLY BEFORE asof_date.
Today's row supplies close/volume/H/L. The profile_lookback window ends at asof_date-1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from analytics.volume_profile import volume_profile


# ---------- Public API ----------

@dataclass
class BreakoutState:
    asof_date: pd.Timestamp
    close: float

    # Resistance break flags
    hvn_break: bool
    swing_high_break: bool
    cycle_high_break: bool
    decadal_high_break: bool  # 20-year (5000-day) high break — catches BHEL-style decadal breakouts

    # The specific levels (None when no HVN exists in window)
    hvn_level: Optional[float]
    swing_high: float
    cycle_high: float
    decadal_high: Optional[float]  # 20-year high (None if insufficient history)
    level_broken: Optional[float]

    # Volume + range
    volume: float
    avg_volume_20d: float
    volume_ratio: float
    close_in_range_pct: float

    # MAs
    sma_50: float
    sma_200: float
    above_50dma: bool
    above_200dma: bool

    # Composite
    breakout_score: float

    # Reproducibility
    swing_lookback: int = 20
    cycle_lookback: int = 252
    profile_lookback: int = 126
    weights: dict = field(default_factory=lambda: {"hvn": 0.4, "swing": 0.3, "cycle": 0.3})

    def __repr__(self) -> str:
        flags = []
        if self.hvn_break: flags.append("HVN")
        if self.swing_high_break: flags.append("SWING")
        if self.cycle_high_break: flags.append("CYCLE")
        flag_str = "+".join(flags) if flags else "none"
        return (
            f"BreakoutState({self.asof_date.date()} close=₹{self.close:.2f} "
            f"breaks=[{flag_str}] vol×{self.volume_ratio:.2f} "
            f"range%={self.close_in_range_pct:.0%} score={self.breakout_score:.0f})"
        )


def breakout_state(
    df: pd.DataFrame,
    asof_date: pd.Timestamp | str,
    swing_lookback: int = 20,
    cycle_lookback: int = 252,
    decadal_lookback: int = 5000,    # ~20 trading years — catches BHEL-style decadal breakouts
    profile_lookback: int = 126,
    hvn_weight: float = 0.4,
    swing_weight: float = 0.3,
    cycle_weight: float = 0.3,
    decadal_weight: float = 0.5,      # weighted EXTRA on top of base — 20yr break is rare + significant
    profile_kwargs: Optional[dict] = None,
) -> BreakoutState:
    """
    Compute breakout state for a single day.

    df: full OHLCV history with DatetimeIndex. Must have columns: open, high, low, close, volume.
    asof_date: the day to evaluate ("today"). Must exist in df.
    """
    _validate(df)
    asof_date = pd.Timestamp(asof_date)
    if asof_date not in df.index:
        raise ValueError(f"asof_date {asof_date.date()} not in df index")

    # Split into "history" (strictly before today) and "today" — anti-look-ahead boundary
    today_idx = df.index.get_loc(asof_date)
    today_row = df.iloc[today_idx]
    history = df.iloc[:today_idx]

    if len(history) == 0:
        raise ValueError(f"No history before {asof_date.date()} — cannot detect breakouts")

    yesterday_close = float(history["close"].iloc[-1])
    today_close = float(today_row["close"])
    today_high = float(today_row["high"])
    today_low = float(today_row["low"])
    today_volume = float(today_row["volume"])

    # Resistance windows EXCLUDE yesterday's bar — otherwise a fresh break yesterday
    # contaminates today's level (yesterday's high becomes "the resistance"), and continuation
    # days after a real breakout incorrectly re-register as fresh breaks.
    pre_yesterday = history.iloc[:-1]

    # ---- 1. Swing high break (N-day) ----
    swing_window = pre_yesterday.tail(swing_lookback)
    swing_high = float(swing_window["high"].max()) if len(swing_window) > 0 else float("-inf")
    swing_high_break = (yesterday_close <= swing_high) and (today_close > swing_high)

    # ---- 2. Cycle high break (52-week / 252-day) ----
    cycle_window = pre_yesterday.tail(cycle_lookback)
    cycle_high = float(cycle_window["high"].max()) if len(cycle_window) > 0 else float("-inf")
    cycle_high_break = (yesterday_close <= cycle_high) and (today_close > cycle_high)

    # ---- 2b. Decadal high break (~20 years) — NEW
    # Only meaningful when we have enough history. If less than decadal_lookback bars, skip.
    decadal_high: Optional[float] = None
    decadal_high_break = False
    if len(pre_yesterday) >= decadal_lookback:
        decadal_window = pre_yesterday.tail(decadal_lookback)
        dec_high = float(decadal_window["high"].max())
        decadal_high = dec_high
        decadal_high_break = (yesterday_close <= dec_high) and (today_close > dec_high)

    # ---- 3. HVN break (uses volume profile from lookback window) ----
    profile_window = history.tail(profile_lookback)
    hvn_level: Optional[float] = None
    hvn_break = False
    if len(profile_window) >= 20:  # need enough days to build a profile
        kwargs = profile_kwargs or {}
        vp = volume_profile(profile_window, **kwargs)
        # Find the highest HVN that yesterday's close was ≤ AND today's close >
        crossed_hvns = [h for h in vp.hvns if yesterday_close <= h < today_close]
        if crossed_hvns:
            hvn_level = max(crossed_hvns)  # the highest one we cleared
            hvn_break = True

    # level_broken: max of the broken levels (the strongest resistance cleared)
    broken_levels = [
        lvl for lvl, broke in [
            (hvn_level, hvn_break),
            (swing_high, swing_high_break),
            (cycle_high, cycle_high_break),
            (decadal_high, decadal_high_break),
        ] if broke and lvl is not None
    ]
    level_broken = max(broken_levels) if broken_levels else None

    # ---- 4. Volume ratio (vs 20-day avg, excluding today) ----
    vol_window = history.tail(20)["volume"]
    avg_vol_20d = float(vol_window.mean()) if len(vol_window) > 0 else 0.0
    volume_ratio = (today_volume / avg_vol_20d) if avg_vol_20d > 0 else 0.0

    # ---- 5. Close in range % ----
    day_range = today_high - today_low
    if day_range > 0:
        close_in_range_pct = (today_close - today_low) / day_range
    else:
        close_in_range_pct = 0.5  # degenerate single-price day → neutral

    # ---- 6. SMA filters (50 / 200) ----
    sma_50_window = history.tail(50)["close"]
    sma_200_window = history.tail(200)["close"]
    sma_50 = float(sma_50_window.mean()) if len(sma_50_window) > 0 else float("nan")
    sma_200 = float(sma_200_window.mean()) if len(sma_200_window) > 0 else float("nan")
    above_50dma = (not np.isnan(sma_50)) and (today_close > sma_50)
    above_200dma = (not np.isnan(sma_200)) and (today_close > sma_200)

    # ---- 7. Composite score ----
    # Decadal break adds an EXTRA bonus on top of base (it's rare + structurally significant).
    base = (
        (hvn_weight if hvn_break else 0.0) +
        (swing_weight if swing_high_break else 0.0) +
        (cycle_weight if cycle_high_break else 0.0) +
        (decadal_weight if decadal_high_break else 0.0)
    )
    vol_mult = float(np.clip(volume_ratio / 2.0, 0.5, 2.0))
    range_mult = float(np.clip(close_in_range_pct + 0.25, 0.5, 1.0))
    ma_mult = (1.0 if above_50dma else 0.7) * (1.0 if above_200dma else 0.85)
    score = min(100.0, base * 100.0 * vol_mult * range_mult * ma_mult)

    return BreakoutState(
        asof_date=asof_date,
        close=today_close,
        hvn_break=hvn_break,
        swing_high_break=swing_high_break,
        cycle_high_break=cycle_high_break,
        decadal_high_break=decadal_high_break,
        hvn_level=hvn_level,
        swing_high=swing_high,
        cycle_high=cycle_high,
        decadal_high=decadal_high,
        level_broken=level_broken,
        volume=today_volume,
        avg_volume_20d=avg_vol_20d,
        volume_ratio=volume_ratio,
        close_in_range_pct=close_in_range_pct,
        sma_50=sma_50,
        sma_200=sma_200,
        above_50dma=above_50dma,
        above_200dma=above_200dma,
        breakout_score=score,
        swing_lookback=swing_lookback,
        cycle_lookback=cycle_lookback,
        profile_lookback=profile_lookback,
        weights={"hvn": hvn_weight, "swing": swing_weight, "cycle": cycle_weight},
    )


def breakout_state_for_ticker(
    ticker: str,
    asof_date: pd.Timestamp | str,
    ohlcv_dir: Optional[Path] = None,
    **kwargs,
) -> BreakoutState:
    """Wrapper: load parquet, call breakout_state."""
    if ohlcv_dir is None:
        ohlcv_dir = Path(__file__).resolve().parent.parent / "data" / "ohlcv"
    path = ohlcv_dir / f"{ticker}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No OHLCV parquet for {ticker} at {path}")
    df = pd.read_parquet(path)
    return breakout_state(df, asof_date, **kwargs)


def scan_breakouts(
    df: pd.DataFrame,
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
    min_score: float = 50.0,
    **kwargs,
) -> list[BreakoutState]:
    """Scan a date range, return BreakoutStates above min_score, sorted by score desc."""
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    candidates = df.loc[start:end].index
    results = []
    for d in candidates:
        try:
            st = breakout_state(df, d, **kwargs)
            if st.breakout_score >= min_score:
                results.append(st)
        except ValueError:
            continue  # not enough history
    return sorted(results, key=lambda s: s.breakout_score, reverse=True)


# ---------- Helpers ----------

def _validate(df: pd.DataFrame) -> None:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Index must be DatetimeIndex")
