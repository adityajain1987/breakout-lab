"""
Range detector — horizontal trading ranges (rectangle patterns) on daily bars.

Companion to breakout_detector.py — same primitives, opposite question:
  breakout_detector: "is the stock breaking out of a level TODAY?"
  range_detector:    "is the stock STILL OSCILLATING in a level over months?"

Pipeline (per plan-eng-review 2026-05-12 — Option G + D):

    df + asof_date
        │
        ▼
    [ 1. anti-look-ahead slice ]         history = df.iloc[:asof_idx]
        │
        ▼
    [ 2. swing pivots ]                  fractal centered window N=10
        │                                + one-sided look-back for last 10 bars
        │                                (right-edge fix — no future-bar dependency)
        ▼
    [ 3. ATR-tolerance clustering ]      1D agglomerative merge by 1.5×ATR
        │                                ATR excludes circuit-hit days
        ▼
    [ 4. pair clusters ]                 R-cluster above S-cluster,
        │                                ≥3 touches each, concurrent ≥9mo
        ▼
    [ 5. vol-normalised width filter ]   width ≥ 1.5× annualised vol
        │
        ▼
    [ 6. volume-profile cross-check ]    (option G — expensive, only on candidates)
        │                                does an HVN sit on the R or S band?
        ▼
    [ 7. star score (4 ranks) + 💰 ]    structure / volume / time-spread / role-reversal
        │                                round-number = icon, not a rank tier
        ▼
    [ 8. recent breakout (≤10 days) ]    one-sided check vs the existing band
        │                                no future-bar dependency
        ▼
    [ 9. quarantine guard ]              Tier 1 split-anomaly in lookback → invalidate
        │
        ▼
    [ 10. maturity tag ]                 9-12m / 12-24m / 24+m
        │
        ▼
    RangeState

All "history" computations use bars STRICTLY BEFORE asof_date (matching breakout_detector).
The Recent Breakout check is one-sided (last 10 closes vs existing band) so the right-edge
blind spot of centered fractals does NOT affect it.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import pandas as pd

from analytics.volume_profile import volume_profile
from backtest.atr import true_range


# ---------- Data classes ----------

@dataclass
class Cluster:
    """A group of swing pivots clustered within ATR-tolerance distance."""
    mean_price: float
    prices: list[float]
    dates: list[pd.Timestamp]

    @property
    def touches(self) -> int:
        return len(self.prices)

    @property
    def date_range_days(self) -> int:
        if not self.dates:
            return 0
        return (max(self.dates) - min(self.dates)).days

    @property
    def upper(self) -> float:
        return max(self.prices)

    @property
    def lower(self) -> float:
        return min(self.prices)


@dataclass
class RangeState:
    """Output of range_state() — everything the dashboard and scanner need."""
    asof_date: pd.Timestamp
    ticker: str
    qualified: bool                      # is this a valid range?
    reason: str                          # why qualified or not (debugging + UI hover)

    # Band info — only populated when qualified
    resistance_mean: Optional[float] = None
    resistance_lower: Optional[float] = None  # band lower edge (tolerance)
    resistance_upper: Optional[float] = None  # band upper edge (tolerance)
    resistance_touches: int = 0
    resistance_touch_dates: list[pd.Timestamp] = field(default_factory=list)
    resistance_touch_prices: list[float] = field(default_factory=list)
    support_mean: Optional[float] = None
    support_lower: Optional[float] = None
    support_upper: Optional[float] = None
    support_touches: int = 0
    support_touch_dates: list[pd.Timestamp] = field(default_factory=list)
    support_touch_prices: list[float] = field(default_factory=list)

    # Duration + maturity
    range_duration_days: int = 0
    maturity_tag: Literal["", "Emerging", "Established", "Major"] = ""
    last_touch_days_ago: int = -1        # days since the most recent R or S touch

    # Star score (1-4) and round-number icon
    stars: int = 0
    round_number_flag: bool = False
    role_reversal_flag: bool = False
    volume_node_confirmed: bool = False

    # Today's classification
    status: Literal["", "In-Range", "Recent Breakout"] = ""
    breakout_direction: Literal["", "up", "down"] = ""
    breakout_days_ago: int = -1

    # Data quality
    quarantine_flag: bool = False        # ⚠️ marker
    invalidated_by_quarantine: bool = False  # Tier 1 inside lookback

    # Reproducibility
    swing_window: int = 10
    atr_tolerance_mult: float = 1.5
    lookback_days: int = 0
    width_pct_of_price: float = 0.0


# ---------- Public API ----------

def range_state(
    df: pd.DataFrame,
    asof_date: pd.Timestamp | str,
    *,
    ticker: str = "",
    swing_window: int = 10,
    atr_tolerance_mult: float = 1.5,
    min_duration_days: int = 270,        # ~9 months (3 earnings cycles)
    min_touches: int = 3,
    min_width_atr_mult: float = 3.0,     # width ≥ 3× tolerance (R and S clearly separate)
    breakout_lookback_days: int = 10,
    max_lookback_years: int = 5,         # ignore touches older than this — focus on recent ranges
    quarantine_db: Optional[Path] = None,
) -> RangeState:
    """
    Detect a horizontal trading range as of asof_date.

    df: full OHLCV history with DatetimeIndex. Columns: open, high, low, close, volume.
    asof_date: the day to evaluate. Must exist in df.index.

    Returns RangeState. If qualified=False, the .reason field explains why.
    """
    _validate(df)
    asof_date = pd.Timestamp(asof_date)
    if asof_date not in df.index:
        return RangeState(asof_date=asof_date, ticker=ticker, qualified=False,
                          reason=f"asof_date {asof_date.date()} not in df.index")

    today_idx = df.index.get_loc(asof_date)
    full_history = df.iloc[:today_idx]
    today_close = float(df.iloc[today_idx]["close"])

    # Cap lookback to the last N years — avoid finding ancient pre-2010 ranges
    # when the user cares about current structure. ATR uses full history (more stable).
    cutoff = asof_date - pd.Timedelta(days=int(365 * max_lookback_years))
    history = full_history.loc[full_history.index >= cutoff] if max_lookback_years > 0 else full_history

    # Need ≥ min_duration_days of history. Use 1.2x buffer for swing window edges.
    needed_bars = int(min_duration_days * 252 / 365 * 1.2)  # ~225 trading days for 9 months
    if len(history) < needed_bars:
        return RangeState(asof_date=asof_date, ticker=ticker, qualified=False,
                          reason=f"insufficient history: {len(history)} bars < {needed_bars} needed")

    # 1. Get quarantine ⚠️ flag (any flag for this ticker → display warning icon).
    # Tier 1 invalidation happens LATER, scoped to the range's actual date window —
    # a 2005 split anomaly should NOT kill a 2024-2026 range.
    quarantine_flag = _has_any_flag(quarantine_db, ticker)

    # 2. ATR (excluding circuit-hit flagged days) — the tolerance unit.
    # Uses FULL history (more stable estimate) even though pivots use capped window.
    atr = _atr_clean(full_history, quarantine_db, ticker, n=14)
    if np.isnan(atr) or atr <= 0:
        return RangeState(asof_date=asof_date, ticker=ticker, qualified=False,
                          quarantine_flag=quarantine_flag,
                          reason=f"insufficient ATR: {atr}")
    tolerance = atr_tolerance_mult * atr

    # 3. Swing pivots — centered for history, one-sided for last 10 bars (right-edge fix)
    swing_highs, swing_lows = _find_swing_pivots(history, swing_window)
    if len(swing_highs) < min_touches or len(swing_lows) < min_touches:
        return RangeState(asof_date=asof_date, ticker=ticker, qualified=False,
                          quarantine_flag=quarantine_flag,
                          reason=f"too few swings: {len(swing_highs)} highs, {len(swing_lows)} lows")

    # 4. Cluster pivots
    high_clusters = _cluster_pivots(swing_highs, tolerance)
    low_clusters = _cluster_pivots(swing_lows, tolerance)

    # 5. Find best (R, S) pair
    width_min = _min_range_width(history, tolerance, min_width_atr_mult)
    pair = _pair_bands(
        high_clusters, low_clusters,
        min_touches=min_touches,
        min_duration_days=min_duration_days,
        min_width=width_min,
    )
    if pair is None:
        return RangeState(asof_date=asof_date, ticker=ticker, qualified=False,
                          quarantine_flag=quarantine_flag,
                          reason="no valid R/S pair: insufficient touches, concurrent overlap, or width")
    r_cluster, s_cluster = pair

    # 5b. Scoped Tier 1 invalidation — does a split-anomaly fall INSIDE the range window?
    range_start, range_end = _range_window_dates(r_cluster, s_cluster)
    if _tier1_in_window(quarantine_db, ticker, range_start, range_end):
        return RangeState(asof_date=asof_date, ticker=ticker, qualified=False,
                          quarantine_flag=quarantine_flag, invalidated_by_quarantine=True,
                          reason=f"Tier 1 anomaly inside range period {range_start.date()}–{range_end.date()}")

    # 6. Volume-profile cross-check (option G — expensive, only runs here)
    vp_window = _range_lookback_window(history, r_cluster, s_cluster)
    vp_confirmed = _volume_profile_confirms(vp_window, r_cluster, s_cluster, tolerance)

    # 7. Star score
    stars, role_reversal = _score_band_pair(history, r_cluster, s_cluster, vp_confirmed, tolerance)
    round_number = (_is_near_round_number(r_cluster.mean_price)
                    or _is_near_round_number(s_cluster.mean_price))

    # 8. Recent breakout (one-sided — only checks last 10 closes vs existing band)
    r_band_upper = r_cluster.mean_price + tolerance
    r_band_lower = r_cluster.mean_price - tolerance
    s_band_upper = s_cluster.mean_price + tolerance
    s_band_lower = s_cluster.mean_price - tolerance
    direction, days_ago = _detect_recent_breakout(
        df, asof_date, r_band_upper, s_band_lower, breakout_lookback_days,
    )
    if direction:
        status = "Recent Breakout"
    elif s_band_lower <= today_close <= r_band_upper:
        status = "In-Range"
    else:
        # Outside band but breakout happened > 10d ago: not flagged
        status = "In-Range" if abs(today_close - r_cluster.mean_price) < tolerance * 2 else ""

    # 9. Duration + maturity — combined span of touches (matches _pair_bands logic)
    range_start, range_end = _range_window_dates(r_cluster, s_cluster)
    duration_days = (range_end - range_start).days
    last_touch_days_ago = (asof_date - range_end).days
    maturity = _maturity_tag(duration_days)

    width = r_cluster.mean_price - s_cluster.mean_price
    width_pct = width / today_close * 100.0

    return RangeState(
        asof_date=asof_date,
        ticker=ticker,
        qualified=True,
        reason="qualified",
        resistance_mean=r_cluster.mean_price,
        resistance_lower=r_band_lower,
        resistance_upper=r_band_upper,
        resistance_touches=r_cluster.touches,
        resistance_touch_dates=list(r_cluster.dates),
        resistance_touch_prices=list(r_cluster.prices),
        support_mean=s_cluster.mean_price,
        support_lower=s_band_lower,
        support_upper=s_band_upper,
        support_touches=s_cluster.touches,
        support_touch_dates=list(s_cluster.dates),
        support_touch_prices=list(s_cluster.prices),
        range_duration_days=duration_days,
        maturity_tag=maturity,
        last_touch_days_ago=last_touch_days_ago,
        stars=stars,
        round_number_flag=round_number,
        role_reversal_flag=role_reversal,
        volume_node_confirmed=vp_confirmed,
        status=status,
        breakout_direction=direction or "",
        breakout_days_ago=days_ago if direction else -1,
        quarantine_flag=quarantine_flag,
        invalidated_by_quarantine=False,
        swing_window=swing_window,
        atr_tolerance_mult=atr_tolerance_mult,
        lookback_days=duration_days,
        width_pct_of_price=width_pct,
    )


def range_state_for_ticker(
    ticker: str,
    asof_date: pd.Timestamp | str,
    ohlcv_dir: Optional[Path] = None,
    quarantine_db: Optional[Path] = None,
    **kwargs,
) -> RangeState:
    """Wrapper: load parquet, call range_state."""
    if ohlcv_dir is None:
        ohlcv_dir = Path(__file__).resolve().parent.parent / "data" / "ohlcv"
    if quarantine_db is None:
        default_q = Path(__file__).resolve().parent.parent / "quarantine" / "quarantine.db"
        quarantine_db = default_q if default_q.exists() else None
    path = ohlcv_dir / f"{ticker}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No OHLCV parquet for {ticker} at {path}")
    df = pd.read_parquet(path)
    return range_state(df, asof_date, ticker=ticker, quarantine_db=quarantine_db, **kwargs)


# ---------- Internal: pivots ----------

def _find_swing_pivots(
    history: pd.DataFrame, swing_window: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Identify swing highs and swing lows.

    For the bulk of history: bar is a swing high if it is the max within a centered
    window of (2N+1) bars. Standard fractal definition.

    For the most recent N bars: cannot complete the forward half of the window, so
    use a one-sided look-back check (bar > all bars in last N). This avoids the
    right-edge blind spot called out in the plan-eng-review outside-voice pass.

    Returns: (swing_highs_df, swing_lows_df), each indexed by date with one column 'price'.
    """
    n = len(history)
    N = swing_window
    if n < N + 1:
        return (pd.DataFrame(columns=["price"]), pd.DataFrame(columns=["price"]))

    highs = history["high"].to_numpy(dtype=np.float64)
    lows = history["low"].to_numpy(dtype=np.float64)
    idx = history.index

    high_mask = np.zeros(n, dtype=bool)
    low_mask = np.zeros(n, dtype=bool)

    # Centered window (need N bars on each side)
    for i in range(N, n - N):
        left_h = highs[i - N:i].max()
        right_h = highs[i + 1:i + N + 1].max()
        if highs[i] > left_h and highs[i] >= right_h:
            high_mask[i] = True
        left_l = lows[i - N:i].min()
        right_l = lows[i + 1:i + N + 1].min()
        if lows[i] < left_l and lows[i] <= right_l:
            low_mask[i] = True

    # Right-edge: one-sided look-back for last N bars
    for i in range(max(N, n - N), n):
        left_h = highs[i - N:i].max()
        if highs[i] > left_h:
            high_mask[i] = True
        left_l = lows[i - N:i].min()
        if lows[i] < left_l:
            low_mask[i] = True

    swing_highs = pd.DataFrame({"price": highs[high_mask]}, index=idx[high_mask])
    swing_lows = pd.DataFrame({"price": lows[low_mask]}, index=idx[low_mask])
    return swing_highs, swing_lows


