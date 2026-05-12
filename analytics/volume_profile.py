"""
Volume profile — price-binned volume histogram for any ticker × window.

Core function `volume_profile(df, ...)` is pure (no I/O) so it's easy to test.
Wrapper `volume_profile_for_ticker(ticker, ...)` loads parquet from data/ohlcv/.

Binning rule (per Phase 1 office hours, Item 4):
  bin_width = bin_width_pct × mid_price   where mid_price = (max + min) / 2
  Default bin_width_pct = 0.005 (0.5%).
  Caps: min bin width = 1 tick (₹0.05), max bins per profile = 100.

Volume distribution (per day):
  Each day's volume is spread uniformly across the bins that overlap [low, high].
  When high == low (degenerate), all volume goes to that single price bin.
  This is the standard "TradingView-style" volume profile algorithm.

Outputs (VolumeProfile dataclass):
  bins: DataFrame[price_bin_low, price_bin_mid, price_bin_high, volume, n_days_in_bin]
  poc: price at point of control (highest-volume bin)
  vah, val: value area high / low (70% volume range around POC, expanded greedily)
  hvns: list of high-volume-node mid prices (local maxima above hvn_significance × median)
  lvns: list of low-volume-node mid prices (local minima between HVNs)
  bin_width: actual bin width used (after caps)
  total_volume, window_start, window_end: window stats
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


# ---------- Public API ----------

@dataclass
class VolumeProfile:
    bins: pd.DataFrame
    poc: float
    vah: float
    val: float
    hvns: list[float] = field(default_factory=list)
    lvns: list[float] = field(default_factory=list)
    bin_width: float = 0.0
    bin_width_pct: float = 0.005
    total_volume: float = 0.0
    window_start: pd.Timestamp | None = None
    window_end: pd.Timestamp | None = None
    n_days: int = 0

    def __repr__(self) -> str:
        return (
            f"VolumeProfile("
            f"bins={len(self.bins)}, "
            f"POC=₹{self.poc:.2f}, "
            f"VA=[{self.val:.2f}, {self.vah:.2f}], "
            f"HVNs={len(self.hvns)}, LVNs={len(self.lvns)}, "
            f"bin_width=₹{self.bin_width:.4f}, "
            f"days={self.n_days})"
        )


def volume_profile(
    df: pd.DataFrame,
    bin_width_pct: float = 0.005,
    min_tick: float = 0.05,
    max_bins: int = 100,
    value_area_pct: float = 0.70,
    hvn_significance: float = 1.5,
    smoothing_window: int = 3,
) -> VolumeProfile:
    """
    Build a volume profile from daily OHLCV data.

    df must have columns: high, low, close, volume. Index must be DatetimeIndex.
    Use a slice of the parquet (e.g., df.loc['2024-01-01':'2024-06-30']).
    """
    _validate(df)

    if len(df) == 0:
        return _empty_profile(bin_width_pct)

    # 1. Compute bin width (per office-hours Item 4)
    pmin = float(df["low"].min())
    pmax = float(df["high"].max())
    if pmin == pmax:
        # Degenerate: all rows at one price
        return _single_price_profile(df, pmin, bin_width_pct)

    mid_price = (pmin + pmax) / 2.0
    bin_width = bin_width_pct * mid_price
    bin_width = max(bin_width, min_tick)  # min cap = 1 tick
    price_range = pmax - pmin

    # 2. Compute n_bins, then enforce max-bins cap by widening bin_width
    n_bins = max(1, int(np.ceil(price_range / bin_width)))
    if n_bins > max_bins:
        n_bins = max_bins
        bin_width = price_range / n_bins

    # 3. Build edges (n_bins + 1 of them); guard last edge against FP boundary issues
    edges = pmin + np.arange(n_bins + 1) * bin_width
    edges[-1] = max(edges[-1], pmax + 1e-9)

    bin_volumes = np.zeros(n_bins, dtype=np.float64)
    bin_day_counts = np.zeros(n_bins, dtype=np.int64)

    # 3. Distribute each day's volume across overlapping bins
    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    vols = df["volume"].to_numpy(dtype=np.float64)

    for i in range(len(df)):
        h, l, v = highs[i], lows[i], vols[i]
        if v <= 0:
            continue  # skip zero-volume days (suspended / holiday gaps)
        if h == l:
            # Single-price day → all volume to one bin
            idx = min(int((h - pmin) / bin_width), n_bins - 1)
            bin_volumes[idx] += v
            bin_day_counts[idx] += 1
            continue

        # Find first/last bin touched by [l, h]
        first = max(0, int((l - pmin) / bin_width))
        last = min(n_bins - 1, int((h - pmin) / bin_width))
        day_range = h - l

        for b in range(first, last + 1):
            bin_lo = edges[b]
            bin_hi = edges[b + 1]
            overlap = min(bin_hi, h) - max(bin_lo, l)
            if overlap > 0:
                bin_volumes[b] += v * (overlap / day_range)
                bin_day_counts[b] += 1

    # 4. Build bins DataFrame
    bin_lows = edges[:-1]
    bin_highs = edges[1:]
    bin_mids = (bin_lows + bin_highs) / 2.0
    bins_df = pd.DataFrame({
        "price_bin_low": bin_lows,
        "price_bin_mid": bin_mids,
        "price_bin_high": bin_highs,
        "volume": bin_volumes,
        "n_days_in_bin": bin_day_counts,
    })

    # 5. POC — bin with max volume
    poc_idx = int(np.argmax(bin_volumes))
    poc_price = float(bin_mids[poc_idx])

    # 6. Value area — expand from POC outward, always take higher-volume neighbor
    val_price, vah_price = _compute_value_area(
        bin_volumes, bin_mids, poc_idx, value_area_pct
    )

    # 7. HVN / LVN detection on smoothed series
    smoothed = _smooth(bin_volumes, smoothing_window)
    median_vol = float(np.median(bin_volumes[bin_volumes > 0])) if (bin_volumes > 0).any() else 0.0
    hvn_threshold = median_vol * hvn_significance
    hvn_idxs = _find_peaks(smoothed, threshold=hvn_threshold)
    lvn_idxs = _find_valleys_between(smoothed, hvn_idxs)

    return VolumeProfile(
        bins=bins_df,
        poc=poc_price,
        vah=float(vah_price),
        val=float(val_price),
        hvns=[float(bin_mids[i]) for i in hvn_idxs],
        lvns=[float(bin_mids[i]) for i in lvn_idxs],
        bin_width=float(bin_width),
        bin_width_pct=bin_width_pct,
        total_volume=float(bin_volumes.sum()),
        window_start=df.index.min(),
        window_end=df.index.max(),
        n_days=len(df),
    )


def volume_profile_for_ticker(
    ticker: str,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    ohlcv_dir: Path | None = None,
    **kwargs,
) -> VolumeProfile:
    """Load parquet for ticker, slice [start, end], compute profile."""
    if ohlcv_dir is None:
        ohlcv_dir = Path(__file__).resolve().parent.parent / "data" / "ohlcv"
    path = ohlcv_dir / f"{ticker}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No OHLCV parquet for {ticker} at {path}")
    df = pd.read_parquet(path)
    if start is not None:
        df = df.loc[pd.Timestamp(start):]
    if end is not None:
        df = df.loc[:pd.Timestamp(end)]
    return volume_profile(df, **kwargs)


# ---------- Helpers ----------

def _validate(df: pd.DataFrame) -> None:
    required = {"high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Index must be DatetimeIndex")


def _empty_profile(bin_width_pct: float) -> VolumeProfile:
    empty = pd.DataFrame(columns=[
        "price_bin_low", "price_bin_mid", "price_bin_high", "volume", "n_days_in_bin",
    ])
    return VolumeProfile(
        bins=empty, poc=float("nan"), vah=float("nan"), val=float("nan"),
        bin_width_pct=bin_width_pct,
    )


def _single_price_profile(df: pd.DataFrame, price: float, bin_width_pct: float) -> VolumeProfile:
    contributing = df[df["volume"] > 0]
    total = float(contributing["volume"].sum())
    bins_df = pd.DataFrame({
        "price_bin_low": [price],
        "price_bin_mid": [price],
        "price_bin_high": [price],
        "volume": [total],
        "n_days_in_bin": [len(contributing)],
    })
    return VolumeProfile(
        bins=bins_df, poc=price, vah=price, val=price,
        hvns=[price], lvns=[],
        bin_width=0.0, bin_width_pct=bin_width_pct,
        total_volume=total, window_start=df.index.min(), window_end=df.index.max(),
        n_days=len(df),
    )


def _compute_value_area(
    volumes: np.ndarray, prices: np.ndarray, poc_idx: int, value_pct: float
) -> tuple[float, float]:
    """Expand from POC, always taking the higher-volume neighbor, until value_pct reached."""
    total = float(volumes.sum())
    if total <= 0:
        return float(prices[poc_idx]), float(prices[poc_idx])
    target = total * value_pct
    cumulative = float(volumes[poc_idx])
    lo = hi = poc_idx
    n = len(volumes)

    while cumulative < target and (lo > 0 or hi < n - 1):
        lo_vol = float(volumes[lo - 1]) if lo > 0 else -1.0
        hi_vol = float(volumes[hi + 1]) if hi < n - 1 else -1.0
        if lo_vol < 0 and hi_vol < 0:
            break
        if lo_vol >= hi_vol:
            lo -= 1
            cumulative += lo_vol
        else:
            hi += 1
            cumulative += hi_vol

    return float(prices[lo]), float(prices[hi])


def _smooth(arr: np.ndarray, window: int) -> np.ndarray:
    """Centred moving average. window=1 returns the array unchanged."""
    if window <= 1 or len(arr) < window:
        return arr.copy()
    kernel = np.ones(window) / window
    # 'same' mode keeps length; pad ends with nearest valid value
    sm = np.convolve(arr, kernel, mode="same")
    return sm


def _find_peaks(arr: np.ndarray, threshold: float = 0.0) -> list[int]:
    """Strict local maxima with value > threshold. No scipy dep."""
    peaks: list[int] = []
    n = len(arr)
    if n < 3:
        return peaks
    for i in range(1, n - 1):
        if arr[i] > arr[i - 1] and arr[i] > arr[i + 1] and arr[i] > threshold:
            peaks.append(i)
    return peaks


def _find_valleys_between(arr: np.ndarray, peak_idxs: list[int]) -> list[int]:
    """Local minima strictly between consecutive peaks."""
    if len(peak_idxs) < 2:
        return []
    valleys: list[int] = []
    for left, right in zip(peak_idxs[:-1], peak_idxs[1:]):
        if right - left < 2:
            continue
        sub = arr[left + 1:right]
        if len(sub) == 0:
            continue
        v = int(np.argmin(sub)) + left + 1
        valleys.append(v)
    return valleys
