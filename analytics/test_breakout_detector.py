"""
Unit tests for breakout_detector.

Strategy:
  1. Synthetic OHLCV with known answers (each component tested in isolation)
  2. Composite score on perfect / zero / mixed setups
  3. Anti-look-ahead invariant: today's row must not influence "history" computations
  4. Real-data scan on MAZDOCK (known multi-bagger with breakouts in 2024-2025)

Run: .venv/bin/python -m pytest analytics/test_breakout_detector.py -v
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.breakout_detector import (
    BreakoutState,
    breakout_state,
    breakout_state_for_ticker,
    scan_breakouts,
)


# ---------- Helpers ----------

def make_df(
    closes: list[float],
    volumes: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    """Build OHLCV df. Defaults: high = close × 1.01, low = close × 0.99, volume = 1M."""
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    df = pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    df.index = pd.date_range(start=start, periods=n, freq="B")
    df.index.name = "date"
    return df


# ---------- 1. Swing high break ----------

def test_swing_high_break_clean():
    """20 days at ₹100, then day 21 closes at ₹102 (above 20-day high of ~₹101)."""
    closes = [100.0] * 20 + [102.0]
    highs = [101.0] * 20 + [102.5]
    lows = [99.0] * 20 + [101.5]
    df = make_df(closes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof, swing_lookback=20)
    assert st.swing_high_break is True
    assert st.swing_high == 101.0


def test_swing_high_no_break_just_below():
    """Day 21 closes at ₹100.99 — below the 20-day high of ₹101."""
    closes = [100.0] * 20 + [100.99]
    highs = [101.0] * 20 + [101.0]
    lows = [99.0] * 20 + [100.5]
    df = make_df(closes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert st.swing_high_break is False


def test_swing_high_no_break_already_above():
    """Already above resistance yesterday — not a fresh break today."""
    closes = [100.0] * 19 + [102.0, 103.0]  # broke yesterday, continued today
    highs = [101.0] * 19 + [102.5, 103.5]
    lows = [99.0] * 19 + [101.5, 102.5]
    df = make_df(closes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    # Yesterday's close (102) > 20-day high computed from window [iloc 1..20] = 102 → not a break today
    assert st.swing_high_break is False


# ---------- 2. Cycle (52-week) high break ----------

def test_cycle_high_break():
    """260 days oscillating in [95, 105], then day 261 hits ₹106."""
    np.random.seed(42)
    closes = list(100 + 5 * np.sin(np.linspace(0, 8 * np.pi, 260))) + [106.0]
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    df = make_df(closes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof, cycle_lookback=252)
    assert st.cycle_high_break is True


def test_cycle_high_no_break_short_history():
    """Only 100 days of history — cycle window has < 252 days but still computable from what exists."""
    closes = [100.0] * 100 + [120.0]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    df = make_df(closes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof, cycle_lookback=252)
    # cycle_high = max of all available history high (since < 252 days available) = 101
    # close 120 > 101, yesterday close 100 ≤ 101 → break
    assert st.cycle_high_break is True


# ---------- 3. HVN break (uses volume profile) ----------

def test_hvn_break_with_clear_resistance():
    """
    Realistic accumulation scenario:
      - 58 days Gaussian-clustered around ₹150 (creates HVN ~₹150)
      - Yesterday: pullback to ₹146 (below HVN, sets up the break)
      - Today: gap+rally to ₹156 with strong volume (fresh HVN break)
    """
    rows = []
    np.random.seed(7)
    for i in range(58):
        c = 150.0 + np.random.normal(0.0, 1.5)
        rows.append({"open": c, "close": c, "high": c + 0.3, "low": c - 0.3, "volume": 5_000_000})
    # Yesterday: pullback below the HVN
    rows.append({"open": 147.0, "close": 146.0, "high": 147.5, "low": 145.8, "volume": 4_000_000})
    # Today: fresh upward break above the HVN
    rows.append({"open": 147.5, "close": 156.0, "high": 156.5, "low": 147.0, "volume": 12_000_000})

    df = pd.DataFrame(rows)
    df.index = pd.date_range(start="2024-01-01", periods=len(rows), freq="B")
    df.index.name = "date"
    asof = df.index[-1]
    st = breakout_state(
        df, asof,
        profile_lookback=60,
        profile_kwargs={"bin_width_pct": 0.005, "hvn_significance": 1.1},
    )
    assert st.hvn_break is True, f"Expected HVN break, got {st}"
    assert st.hvn_level is not None
    assert 148 <= st.hvn_level <= 152


# ---------- 4. Volume ratio ----------

def test_volume_ratio_high():
    closes = [100.0] * 20 + [105.0]
    volumes = [1_000_000] * 20 + [5_000_000]  # 5x avg
    df = make_df(closes, volumes=volumes)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert abs(st.volume_ratio - 5.0) < 0.01


def test_volume_ratio_normal():
    closes = [100.0] * 20 + [105.0]
    volumes = [1_000_000] * 21  # equal vol
    df = make_df(closes, volumes=volumes)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert abs(st.volume_ratio - 1.0) < 0.01


# ---------- 5. Close-in-range % ----------

def test_close_in_range_at_high():
    closes = [100.0] * 20 + [110.0]
    highs = [c * 1.01 for c in closes[:-1]] + [110.0]   # close == high
    lows = [c * 0.99 for c in closes[:-1]] + [100.0]
    df = make_df(closes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert abs(st.close_in_range_pct - 1.0) < 0.001


def test_close_in_range_at_low():
    closes = [100.0] * 20 + [100.0]
    highs = [c * 1.01 for c in closes[:-1]] + [110.0]
    lows = [c * 0.99 for c in closes[:-1]] + [100.0]    # close == low
    df = make_df(closes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert abs(st.close_in_range_pct - 0.0) < 0.001


def test_close_in_range_single_price_day():
    """High == low (extreme circuit-hit case) → neutral 0.5."""
    closes = [100.0] * 20 + [100.0]
    highs = [c * 1.01 for c in closes[:-1]] + [100.0]
    lows = [c * 0.99 for c in closes[:-1]] + [100.0]
    df = make_df(closes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert st.close_in_range_pct == 0.5


# ---------- 6. SMA filters ----------

def test_above_both_dmas():
    closes = list(np.linspace(50, 100, 200)) + [105.0]  # uptrend, today above both
    df = make_df(closes)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert st.above_50dma is True
    assert st.above_200dma is True


def test_below_both_dmas():
    closes = list(np.linspace(150, 100, 200)) + [95.0]  # downtrend, today below both
    df = make_df(closes)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert st.above_50dma is False
    assert st.above_200dma is False


# ---------- 7. Composite score ----------

def test_score_zero_no_breaks():
    """Stuck inside the range, no breaks, mediocre everything → low score."""
    closes = [100.0] * 30
    df = make_df(closes)
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert st.breakout_score == 0.0


def test_score_high_with_swing_break_volume_and_strong_close():
    """Flat baseline 30d at ₹100 (high 101) → today closes at 102 with 5x volume + close at top of range."""
    closes = [100.0] * 30 + [102.0]
    highs = [101.0] * 30 + [102.5]
    lows = [99.0] * 30 + [99.5]
    volumes = [1_000_000] * 30 + [5_000_000]
    df = make_df(closes, volumes=volumes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof, swing_lookback=20)
    assert st.swing_high_break is True
    # Swing weight 0.3 × 100 × vol_mult(2.0) × range_mult(1.0) × ma_mult(<1 since 30d isn't enough for 200dma)
    # = 0.3 × 100 × 2.0 × 1.0 × 0.85 ≈ 51
    assert st.breakout_score > 30


# ---------- 8. Anti-look-ahead invariant ----------

def test_no_look_ahead_swing_high():
    """
    Today has a huge spike that creates a new high — but it should NOT raise the swing_high
    used for the break check. Swing high is from history STRICTLY BEFORE today.
    """
    closes = [100.0] * 20 + [200.0]   # massive spike
    highs = [101.0] * 20 + [205.0]
    lows = [99.0] * 20 + [195.0]
    df = make_df(closes, highs=highs, lows=lows)
    asof = df.index[-1]
    st = breakout_state(df, asof, swing_lookback=20)
    # swing_high should be 101 (max of past 20 highs), not 205
    assert st.swing_high == 101.0
    assert st.swing_high_break is True


# ---------- 9. Real-data sanity ----------

OHLCV_DIR = Path(__file__).resolve().parent.parent / "data" / "ohlcv"


@pytest.mark.skipif(
    not (OHLCV_DIR / "MAZDOCK.parquet").exists(),
    reason="Run data/fetch_samples.py (P0.0) first",
)
def test_mazdock_scan_finds_breakouts():
    """
    MAZDOCK ran from ~₹500 in mid-2023 to ~₹3700 peak in 2024-2025.
    A scan over 2024 must surface at least 5 breakouts above min_score=40.
    """
    df = pd.read_parquet(OHLCV_DIR / "MAZDOCK.parquet")
    breakouts = scan_breakouts(df, "2024-01-01", "2024-12-31", min_score=40.0)
    assert len(breakouts) >= 5
    # Highest-scored breakout should have at least one resistance break
    top = breakouts[0]
    assert top.swing_high_break or top.cycle_high_break or top.hvn_break


@pytest.mark.skipif(
    not (OHLCV_DIR / "RELIANCE.parquet").exists(),
    reason="Run data/fetch_samples.py (P0.0) first",
)
def test_reliance_recent_state_is_consistent():
    """RELIANCE state on a known recent day has all fields populated, score in [0, 100]."""
    df = pd.read_parquet(OHLCV_DIR / "RELIANCE.parquet")
    asof = df.index[-1]
    st = breakout_state(df, asof)
    assert 0.0 <= st.breakout_score <= 100.0
    assert st.volume > 0
    assert st.sma_50 > 0
    assert st.sma_200 > 0