# ---------- Internal: clustering ----------

def _cluster_pivots(pivots: pd.DataFrame, tolerance: float) -> list[Cluster]:
    """
    1D agglomerative clustering on prices. Sort by price, merge adjacent pivots
    whose gap ≤ tolerance. Returns Cluster objects.
    """
    if len(pivots) == 0:
        return []

    sorted_pivots = pivots.sort_values("price")
    clusters: list[Cluster] = []
    cur_prices: list[float] = [float(sorted_pivots.iloc[0]["price"])]
    cur_dates: list[pd.Timestamp] = [sorted_pivots.index[0]]

    for i in range(1, len(sorted_pivots)):
        p = float(sorted_pivots.iloc[i]["price"])
        d = sorted_pivots.index[i]
        # Compare to the CURRENT cluster's max (since list is sorted, last added = max)
        if p - cur_prices[-1] <= tolerance:
            cur_prices.append(p)
            cur_dates.append(d)
        else:
            clusters.append(Cluster(
                mean_price=float(np.mean(cur_prices)),
                prices=cur_prices,
                dates=cur_dates,
            ))
            cur_prices = [p]
            cur_dates = [d]

    clusters.append(Cluster(
        mean_price=float(np.mean(cur_prices)),
        prices=cur_prices,
        dates=cur_dates,
    ))
    return clusters


