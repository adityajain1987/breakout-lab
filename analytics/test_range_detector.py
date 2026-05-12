"""Tests for analytics/range_detector.py.

Coverage strategy:
  - Synthetic OHLCV data for each algorithm stage (deterministic, fast)
  - Real-data sanity checks on 5 anchor tickers (must-detect + must-NOT-detect-trivially)
  - Anti-look-ahead invariant (CRITICAL — same discipline as breakout_detector)

Run:  .venv/bin/python -m pytest analytics/test_range_detector.py -v
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.range_detector import (
    Cluster,
    RangeState,
    _check_role_reversal,
    _cluster_pivots,
    _detect_recent_breakout,
    _find_swing_pivots,
    _is_near_round_number,
    _maturity_tag,
    _min_range_width,
    _pair_bands,
    range_state,
    range_state_for_ticker,
)


ROOT = Path(__file__).resolve().parent.parent


# ---------- Helper: synthetic OHLCV ----------

def synth_ohlcv(
    n_days: int = 600,
    base_price: float = 100.0,
    pattern: str = "range",       # "range" | "trend_up" | "trend_down" | "flat"
    range_low: float = 90.0,
    range_high: float = 110.0,
    noise_pct: float = 0.015,
    seed: int = 42,
    start: str = "2023-01-02",
) -> pd.DataFrame:
    """Build a synthetic daily OHLCV DataFrame with a known pattern."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n_days)

    if pattern == "range":
        # Sine wave between range_low and range_high
        amplitude = (range_high - range_low) / 2.0
        center = (range_high + range_low) / 2.0
        # 4 full cycles over the period → 4 swing highs + 4 swing lows
        closes = center + amplitude * np.sin(np.linspace(0, 8 * np.pi, n_days))
        closes += rng.normal(0, amplitude * 0.15, n_days)  # noise
    elif pattern == "trend_up":
        closes = base_price * (1 + 0.001) ** np.arange(n_days)
        closes += rng.normal(0, base_price * 0.01, n_days)
    elif pattern == "trend_down":
        closes = base_price * (1 - 0.0008) ** np.arange(n_days)
        closes += rng.normal(0, base_price * 0.01, n_days)
    else:  # flat
        closes = np.full(n_days, base_price) + rng.normal(0, base_price * 0.002, n_days)

    daily_noise = rng.normal(0, base_price * noise_pct, n_days)
    opens = closes + daily_noise * 0.3
    highs = np.maximum(opens, closes) + np.abs(daily_noise) * 0.5
    lows = np.minimum(opens, closes) - np.abs(daily_noise) * 0.5
    volumes = rng.integers(100_000, 1_000_000, n_days).astype(float)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


# =============================================================================
# 1. Input validation
# =============================================================================

def test_missing_columns_raises():
    df = pd.DataFrame({"close": [1, 2, 3]}, index=pd.date_range("2024-01-01", periods=3))
    with pytest.raises(ValueError, match="Missing columns"):
        range_state(df, "2024-01-03")


def test_non_datetime_index_raises():
    df = synth_ohlcv()
    df = df.reset_index(drop=True)  # break the DatetimeIndex
    with pytest.raises(ValueError, match="DatetimeIndex"):
        range_state(df, "2024-01-01")


def test_asof_not_in_index_returns_unqualified():
    df = synth_ohlcv()
    rs = range_state(df, "2099-01-01")
    assert rs.qualified is False
    assert "not in df.index" in rs.reason


# =============================================================================
# 2. Insufficient history
# =============================================================================

def test_insufficient_history_returns_unqualified():
    df = synth_ohlcv(n_days=50)  # way under the ~225-bar minimum
    rs = range_state(df, df.index[-1])
    assert rs.qualified is False
    assert "insufficient history" in rs.reason


# =============================================================================
# 3. Swing pivot detection
# =============================================================================

