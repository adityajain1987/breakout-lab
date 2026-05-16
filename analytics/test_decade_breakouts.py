"""
Unit tests for decade_breakouts.

Strategy:
  1. Synthetic OHLCV with hand-crafted shapes — each gate tested in isolation
  2. Anti-look-ahead invariant: today's high is NOT used to define H_recent
  3. Real-data smoke: RELIANCE (long history) should at least produce a determinate state

Run: cd ~/Desktop/Claude/breakout-lab && .venv/bin/python -m pytest analytics/test_decade_breakouts.py -v
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.decade_breakouts import (
    DecadeBreakoutState,
    decade_breakout_state,
    decade_breakout_state_for_ticker,
)


# ---------- Helpers ----------

def make_df(
    highs: list[float],
    closes: list[float] | None = None,
    start: str = "2010-01-01",
) -> pd.DataFrame:
    """Build minimal OHLCV df. Default close = high × 0.99. Business-day index."""
    n = len(highs)
    if closes is None:
        closes = [h * 0.99 for h in highs]
    lows = [c * 0.99 for c in closes]
    df = pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1_000_000] * n,
    })
    df.index = pd.date_range(start=start, periods=n, freq="B")
    df.index.name = "date"
    return df


# ---------- 1. History-length gate ----------

def test_rejects_short_history():
    """5 years of data, asks for 11-year minimum — rejected."""
    df = make_df(highs=[100.0] * (5 * 252), start="2020-01-01")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, min_history_years=11)
    assert not state.eligible
    assert "insufficient history" in state.reason


def test_accepts_sufficient_history():
    """13 years of flat data → passes history gate but fails the touched-in-window check
    (H_old == H_recent in this trivial case)."""
    df = make_df(highs=[100.0] * (13 * 252), start="2013-01-01")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, min_history_years=11)
    # Fails for a different reason (touched), not history.
    assert "insufficient history" not in state.reason


# ---------- 2. The classic decade-breakout shape ----------

def test_classic_decade_breakout_approaching():
    """
    Shape:
      Year 1-2: stock peaks at ₹100 (set 12 years ago — H_old).
      Year 3-12: stock trades 70-80 the entire time, never touches 100.
      Today: close at 98.5 (1.5% below H_old).
    Expected: eligible, status "Approaching".
    """
    n_old = 2 * 252       # 2 years of "old" data with peak
    n_recent = 10 * 252   # 10 years of "recent" data well below peak
    old_highs = [50.0] * 100 + [100.0] + [50.0] * (n_old - 101)   # one bar at 100
    recent_highs = [80.0] * n_recent   # always below 100
    highs = old_highs + recent_highs
    # Close on the LAST day = 98.5 (within 2% of 100)
    closes = [h * 0.99 for h in highs]
    closes[-1] = 98.5
    df = make_df(highs=highs, closes=closes, start="2014-01-01")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, proximity_pct=2.0,
                                  min_history_years=11)
    assert state.eligible, f"expected eligible, got: {state.reason}"
    assert state.status == "Approaching"
    assert state.H_old == pytest.approx(100.0)
    assert state.today_close == pytest.approx(98.5)
    assert state.gap_pct == pytest.approx(1.5, abs=0.01)
    assert state.H_old_age_years >= 10.0


def test_classic_decade_breakout_broke_today():
    """Same setup, but today's close is at 101 — first cross in 10+ years."""
    n_old = 2 * 252
    n_recent = 10 * 252
    highs = [50.0] * 100 + [100.0] + [50.0] * (n_old - 101) + [80.0] * n_recent
    closes = [h * 0.99 for h in highs]
    closes[-1] = 101.0
    highs[-1] = 102.0   # today's high also breaks (but we EXCLUDE today from H_recent)
    df = make_df(highs=highs, closes=closes, start="2014-01-01")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, proximity_pct=2.0,
                                  min_history_years=11)
    assert state.eligible, f"expected eligible, got: {state.reason}"
    assert state.status == "Broke today"
    assert state.gap_pct < 0   # close above H_old → negative gap


# ---------- 3. Touched-in-window disqualification ----------

def test_disqualified_by_intraday_touch():
    """
    Setup: peak 100 set 12 years ago. 5 years ago, the stock had ONE intraday high of 100
    (close was 95, but the high tagged the level). User said 'not once intraday' — must fail.
    """
    n_old = 2 * 252
    n_recent = 10 * 252
    old_highs = [50.0] * 100 + [100.0] + [50.0] * (n_old - 101)
    recent_highs = [80.0] * n_recent
    # Plant a single intraday touch 5 years before asof
    touch_idx = n_recent // 2
    recent_highs[touch_idx] = 100.0   # intraday high == H_old → disqualifies
    highs = old_highs + recent_highs
    closes = [h * 0.99 for h in highs]
    closes[-1] = 98.5
    df = make_df(highs=highs, closes=closes, start="2014-01-01")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, proximity_pct=2.0,
                                  min_history_years=11)
    assert not state.eligible
    assert "touched in lookback" in state.reason


