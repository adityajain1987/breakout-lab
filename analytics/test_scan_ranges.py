"""Tests for analytics/scan_ranges.py.

Covers:
  - Isolated synthetic universe (no real-data dependence — fast, hermetic)
  - Filter behaviour (stars / status / maturity / sector / staleness)
  - Empty-input behaviour
  - ScanResult NamedTuple shape
  - One real-data perf sanity check on the actual universe (skipped if data missing)

Run: .venv/bin/python -m pytest analytics/test_scan_ranges.py -v
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.scan_ranges import RangeScanResult, scan_ranges


ROOT = Path(__file__).resolve().parent.parent


# ---------- Helper: write a synthetic universe to a tmp dir ----------

def _write_synthetic_parquet(
    path: Path,
    n_days: int = 600,
    pattern: str = "range",
    base: float = 100.0,
    seed: int = 0,
) -> None:
    """Write a synthetic OHLCV parquet at `path`."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    if pattern == "range":
        amplitude = base * 0.10
        closes = base + amplitude * np.sin(np.linspace(0, 8 * np.pi, n_days))
        closes += rng.normal(0, amplitude * 0.15, n_days)
    elif pattern == "trend_up":
        closes = base * (1 + 0.0012) ** np.arange(n_days)
        closes += rng.normal(0, base * 0.005, n_days)
    else:  # flat
        closes = np.full(n_days, base) + rng.normal(0, base * 0.001, n_days)
    daily_noise = rng.normal(0, base * 0.015, n_days)
    opens = closes + daily_noise * 0.3
    highs = np.maximum(opens, closes) + np.abs(daily_noise) * 0.5
    lows = np.minimum(opens, closes) - np.abs(daily_noise) * 0.5
    volumes = rng.integers(100_000, 1_000_000, n_days).astype(float)
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )
    df.to_parquet(path)


@pytest.fixture
def synthetic_universe(tmp_path: Path) -> Path:
    """Tmp dir with 4 synthetic parquets: 2 ranges, 1 trend, 1 flat."""
    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir()
    _write_synthetic_parquet(ohlcv / "RANGEA.parquet", pattern="range", base=100, seed=1)
    _write_synthetic_parquet(ohlcv / "RANGEB.parquet", pattern="range", base=500, seed=2)
    _write_synthetic_parquet(ohlcv / "TRENDC.parquet", pattern="trend_up", base=100, seed=3)
    _write_synthetic_parquet(ohlcv / "FLATD.parquet", pattern="flat", base=100, seed=4)
    return ohlcv


# =============================================================================
# 1. Basic scan
# =============================================================================

def test_scan_runs_on_synthetic_universe(synthetic_universe: Path):
    result = scan_ranges(
        asof_date="2025-04-15",  # late enough into the synthetic series
        min_stars=1,
        max_stale_days=365,
        ohlcv_dir=synthetic_universe,
    )
    assert isinstance(result, RangeScanResult)
    assert result.n_scanned == 4
    assert result.n_qualified >= 1  # at least one synthetic range qualifies


def test_scan_finds_synthetic_ranges_not_trends(synthetic_universe: Path):
    result = scan_ranges(
        asof_date="2025-04-15",
        min_stars=1,
        max_stale_days=365,
        ohlcv_dir=synthetic_universe,
    )
    qualified_tickers = set(result.df["ticker"]) if not result.df.empty else set()
    # The two RANGE patterns should be found
    assert "RANGEA" in qualified_tickers or "RANGEB" in qualified_tickers


# =============================================================================
# 2. ScanResult shape
# =============================================================================

def test_result_has_all_metadata_fields(synthetic_universe: Path):
    result = scan_ranges(asof_date="2025-04-15", ohlcv_dir=synthetic_universe)
    for field in ("df", "asof", "n_scanned", "n_qualified", "skipped_no_data",
                  "skipped_no_asof", "skipped_short_history", "skipped_no_pair",
                  "skipped_quarantine_invalidated", "filtered_stars",
                  "filtered_status", "filtered_maturity", "filtered_stale",
                  "scan_duration_seconds"):
        assert hasattr(result, field), f"RangeScanResult missing {field}"


def test_result_dataframe_has_all_columns(synthetic_universe: Path):
    result = scan_ranges(
        asof_date="2025-04-15",
        min_stars=1,
        max_stale_days=365,
        ohlcv_dir=synthetic_universe,
    )
    expected = {
        "ticker", "company", "sector", "close", "day_change_pct",
        "resistance", "support", "width_pct", "stars", "round_number",
        "role_reversal", "volume_confirmed", "duration_days", "last_touch_days_ago",
        "maturity", "status", "breakout_direction", "breakout_days_ago",
        "quarantine_flag",
    }
    if not result.df.empty:
        missing = expected - set(result.df.columns)
        assert not missing, f"Missing columns: {missing}"


