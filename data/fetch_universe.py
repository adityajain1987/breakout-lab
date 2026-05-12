"""
P0.2 — Fetch OHLCV for the full 1000Cr+ universe.

Generalisation of fetch_samples.py — reads data/universe_1000cr.csv and pulls every ticker.
Resumable: skips tickers that already have a parquet in data/ohlcv/.

Run: .venv/bin/python -m data.fetch_universe
Time: ~10-15 min for 500 tickers (0.4s pause × 500 = 200s base + ~1s/ticker download).

Output: data/ohlcv/{TICKER}.parquet for each ticker
        data/fetch_universe_log.csv (audit trail)
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf


ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_CSV = ROOT / "data" / "universe_1000cr.csv"
OHLCV_DIR = ROOT / "data" / "ohlcv"
LOG_PATH = ROOT / "data" / "fetch_universe_log.csv"

START = "2005-01-01"           # 20+ years history (catches decadal-cycle breakouts like BHEL 2026)
END = date.today().isoformat()
PAUSE_SEC = 0.4
MAX_RETRIES = 2


def fetch_one(ticker: str, retries: int = MAX_RETRIES) -> pd.DataFrame | None:
    for attempt in range(retries + 1):
        try:
            df = yf.download(
                ticker, start=START, end=END,
                auto_adjust=True, progress=False, threads=False,
            )
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            df = df.rename(columns=str.lower)
            df.index.name = "date"
            return df
        except Exception:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return None
    return None


def main() -> None:
    OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(UNIVERSE_CSV)
    tickers = universe[["SYMBOL", "YF_TICKER"]].to_dict("records")
    total = len(tickers)
    print(f"Fetching {total} tickers from Nifty 500 universe...")

    log_rows = []
    fetched = skipped = failed = 0
    t0 = time.time()

    for i, row in enumerate(tickers, 1):
        sym, yt = row["SYMBOL"], row["YF_TICKER"]
        path = OHLCV_DIR / f"{sym}.parquet"

        if path.exists():
            skipped += 1
            log_rows.append({"ticker": yt, "rows": -1, "status": "cached"})
            if i % 50 == 0:
                elapsed = time.time() - t0
                print(f"  [{i:3d}/{total}] cached so far: {skipped}, fetched: {fetched}, failed: {failed}  ({elapsed:.0f}s)")
            continue

        df = fetch_one(yt)
        if df is not None and len(df) > 0:
            df.to_parquet(path, compression="snappy")
            fetched += 1
            log_rows.append({"ticker": yt, "rows": len(df), "status": "ok"})
            if fetched % 10 == 0:
                elapsed = time.time() - t0
                rate = fetched / elapsed if elapsed > 0 else 0
                print(f"  [{i:3d}/{total}] ✓ {sym} ({len(df)} rows)  rate: {rate:.1f}/s  failed: {failed}")
        else:
            failed += 1
            log_rows.append({"ticker": yt, "rows": 0, "status": "failed"})
            print(f"  [{i:3d}/{total}] ✗ {sym} failed")

        time.sleep(PAUSE_SEC)

    pd.DataFrame(log_rows).to_csv(LOG_PATH, index=False)
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed/60:.1f} min. fetched={fetched}  cached={skipped}  failed={failed}")


if __name__ == "__main__":
    main()
