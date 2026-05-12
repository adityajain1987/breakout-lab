"""
Quarantine check functions.

Each check is a pure function: takes a DataFrame (and optionally other context),
returns a list of flag dicts ready for `insert_flags`.

Flag dict shape:
  {
    "date": "YYYY-MM-DD" or None,   # date-specific or full-ticker
    "symbol": "TICKER" or None,     # ticker-specific or all-tickers
    "check_name": "split_anomaly",
    "severity": "tier1",
    "tier": 1,
    "details": "Free-form explanation",
  }
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd


# ---------- Tier 1: Must-pass (data-corruption risks) ----------

def check_split_anomaly(symbol: str, df: pd.DataFrame, threshold_pct: float = 50.0) -> list[dict]:
    """
    Flag any day where day-over-day return exceeds ±threshold_pct (default ±50%).
    With auto_adjust=True, splits are pre-baked. So a 50%+ jump means EITHER:
      - A real 50%+ one-day move (rare — Yes Bank, Adani crisis days)
      - yfinance missed a corporate action and prices are misaligned
    Either way, worth a human review flag.
    """
    if len(df) < 2 or "close" not in df.columns:
        return []
    closes = df["close"].astype(float)
    returns = closes.pct_change().abs() * 100
    spikes = returns[returns > threshold_pct]
    return [{
        "date": str(d.date()),
        "symbol": symbol,
        "check_name": "split_anomaly",
        "severity": "tier1",
        "tier": 1,
        "details": f"day-over-day return = {returns[d]:.1f}% (close {closes.loc[:d].iloc[-2]:.2f} → {closes[d]:.2f}). "
                   f"Likely real big-move day OR missed corporate action. Human-review.",
    } for d in spikes.index]


def check_dummy_ticker(symbol: str, df: pd.DataFrame) -> list[dict]:
    """
    Flag DUMMY* tickers (demerger artifacts) and full-history-zero-volume tickers.
    These should never enter analytics.
    """
    flags: list[dict] = []
    if re.match(r"^DUMMY", symbol, re.IGNORECASE):
        flags.append({
            "date": None, "symbol": symbol,
            "check_name": "dummy_ticker_name",
            "severity": "tier1", "tier": 1,
            "details": "Ticker name matches DUMMY* pattern (likely demerger placeholder).",
        })
    if "volume" in df.columns and len(df) > 0 and (df["volume"] == 0).all():
        flags.append({
            "date": None, "symbol": symbol,
            "check_name": "all_zero_volume",
            "severity": "tier1", "tier": 1,
            "details": f"All {len(df)} rows have zero volume. Not a tradeable instrument.",
        })
    return flags


# ---------- Tier 2: Signal-distortion days (flag, don't exclude) ----------

def check_circuit_hits(symbol: str, df: pd.DataFrame, vol_drop_threshold: float = 0.5) -> list[dict]:
    """
    Heuristic: abs(day_change) ≈ {5, 10, 20}% AND volume < vol_drop_threshold × 20-day avg.
    Locked-circuit days have low volume because bid/ask are stuck at the band.
    """
    if len(df) < 22 or "close" not in df.columns:
        return []
    closes = df["close"].astype(float)
    vols = df["volume"].astype(float)
    pct_change = closes.pct_change().abs() * 100
    vol_20d = vols.rolling(20).mean()
    vol_ratio = vols / vol_20d

    # Circuit bands with ±0.05% tolerance
    on_band = (
        ((pct_change > 4.95) & (pct_change < 5.05)) |
        ((pct_change > 9.95) & (pct_change < 10.05)) |
        ((pct_change > 19.95) & (pct_change < 20.05))
    )
    suspect = on_band & (vol_ratio < vol_drop_threshold)
    flags = []
    for d in df.index[suspect]:
        band = round(pct_change[d], 0)
        flags.append({
            "date": str(d.date()),
            "symbol": symbol,
            "check_name": "circuit_hit",
            "severity": "tier2",
            "tier": 2,
            "details": f"~{band}% move with volume {vol_ratio[d]:.1%} of 20d avg. "
                       f"Likely locked at circuit; price discovery suspended.",
        })
    return flags


def is_fno_expiry(date: pd.Timestamp) -> bool:
    """Last Thursday of the month = monthly F&O expiry. Volume artificially elevated from rolls."""
    # Find last Thursday: start from end of month, walk back to first Thursday found
    month_end = (date + pd.offsets.MonthEnd(0))
    days_back = (month_end.dayofweek - 3) % 7  # Thursday is dayofweek=3
    last_thursday = month_end - pd.Timedelta(days=days_back)
    return date.normalize() == last_thursday.normalize()


# ---------- Tier 3: Universe-build filters ----------

def check_recent_ipo(symbol: str, df: pd.DataFrame, min_trading_days: int = 250) -> list[dict]:
    """Less than ~1 year of trading data → exclude from analytics universe."""
    if len(df) < min_trading_days:
        return [{
            "date": None, "symbol": symbol,
            "check_name": "recent_ipo",
            "severity": "tier3", "tier": 3,
            "details": f"Only {len(df)} trading days available (< {min_trading_days} required for stable volume profile).",
        }]
    return []


def check_suspended_periods(symbol: str, df: pd.DataFrame, run_length: int = 5) -> list[dict]:
    """Detect runs of ≥ run_length consecutive zero-volume days = trading halt period."""
    if "volume" not in df.columns or len(df) < run_length:
        return []
    is_zero = (df["volume"].astype(float) == 0).to_numpy()
    flags = []
    n = len(is_zero)
    i = 0
    while i < n:
        if is_zero[i]:
            j = i
            while j < n and is_zero[j]:
                j += 1
            if j - i >= run_length:
                flags.append({
                    "date": str(df.index[i].date()),
                    "symbol": symbol,
                    "check_name": "suspended_period",
                    "severity": "tier3",
                    "tier": 3,
                    "details": f"{j - i} consecutive zero-volume days from {df.index[i].date()} to {df.index[j-1].date()}. "
                               f"Likely trading halt or suspension period.",
                })
            i = j
        else:
            i += 1
    return flags


# ---------- Top-level: run all per-ticker checks ----------

def all_checks_for_ticker(symbol: str, df: pd.DataFrame) -> list[dict]:
    """Run all per-ticker checks and aggregate flags."""
    flags: list[dict] = []
    flags.extend(check_dummy_ticker(symbol, df))
    flags.extend(check_recent_ipo(symbol, df))
    if len(df) > 0:
        flags.extend(check_split_anomaly(symbol, df))
        flags.extend(check_circuit_hits(symbol, df))
        flags.extend(check_suspended_periods(symbol, df))
    return flags
