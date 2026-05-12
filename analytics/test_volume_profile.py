"""
Unit tests for volume_profile.

Strategy:
  1. Synthetic tests with known answers (single price, uniform, bimodal, edge cases)
  2. Binning-adapts test across price ranges (₹50, ₹500, ₹5000)
  3. Real data sanity (RELIANCE, NESTLEIND parquets must exist — run P0.0 first)

Run: .venv/bin/python -m pytest analytics/test_volume_profile.py -v
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.volume_profile import (
    VolumeProfile,
    volume_profile,
    volume_profile_for_ticker,
)


# ---------- Helpers ----------

def make_df(rows: list[dict], start: str = "2024-01-01") -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of {high, low, close, volume} dicts."""
    df = pd.DataFrame(rows)
    df.index = pd.date_range(start=start, periods=len(rows), freq="B")
    df.index.name = "date"
    df["open"] = df["close"]  # not used by volume_profile but keep shape
    return df


# ---------- 1. Synthetic tests ----------

def test_single_price_all_volume_at_one_bin():
    """All volume at exactly ₹100 → POC = ₹100, single bin."""
    df = make_df([{"high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000}] * 5)
    vp = volume_profile(df)
    assert vp.poc == 100.0
    assert len(vp.bins) == 1
    assert vp.total_volume == 5000


def test_uniform_distribution_poc_near_center():
    """Day spanning ₹100-200 with constant volume → POC near center, VA covers most."""
    df = make_df([{"high": 200.0, "low": 100.0, "close": 150.0, "volume": 1000}] * 10)
    vp = volume_profile(df, bin_width_pct=0.01)  # 1.5% × 150 = ₹1.5 bins
    # Uniform → all bins should have similar volume; POC anywhere
    assert 100 <= vp.poc <= 200
    # VA should cover ~70% of the range (greedy expansion fills bins evenly)
    assert (vp.vah - vp.val) >= 0.6 * 100


def test_bimodal_two_clusters():
    """Two volume clusters at ₹100 and ₹200. POC = the heavier cluster, both should appear as HVNs."""
    rows = []
    # Cluster at ₹100 (smaller)
    for _ in range(10):
        rows.append({"high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000})
    # Cluster at ₹200 (larger — 3x volume)
    for _ in range(10):
        rows.append({"high": 201.0, "low": 199.0, "close": 200.0, "volume": 3000})
    df = make_df(rows)
    vp = volume_profile(df, bin_width_pct=0.005, hvn_significance=1.2)
    # POC must be at the heavier cluster (~₹200)
    assert 195 <= vp.poc <= 205
    # Both clusters should be detectable in the volume distribution
    cluster_100_vol = vp.bins[(vp.bins.price_bin_mid >= 95) & (vp.bins.price_bin_mid <= 105)].volume.sum()
    cluster_200_vol = vp.bins[(vp.bins.price_bin_mid >= 195) & (vp.bins.price_bin_mid <= 205)].volume.sum()
    assert cluster_100_vol > 0
    assert cluster_200_vol > cluster_100_vol  # heavier cluster bigger


def test_zero_volume_days_skipped():
    """Suspended-trading days (volume=0) should not affect the profile."""
    rows = [
        {"high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
        {"high": 100.0, "low": 100.0, "close": 100.0, "volume": 0},     # skipped
        {"high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
    ]
    df = make_df(rows)
    vp = volume_profile(df)
    assert vp.total_volume == 2000
    assert vp.bins.iloc[0]["n_days_in_bin"] == 2  # only 2 contributing days


def test_empty_dataframe_returns_empty_profile():
    df = pd.DataFrame(columns=["high", "low", "close", "open", "volume"])
    df.index = pd.DatetimeIndex([])
    vp = volume_profile(df)
    assert len(vp.bins) == 0
    assert np.isnan(vp.poc)


# ---------- 2. Binning-adapts across price ranges (the Q4 office-hours test) ----------

@pytest.mark.parametrize("price_level", [50.0, 500.0, 5000.0])
def test_bin_width_adapts_to_price_level(price_level: float):
    """
    Per office-hours Item 4: 0.5% × mid_price. So bin width should scale linearly with price.
    Caps: min ₹0.05, max bins = 100.
    """
    # Synthetic 6M of data at ±10% of price_level
    n_days = 120
    np.random.seed(42)
    closes = price_level * (1 + 0.10 * np.sin(np.linspace(0, 4 * np.pi, n_days)))
    rows = [
        {"high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1_000_000}
        for c in closes
    ]
    df = make_df(rows)
    vp = volume_profile(df, bin_width_pct=0.005)

    # Expected bin_width ≈ 0.5% × mid_price (with caps)
    expected = 0.005 * price_level
    expected_capped = max(0.05, expected)

    # Allow some tolerance for max-bins cap on the highest price
    assert vp.bin_width >= 0.05  # min tick
    assert len(vp.bins) <= 100    # max bins
    # For mid range (500), bin_width should be ~₹2.50, no cap fired
    if price_level == 500:
        assert abs(vp.bin_width - expected_capped) < expected_capped * 0.5


def test_max_bins_cap_widens_bin_width():
    """Very wide range with default 0.5% should hit the 100-bin cap and widen bin_width."""
    # Range from ₹100 to ₹500 (4x). At 0.5% × mid_price (₹300) = ₹1.50 → would need 267 bins.
    n = 100
    rows = []
    for i in range(n):
        c = 100.0 + (400.0 * i / n)  # walk from 100 → 500
        rows.append({"high": c + 1, "low": c - 1, "close": c, "volume": 1000})
    df = make_df(rows)
    vp = volume_profile(df, bin_width_pct=0.005, max_bins=100)
    assert len(vp.bins) <= 100
    assert vp.bin_width > 0.005 * 300  # widened above the requested 1.5


def test_min_tick_cap_for_penny_stock():
    """₹2 stock at 0.5% would want ₹0.01 bins. Should cap at ₹0.05 (1 tick)."""
    rows = [{"high": 2.05, "low": 1.95, "close": 2.0, "volume": 1000}] * 30
    df = make_df(rows)
    vp = volume_profile(df, bin_width_pct=0.005)
    assert vp.bin_width >= 0.05


# ---------- 3. Value area logic ----------

def test_value_area_contains_poc():
    rows = []
    # Heavy bin at ₹150, lighter on both sides
    for _ in range(20):
        rows.append({"high": 151.0, "low": 149.0, "close": 150.0, "volume": 5000})
    for _ in range(5):
        rows.append({"high": 161.0, "low": 159.0, "close": 160.0, "volume": 1000})
    for _ in range(5):
        rows.append({"high": 141.0, "low": 139.0, "close": 140.0, "volume": 1000})
    df = make_df(rows)
    vp = volume_profile(df, bin_width_pct=0.005, value_area_pct=0.70)
    assert vp.val <= vp.poc <= vp.vah


# ---------- 4. Real-data sanity (run P0.0 first) ----------

OHLCV_DIR = Path(__file__).resolve().parent.parent / "data" / "ohlcv"


@pytest.mark.skipif(
    not (OHLCV_DIR / "RELIANCE.parquet").exists(),
    reason="Run data/fetch_samples.py (P0.0) first",
)
def test_reliance_real_data_sanity():
    vp = volume_profile_for_ticker("RELIANCE", start="2024-01-01", end="2024-12-31")
    # POC must be within actual price range of 2024
    df = pd.read_parquet(OHLCV_DIR / "RELIANCE.parquet")
    df_2024 = df.loc["2024-01-01":"2024-12-31"]
    assert df_2024["low"].min() <= vp.poc <= df_2024["high"].max()
    # Total volume in profile should equal total volume in window (within rounding)
    assert abs(vp.total_volume - df_2024["volume"].sum()) / df_2024["volume"].sum() < 0.001
    # VAH > POC > VAL (or equal at edges)
    assert vp.val <= vp.poc <= vp.vah


@pytest.mark.skipif(
    not (OHLCV_DIR / "MAZDOCK.parquet").exists(),
    reason="Run data/fetch_samples.py (P0.0) first",
)
def test_mazdock_multibagger_window():
    """MAZDOCK has run 10x in 2024-2025. Volume profile should show distinct accumulation zones."""
    vp = volume_profile_for_ticker("MAZDOCK", start="2024-01-01", end="2025-12-31")
    assert vp.n_days > 200  # had a full year+ of trading
    assert len(vp.hvns) >= 1  # at least one accumulation zone
