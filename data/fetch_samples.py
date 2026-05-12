"""
P0.0 — Sample-data prefetch.

Fetches 5 hand-picked tickers chosen to surface edge cases:
  - RELIANCE      — high-liquidity blue chip, sanity baseline
  - NESTLEIND     — recent 1:10 split (2024-01), verify yfinance auto-adjust works
  - KOTAKBANK     — historical splits, financial sector sanity
  - MAZDOCK       — multi-bagger run, useful for breakout / volume profile testing
  - GROWW         — recent IPO (Nov 2025), verify partial-history handling

Plus the Nifty 50 index (^NSEI) for regime filter when adapted.

Output:
  data/ohlcv/{SYMBOL}.parquet
  data/ohlcv/_NSEI.parquet
  data/fetch_samples_log.csv

Period: 2017-01-01 → today  (5+ years for volume profile / backtest)

Run: .venv/bin/python data/fetch_samples.py
Resumable: rerun skips tickers that already have a parquet file.
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent
OHLCV_DIR = ROOT / "ohlcv"
LOG_PATH = ROOT / "fetch_samples_log.csv"

# Sample universe — chosen for edge-case coverage, not statistical representativeness
SAMPLES = [
    {"symbol": "RELIANCE",   "yf_ticker": "RELIANCE.NS",  "note": "blue-chip baseline"},
    {"symbol": "NESTLEIND",  "yf_ticker": "NESTLEIND.NS", "note": "1:10 split 2024-01 — verify auto-adjust"},
    {"symbol": "KOTAKBANK",  "yf_ticker": "KOTAKBANK.NS", "note": "split history — financials"},
    {"symbol": "MAZDOCK",    "yf_ticker": "MAZDOCK.NS",   "note": "multi-bagger — breakout test"},
    {"symbol": "GROWW",      "yf_ticker": "GROWW.NS",     "note": "recent IPO — partial history"},
]
INDEX_TICKER = "^NSEI"
INDEX_FILE = "_NSEI.parquet"

START = "2017-01-01"
END = date.today().isoformat()

PAUSE_SEC = 0.5
MAX_RETRIES = 2


def fetch_one(ticker: str, retries: int = MAX_RETRIES) -> pd.DataFrame | None:
    """Pull daily OHLCV for one ticker. Auto-adjusts for splits/dividends."""
    for attempt in range(retries + 1):
        try:
            df = yf.download(
                ticker,
                start=START,
                end=END,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            df = df.rename(columns=str.lower)
            df.index.name = "date"
            return df
        except Exception as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"  ! {ticker}: failed after {retries+1} attempts — {e}")
            return None
    return None


def main() -> None:
    OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    log_rows: list[dict] = []
    fetched = skipped = failed = 0

    # Index first
    idx_path = OHLCV_DIR / INDEX_FILE
    if idx_path.exists():
        print(f"= {INDEX_TICKER}: cached")
        skipped += 1
        log_rows.append({"ticker": INDEX_TICKER, "rows": -1, "status": "skipped_cached", "note": "Nifty 50"})
    else:
        print(f"+ {INDEX_TICKER}: fetching Nifty 50 ...")
        df = fetch_one(INDEX_TICKER)
        if df is not None and len(df) > 0:
            df.to_parquet(idx_path, compression="snappy")
            fetched += 1
            print(f"  ✓ {len(df)} rows → {idx_path.name}")
            log_rows.append({"ticker": INDEX_TICKER, "rows": len(df), "status": "ok", "note": "Nifty 50"})
        else:
            failed += 1
            print(f"  ✗ {INDEX_TICKER} failed")
            log_rows.append({"ticker": INDEX_TICKER, "rows": 0, "status": "failed", "note": "Nifty 50"})
        time.sleep(PAUSE_SEC)

    # Samples
    total = len(SAMPLES)
    for i, row in enumerate(SAMPLES, 1):
        sym, yt, note = row["symbol"], row["yf_ticker"], row["note"]
        path = OHLCV_DIR / f"{sym}.parquet"

        if path.exists():
            skipped += 1
            log_rows.append({"ticker": yt, "rows": -1, "status": "skipped_cached", "note": note})
            print(f"  [{i}/{total}] = {sym} cached")
            continue

        df = fetch_one(yt)
        if df is not None and len(df) > 0:
            df.to_parquet(path, compression="snappy")
            fetched += 1
            log_rows.append({"ticker": yt, "rows": len(df), "status": "ok", "note": note})
            print(f"  [{i}/{total}] ✓ {sym} — {len(df)} rows ({note})")
        else:
            failed += 1
            log_rows.append({"ticker": yt, "rows": 0, "status": "failed", "note": note})
            print(f"  [{i}/{total}] ✗ {sym} — failed ({note})")

        time.sleep(PAUSE_SEC)

    pd.DataFrame(log_rows).to_csv(LOG_PATH, index=False)

    print("\n" + "=" * 60)
    print(f"P0.0 done. fetched={fetched}  cached={skipped}  failed={failed}")
    print(f"Log:   {LOG_PATH}")
    print(f"Cache: {OHLCV_DIR}/")


if __name__ == "__main__":
    main()
