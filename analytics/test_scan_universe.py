"""Smoke tests for scan_universe — composition over breakout_state across multiple parquets."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.scan_universe import scan_universe, ScanResult


# ---------- Helpers ----------

def make_parquet(path: Path, closes: list[float], volumes: list[float] | None = None) -> None:
    """Write a synthetic OHLCV parquet for testing."""
    n = len(closes)
    df = pd.DataFrame({
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": volumes if volumes is not None else [1_000_000] * n,
    })
    df.index = pd.date_range(start="2024-01-01", periods=n, freq="B")
    df.index.name = "date"
    df.to_parquet(path, compression="snappy")


# ---------- Tests ----------

def test_scan_returns_named_tuple_with_required_fields(tmp_path: Path):
    """Empty universe returns valid ScanResult with all metadata fields."""
    result = scan_universe(asof_date="2024-06-03", ohlcv_dir=tmp_path)
    assert isinstance(result, ScanResult)
    assert result.df.empty
    assert result.n_scanned == 0
    assert result.n_qualified == 0


def test_scan_finds_clear_breakout_among_noise(tmp_path: Path):
    """One clear breakout ticker + several flat noise tickers → only the breakout shows up."""
    # Flat tickers (no breakouts)
    for i in range(5):
        make_parquet(tmp_path / f"FLAT{i}.parquet", [100.0] * 250)
    # One real breakout: walk up from 80→100 over 250d, then today closes at 105 with 5x vol
    closes = list(np.linspace(80, 100, 250)) + [105.0]
    volumes = [1_000_000] * 250 + [5_000_000]
    make_parquet(tmp_path / "RALLY.parquet", closes, volumes)

    asof = pd.Timestamp("2024-01-01") + pd.tseries.offsets.BDay(250)
    result = scan_universe(
        asof_date=asof,
        ohlcv_dir=tmp_path,
        min_score=10.0,
        min_volume_ratio=2.0,
        require_above_50dma=True,
    )
    assert result.n_scanned == 6
    assert result.n_qualified >= 1
    assert "RALLY" in result.df["ticker"].values


def test_scan_filters_below_50dma(tmp_path: Path):
    """A 'breakout' below 50dma should be filtered out by default."""
    # Downtrend + small uptick today (above recent low but below 50dma)
    closes = list(np.linspace(150, 100, 250)) + [101.0]
    volumes = [1_000_000] * 250 + [3_000_000]
    make_parquet(tmp_path / "DOWNTREND.parquet", closes, volumes)

    asof = pd.Timestamp("2024-01-01") + pd.tseries.offsets.BDay(250)
    result_filtered = scan_universe(
        asof_date=asof,
        ohlcv_dir=tmp_path,
        min_score=0.0,
        min_volume_ratio=1.0,
        require_above_50dma=True,
    )
    assert "DOWNTREND" not in result_filtered.df["ticker"].values
    assert result_filtered.filtered_ma >= 1


def test_scan_excludes_underscore_prefixed_files(tmp_path: Path):
    """_NSEI.parquet (the index) should not appear as a scanned ticker."""
    make_parquet(tmp_path / "_NSEI.parquet", [20000.0] * 250)
    make_parquet(tmp_path / "REAL.parquet", [100.0] * 250)
    result = scan_universe(asof_date="2024-12-13", ohlcv_dir=tmp_path)
    assert result.n_scanned == 1


def test_scan_skipped_when_asof_not_in_index(tmp_path: Path):
    """Tickers without the asof date should be counted as skipped, not failed."""
    make_parquet(tmp_path / "OLD.parquet", [100.0] * 50)  # data ends in early 2024
    result = scan_universe(asof_date="2026-04-30", ohlcv_dir=tmp_path)
    assert result.skipped_no_asof == 1
    assert result.df.empty
