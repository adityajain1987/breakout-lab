"""Unit tests for the regime filter."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.simulator import regime_active


def make_idx(closes: list[float], start: str = "2020-01-01") -> pd.DataFrame:
    df = pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [0] * len(closes),
    })
    df.index = pd.date_range(start=start, periods=len(closes), freq="B")
    df.index.name = "date"
    return df


def test_regime_active_when_index_above_sma():
    """200 days at 100, then today at 110 → today > SMA(100), regime ON."""
    closes = [100.0] * 200 + [110.0]
    df = make_idx(closes)
    assert regime_active(df, df.index[-1], ma_period=200) is True


def test_regime_inactive_when_index_below_sma():
    """200 days at 100, then today at 90 → today < SMA(100), regime OFF."""
    closes = [100.0] * 200 + [90.0]
    df = make_idx(closes)
    assert regime_active(df, df.index[-1], ma_period=200) is False


def test_regime_defaults_to_active_with_insufficient_history():
    """Less than ma_period days → can't compute SMA, default to active."""
    closes = [100.0] * 50
    df = make_idx(closes)
    assert regime_active(df, df.index[-1], ma_period=200) is True


def test_regime_defaults_to_active_when_asof_missing():
    closes = [100.0] * 200
    df = make_idx(closes)
    missing_date = df.index[-1] + pd.Timedelta(days=10)
    assert regime_active(df, missing_date, ma_period=200) is True


def test_regime_uses_history_through_asof_inclusive():
    """SMA calculation should include asof_date's own close (no look-ahead beyond that)."""
    closes = list(range(100, 300))  # walk up from 100 to 299
    df = make_idx(closes)
    # On day 199: close=299, last 200 closes mean = (100+...+299)/200 = 199.5
    # 299 > 199.5 → True
    assert regime_active(df, df.index[199], ma_period=200) is True
    # On day 50: close=150, last 50 closes 100..149 mean = 124.5
    # ma_period=50 here means we use last 50: 101..150, mean = 125.5
    # 150 > 125.5 → True
    assert regime_active(df, df.index[50], ma_period=50) is True
