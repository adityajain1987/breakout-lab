"""
P0.1 — Universe builder.

Per Phase 1 office hours Item 1: use Nifty 500 list AS the 1000Cr+ universe (instead of
computing market cap from Bhavcopy × shares outstanding which requires data we don't have
cleanly). Justification: smallest current Nifty 500 constituent is well above ₹5,000Cr,
so this is a strict superset of "all 1000Cr+ stocks Amit cares about" with zero false
inclusions.

Output: data/universe_1000cr.csv with columns: SYMBOL, COMPANY, SECTOR, ISIN, YF_TICKER, BUILT_AT
Versioned snapshot: data/universe_history/{YYYY-MM-DD}.csv (for future point-in-time backtest)

Run: .venv/bin/python -m data.build_universe
Idempotent: re-runs overwrite the CSV but keep the dated snapshot.
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd
import requests


NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
UNIVERSE_CSV = DATA_DIR / "universe_1000cr.csv"
HISTORY_DIR = DATA_DIR / "universe_history"


def fetch_nifty500() -> pd.DataFrame:
    """Pull the current Nifty 500 list from NSE."""
    r = requests.get(NIFTY500_URL, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    raw = pd.read_csv(io.StringIO(r.text))
    raw.columns = [c.strip() for c in raw.columns]
    return raw


def build() -> pd.DataFrame:
    """Build the universe CSV. Returns the DataFrame."""
    raw = fetch_nifty500()
    today = date.today().isoformat()

    out = pd.DataFrame({
        "SYMBOL":     raw["Symbol"].astype(str).str.strip(),
        "COMPANY":    raw["Company Name"].astype(str).str.strip(),
        "SECTOR":     raw["Industry"].astype(str).str.strip(),
        "ISIN":       raw["ISIN Code"].astype(str).str.strip(),
        "YF_TICKER":  raw["Symbol"].astype(str).str.strip() + ".NS",
        "BUILT_AT":   today,
    })

    # Filter to EQ series only (exclude BE/BL trade-to-trade)
    if "Series" in raw.columns:
        eq_mask = raw["Series"].astype(str).str.strip() == "EQ"
        out = out[eq_mask].reset_index(drop=True)

    # Persist
    DATA_DIR.mkdir(exist_ok=True)
    HISTORY_DIR.mkdir(exist_ok=True)
    out.to_csv(UNIVERSE_CSV, index=False)
    snapshot = HISTORY_DIR / f"{today}.csv"
    out.to_csv(snapshot, index=False)

    return out


def main() -> None:
    df = build()
    print(f"✓ Universe built: {len(df)} EQ-series tickers from Nifty 500")
    print(f"  Output: {UNIVERSE_CSV}")
    print(f"  Snapshot: {HISTORY_DIR}/{date.today()}.csv")
    print()
    print("  Sector distribution (top 10):")
    sectors = df["SECTOR"].value_counts().head(10)
    for sec, n in sectors.items():
        print(f"    {sec:30s}  {n:3d}")
    print()
    print(f"  Sample tickers (first 10): {', '.join(df['SYMBOL'].head(10))}")


if __name__ == "__main__":
    main()
