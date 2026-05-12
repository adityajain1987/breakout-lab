"""ATR unit tests."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.atr import true_range, compute_atr, atr_at


def make_ohlcv(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.index = pd.date_range(start="2024-01-01", periods=len(rows), freq="B")
    df.index.name = "date"
    return df


def test_true_range_uses_max_of_three_ranges():
    """First row TR is NaN; subsequent rows max(H-L, |H-prev_C|, |L-prev_C|)."""
    df = make_ohlcv([
        {"high": 100.0, "low": 95.0, "close": 98.0},     # TR = NaN (first row)
        {"high": 110.0, "low": 99.0, "close": 105.0},    # max(11, |110-98|, |99-98|) = 12
        {"high": 108.0, "low": 102.0, "close": 103.0},   # max(6, |108-105|, |102-105|) = 6
    ])
    tr = true_range(df)
    assert pd.isna(tr.iloc[0])
    assert tr.iloc[1] == 12.0
    assert tr.iloc[2] == 6.0


def test_compute_atr_n_period_sma():
    """ATR = SMA of TR over n periods. First n-1 values NaN."""
    df = make_ohlcv([
        {"high": 100.0, "low": 95.0, "close": 98.0},
        {"high": 102.0, "low": 96.0, "close": 100.0},   # TR ~6
        {"high": 105.0, "low": 99.0, "close": 102.0},   # TR ~6
        {"high": 108.0, "low": 100.0, "close": 105.0},  # TR ~8
    ])
    atr = compute_atr(df, n=3)
    assert pd.isna(atr.iloc[0])
    assert pd.isna(atr.iloc[1])
    assert pd.isna(atr.iloc[2])
    assert atr.iloc[3] == pytest.approx((6 + 6 + 8) / 3, abs=0.01)


def test_atr_at_returns_value_when_history_sufficient():
    df = make_ohlcv([
        {"high": 100.0 + i, "low": 95.0 + i, "close": 98.0 + i}
        for i in range(20)
    ])
    val = atr_at(df, df.index[18], n=14)
    assert val > 0
    assert not pd.isna(val)
