"""Unit tests for deals.store — schema, dedupe, T+1 shift, disclosed-volume math."""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from deals.store import (
    init_db, insert_deals, query_deals,
    shift_for_backtest, disclosed_volume_pct,
)


# ---------- Helpers ----------

def make_deals(rows: list[dict]) -> pd.DataFrame:
    """Build a deals frame with the canonical schema."""
    df = pd.DataFrame(rows)
    if "remarks" not in df.columns:
        df["remarks"] = ""
    return df


def make_ohlcv(start: str, end: str, daily_volume: float = 1_000_000) -> pd.DataFrame:
    idx = pd.date_range(start=start, end=end, freq="B")
    return pd.DataFrame({
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": [daily_volume] * len(idx),
    }, index=idx)


# ---------- 1. Schema + insert ----------

def test_init_creates_schema(tmp_path: Path):
    db = tmp_path / "deals.db"
    init_db(db)
    assert db.exists()
    # Empty query should return empty frame
    assert query_deals(db).empty


def test_insert_basic_row(tmp_path: Path):
    db = tmp_path / "deals.db"
    df = make_deals([{
        "date": "2026-04-30", "symbol": "RELIANCE", "deal_type": "bulk",
        "client": "Goldman Sachs", "side": "BUY", "quantity": 100000, "price": 1430.5,
    }])
    n = insert_deals(db, df)
    assert n == 1
    out = query_deals(db, symbol="RELIANCE")
    assert len(out) == 1
    assert out.iloc[0]["client"] == "Goldman Sachs"


def test_insert_dedupe_on_re_run(tmp_path: Path):
    """Re-inserting the same row should be a no-op."""
    db = tmp_path / "deals.db"
    df = make_deals([{
        "date": "2026-04-30", "symbol": "RELIANCE", "deal_type": "bulk",
        "client": "Goldman", "side": "BUY", "quantity": 100000, "price": 1430.5,
    }])
    assert insert_deals(db, df) == 1
    assert insert_deals(db, df) == 0  # idempotent


def test_insert_buy_and_sell_sides_are_distinct_rows(tmp_path: Path):
    """When NSE reports both sides, both should be stored (the dedupe-then-sum
    happens at the math layer, not the storage layer — keeps audit trail)."""
    db = tmp_path / "deals.db"
    df = make_deals([
        {"date": "2026-04-30", "symbol": "TEST", "deal_type": "bulk",
         "client": "BuyerCo", "side": "BUY", "quantity": 100000, "price": 100.0},
        {"date": "2026-04-30", "symbol": "TEST", "deal_type": "bulk",
         "client": "SellerCo", "side": "SELL", "quantity": 100000, "price": 100.0},
    ])
    assert insert_deals(db, df) == 2
    assert len(query_deals(db, symbol="TEST")) == 2


# ---------- 2. T+1 shift ----------

def test_shift_for_backtest_thursday_to_friday():
    df = make_deals([
        {"date": "2026-04-30", "symbol": "X", "deal_type": "bulk",
         "client": "A", "side": "BUY", "quantity": 1, "price": 1.0},
    ])
    df["date"] = pd.to_datetime(df["date"])
    out = shift_for_backtest(df)
    # 2026-04-30 is Thursday → next BDay is Friday 2026-05-01
    assert out.iloc[0]["available_date"] == pd.Timestamp("2026-05-01")


def test_shift_for_backtest_friday_to_monday():
    df = make_deals([
        {"date": "2026-05-01", "symbol": "X", "deal_type": "bulk",
         "client": "A", "side": "BUY", "quantity": 1, "price": 1.0},
    ])
    df["date"] = pd.to_datetime(df["date"])
    out = shift_for_backtest(df)
    # Friday → next BDay is Monday
    assert out.iloc[0]["available_date"] == pd.Timestamp("2026-05-04")


def test_shift_for_backtest_with_calendar_skips_holiday():
    """Custom calendar that skips a Wednesday should jump deal-on-Tuesday to Thursday."""
    cal = pd.DatetimeIndex(["2026-04-27", "2026-04-28", "2026-04-30", "2026-05-01"])  # Wed 29 missing
    df = make_deals([
        {"date": "2026-04-28", "symbol": "X", "deal_type": "bulk",
         "client": "A", "side": "BUY", "quantity": 1, "price": 1.0},
    ])
    df["date"] = pd.to_datetime(df["date"])
    out = shift_for_backtest(df, trading_calendar=cal)
    # Tue 28 → next trading day is Thu 30 (Wed 29 is missing from calendar)
    assert out.iloc[0]["available_date"] == pd.Timestamp("2026-04-30")


# ---------- 3. Disclosed-volume math ----------

def test_disclosed_volume_zero_when_no_deals():
    deals = make_deals([])
    ohlcv = make_ohlcv("2026-04-01", "2026-04-30")
    res = disclosed_volume_pct(deals, ohlcv)
    assert res["disclosed_qty"] == 0
    assert res["pct"] == 0.0
    assert "100.0%" in res["label"]
    assert "anonymous" in res["label"]


def test_disclosed_volume_dedupes_buy_sell_same_transaction():
    """Same (date, symbol, qty, price) reported as BUY+SELL = ONE transaction, count once."""
    deals = make_deals([
        {"date": "2026-04-15", "symbol": "X", "deal_type": "bulk",
         "client": "A", "side": "BUY", "quantity": 100000, "price": 100.0},
        {"date": "2026-04-15", "symbol": "X", "deal_type": "bulk",
         "client": "B", "side": "SELL", "quantity": 100000, "price": 100.0},  # same trade
    ])
    deals["date"] = deals["date"].astype(str)  # match what the DB returns
    ohlcv = make_ohlcv("2026-04-01", "2026-04-30", daily_volume=1_000_000)
    res = disclosed_volume_pct(deals, ohlcv)
    # Should count 100k once, not 200k
    assert res["disclosed_qty"] == 100_000
    assert res["n_unique_deals"] == 1
    assert res["n_raw_rows"] == 2


def test_disclosed_volume_keeps_distinct_trades_at_different_prices():
    """Same client buy then sell at slightly different prices = TWO transactions (warehouse trade pattern)."""
    deals = make_deals([
        {"date": "2026-04-15", "symbol": "X", "deal_type": "bulk",
         "client": "Microcurves", "side": "SELL", "quantity": 2766287, "price": 482.02},
        {"date": "2026-04-15", "symbol": "X", "deal_type": "bulk",
         "client": "Microcurves", "side": "BUY", "quantity": 2766287, "price": 481.90},
    ])
    deals["date"] = deals["date"].astype(str)
    ohlcv = make_ohlcv("2026-04-01", "2026-04-30", daily_volume=10_000_000)
    res = disclosed_volume_pct(deals, ohlcv)
    # Different prices → different trades → both count
    assert res["disclosed_qty"] == 2 * 2766287
    assert res["n_unique_deals"] == 2


def test_disclosed_volume_short_window_warning():
    """Window < 22 trading days should append the variance warning."""
    deals = make_deals([])
    ohlcv = make_ohlcv("2026-04-25", "2026-04-30")  # ~5 days
    res = disclosed_volume_pct(deals, ohlcv)
    assert "Switch to longer view" in res["label"]


def test_disclosed_volume_long_window_no_warning():
    """Window >= 22 trading days should NOT have variance warning."""
    deals = make_deals([])
    ohlcv = make_ohlcv("2026-01-01", "2026-04-30")  # ~85 days
    res = disclosed_volume_pct(deals, ohlcv)
    assert "Switch to longer view" not in res["label"]