# ---------- Internal: pairing ----------

def _pair_bands(
    high_clusters: list[Cluster],
    low_clusters: list[Cluster],
    *,
    min_touches: int,
    min_duration_days: int,
    min_width: float,
) -> Optional[tuple[Cluster, Cluster]]:
    """
    Find the strongest (R, S) pair where:
      - R.mean > S.mean
      - both have ≥ min_touches touches
      - their touch-date ranges overlap ≥ min_duration_days
      - R.mean − S.mean ≥ min_width
    Score by total touches + overlap months. Returns highest-scoring pair, or None.
    """
    best: Optional[tuple[Cluster, Cluster]] = None
    best_score = -1.0

    qualified_r = [c for c in high_clusters if c.touches >= min_touches]
    qualified_s = [c for c in low_clusters if c.touches >= min_touches]

    for r in qualified_r:
        r_start, r_end = min(r.dates), max(r.dates)
        for s in qualified_s:
            if r.mean_price <= s.mean_price:
                continue
            if (r.mean_price - s.mean_price) < min_width:
                continue
            s_start, s_end = min(s.dates), max(s.dates)
            # Intervals must intersect at all (otherwise R is from 2020 and S from 2024 —
            # not a real range, just two unrelated levels).
            if max(r_start, s_start) > min(r_end, s_end):
                continue
            # Range duration = combined span of all touches. A real range can have R
            # touched mostly early and S touched mostly later (e.g., Mahindra: S tested
            # in 2024, R tested through 2025-26). What matters is the OUTER span.
            combined_start = min(r_start, s_start)
            combined_end = max(r_end, s_end)
            span_days = (combined_end - combined_start).days
            if span_days < min_duration_days:
                continue
            score = r.touches + s.touches + (span_days / 30.0)
            if score > best_score:
                best_score = score
                best = (r, s)

    return best


