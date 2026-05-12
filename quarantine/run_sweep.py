"""
P0.7 — Run all quarantine checks across the universe, write flags to quarantine.db.

Run: .venv/bin/python -m quarantine.run_sweep
     .venv/bin/python -m quarantine.run_sweep --rebuild   (drops existing flags first)
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from quarantine.checks import all_checks_for_ticker, is_fno_expiry
from quarantine.store import init_db, insert_flags, summary


ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = ROOT / "data" / "ohlcv"
DEFAULT_DB = ROOT / "quarantine" / "quarantine.db"


def sweep_per_ticker_checks(db_path: Path, ohlcv_dir: Path) -> dict:
    """Run all per-ticker checks across every parquet."""
    parquets = sorted(p for p in ohlcv_dir.glob("*.parquet") if not p.stem.startswith("_"))
    print(f"Sweeping {len(parquets)} tickers for per-ticker quarantine checks...")

    total_flags = 0
    inserted = 0
    flagged_tickers = set()
    for i, p in enumerate(parquets, 1):
        symbol = p.stem
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            print(f"  ! {symbol} parquet read failed: {e}")
            continue
        flags = all_checks_for_ticker(symbol, df)
        if flags:
            flagged_tickers.add(symbol)
            total_flags += len(flags)
            n_inserted = insert_flags(db_path, flags)
            inserted += n_inserted
        if i % 100 == 0:
            print(f"  [{i:3d}/{len(parquets)}] {len(flagged_tickers)} tickers flagged so far, {total_flags} total flags")

    return {
        "n_scanned": len(parquets),
        "n_flagged_tickers": len(flagged_tickers),
        "total_flags_emitted": total_flags,
        "n_inserted": inserted,
    }


def sweep_date_level_checks(db_path: Path, ohlcv_dir: Path) -> dict:
    """Date-level facts (e.g., F&O expiry) — flag once per date, symbol=NULL."""
    # Use _NSEI as the canonical trading calendar
    nsei = ohlcv_dir / "_NSEI.parquet"
    if not nsei.exists():
        return {"n_inserted": 0, "skipped": "no calendar"}
    df = pd.read_parquet(nsei)

    flags = []
    for d in df.index:
        if is_fno_expiry(d):
            flags.append({
                "date": str(d.date()),
                "symbol": None,  # applies to all
                "check_name": "fno_expiry",
                "severity": "tier2",
                "tier": 2,
                "details": "Last Thursday of month — monthly F&O contract expiry. "
                           "Volume artificially elevated from position rolls; not real demand transfer.",
            })

    n_inserted = insert_flags(db_path, flags)
    return {"n_emitted": len(flags), "n_inserted": n_inserted}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="Drop existing flags before sweeping")
    args = ap.parse_args()

    if args.rebuild and DEFAULT_DB.exists():
        DEFAULT_DB.unlink()
        print(f"Dropped existing {DEFAULT_DB}")

    init_db(DEFAULT_DB)

    print("=" * 70)
    print("Phase 1: per-ticker checks (split anomaly, dummy, circuit, IPO, suspended)")
    print("=" * 70)
    s1 = sweep_per_ticker_checks(DEFAULT_DB, OHLCV_DIR)
    print(f"\n  scanned={s1['n_scanned']}  flagged_tickers={s1['n_flagged_tickers']}  "
          f"total_flags={s1['total_flags_emitted']}  inserted={s1['n_inserted']}")

    print("\n" + "=" * 70)
    print("Phase 2: date-level checks (F&O expiry calendar)")
    print("=" * 70)
    s2 = sweep_date_level_checks(DEFAULT_DB, OHLCV_DIR)
    print(f"  emitted={s2.get('n_emitted', 0)}  inserted={s2['n_inserted']}")

    print("\n" + "=" * 70)
    print("Quarantine summary")
    print("=" * 70)
    summ = summary(DEFAULT_DB)
    if not summ.empty:
        print(summ.to_string(index=False))
    else:
        print("  (no flags)")


if __name__ == "__main__":
    main()
