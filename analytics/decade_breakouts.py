"""
Decade Breakouts — pre-breakout watchlist for stocks approaching a >10-year-old high.

Phase 7 (2026-05-13). User spec:

  "Last high was 100 for example and it's trading below 100 only for the last 10 years
   like 70-80 range and it didn't touch the high 100, not once intraday or any way.
   We need to pop if they come close to the high 100 maybe 1 or 2% before."

Definitions (all touches use intraday HIGH, not close — matches user's 'not once intraday'):

  H_old    = max(High) over bars STRICTLY OLDER than (asof - lookback_years).
  H_recent = max(High) over bars within the last `lookback_years` (excluding today's bar
             so an alert never disqualifies itself).

  Eligible IFF:
    1. stock has ≥ min_history_years of price history
    2. H_old exists (≥ 1 bar older than the cutoff)
    3. H_recent < H_old   — strict; even one intraday touch in the last 10y disqualifies
    4. today_close ≥ H_old × (1 - proximity_pct/100)   — within alert distance

Status flag:
  "Approaching"  — today_close < H_old (still below the level)
  "Broke today"  — today_close ≥ H_old (just crossed — first time in 10+ years)

Why a separate module from breakout_detector?
  breakout_detector.decadal_high_break asks "did today's close cross a 20-year high?"
  decade_breakouts asks "is today's close within 2% of a 10-year-old high that has been
  untouched for the entire window?" — this is the pre-breakout watchlist (catch it 1-2%
  before the break, not the day after).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import pandas as pd


@dataclass
class DecadeBreakoutState:
    """Output of decade_breakout_state() — everything the dashboard and scanner need."""
    asof_date: pd.Timestamp
    ticker: str
    eligible: bool
    reason: str

    # Reference high (set > lookback_years ago, never touched since)
    H_old: Optional[float] = None
    H_old_date: Optional[pd.Timestamp] = None
    H_old_age_years: float = 0.0

    # Recent-window highest (must be < H_old to qualify) — useful debug info
    H_recent: Optional[float] = None
    H_recent_date: Optional[pd.Timestamp] = None

    # Today
    today_close: float = 0.0
    gap_pct: float = 0.0          # (H_old - today_close) / H_old × 100; negative if above
    status: Literal["", "Approaching", "Broke today"] = ""

    # Reproducibility
    lookback_years: int = 10
    proximity_pct: float = 2.0
    min_history_years: int = 11


def decade_breakout_state(
    df: pd.DataFrame,
    asof_date: pd.Timestamp | str,
    *,
    ticker: str = "",
    lookback_years: int = 10,
    proximity_pct: float = 2.0,
    min_history_years: int = 11,
) -> DecadeBreakoutState:
    """
    Detect decade-breakout setup as of asof_date.

    df: OHLCV with DatetimeIndex. Required columns: high, close. (open/low/volume ignored.)
    asof_date: the day to evaluate. Must exist in df.index.

    Returns DecadeBreakoutState. If eligible=False, .reason explains why.
    """
    asof = pd.Timestamp(asof_date)
    _validate(df)
    if asof not in df.index:
        return _fail(
            asof, ticker, f"asof_date {asof.date()} not in df.index",
            lookback_years, proximity_pct, min_history_years,
        )

    today_idx = df.index.get_loc(asof)
    today_close = float(df.iloc[today_idx]["close"])

    # 1. History-length gate.
    span_years = (asof - df.index[0]).days / 365.25
    if span_years < min_history_years:
        return _fail(
            asof, ticker,
            f"insufficient history: {span_years:.1f}y < {min_history_years}y needed",
            lookback_years, proximity_pct, min_history_years,
            today_close=today_close,
        )

    # 2. Split into old (> lookback_years ago) and recent (last lookback_years, excluding
    #    today's bar — otherwise a "Broke today" event would disqualify itself).
    cutoff = asof - pd.Timedelta(days=int(365.25 * lookback_years))
    history = df.iloc[:today_idx]   # excludes today
    old_bars = history.loc[history.index < cutoff]
    recent_bars = history.loc[history.index >= cutoff]

    if old_bars.empty:
        return _fail(
            asof, ticker, f"no bars older than {cutoff.date()}",
            lookback_years, proximity_pct, min_history_years,
            today_close=today_close,
        )

    H_old = float(old_bars["high"].max())
    H_old_date = pd.Timestamp(old_bars["high"].idxmax())
    H_old_age_years = (asof - H_old_date).days / 365.25

    H_recent = float(recent_bars["high"].max()) if not recent_bars.empty else 0.0
    H_recent_date = (pd.Timestamp(recent_bars["high"].idxmax())
                     if not recent_bars.empty else None)

    # 3. Strict untouched check — recent window's intraday high must be < H_old.
    if H_recent >= H_old:
        return DecadeBreakoutState(
            asof_date=asof, ticker=ticker, eligible=False,
            reason=(f"touched in lookback: recent {lookback_years}y high ₹{H_recent:.2f} "
                    f"≥ old high ₹{H_old:.2f} on {H_recent_date.date()} — disqualified"),
            H_old=H_old, H_old_date=H_old_date, H_old_age_years=H_old_age_years,
            H_recent=H_recent, H_recent_date=H_recent_date,
            today_close=today_close,
            lookback_years=lookback_years, proximity_pct=proximity_pct,
            min_history_years=min_history_years,
        )

    # 4. Proximity check.
    gap_pct = (H_old - today_close) / H_old * 100.0
    if today_close < H_old * (1 - proximity_pct / 100.0):
        return DecadeBreakoutState(
            asof_date=asof, ticker=ticker, eligible=False,
            reason=(f"too far from H_old: close ₹{today_close:.2f} is {gap_pct:.1f}% below "
                    f"₹{H_old:.2f} (need ≤ {proximity_pct}%)"),
            H_old=H_old, H_old_date=H_old_date, H_old_age_years=H_old_age_years,
            H_recent=H_recent, H_recent_date=H_recent_date,
            today_close=today_close, gap_pct=gap_pct,
            lookback_years=lookback_years, proximity_pct=proximity_pct,
            min_history_years=min_history_years,
        )

    status: Literal["Approaching", "Broke today"] = (
        "Broke today" if today_close >= H_old else "Approaching"
    )

    return DecadeBreakoutState(
        asof_date=asof, ticker=ticker, eligible=True, reason="eligible",
        H_old=H_old, H_old_date=H_old_date, H_old_age_years=H_old_age_years,
        H_recent=H_recent, H_recent_date=H_recent_date,
        today_close=today_close, gap_pct=gap_pct, status=status,
        lookback_years=lookback_years, proximity_pct=proximity_pct,
        min_history_years=min_history_years,
    )


def decade_breakout_state_for_ticker(
    ticker: str,
    asof_date: pd.Timestamp | str,
    ohlcv_dir: Optional[Path] = None,
    **kwargs,
) -> DecadeBreakoutState:
    """Wrapper: load parquet, call decade_breakout_state."""
    if ohlcv_dir is None:
        ohlcv_dir = Path(__file__).resolve().parent.parent / "data" / "ohlcv"
    path = ohlcv_dir / f"{ticker}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No OHLCV parquet for {ticker} at {path}")
    df = pd.read_parquet(path)
    return decade_breakout_state(df, asof_date, ticker=ticker, **kwargs)


# ---------- Internal ----------

def _validate(df: pd.DataFrame) -> None:
    required = {"high", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Index must be DatetimeIndex")


def _fail(
    asof: pd.Timestamp,
    ticker: str,
    reason: str,
    lookback_years: int,
    proximity_pct: float,
    min_history_years: int,
    today_close: float = 0.0,
) -> DecadeBreakoutState:
    return DecadeBreakoutState(
        asof_date=asof, ticker=ticker, eligible=False, reason=reason,
        today_close=today_close,
        lookback_years=lookback_years, proximity_pct=proximity_pct,
        min_history_years=min_history_years,
    )