def _min_range_width(history: pd.DataFrame, tolerance: float, atr_mult: float) -> float:
    """
    Minimum range width = max(5% of price, atr_mult × tolerance).

    Two safety floors:
    - 5% of price: filters tiny noise ranges on any stock (Reliance, low-vol PSUs)
    - 3 × tolerance: the R-band upper and S-band lower must not overlap → R and S are
      DISTINCT levels, not the same level with noise

    For a typical large-cap (ATR ₹80, tolerance ₹120, price ₹3000):
      5% × 3000 = 150, 3 × 120 = 360 → min width 360 (12% of price)
    For a low-vol stock (ATR ₹5, tolerance ₹7.5, price ₹500):
      5% × 500 = 25, 3 × 7.5 = 22.5 → min width 25 (5% of price)
    """
    close = float(history["close"].iloc[-1])
    return max(0.05 * close, atr_mult * tolerance)


# ---------- Internal: volume profile cross-check ----------

def _range_lookback_window(
    history: pd.DataFrame, r: Cluster, s: Cluster
) -> pd.DataFrame:
    """Slice history to the date range covered by the R+S touches."""
    all_dates = list(r.dates) + list(s.dates)
    start = min(all_dates)
    end = max(all_dates)
    return history.loc[start:end]


def _volume_profile_confirms(
    window: pd.DataFrame, r: Cluster, s: Cluster, tolerance: float
) -> bool:
    """
    Run volume_profile on the range's date window. If an HVN sits within tolerance of
    BOTH R.mean and S.mean, the level is confirmed by volume. Returns True if confirmed.

    Cheap-first: skip if window too small.
    """
    if len(window) < 20:
        return False
    try:
        vp = volume_profile(window)
    except Exception:
        return False
    if not vp.hvns:
        return False
    r_match = any(abs(h - r.mean_price) <= tolerance for h in vp.hvns)
    s_match = any(abs(h - s.mean_price) <= tolerance for h in vp.hvns)
    return r_match and s_match


