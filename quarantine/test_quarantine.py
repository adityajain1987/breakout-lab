"""Unit tests for quarantine — checks + store + dedupe + F&O expiry calendar."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quarantine.checks import (
    check_split_anomaly, check_dummy_ticker, check_circuit_hits,
    check_recent_ipo, check_suspended_periods, is_fno_expiry,
    all_checks_for_ticker,
)
from quarantine.store import init_db, insert_flags, query_flags, summary


# ---------- Helpers ----------

def make_df(closes: list[float], volumes: list[float] | None = None, start: str = "2024-01-01") -> pd.DataFrame:
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    df = pd.DataFrame({
        "open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
        "close": closes, "volume": volumes,
    })
    df.index = pd.date_range(start=start, periods=n, freq="B")
    df.index.name = "date"
    return df


# ---------- check_split_anomaly ----------

def test_split_anomaly_no_flags_for_normal_data():
    df = make_df([100.0, 101.0, 102.0, 100.5, 101.5])
    flags = check_split_anomaly("X", df)
    assert flags == []


def test_split_anomaly_flags_60_percent_jump():
    df = make_df([100.0, 100.0, 40.0, 40.5, 41.0])  # -60% on day 3
    flags = check_split_anomaly("X", df)
    assert len(flags) == 1
    assert flags[0]["severity"] == "tier1"
    assert flags[0]["check_name"] == "split_anomaly"


# ---------- check_dummy_ticker ----------

def test_dummy_name_pattern_flagged():
    df = make_df([100.0] * 5)
    flags = check_dummy_ticker("DUMMYVEDL4", df)
    assert any(f["check_name"] == "dummy_ticker_name" for f in flags)


def test_all_zero_volume_flagged_even_if_name_normal():
    df = make_df([100.0] * 10, volumes=[0] * 10)
    flags = check_dummy_ticker("REAL", df)
    assert any(f["check_name"] == "all_zero_volume" for f in flags)


def test_normal_ticker_no_dummy_flag():
    df = make_df([100.0] * 10)
    flags = check_dummy_ticker("RELIANCE", df)
    assert flags == []


# ---------- check_circuit_hits ----------

def test_circuit_hit_5pct_with_low_volume_flagged():
    """Day 25: +5.0% with volume = 30% of 20d avg → should flag."""
    closes = [100.0] * 24 + [105.0]
    volumes = [1_000_000] * 24 + [300_000]  # vol drops to 30% of avg
    df = make_df(closes, volumes)
    flags = check_circuit_hits("X", df)
    assert len(flags) == 1
    assert "5" in flags[0]["details"] or "5.0" in flags[0]["details"]


def test_circuit_hit_5pct_with_normal_volume_not_flagged():
    """Same +5% move but with normal volume = real demand, not locked circuit."""
    closes = [100.0] * 24 + [105.0]
    volumes = [1_000_000] * 24 + [2_000_000]  # 2x normal — real demand
    df = make_df(closes, volumes)
    flags = check_circuit_hits("X", df)
    assert flags == []


# ---------- is_fno_expiry ----------

def test_fno_expiry_last_thursday_of_april_2026():
    """April 2026: last Thursday is April 30."""
    assert is_fno_expiry(pd.Timestamp("2026-04-30")) is True
    assert is_fno_expiry(pd.Timestamp("2026-04-23")) is False  # second-to-last Thursday
    assert is_fno_expiry(pd.Timestamp("2026-04-29")) is False  # Wednesday before


def test_fno_expiry_february_2024_handles_short_month():
    """Feb 2024: last Thursday is Feb 29 (leap year)."""
    assert is_fno_expiry(pd.Timestamp("2024-02-29")) is True


# ---------- check_recent_ipo ----------

def test_recent_ipo_flagged_below_threshold():
    df = make_df([100.0] * 100)  # only 100 trading days
    flags = check_recent_ipo("X", df, min_trading_days=250)
    assert len(flags) == 1
    assert flags[0]["tier"] == 3


def test_recent_ipo_not_flagged_above_threshold():
    df = make_df([100.0] * 300)
    flags = check_recent_ipo("X", df, min_trading_days=250)
    assert flags == []


# ---------- check_suspended_periods ----------

def test_suspended_period_5_consecutive_zero_volume_flagged():
    closes = [100.0] * 30
    volumes = [1_000_000] * 10 + [0] * 6 + [1_000_000] * 14
    df = make_df(closes, volumes)
    flags = check_suspended_periods("X", df, run_length=5)
    assert len(flags) == 1
    assert "6 consecutive" in flags[0]["details"]


def test_suspended_period_4_zero_days_not_flagged():
    closes = [100.0] * 30
    volumes = [1_000_000] * 10 + [0] * 4 + [1_000_000] * 16
    df = make_df(closes, volumes)
    flags = check_suspended_periods("X", df, run_length=5)
    assert flags == []


def test_suspended_multiple_separate_runs_each_flagged():
    closes = [100.0] * 40
    volumes = [1_000_000] * 5 + [0] * 6 + [1_000_000] * 10 + [0] * 5 + [1_000_000] * 14
    df = make_df(closes, volumes)
    flags = check_suspended_periods("X", df, run_length=5)
    assert len(flags) == 2


# ---------- store + dedupe ----------

def test_insert_dedupes_on_re_run(tmp_path: Path):
    db = tmp_path / "q.db"
    flags = [{
        "date": "2024-04-30", "symbol": "RELIANCE",
        "check_name": "circuit_hit", "severity": "tier2", "tier": 2,
        "details": "test",
    }]
    assert insert_flags(db, flags) == 1
    assert insert_flags(db, flags) == 0  # idempotent


def test_query_includes_date_level_flags_for_symbol_query(tmp_path: Path):
    """Querying for symbol X should also return date-level flags (symbol IS NULL)."""
    db = tmp_path / "q.db"
    insert_flags(db, [
        {"date": "2024-04-25", "symbol": "RELIANCE", "check_name": "circuit_hit",
         "severity": "tier2", "tier": 2, "details": "ticker-specific"},
        {"date": "2024-04-25", "symbol": None, "check_name": "fno_expiry",
         "severity": "tier2", "tier": 2, "details": "applies to all"},
    ])
    out = query_flags(db, symbol="RELIANCE")
    assert len(out) == 2  # both should be returned


def test_summary_aggregates_correctly(tmp_path: Path):
    db = tmp_path / "q.db"
    insert_flags(db, [
        {"date": "2024-01-01", "symbol": "A", "check_name": "circuit_hit",
         "severity": "tier2", "tier": 2, "details": ""},
        {"date": "2024-01-02", "symbol": "B", "check_name": "circuit_hit",
         "severity": "tier2", "tier": 2, "details": ""},
        {"date": None, "symbol": "C", "check_name": "recent_ipo",
         "severity": "tier3", "tier": 3, "details": ""},
    ])
    s = summary(db)
    assert len(s) == 2
    circuit_row = s[s["check_name"] == "circuit_hit"].iloc[0]
    assert circuit_row["n_flags"] == 2
    assert circuit_row["n_symbols"] == 2


# ---------- all_checks_for_ticker integration ----------

def test_all_checks_runs_without_error_on_normal_data():
    df = make_df([100.0 + i * 0.1 for i in range(300)])
    flags = all_checks_for_ticker("NORMAL", df)
    # Normal data should produce no flags
    assert flags == []


def test_all_checks_catches_dummy_and_short_history():
    df = make_df([100.0] * 50)  # short + (we'll pass DUMMY name)
    flags = all_checks_for_ticker("DUMMYTEST", df)
    check_names = {f["check_name"] for f in flags}
    assert "dummy_ticker_name" in check_names
    assert "recent_ipo" in check_names
