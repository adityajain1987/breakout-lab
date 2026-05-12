"""Smoke tests for build_universe_history — synthetic parquets verify the filter logic."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from data.build_universe_history import build_month_snapshot, month_iter


def make_parquet(path: Path, start: str, end: str) -> None:
    """Write a minimal parquet covering the given date range (business days)."""
    idx = pd.date_range(start=start, end=end, freq="B")
    pd.DataFrame({"close": [100.0] * len(idx)}, index=idx).to_parquet(path)


def make_universe(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "SYMBOL":   symbols,
        "COMPANY":  [f"{s} Ltd" for s in symbols],
        "SECTOR":   ["Test"] * len(symbols),
        "ISIN":     [f"INE{i:09d}" for i in range(len(symbols))],
        "YF_TICKER": [f"{s}.NS" for s in symbols],
        "BUILT_AT": ["2026-05-01"] * len(symbols),
    })


def test_month_iter_inclusive():
    """month_iter should yield strings inclusive of both ends."""
    months = list(month_iter("2024-01", "2024-04"))
    assert months == ["2024-01", "2024-02", "2024-03", "2024-04"]


def test_recent_ipo_excluded_from_pre_existence_months(tmp_path: Path):
    """Ticker that started trading in 2024-06 must be ABSENT from 2024-05 snapshot."""
    make_parquet(tmp_path / "OLDCO.parquet", "2020-01-01", "2026-04-30")
    make_parquet(tmp_path / "IPO2024.parquet", "2024-06-01", "2026-04-30")
    universe = make_universe(["OLDCO", "IPO2024"])

    snap_pre = build_month_snapshot("2024-05", universe, tmp_path)
    snap_post = build_month_snapshot("2024-08", universe, tmp_path)

    assert "OLDCO" in snap_pre["SYMBOL"].values
    assert "IPO2024" not in snap_pre["SYMBOL"].values  # IPO was June, not yet in May
    assert "OLDCO" in snap_post["SYMBOL"].values
    assert "IPO2024" in snap_post["SYMBOL"].values     # IPO was June, present by August


def test_missing_parquet_silently_skipped(tmp_path: Path):
    """Universe ticker with no parquet should not appear in any snapshot."""
    make_parquet(tmp_path / "EXISTS.parquet", "2024-01-01", "2026-04-30")
    universe = make_universe(["EXISTS", "MISSING"])
    snap = build_month_snapshot("2024-06", universe, tmp_path)
    assert "EXISTS" in snap["SYMBOL"].values
    assert "MISSING" not in snap["SYMBOL"].values


def test_empty_month_returns_empty_df(tmp_path: Path):
    """No tickers had data in target month → empty DataFrame."""
    make_parquet(tmp_path / "T1.parquet", "2024-01-01", "2024-06-30")
    universe = make_universe(["T1"])
    # Query a month after T1's data ends
    snap = build_month_snapshot("2025-01", universe, tmp_path)
    assert snap.empty


def test_snapshot_carries_as_of_month_column(tmp_path: Path):
    """Output has AS_OF_MONTH column for traceability when concatenating snapshots."""
    make_parquet(tmp_path / "X.parquet", "2024-01-01", "2026-04-30")
    universe = make_universe(["X"])
    snap = build_month_snapshot("2024-06", universe, tmp_path)
    assert "AS_OF_MONTH" in snap.columns
    assert snap["AS_OF_MONTH"].iloc[0] == "2024-06"
