"""
NSE Bhavcopy fetcher — official daily OHLCV files.

URL format (verified working 2026-05): https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
Schema: SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE,
        CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES,
        DELIV_QTY, DELIV_PER

Note the bonus columns vs yfinance: DELIV_QTY and DELIV_PER (delivery percentage).
Delivery % is a real institutional signal — high delivery = positions held overnight,
low delivery = day traders churning. We get this for free with Bhavcopy.

Storage: data/bhav_raw/sec_bhavdata_full_DDMMYYYY.csv per day. Resumable (skips existing).
Throttled at 0.5s/request to be polite to NSE.

Run:
  .venv/bin/python -m data.fetch_bhavcopy --start 2024-01-01 --end 2026-04-30
  .venv/bin/python -m data.fetch_bhavcopy --days 250                    # last 250 trading days
  .venv/bin/python -m data.fetch_bhavcopy --year 2025                   # one calendar year
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "bhav_raw"
NSEI_PARQUET = ROOT / "data" / "ohlcv" / "_NSEI.parquet"

URL_FMT = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0"
PAUSE_SEC = 0.5
TIMEOUT = 20


def fetch_one_day(target_date: date, raw_dir: Path) -> dict:
    """Fetch a single day's Bhavcopy. Returns {ok, status_code, size, path, skipped}."""
    ddmmyyyy = target_date.strftime("%d%m%Y")
    out = raw_dir / f"sec_bhavdata_full_{ddmmyyyy}.csv"
    if out.exists() and out.stat().st_size > 1000:  # arbitrary "real file" threshold
        return {"ok": True, "skipped": True, "path": str(out), "size": out.stat().st_size}
    url = URL_FMT.format(ddmmyyyy=ddmmyyyy)
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code == 200 and len(r.content) > 1000:
            out.write_bytes(r.content)
            return {"ok": True, "skipped": False, "path": str(out), "size": len(r.content)}
        return {"ok": False, "status_code": r.status_code, "size": len(r.content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def trading_days_from_index(start: date, end: date) -> list[date]:
    """Determine NSE trading days in [start, end].

    Strategy: use _NSEI parquet (yfinance Nifty 50) for historical days where it's
    reliable, but for any days AFTER the parquet ends, fall back to pandas BDay
    (skip weekends; NSE holidays will return 404 from the bhavcopy fetcher and just
    get logged as failed — that's fine).

    This way, yfinance India lag doesn't block the Bhavcopy auto-refresh from
    trying recent days that the calendar parquet hasn't caught up to yet.
    """
    if not NSEI_PARQUET.exists():
        idx = pd.date_range(start=start, end=end, freq="B")
        return [d.date() for d in idx]
    df = pd.read_parquet(NSEI_PARQUET, columns=["close"])
    nsei_last = df.index[-1].date()
    # Days inside the NSEI calendar
    sub = df.loc[pd.Timestamp(start):pd.Timestamp(end)]
    days_from_nsei = [d.date() for d in sub.index]
    # Days after NSEI ends — use BDay fallback
    if end > nsei_last:
        bday_start = max(start, nsei_last + pd.Timedelta(days=1).to_pytimedelta())
        bday_idx = pd.bdate_range(start=bday_start, end=end)
        days_from_bday = [d.date() for d in bday_idx]
        return sorted(set(days_from_nsei) | set(days_from_bday))
    return days_from_nsei


def fetch_range(start: date, end: date, raw_dir: Path = RAW_DIR) -> dict:
    """Fetch all trading days in [start, end]. Resumable."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    days = trading_days_from_index(start, end)
    print(f"Fetching {len(days)} trading days from {start} to {end}...")

    fetched = skipped = failed = 0
    failed_dates: list[str] = []
    t0 = time.time()
    for i, d in enumerate(days, 1):
        r = fetch_one_day(d, raw_dir)
        if r["ok"]:
            if r.get("skipped"):
                skipped += 1
            else:
                fetched += 1
            if i % 25 == 0:
                elapsed = time.time() - t0
                rate = fetched / elapsed if elapsed > 0 else 0
                print(f"  [{i:4d}/{len(days)}] {d}  fetched={fetched}  cached={skipped}  failed={failed}  rate={rate:.1f}/s")
        else:
            failed += 1
            failed_dates.append(str(d))
            print(f"  [{i:4d}/{len(days)}] {d}  FAILED ({r.get('status_code', r.get('error'))})")
        if not r.get("skipped"):
            time.sleep(PAUSE_SEC)

    elapsed = time.time() - t0
    summary = {
        "elapsed_s": round(elapsed, 1),
        "n_days_target": len(days),
        "fetched": fetched, "cached": skipped, "failed": failed,
        "failed_dates": failed_dates,
    }
    print(f"\nDone in {elapsed/60:.1f} min. {summary}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", help="YYYY-MM-DD (used with --end)")
    g.add_argument("--days", type=int, help="Last N trading days")
    g.add_argument("--year", type=int, help="One calendar year (e.g. 2024)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD")
    args = ap.parse_args()

    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
    elif args.days:
        end = date.today()
        # rough estimate; the trading_days_from_index will refine this
        start = end - timedelta(days=int(args.days * 1.5))
    elif args.year:
        start = date(args.year, 1, 1)
        end = date(args.year, 12, 31)
    else:
        sys.exit("Pass --start/--end, --days, or --year")

    fetch_range(start, end)


if __name__ == "__main__":
    main()