# ---------- Internal: scoring ----------

def _score_band_pair(
    history: pd.DataFrame,
    r: Cluster,
    s: Cluster,
    volume_confirmed: bool,
    tolerance: float,
) -> tuple[int, bool]:
    """
    Compute 4-rank star score.

    ★    Structure: peaks line up (we got here, so always true)
    ★★   + Volume node confirms
    ★★★  + Touches spread ≥ 9 months
    ★★★★ + Role reversal — a level acted as both R and S at different times

    Returns (stars, role_reversal_flag).
    """
    # Additive scoring — each independent signal adds a star.
    # ★ baseline (structure: clusters exist, we got here)
    # +★ volume node confirms
    # +★ touches spread ≥ 9 months (mature range)
    # +★ role reversal — level acted as both R and S at different times
    # → max 4★. Sequential was confusing (high-quality role reversal stuck at 1★ if
    # volume profile didn't have a clean HVN). Additive surfaces each strength.
    stars = 1

    if volume_confirmed:
        stars += 1

    all_dates = list(r.dates) + list(s.dates)
    span_days = (max(all_dates) - min(all_dates)).days
    if span_days >= 270:
        stars += 1

    role_reversal = _check_role_reversal(history, r, tolerance) or \
                    _check_role_reversal(history, s, tolerance)
    if role_reversal:
        stars += 1

    return stars, role_reversal