def test_disqualified_by_intraday_exceedance():
    """Even one intraday print ABOVE H_old in the window → disqualified."""
    n_old = 2 * 252
    n_recent = 10 * 252
    highs = [50.0] * 100 + [100.0] + [50.0] * (n_old - 101) + [80.0] * n_recent
    highs[n_old + 1000] = 102.0   # intraday high pierced H_old years ago
    closes = [h * 0.99 for h in highs]
    closes[-1] = 98.5
    df = make_df(highs=highs, closes=closes, start="2014-01-01")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, proximity_pct=2.0,
                                  min_history_years=11)
    assert not state.eligible
    assert "touched in lookback" in state.reason


# ---------- 4. Proximity gate ----------

def test_too_far_from_high_rejected():
    """H_old=100, today close=85 (15% below). Not in proximity → reject."""
    n_old = 2 * 252
    n_recent = 10 * 252
    highs = [50.0] * 100 + [100.0] + [50.0] * (n_old - 101) + [80.0] * n_recent
    closes = [h * 0.99 for h in highs]
    closes[-1] = 85.0
    df = make_df(highs=highs, closes=closes, start="2014-01-01")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, proximity_pct=2.0,
                                  min_history_years=11)
    assert not state.eligible
    assert "too far" in state.reason
    assert state.H_old == pytest.approx(100.0)
    assert state.gap_pct == pytest.approx(15.0, abs=0.01)


def test_proximity_slider_loosen():
    """Same setup as above (close=85, 15% gap), but proximity_pct=20 → eligible."""
    n_old = 2 * 252
    n_recent = 10 * 252
    highs = [50.0] * 100 + [100.0] + [50.0] * (n_old - 101) + [80.0] * n_recent
    closes = [h * 0.99 for h in highs]
    closes[-1] = 85.0
    df = make_df(highs=highs, closes=closes, start="2014-01-01")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, proximity_pct=20.0,
                                  min_history_years=11)
    assert state.eligible, f"expected eligible at 20% proximity: {state.reason}"
    assert state.status == "Approaching"


# ---------- 5. Anti-look-ahead invariant ----------

def test_today_high_does_not_count_as_recent_touch():
    """
    A breakout day has today's high > H_old. That MUST NOT cause self-disqualification.
    Today is the breakout candidate, not part of the lookback window.
    """
    n_old = 2 * 252
    n_recent = 10 * 252
    highs = [50.0] * 100 + [100.0] + [50.0] * (n_old - 101) + [80.0] * n_recent
    highs[-1] = 105.0   # today's intraday high pierces H_old
    closes = [h * 0.99 for h in highs]
    closes[-1] = 103.0
    df = make_df(highs=highs, closes=closes, start="2014-01-01")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, proximity_pct=2.0,
                                  min_history_years=11)
    assert state.eligible, f"today's high should be excluded from H_recent: {state.reason}"
    assert state.status == "Broke today"


# ---------- 6. Edge cases ----------

def test_asof_not_in_index():
    df = make_df(highs=[100.0] * 100, start="2014-01-01")
    state = decade_breakout_state(df, "2099-01-01", lookback_years=10)
    assert not state.eligible
    assert "not in df.index" in state.reason


def test_missing_columns_raises():
    df = pd.DataFrame({"foo": [1, 2, 3]}, index=pd.date_range("2024-01-01", periods=3))
    with pytest.raises(ValueError, match="Missing columns"):
        decade_breakout_state(df, df.index[-1])


def test_non_datetime_index_raises():
    df = pd.DataFrame({"high": [1, 2], "close": [1, 2]})
    with pytest.raises(ValueError, match="DatetimeIndex"):
        decade_breakout_state(df, 0)


# ---------- 7. Real-data smoke ----------

REAL_OHLCV = Path(__file__).resolve().parent.parent / "data" / "ohlcv"


@pytest.mark.skipif(not (REAL_OHLCV / "RELIANCE.parquet").exists(),
                    reason="real OHLCV cache not present")
def test_real_data_runs():
    """RELIANCE has 21y history. Whatever the answer, the function must terminate cleanly."""
    df = pd.read_parquet(REAL_OHLCV / "RELIANCE.parquet")
    asof = df.index[-1]
    state = decade_breakout_state(df, asof, lookback_years=10, proximity_pct=2.0,
                                  min_history_years=11, ticker="RELIANCE")
    assert isinstance(state, DecadeBreakoutState)
    assert state.H_old is not None   # has enough history → H_old populated regardless of eligibility
    assert state.today_close > 0


@pytest.mark.skipif(not (REAL_OHLCV / "RELIANCE.parquet").exists(),
                    reason="real OHLCV cache not present")
def test_real_data_loose_proximity_finds_something_or_explains():
    """At 100% proximity, every stock with sufficient history + untouched window qualifies."""
    state = decade_breakout_state_for_ticker(
        "RELIANCE", pd.read_parquet(REAL_OHLCV / "RELIANCE.parquet").index[-1],
        lookback_years=10, proximity_pct=100.0, min_history_years=11,
    )
    # RELIANCE's high has been refreshed recently — expect "touched in lookback".
    assert not state.eligible
    assert "touched in lookback" in state.reason
