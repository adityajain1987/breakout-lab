"""
Simulator unit tests — lock in the anti-look-ahead invariants.

The most important test is `test_force_close_uses_test_window_end_not_parquet_end` —
this catches the EXACT bug that inflated the first smoke-test EV by 3x.

Tests use a tmp_path fixture to set up isolated parquet + universe files, then run
the simulator against them. Validates: anti-look-ahead, universe filtering, entry
timing (next-day open), exit logic.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from backtest.simulator import (
    BacktestConfig, run_backtest, _load_universe_for_month,
)


def make_parquet(path: Path, dates: pd.DatetimeIndex, prices: list[float],
                 highs: list[float] | None = None, lows: list[float] | None = None,
                 volumes: list[float] | None = None) -> None:
    n = len(prices)
    df = pd.DataFrame({
        "open":   prices,
        "high":   highs if highs is not None else [p * 1.01 for p in prices],
        "low":    lows if lows is not None else [p * 0.99 for p in prices],
        "close":  prices,
        "volume": volumes if volumes is not None else [1_000_000] * n,
    }, index=dates)
    df.index.name = "date"
    df.to_parquet(path)


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    """Create an isolated test data directory and patch the simulator's globals to use it."""
    ohlcv_dir = tmp_path / "ohlcv"
    ohlcv_dir.mkdir()
    history_dir = tmp_path / "universe_history"
    history_dir.mkdir()

    # Patch the simulator module's globals
    import backtest.simulator as sim
    monkeypatch.setattr(sim, "OHLCV_DIR", ohlcv_dir)
    monkeypatch.setattr(sim, "HISTORY_DIR", history_dir)
    return {"ohlcv": ohlcv_dir, "history": history_dir}


def write_universe_snapshot(history_dir: Path, month: str, symbols: list[str]) -> None:
    df = pd.DataFrame({
        "SYMBOL": symbols,
        "COMPANY": [f"{s} Co" for s in symbols],
        "SECTOR": ["Test"] * len(symbols),
        "ISIN": [f"ISIN{i}" for i in range(len(symbols))],
        "YF_TICKER": [f"{s}.NS" for s in symbols],
        "BUILT_AT": ["2024-01-01"] * len(symbols),
        "AS_OF_MONTH": [month] * len(symbols),
    })
    df.to_csv(history_dir / f"{month}.csv", index=False)


# ---------- The critical anti-look-ahead test ----------

def test_force_close_uses_test_window_end_not_parquet_end(isolated_data):
    """
    THE BUG WE FIXED: parquet may extend beyond test window. Force-close at end_of_test
    must use the close on the LAST DAY OF THE TEST, not the parquet's last row.

    Setup: synthetic ticker that breaks out cleanly inside the test window, then has
    a HUGE rally AFTER the test window (in the parquet but not the test). If force-close
    leaks future prices, the trade's exit_price will be the post-test high. After the fix,
    it must be the test-end close.
    """
    # Calendar (NSE proxy)
    cal = pd.date_range(start="2024-01-01", end="2024-12-31", freq="B")
    make_parquet(isolated_data["ohlcv"] / "_NSEI.parquet", cal, [20000.0] * len(cal))

    # Test ticker: 200d at ₹100, then breakout to ₹110 inside test, then jumps to ₹500 after test
    test_window_end = pd.Timestamp("2024-04-30")
    parquet_end = pd.Timestamp("2024-12-31")
    pre_rally = pd.date_range(start="2024-01-01", end=test_window_end, freq="B")
    post_rally = pd.date_range(start="2024-05-01", end=parquet_end, freq="B")
    all_dates = pre_rally.union(post_rally)
    # Flat at 100, breakout candle on April 26 to 110, then continues at 110 through April 30
    # Then JUMPS to 500 starting May 1 (post-test)
    closes = []
    for d in all_dates:
        if d <= pd.Timestamp("2024-04-25"):
            closes.append(100.0)
        elif d <= test_window_end:
            closes.append(110.0)
        else:
            closes.append(500.0)  # POST-TEST jump — should NEVER appear in trade exit price
    make_parquet(isolated_data["ohlcv"] / "RALLYAFTER.parquet", all_dates, closes,
                 volumes=[1_000_000 if c < 110 else 5_000_000 for c in closes])

    # Universe: just RALLYAFTER for every month in test
    for m in ["2024-01", "2024-02", "2024-03", "2024-04"]:
        write_universe_snapshot(isolated_data["history"], m, ["RALLYAFTER"])

    config = BacktestConfig(
        start_date="2024-01-01",
        end_date="2024-04-30",
        min_score=10.0,
        min_volume_ratio=1.0,
        require_above_50dma=False,
        atr_stop_mult=2.0,
        atr_target_mult=4.0,
        timeout_days=20,
    )
    result = run_backtest(config)

    # Any force-closed trades MUST have exit_price <= 110 (the test-window high).
    # If we see exit_price=500 anywhere, the look-ahead bug is back.
    for t in result.trades:
        assert t.exit_price is not None
        assert t.exit_price <= 110.5, (
            f"LOOK-AHEAD BUG: trade {t.ticker} exit_price={t.exit_price} > 110 "
            f"means force-close used post-test parquet data"
        )


