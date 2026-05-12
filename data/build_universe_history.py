"""
P0.6 — Historical universe builder (pragmatic version).

PROBLEM: True point-in-time NSE 500 membership for 2020-2026 requires either NSE's
historical constituents API (doesn't exist as a clean URL) or paid index history (out of
scope for personal-use research tool).

PRAGMATIC APPROACH: for each historical month, take today's Nifty 500 universe and
filter to tickers that had OHLCV data on at least one day in that month. This:
  ✓ Excludes recent IPOs from their pre-existence months (kills worst bias)
  ✓ Reflects the actual data we can backtest against
  ✓ Is fully reproducible from on-disk parquets — no external API dependency
  ✗ Still includes survivors that have remained in Nifty 500 throughout (residual bias)
  ✗ Misses stocks that were in Nifty 500 historically but dropped out (delisted / demoted)
     Those failed-breakout cases are invisible to backtest. Real bias direction:
     OPTIMISTIC (failure cases under-represented), so live results may be worse than backtest.

Documented limitation. Same compromise momentum-dashboard accepted, but now explicit
and queryable. True point-in-time backfill = P0.6b (deferred, needs paid feed).

Output: data/universe_history/{YYYY-MM}.csv per month
        data/universe_history/_summary.csv (one row per month with count + earliest/latest IPO)

Run: .venv/bin/python -m data.build_universe_history
     .venv/bin/python -m data.build_universe_history --start 2024-01 --end 2024-12
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_CSV = ROOT / "data" / "universe_1000cr.csv"
OHLCV_DIR = ROOT / "data" / "ohlcv"
HISTORY_DIR = ROOT / "data" / "universe_history"

# Default range — matches our OHLCV cache (started fetching from 2020-01-01)
# End defaults to PREVIOUS month — current month has only partial data and would render as 0
DEFAULT_START = "2020-01"
DEFAULT_END = (pd.Timestamp.today() - pd.offsets.MonthBegin(1)).strftime("%Y-%m")


def month_iter(start: str, end: str):
    """Yield "YYYY-MM" strings inclusive."""
    cur = pd.Timestamp(start + "-01")
    end_ts = pd.Timestamp(end + "-01")
    while cur <= end_ts:
        yield cur.strftime("%Y-%m")
        cur = cur + pd.offsets.MonthBegin(1)


def build_month_snapshot(
    target_month: str,
    today_universe: pd.DataFrame,
    ohlcv_dir: Path,
) -> pd.DataFrame:
    """Filter today's universe to tickers that had data in target_month."""
    month_start = pd.Timestamp(f"{target_month}-01")
    month_end = month_start + pd.offsets.MonthEnd(0)

    keep_rows = []
    for _, t in today_universe.iterrows():
        parquet = ohlcv_dir / f"{t['SYMBOL']}.parquet"
        if not parquet.exists():
            continue
        try:
            df = pd.read_parquet(parquet, columns=["close"])
        except Exception:
            continue
        in_month = df.loc[month_start:month_end]
        if len(in_month) > 0:
            keep_rows.append(t)

    out = pd.DataFrame(keep_rows).reset_index(drop=True)
    if not out.empty:
        out["AS_OF_MONTH"] = target_month
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START, help="YYYY-MM")
    ap.add_argument("--end", default=DEFAULT_END, help="YYYY-MM")
    args = ap.parse_args()

    if not UNIVERSE_CSV.exists():
        raise SystemExit(f"Run build_universe.py first — {UNIVERSE_CSV} missing")

    today_universe = pd.read_csv(UNIVERSE_CSV)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Building monthly historical snapshots from {args.start} to {args.end}")
    print(f"Source universe: {len(today_universe)} tickers (today's Nifty 500)")
    print()

    summary_rows = []
    for ym in month_iter(args.start, args.end):
        snap = build_month_snapshot(ym, today_universe, OHLCV_DIR)
        out_path = HISTORY_DIR / f"{ym}.csv"
        snap.to_csv(out_path, index=False)
        summary_rows.append({
            "month": ym,
            "n_tickers": len(snap),
            "delta_vs_today": len(snap) - len(today_universe),
        })
        bar = "█" * (len(snap) // 10)
        print(f"  {ym}  {len(snap):4d} tickers  {bar}")

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(HISTORY_DIR / "_summary.csv", index=False)
    print(f"\n✓ {len(summary_rows)} monthly snapshots written to {HISTORY_DIR}/")
    print(f"  Range: {summary['n_tickers'].min()} → {summary['n_tickers'].max()} tickers/month")
    print(f"  Summary: {HISTORY_DIR}/_summary.csv")


if __name__ == "__main__":
    main()