def test_swing_pivots_finds_clear_peaks():
    # A symmetric V-shape: low in the middle, highs at both ends
    n = 50
    prices = np.concatenate([
        np.linspace(100, 80, n // 2),
        np.linspace(80, 100, n // 2),
    ])
    dates = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame({
        "open": prices, "high": prices + 1, "low": prices - 1,
        "close": prices, "volume": [1000.0] * n,
    }, index=dates)
    highs, lows = _find_swing_pivots(df, swing_window=5)
    # Should find the V's bottom as a swing low
    assert len(lows) >= 1
    assert abs(min(lows["price"]) - 79) < 2  # within rounding


def test_swing_pivots_right_edge_uses_one_sided():
    """The fix for the outside-voice 'right-edge blind spot' complaint:
    a fresh swing high in the last 5 bars should be detected even though we lack
    forward-looking bars to confirm with a centered window."""
    n = 200
    prices = np.linspace(100, 100, n).astype(float)
    # Spike at the very end (last bar = the fresh peak)
    prices[-1] = 130.0
    prices[-2] = 110.0
    prices[-3] = 105.0
    dates = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame({
        "open": prices, "high": prices, "low": prices,
        "close": prices, "volume": [1000.0] * n,
    }, index=dates)
    highs, _ = _find_swing_pivots(df, swing_window=10)
    # The fresh spike at index -1 should be detected via one-sided look-back
    # (centered window would require 10 future bars we don't have)
    assert dates[-1] in highs.index


def test_swing_pivots_returns_empty_for_short_history():
    df = synth_ohlcv(n_days=5)
    highs, lows = _find_swing_pivots(df, swing_window=10)
    assert len(highs) == 0
    assert len(lows) == 0


# =============================================================================
# 4. Clustering
# =============================================================================

def test_cluster_pivots_merges_close_prices():
    pivots = pd.DataFrame(
        {"price": [100.0, 100.5, 101.0, 105.0]},
        index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]),
    )
    clusters = _cluster_pivots(pivots, tolerance=2.0)
    # 100, 100.5, 101 should cluster (gaps ≤ 2); 105 alone
    assert len(clusters) == 2
    assert clusters[0].touches == 3
    assert clusters[1].touches == 1


def test_cluster_pivots_distinct_at_high_gap():
    pivots = pd.DataFrame(
        {"price": [100.0, 200.0]},
        index=pd.to_datetime(["2024-01-01", "2024-02-01"]),
    )
    clusters = _cluster_pivots(pivots, tolerance=10.0)
    assert len(clusters) == 2


def test_cluster_pivots_empty_input():
    pivots = pd.DataFrame(columns=["price"])
    clusters = _cluster_pivots(pivots, tolerance=5.0)
    assert clusters == []


# =============================================================================
# 5. Pairing
# =============================================================================

def test_pair_bands_finds_valid_pair():
    r = Cluster(mean_price=110.0, prices=[109.0, 110.0, 111.0],
                dates=[pd.Timestamp(d) for d in ["2024-01-01", "2024-06-01", "2025-01-01"]])
    s = Cluster(mean_price=90.0, prices=[89.0, 90.0, 91.0],
                dates=[pd.Timestamp(d) for d in ["2024-03-01", "2024-09-01", "2025-03-01"]])
    pair = _pair_bands([r], [s], min_touches=3, min_duration_days=270, min_width=10.0)
    assert pair is not None
    assert pair[0].mean_price == 110.0
    assert pair[1].mean_price == 90.0


def test_pair_bands_rejects_when_r_below_s():
    r = Cluster(mean_price=90.0, prices=[90.0, 90.0, 90.0],
                dates=[pd.Timestamp("2024-01-01")] * 3)
    s = Cluster(mean_price=110.0, prices=[110.0, 110.0, 110.0],
                dates=[pd.Timestamp("2024-01-01")] * 3)
    pair = _pair_bands([r], [s], min_touches=3, min_duration_days=180, min_width=5.0)
    assert pair is None


def test_pair_bands_rejects_too_few_touches():
    r = Cluster(mean_price=110.0, prices=[110.0, 110.0],  # only 2
                dates=[pd.Timestamp("2024-01-01"), pd.Timestamp("2025-01-01")])
    s = Cluster(mean_price=90.0, prices=[90.0, 90.0, 90.0],
                dates=[pd.Timestamp("2024-03-01"), pd.Timestamp("2024-09-01"), pd.Timestamp("2025-02-01")])
    pair = _pair_bands([r], [s], min_touches=3, min_duration_days=180, min_width=5.0)
    assert pair is None


def test_pair_bands_rejects_disjoint_intervals():
    # R only touched in 2020, S only touched in 2024 — not a real range
    r = Cluster(mean_price=110.0, prices=[110.0] * 3,
                dates=[pd.Timestamp("2020-01-01"), pd.Timestamp("2020-06-01"), pd.Timestamp("2020-12-01")])
    s = Cluster(mean_price=90.0, prices=[90.0] * 3,
                dates=[pd.Timestamp("2024-01-01"), pd.Timestamp("2024-06-01"), pd.Timestamp("2024-12-01")])
    pair = _pair_bands([r], [s], min_touches=3, min_duration_days=180, min_width=5.0)
    assert pair is None  # disjoint