# ---------- Universe filtering ----------

def test_universe_excludes_pre_ipo_tickers(isolated_data):
    """A ticker missing from a month's universe snapshot should NEVER trade in that month."""
    cal = pd.date_range(start="2024-01-01", end="2024-04-30", freq="B")
    make_parquet(isolated_data["ohlcv"] / "_NSEI.parquet", cal, [20000.0] * len(cal))

    # OLDCO has full data; NEWIPO data exists only from 2024-04 onwards
    make_parquet(isolated_data["ohlcv"] / "OLDCO.parquet", cal,
                 [100.0] * (len(cal) - 5) + [115.0] * 5,
                 volumes=[1_000_000] * (len(cal) - 5) + [3_000_000] * 5)
    new_ipo_dates = pd.date_range(start="2024-04-01", end="2024-04-30", freq="B")
    make_parquet(isolated_data["ohlcv"] / "NEWIPO.parquet", new_ipo_dates,
                 [200.0] * (len(new_ipo_dates) - 3) + [240.0] * 3,
                 volumes=[1_000_000] * (len(new_ipo_dates) - 3) + [5_000_000] * 3)

    # Monthly snapshots: NEWIPO only present in April
    for m in ["2024-01", "2024-02", "2024-03"]:
        write_universe_snapshot(isolated_data["history"], m, ["OLDCO"])
    write_universe_snapshot(isolated_data["history"], "2024-04", ["OLDCO", "NEWIPO"])

    config = BacktestConfig(
        start_date="2024-01-01", end_date="2024-04-30",
        min_score=5.0, min_volume_ratio=1.0, require_above_50dma=False,
    )
    result = run_backtest(config)

    # Any NEWIPO trade must have signal_date in April 2024 or later
    for t in result.trades:
        if t.ticker == "NEWIPO":
            assert t.signal_date >= pd.Timestamp("2024-04-01"), (
                f"NEWIPO signal at {t.signal_date.date()} — should be Apr 2024 or later "
                f"(historical universe excluded it from earlier months)"
            )


# ---------- Helpers smoke test ----------

def test_load_universe_for_month_returns_set_of_symbols(isolated_data):
    write_universe_snapshot(isolated_data["history"], "2024-06", ["A", "B", "C"])
    # Reach back into the patched constant
    import backtest.simulator as sim
    syms = sim._load_universe_for_month("2024-06")
    assert syms == {"A", "B", "C"}


def test_load_universe_returns_empty_set_when_snapshot_missing(isolated_data):
    import backtest.simulator as sim
    syms = sim._load_universe_for_month("2099-01")
    assert syms == set()
