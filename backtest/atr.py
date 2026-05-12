"""
ATR (Average True Range) — volatility-based position sizing input.

True Range for day D = max(
    high_D - low_D,
    abs(high_D - close_{D-1}),
    abs(low_D - close_{D-1}),
)
ATR = simple moving average of True Range over N days (default 14, Wilder's classic).

Used for:
  - Stop loss: entry - K × ATR  (default K=2)
  - Target:    entry + M × ATR  (default M=4 for 2:1 reward:risk)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """Compute true range series. df must have high, low, close.
    First row is explicitly NaN (no prev_close, TR undefined per Wilder)."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = df["close"].astype(float).shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    if len(tr) > 0:
        tr.iloc[0] = float("nan")  # explicit: TR[0] undefined per Wilder's definition
    return tr


def compute_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Simple-moving-average ATR. First n-1 rows will be NaN."""
    return true_range(df).rolling(n).mean()


def atr_at(df: pd.DataFrame, asof_date: pd.Timestamp, n: int = 14) -> float:
    """ATR value at a specific date. Returns NaN if insufficient history."""
    if asof_date not in df.index:
        return float("nan")
    atr_series = compute_atr(df.loc[:asof_date], n=n)
    return float(atr_series.iloc[-1]) if len(atr_series) > 0 else float("nan")