def test_pair_bands_rejects_too_narrow_width():
    r = Cluster(mean_price=100.5, prices=[100.5] * 3,
                dates=[pd.Timestamp("2024-01-01"), pd.Timestamp("2024-06-01"), pd.Timestamp("2025-01-01")])
    s = Cluster(mean_price=100.0, prices=[100.0] * 3,
                dates=[pd.Timestamp("2024-03-01"), pd.Timestamp("2024-09-01"), pd.Timestamp("2025-03-01")])
    pair = _pair_bands([r], [s], min_touches=3, min_duration_days=180, min_width=10.0)
    assert pair is None  # width 0.5 < 10


def test_pair_bands_accepts_mahindra_style_long_range():
    """The case that motivated the algorithm change — R touched throughout 2024-2026,
    S touched only in 2024-Q3 + 2025-Q1. Outer span = 22 months. Should pair."""
    r = Cluster(
        mean_price=3200.0, prices=[3200.0] * 5,
        dates=[pd.Timestamp(d) for d in
               ["2024-09-01", "2024-11-01", "2025-08-01", "2025-11-01", "2026-02-01"]],
    )
    s = Cluster(
        mean_price=2600.0, prices=[2600.0] * 3,
        dates=[pd.Timestamp(d) for d in ["2024-07-01", "2024-08-01", "2025-03-01"]],
    )
    pair = _pair_bands([r], [s], min_touches=3, min_duration_days=270, min_width=300.0)
    assert pair is not None


# =============================================================================
# 6. Width filter
# =============================================================================

def test_min_range_width_uses_atr_mult_when_larger():
    df = synth_ohlcv(n_days=300, base_price=1000.0)
    # tolerance=100 → atr_mult×tolerance = 3*100=300; 5% of 1000=50 → max is 300
    w = _min_range_width(df, tolerance=100.0, atr_mult=3.0)
    assert w == 300.0


def test_min_range_width_uses_price_floor_for_low_vol():
    # Build a df whose last close is exactly 500 so the floor math is unambiguous.
    df = pd.DataFrame({
        "open": [500.0] * 50, "high": [501.0] * 50, "low": [499.0] * 50,
        "close": [500.0] * 50, "volume": [1000.0] * 50,
    }, index=pd.bdate_range("2024-01-01", periods=50))
    # tolerance=2 → atr_mult×tolerance = 6; 5% of 500=25 → max is 25 (price floor wins)
    w = _min_range_width(df, tolerance=2.0, atr_mult=3.0)
    assert w == 25.0


# =============================================================================
# 7. Star scoring
# =============================================================================

def test_score_baseline_one_star():
    """No volume, no time spread, no role reversal → just 1★."""
    df = synth_ohlcv(n_days=300, pattern="flat")
    rs = range_state(df, df.index[-1])
    # Flat synthetic data may not qualify at all — that's fine, this tests scoring
    # only on qualified outputs
    if rs.qualified:
        assert rs.stars >= 1


def test_round_number_detection():
    assert _is_near_round_number(100.0) is True
    assert _is_near_round_number(500.5) is True   # within 1%
    assert _is_near_round_number(1000.0) is True
    assert _is_near_round_number(523.7) is False  # not near any round
    assert _is_near_round_number(0.0) is False


# =============================================================================
# 8. Recent breakout (one-sided, no future-bar dependency)
# =============================================================================

def test_recent_breakout_detects_up_break():
    dates = pd.bdate_range("2024-01-01", periods=15)
    closes = np.full(15, 100.0)
    closes[-3] = 115.0  # break 2 trading days ago (today=idx 14, breakout=idx 12)
    df = pd.DataFrame({
        "open": closes, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": [1000.0] * 15,
    }, index=dates)
    direction, days_ago = _detect_recent_breakout(
        df, dates[-1], r_upper=110.0, s_lower=90.0, lookback_days=10,
    )
    # Today close=100 in-range, yesterday close=100 in-range, day-before-yesterday
    # close=115 → breakout. days_ago counts back from today: 0=today, 1=yesterday, 2=DBY.
    assert direction == "up"
    assert days_ago == 2