def _check_role_reversal(history: pd.DataFrame, cluster: Cluster, tolerance: float) -> bool:
    """
    A level shows role reversal when price touched it AS resistance (touched from below
    and went back down) at some point AND touched it AS support (touched from above and
    went back up) at another point — across the cluster's date range.

    Pragmatic check: does the daily close cross this level from below at least once AND
    from above at least once within the cluster's date range?
    """
    if not cluster.dates:
        return False
    start = min(cluster.dates)
    end = max(cluster.dates)
    window = history.loc[start:end, "close"]
    if len(window) < 5:
        return False
    level = cluster.mean_price
    above = window > level
    crosses = above.astype(int).diff().fillna(0)
    crossed_up = (crosses == 1).any()       # close went above
    crossed_down = (crosses == -1).any()     # close went below
    return bool(crossed_up and crossed_down)


def _is_near_round_number(price: float) -> bool:
    """
    Is the price within 1% of a meaningful round number (100, 500, 1000, 5000, etc.)?
    Indian retail traders watch these levels (₹1000 anchors are real).
    """
    if price <= 0:
        return False
    tol = price * 0.01
    candidates = []
    # Powers of 10 anchors
    for power in [100, 500, 1000, 5000, 10000]:
        nearest = round(price / power) * power
        if nearest > 0:
            candidates.append(nearest)
    return any(abs(price - c) <= tol for c in candidates)


# ---------- Internal: recent breakout (one-sided) ----------

