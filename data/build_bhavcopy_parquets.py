"""
Assemble per-ticker OHLCV parquets from raw Bhavcopy daily CSVs.

Output schema (compatible with yfinance parquets we already have, plus 2 bonus columns):
  date (index, DatetimeIndex)
  open, high, low, close, volume      ← matches yfinance
  prev_close                          ← from Bhavcopy, used for split detection
  deliv_qty                           ← Bhavcopy bonus: delivery quantity
  deliv_per                           ← Bhavcopy bonus: delivery percentage (institutional signal)

Output: data/ohlcv_bhav/{SYMBOL}.parquet   (separate from existing data/ohlcv/ — yfinance ones)

This SEPARATE directory is intentional for the migration:
  - data/ohlcv/        — existing yfinance parquets (still used by everything currently)
  - data/ohlcv_bhav/   — new Bhavcopy parquets (for verification + future swap)

Once cross-checked and trusted, the swap path is: rename ohlcv_bhav → ohlcv (and back up
the old yfinance parquets).

Run: .venv/bin/python -m data.build_bhavcopy_parquets
     .venv/bin/python -m data.build_bhavcopy_parquets --series EQ BE
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "bhav_raw"
OUT_DIR = ROOT / "data" / "ohlcv_bhav"


def parse_one_bhav(csv_path: Path, allowed_series: set[str]) -> pd.DataFrame:
    """Parse a single bhav CSV. Returns long-form DataFrame with one row per ticker."""
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]  # NSE has leading whitespace
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
    df["SERIES"] = df["SERIES"].astype(str).str.strip()
    df = df[df["SERIES"].isin(allowed_series)]
    df["DATE1"] = df["DATE1"].astype(str).str.strip()
    df["date"] = pd.to_datetime(df["DATE1"], format="%d-%b-%Y")
    out = pd.DataFrame({
        "symbol":     df["SYMBOL"],
        "date":       df["date"],
        "open":       pd.to_numeric(df["OPEN_PRICE"], errors="coerce"),
        "high":       pd.to_numeric(df["HIGH_PRICE"], errors="coerce"),
        "low":        pd.to_numeric(df["LOW_PRICE"], errors="coerce"),
        "close":      pd.to_numeric(df["CLOSE_PRICE"], errors="coerce"),
        "volume":     pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce"),
        "prev_close": pd.to_numeric(df["PREV_CLOSE"], errors="coerce"),
        "deliv_qty":  pd.to_numeric(df["DELIV_QTY"], errors="coerce"),
        "deliv_per":  pd.to_numeric(df["DELIV_PER"], errors="coerce"),
    })
    return out.dropna(subset=["close"])


def build(allowed_series: set[str], raw_dir: Path = RAW_DIR, out_dir: Path = OUT_DIR) -> dict:
    """Read all bhav files in raw_dir, group by ticker, write parquets."""
    out_dir.mkdir(parents=True, exist_ok=True)
    csvs = sorted(raw_dir.glob("sec_bhavdata_full_*.csv"))
    print(f"Reading {len(csvs)} bhav files from {raw_dir}...")

    by_ticker: dict[str, list[pd.DataFrame]] = defaultdict(list)
    for i, csv in enumerate(csvs, 1):
        try:
            df = parse_one_bhav(csv, allowed_series)
            for sym, sub in df.groupby("symbol"):
                by_ticker[sym].append(sub)
        except Exception as e:
            print(f"  ! {csv.name}: parse failed — {e}")
        if i % 50 == 0:
            print(f"  [{i:4d}/{len(csvs)}] {csv.stem.replace('sec_bhavdata_full_', '')}: {len(by_ticker)} tickers so far")

    print(f"\nAssembling parquets for {len(by_ticker)} tickers...")
    summary = {"n_tickers": 0, "n_total_rows": 0, "skipped_short": 0}
    for sym, frames in by_ticker.items():
        full = pd.concat(frames, ignore_index=True)
        full = full.drop_duplicates(subset=["date"]).sort_values("date").set_index("date")
        full = full.drop(columns=["symbol"])
        if len(full) < 5:
            summary["skipped_short"] += 1
            continue
        full.to_parquet(out_dir / f"{sym}.parquet", compression="snappy")
        summary["n_tickers"] += 1
        summary["n_total_rows"] += len(full)
    print(f"  Wrote {summary['n_tickers']} parquets ({summary['n_total_rows']:,} total rows). "
          f"{summary['skipped_short']} skipped (<5 rows).")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="+", default=["EQ"],
                    help="NSE series codes to include (default: EQ only). "
                         "Common: EQ (cash equity), BE (Trade-to-Trade), BL (Block deal series)")
    args = ap.parse_args()
    build(set(args.series))


if __name__ == "__main__":
    main()