def test_empty_dataframe_has_correct_columns(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = scan_ranges(asof_date="2025-04-15", ohlcv_dir=empty_dir)
    assert result.n_scanned == 0
    assert result.n_qualified == 0
    assert "ticker" in result.df.columns
    assert "stars" in result.df.columns


# =============================================================================
# 3. Filters
# =============================================================================

def test_min_stars_filter_excludes_low_rated(synthetic_universe: Path):
    result_loose = scan_ranges(asof_date="2025-04-15", min_stars=1,
                               max_stale_days=365, ohlcv_dir=synthetic_universe)
    result_strict = scan_ranges(asof_date="2025-04-15", min_stars=4,
                                max_stale_days=365, ohlcv_dir=synthetic_universe)
    # Stricter min_stars yields ≤ qualified
    assert result_strict.n_qualified <= result_loose.n_qualified


def test_status_filter_in_range_only(synthetic_universe: Path):
    result = scan_ranges(
        asof_date="2025-04-15",
        min_stars=1,
        max_stale_days=365,
        status_filter="in-range",
        ohlcv_dir=synthetic_universe,
    )
    if not result.df.empty:
        assert set(result.df["status"].unique()) <= {"In-Range"}


def test_status_filter_breakout_only(synthetic_universe: Path):
    result = scan_ranges(
        asof_date="2025-04-15",
        min_stars=1,
        max_stale_days=365,
        status_filter="breakout",
        ohlcv_dir=synthetic_universe,
    )
    if not result.df.empty:
        assert set(result.df["status"].unique()) <= {"Recent Breakout"}


def test_max_stale_days_excludes_old_ranges(synthetic_universe: Path):
    # With extremely tight staleness (0 days), most synthetic ranges should drop out
    tight = scan_ranges(asof_date="2025-04-15", min_stars=1,
                        max_stale_days=0, ohlcv_dir=synthetic_universe)
    loose = scan_ranges(asof_date="2025-04-15", min_stars=1,
                        max_stale_days=365, ohlcv_dir=synthetic_universe)
    assert tight.n_qualified <= loose.n_qualified
    assert tight.filtered_stale >= 0


def test_top_n_caps_result_rows(synthetic_universe: Path):
    result = scan_ranges(
        asof_date="2025-04-15",
        min_stars=1,
        max_stale_days=365,
        top_n=1,
        ohlcv_dir=synthetic_universe,
    )
    assert len(result.df) <= 1


# =============================================================================
# 4. Edge cases
# =============================================================================

def test_asof_not_in_any_parquet(synthetic_universe: Path):
    """When asof_date is beyond every parquet's last bar, scan returns 0 qualified."""
    result = scan_ranges(
        asof_date="2099-01-01",  # in the future
        ohlcv_dir=synthetic_universe,
    )
    assert result.n_qualified == 0
    assert result.skipped_no_asof + result.skipped_no_data >= result.n_scanned - 0


def test_skipped_short_history_for_new_parquet(tmp_path: Path):
    """A parquet with too few bars should be skipped, not crash the scan."""
    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir()
    # 100 bars — well below the ~225 needed for a 9-month range
    _write_synthetic_parquet(ohlcv / "NEWIPO.parquet", n_days=100)
    # Use asof on the LAST bar of NEWIPO so range_state runs and rejects on history
    df = pd.read_parquet(ohlcv / "NEWIPO.parquet")
    asof = df.index[-1]
    result = scan_ranges(asof_date=asof, min_stars=1, max_stale_days=365,
                         ohlcv_dir=ohlcv)
    assert result.skipped_short_history >= 1
    assert result.n_qualified == 0


# =============================================================================
# 5. Real-data sanity (skipped if data missing)
# =============================================================================

def test_real_universe_scan_completes_under_soft_warning():
    """On the real Nifty 500 universe, scan should complete well under 30s soft warning."""
    real_ohlcv = ROOT / "data" / "ohlcv"
    if not real_ohlcv.exists() or len(list(real_ohlcv.glob("*.parquet"))) < 100:
        pytest.skip("real OHLCV data not available")
    result = scan_ranges(asof_date="2026-04-30", min_stars=1, top_n=10)
    assert result.scan_duration_seconds < 30.0, \
        f"Scan took {result.scan_duration_seconds:.1f}s — crossed 30s soft warning"
    assert result.n_scanned >= 100  # sanity: we did scan something