def _detect_recent_breakout(
    df: pd.DataFrame,
    asof_date: pd.Timestamp,
    r_upper: float,
    s_lower: float,
    lookback_days: int,
) -> tuple[Optional[str], int]:
    """
    Check the last N trading days (including asof) for a close beyond the band.

    Returns ('up', days_ago) if close > r_upper, ('down', days_ago) if close < s_lower,
    else (None, -1). days_ago = 0 means today, 1 = yesterday, etc.

    One-sided check — uses only past+current bars. No future-bar dependency.
    """
    today_idx = df.index.get_loc(asof_date)
    start_idx = max(0, today_idx - lookback_days + 1)
    recent = df.iloc[start_idx:today_idx + 1]
    for offset in range(len(recent) - 1, -1, -1):
        close = float(recent.iloc[offset]["close"])
        days_ago = len(recent) - 1 - offset
        if close > r_upper:
            return ("up", days_ago)
        if close < s_lower:
            return ("down", days_ago)
    return (None, -1)


# ---------- Internal: quarantine + ATR cleanup ----------

def _has_any_flag(quarantine_db: Optional[Path], ticker: str) -> bool:
    """Does this ticker have ANY quarantine flag? Used to show the ⚠️ icon."""
    if quarantine_db is None or not quarantine_db.exists() or not ticker:
        return False
    try:
        with sqlite3.connect(quarantine_db) as conn:
            cur = conn.execute(
                "SELECT 1 FROM flags WHERE symbol = ? LIMIT 1", (ticker,)
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def _tier1_in_window(
    quarantine_db: Optional[Path],
    ticker: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> bool:
    """
    Does a Tier 1 flag (split anomaly, dummy ticker) fall INSIDE the given date window?

    Tier 1 events destroy horizontal levels — a 1:1 split in the middle of a range means
    every "level" before the split is at the wrong price. Range must be invalidated.
    A 2005 anomaly does NOT invalidate a 2024-2026 range (scoped check).
    """
    if quarantine_db is None or not quarantine_db.exists() or not ticker:
        return False
    try:
        with sqlite3.connect(quarantine_db) as conn:
            df = pd.read_sql(
                "SELECT date FROM flags WHERE symbol = ? AND tier = 1",
                conn, params=(ticker,),
            )
    except Exception:
        return False
    if df.empty:
        return False
    df["date_ts"] = pd.to_datetime(df["date"], errors="coerce")
    in_window = df[(df["date_ts"] >= start) & (df["date_ts"] <= end)]
    return not in_window.empty


def _range_window_dates(r: Cluster, s: Cluster) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Earliest and latest touch date across the R+S clusters."""
    all_dates = list(r.dates) + list(s.dates)
    return (min(all_dates), max(all_dates))


def _atr_clean(
    history: pd.DataFrame,
    quarantine_db: Optional[Path],
    ticker: str,
    n: int = 14,
) -> float:
    """
    ATR computed over the last n bars, excluding any circuit-hit flagged days.

    Circuit-hit days have artificially distorted true range (price was locked at the band).
    Including them inflates ATR and widens the tolerance band, letting noise sneak in.
    """
    if len(history) < n:
        return float("nan")
    tr = true_range(history)
    # Drop circuit-hit dates
    if quarantine_db is not None and quarantine_db.exists() and ticker:
        try:
            with sqlite3.connect(quarantine_db) as conn:
                circuit = pd.read_sql(
                    "SELECT date FROM flags WHERE symbol = ? AND check_name = 'circuit_hits'",
                    conn, params=(ticker,),
                )
            if not circuit.empty:
                bad_dates = pd.to_datetime(circuit["date"], errors="coerce").dropna()
                tr = tr.drop(index=bad_dates.tolist(), errors="ignore")
        except Exception:
            pass
    recent = tr.tail(n).dropna()
    if len(recent) == 0:
        return float("nan")
    return float(recent.mean())


# ---------- Internal: maturity ----------

def _maturity_tag(duration_days: int) -> Literal["", "Emerging", "Established", "Major"]:
    """9-12m = Emerging, 12-24m = Established, 24m+ = Major."""
    if duration_days < 270:
        return ""
    if duration_days < 360:
        return "Emerging"
    if duration_days < 720:
        return "Established"
    return "Major"


# ---------- Internal: validation ----------

def _validate(df: pd.DataFrame) -> None:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Index must be DatetimeIndex")