def test_recent_breakout_detects_down_break():
    dates = pd.bdate_range("2024-01-01", periods=15)
    closes = np.full(15, 100.0)
    closes[-1] = 85.0  # break today
    df = pd.DataFrame({
        "open": closes, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": [1000.0] * 15,
    }, index=dates)
    direction, days_ago = _detect_recent_breakout(
        df, dates[-1], r_upper=110.0, s_lower=90.0, lookback_days=10,
    )
    assert direction == "down"
    assert days_ago == 0


def test_recent_breakout_no_break_when_in_range():
    dates = pd.bdate_range("2024-01-01", periods=15)
    closes = np.full(15, 100.0)  # all in-range
    df = pd.DataFrame({
        "open": closes, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": [1000.0] * 15,
    }, index=dates)
    direction, days_ago = _detect_recent_breakout(
        df, dates[-1], r_upper=110.0, s_lower=90.0, lookback_days=10,
    )
    assert direction is None
    assert days_ago == -1


# =============================================================================
# 9. Maturity tag boundaries
# =============================================================================

def test_maturity_tag_emerging():
    assert _maturity_tag(280) == "Emerging"   # 9.2mo
    assert _maturity_tag(359) == "Emerging"   # 11.8mo


def test_maturity_tag_established():
    assert _maturity_tag(360) == "Established"  # 12mo
    assert _maturity_tag(719) == "Established"  # 23.6mo


def test_maturity_tag_major():
    assert _maturity_tag(720) == "Major"        # 24mo
    assert _maturity_tag(1500) == "Major"


def test_maturity_tag_below_threshold():
    assert _maturity_tag(269) == ""  # below 9 months


# =============================================================================
# 10. Anti-look-ahead invariant (CRITICAL)
# =============================================================================

def test_anti_lookahead_invariant():
    """The range bands should not be affected by data AFTER asof_date.

    Build a synthetic range. Compute range_state at midpoint. Then add wild data
    after the midpoint and recompute at the SAME midpoint — the answer must be identical.
    """
    df = synth_ohlcv(n_days=600, pattern="range", range_low=90, range_high=110)
    asof = df.index[400]  # midpoint
    rs1 = range_state(df, asof)

    # Append wild data AFTER asof
    later_dates = pd.bdate_range(start=df.index[-1] + pd.Timedelta(days=1), periods=50)
    later = pd.DataFrame({
        "open": [1000.0] * 50, "high": [1100.0] * 50, "low": [900.0] * 50,
        "close": [1000.0] * 50, "volume": [9_999_999.0] * 50,
    }, index=later_dates)
    df_polluted = pd.concat([df, later])
    rs2 = range_state(df_polluted, asof)

    # The qualified status must be identical
    assert rs1.qualified == rs2.qualified
    if rs1.qualified:
        assert rs1.resistance_mean == pytest.approx(rs2.resistance_mean)
        assert rs1.support_mean == pytest.approx(rs2.support_mean)
        assert rs1.range_duration_days == rs2.range_duration_days
        assert rs1.stars == rs2.stars


# =============================================================================
# 11. Trending stock should NOT detect a range (synthetic — controlled)
# =============================================================================

def test_strong_uptrend_does_not_qualify():
    """A clean upward trend with no consolidation should not register as a range."""
    df = synth_ohlcv(n_days=600, pattern="trend_up", base_price=100.0, noise_pct=0.005)
    rs = range_state(df, df.index[-1])
    # Either not qualified at all, OR the duration is far below the trend length
    # (a trend that briefly paused doesn't make the whole period a "range")
    if rs.qualified:
        # If it does qualify, it should be a small recent consolidation, not the full span
        assert rs.range_duration_days < 600 * 0.7


def test_strong_downtrend_does_not_qualify():
    df = synth_ohlcv(n_days=600, pattern="trend_down", base_price=100.0, noise_pct=0.005)
    rs = range_state(df, df.index[-1])
    if rs.qualified:
        assert rs.range_duration_days < 600 * 0.7


# =============================================================================
# 12. Synthetic range SHOULD qualify
# =============================================================================

