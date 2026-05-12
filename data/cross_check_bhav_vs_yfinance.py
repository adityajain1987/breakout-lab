"""
Cross-check Bhavcopy parquets vs yfinance parquets for 5 sample tickers.

For each ticker, compute on the overlap window:
  - Mean abs % difference in close, volume
  - Days where the difference > 1% (potential split-adjustment artifacts)
  - Coverage: how many bhav days vs yfinance days

Output: console table + saved CSV. This is the gate before considering swapping
yfinance → Bhavcopy as the primary OHLCV source.

Run: .venv/bin/python -m data.cross_check_bhav_vs_yfinance
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
YF_DIR = ROOT / "data" / "ohlcv"
BHAV_DIR = ROOT / "data" / "ohlcv_bhav"
OUT_CSV = ROOT / "reports" / "bhav_vs_yfinance_audit.csv"

TICKERS = ["RELIANCE", "NESTLEIND", "KOTAKBANK", "MAZDOCK", "SYNGENE"]


def compare_one(ticker: str) -> dict:
    yf = YF_DIR / f"{ticker}.parquet"
    bv = BHAV_DIR / f"{ticker}.parquet"
    if not yf.exists():
        return {"ticker": ticker, "error": "yfinance parquet missing"}
    if not bv.exists():
        return {"ticker": ticker, "error": "bhavcopy parquet missing"}

    yf_df = pd.read_parquet(yf)
    bv_df = pd.read_parquet(bv)

    # Overlap window (intersection of dates)
    overlap = yf_df.index.intersection(bv_df.index)
    if len(overlap) == 0:
        return {"ticker": ticker, "error": "no overlap window"}

    a = yf_df.loc[overlap].sort_index()
    b = bv_df.loc[overlap].sort_index()

    close_diff_pct = (a["close"] - b["close"]) / b["close"] * 100
    vol_diff_pct = (a["volume"] - b["volume"]) / b["volume"].replace(0, np.nan) * 100

    return {
        "ticker": ticker,
        "yf_rows": len(yf_df),
        "bhav_rows": len(bv_df),
        "overlap_days": len(overlap),
        "overlap_start": str(overlap[0].date()),
        "overlap_end": str(overlap[-1].date()),
        "close_mean_abs_diff_pct": round(close_diff_pct.abs().mean(), 4),
        "close_max_abs_diff_pct": round(close_diff_pct.abs().max(), 2),
        "n_close_diffs_above_1pct": int((close_diff_pct.abs() > 1.0).sum()),
        "n_close_diffs_above_5pct": int((close_diff_pct.abs() > 5.0).sum()),
        "vol_mean_abs_diff_pct": round(vol_diff_pct.abs().mean(skipna=True), 2),
        "n_vol_diffs_above_5pct": int((vol_diff_pct.abs() > 5.0).sum()),
        "yf_today_close": round(float(yf_df["close"].iloc[-1]), 2),
        "bhav_today_close": round(float(bv_df["close"].iloc[-1]), 2),
    }


def main() -> None:
    rows = [compare_one(t) for t in TICKERS]
    df = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"\n{df.to_string(index=False, max_colwidth=20)}\n")
    print(f"Saved: {OUT_CSV}")
    print("\n=== INTERPRETATION ===")
    print("close_mean_abs_diff_pct: average % difference in close price between yfinance and Bhavcopy.")
    print("  • Near-zero (< 0.1%): same data, just rounding")
    print("  • 1-5%: yfinance is dividend-adjusting historically (expected for stocks with heavy divs)")
    print("  • > 5%: real discrepancy — investigate")
    print("vol_mean_abs_diff_pct: volume should be near-identical (Bhavcopy is the source of truth).")
    print("  • Near-zero: data quality verified")
    print("  • > 1%: yfinance has the wrong volume — this is a known yfinance issue.")


if __name__ == "__main__":
    main()
