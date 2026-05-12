"""
NSE bulk + block deals daily fetcher.

Strategy: poll the static archive endpoints — they work without cookies/auth and serve
the latest day's snapshot. Run daily (cron or manual). Deals accumulate into deals.db
over time via the dedupe-on-insert UNIQUE constraint.

Endpoints (verified working 2026-05-01):
  https://archives.nseindia.com/content/equities/bulk.csv
  https://archives.nseindia.com/content/equities/block.csv

Historical backfill: NSE's JSON API for date-range queries is broken (404s even with
full Chrome TLS impersonation via curl_cffi). Tracked as P0.3b — needs alternative
(manual UI download + import, paid feed, or scraping the deals report HTML page).

CSV columns (both bulk + block):
  Date, Symbol, Security Name, Client Name, Buy/Sell, Quantity Traded,
  Trade Price / Wght. Avg. Price, Remarks (bulk only)

Run: .venv/bin/python -m deals.scraper
Idempotent: rerunning the same day's fetch inserts 0 rows.
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from deals.store import insert_deals


BULK_URL = "https://archives.nseindia.com/content/equities/bulk.csv"
BLOCK_URL = "https://archives.nseindia.com/content/equities/block.csv"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "deals" / "deals.db"


def _fetch_csv(url: str, deal_type: str) -> pd.DataFrame:
    """Fetch the latest archive CSV and parse to standardised schema."""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    text = r.content.decode("utf-8", errors="replace")

    # Some archive responses for empty days have "NO RECORDS" placeholder
    if "NO RECORDS" in text.split("\n")[1] if len(text.split("\n")) > 1 else True:
        # Try parsing anyway — empty body still has the header
        pass

    raw = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    if raw.empty:
        return _empty_frame()

    # Drop the "NO RECORDS" sentinel row if present
    raw = raw[~raw["Symbol"].astype(str).str.contains("NO RECORDS", na=False)]
    raw = raw.dropna(subset=["Symbol"])

    if raw.empty:
        return _empty_frame()

    # Normalise to our schema. NSE column names are inconsistent (whitespace, casing).
    raw.columns = [c.strip() for c in raw.columns]

    # Date arrives as "30-APR-2026" → ISO "2026-04-30"
    raw["date"] = pd.to_datetime(raw["Date"], format="%d-%b-%Y").dt.strftime("%Y-%m-%d")

    out = pd.DataFrame({
        "date":      raw["date"],
        "symbol":    raw["Symbol"].astype(str).str.strip(),
        "deal_type": deal_type,
        "client":    raw["Client Name"].astype(str).str.strip(),
        "side":      raw["Buy/Sell"].astype(str).str.upper().str.strip(),
        "quantity":  raw["Quantity Traded"].astype(int),
        "price":     raw["Trade Price / Wght. Avg. Price"].astype(float),
        "remarks":   raw.get("Remarks", pd.Series([""] * len(raw))).astype(str),
    })
    return out


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "date", "symbol", "deal_type", "client", "side", "quantity", "price", "remarks",
    ])


def fetch_and_store(db_path: Path = DEFAULT_DB) -> dict:
    """Pull both archives, write to db, return summary."""
    summary = {"timestamp": datetime.utcnow().isoformat(timespec="seconds")}

    print(f"[{summary['timestamp']}] fetching NSE bulk + block deals (latest snapshot)")

    # Bulk
    try:
        bulk_df = _fetch_csv(BULK_URL, "bulk")
        bulk_inserted = insert_deals(db_path, bulk_df)
        summary["bulk"] = {"fetched": len(bulk_df), "inserted": bulk_inserted, "ok": True}
        if not bulk_df.empty:
            summary["bulk"]["date_in_csv"] = bulk_df["date"].iloc[0]
        print(f"  bulk:  fetched={len(bulk_df):4d}  inserted={bulk_inserted:4d}  date={summary['bulk'].get('date_in_csv', 'n/a')}")
    except Exception as e:
        summary["bulk"] = {"ok": False, "error": str(e)}
        print(f"  bulk:  FAILED — {e}")

    # Block
    try:
        block_df = _fetch_csv(BLOCK_URL, "block")
        block_inserted = insert_deals(db_path, block_df)
        summary["block"] = {"fetched": len(block_df), "inserted": block_inserted, "ok": True}
        if not block_df.empty:
            summary["block"]["date_in_csv"] = block_df["date"].iloc[0]
        print(f"  block: fetched={len(block_df):4d}  inserted={block_inserted:4d}  date={summary['block'].get('date_in_csv', 'n/a')}")
    except Exception as e:
        summary["block"] = {"ok": False, "error": str(e)}
        print(f"  block: FAILED — {e}")

    return summary


if __name__ == "__main__":
    fetch_and_store()