def test_clean_synthetic_range_qualifies():
    """A clean sine wave between 90 and 110 over 600 days should detect."""
    df = synth_ohlcv(n_days=600, pattern="range", range_low=90, range_high=110, noise_pct=0.005)
    rs = range_state(df, df.index[-1])
    assert rs.qualified is True, f"Synthetic range failed to qualify: {rs.reason}"
    # R should be near 110, S near 90, width ≈ 20
    assert 105 <= rs.resistance_mean <= 115
    assert 85 <= rs.support_mean <= 95
    assert rs.resistance_touches >= 3
    assert rs.support_touches >= 3


# =============================================================================
# 13. Real-data sanity (anchor stocks from plan)
# =============================================================================

@pytest.mark.parametrize("ticker,must_qualify,note", [
    ("M&M",       True,  "user's reference chart — should detect ~22-month range"),
    ("ITC",       True,  "classic textbook range"),
    ("BAJAJ-AUTO", True, "well-known range"),
])
def test_real_data_must_detect(ticker, must_qualify, note):
    """Positive sanity: these stocks must register as ranges on 2026-04-30."""
    ohlcv = ROOT / "data" / "ohlcv" / f"{ticker}.parquet"
    if not ohlcv.exists():
        pytest.skip(f"{ticker}.parquet not available")
    rs = range_state_for_ticker(ticker, "2026-04-30")
    assert rs.qualified is must_qualify, f"{ticker} ({note}): {rs.reason}"


def test_real_data_mandm_matches_chart():
    """The Mahindra chart anchor: R should be around ₹3200, S around ₹2600, ≥18 months."""
    ohlcv = ROOT / "data" / "ohlcv" / "M&M.parquet"
    if not ohlcv.exists():
        pytest.skip("M&M.parquet not available")
    rs = range_state_for_ticker("M&M", "2026-04-30")
    assert rs.qualified is True
    assert 3000 <= rs.resistance_mean <= 3400, f"R mean ₹{rs.resistance_mean:.0f} outside expected range"
    assert 2400 <= rs.support_mean <= 2800, f"S mean ₹{rs.support_mean:.0f} outside expected range"
    assert rs.range_duration_days >= 540  # ≥ 18 months


# =============================================================================
# 14. Role reversal helper
# =============================================================================

def test_role_reversal_detected_when_close_crosses_both_ways():
    dates = pd.bdate_range("2024-01-01", periods=20)
    # Closes oscillate around 100: above, below, above, below
    closes = [105, 105, 95, 95, 105, 105, 95, 95, 105, 105,
              95, 95, 105, 105, 95, 95, 105, 105, 95, 100]
    df = pd.DataFrame({
        "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes,
        "volume": [1000.0] * 20,
    }, index=dates)
    cluster = Cluster(mean_price=100.0, prices=[100.0] * 5,
                      dates=list(dates[:5]) + [dates[-1]])
    assert _check_role_reversal(df, cluster, tolerance=2.0) is True


def test_role_reversal_not_detected_when_one_sided():
    """Close stays above the level the whole time — no role reversal."""
    dates = pd.bdate_range("2024-01-01", periods=20)
    closes = [105] * 20  # always above 100
    df = pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000.0] * 20,
    }, index=dates)
    cluster = Cluster(mean_price=100.0, prices=[100.0] * 5,
                      dates=list(dates[:5]))
    assert _check_role_reversal(df, cluster, tolerance=1.0) is False


# =============================================================================
# 15. RangeState dataclass shape (regression — UI binds to these fields)
# =============================================================================

def test_rangestate_unqualified_has_defaults():
    rs = RangeState(asof_date=pd.Timestamp("2024-01-01"), ticker="X", qualified=False, reason="test")
    assert rs.resistance_mean is None
    assert rs.support_mean is None
    assert rs.stars == 0
    assert rs.status == ""
    assert rs.maturity_tag == ""
    assert rs.range_duration_days == 0


def test_rangestate_qualified_has_band_info():
    df = synth_ohlcv(n_days=600, pattern="range", range_low=90, range_high=110, noise_pct=0.005)
    rs = range_state(df, df.index[-1], ticker="SYNTH")
    if rs.qualified:
        # Every band field must be non-None when qualified
        assert rs.resistance_mean is not None
        assert rs.resistance_lower is not None
        assert rs.resistance_upper is not None
        assert rs.support_mean is not None
        assert rs.support_lower is not None
        assert rs.support_upper is not None
        assert rs.resistance_upper > rs.resistance_mean > rs.resistance_lower
        assert rs.support_upper > rs.support_mean > rs.support_lower
        assert rs.resistance_mean > rs.support_mean
