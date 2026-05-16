"""
Scan all cached tickers for decade-breakout setups as of a given date.

Companion to scan_universe.py / scan_ranges.py — same shape, different question.
  scan_universe:         "which stocks are BREAKING OUT today?"
  scan_ranges:           "which stocks are still IN a range?"
  scan_decade_breakouts: "which stocks are APPROACHING a >10-year-old, never-tested high?"

Composes the tested `decade_breakout_state` across every parquet in data/ohlcv/.
Output sorted with "Broke today" first, then closest "Approaching" by smallest gap_pct.

Run:
  .venv/bin/python -m analytics.scan_decade_breakouts --asof 2026-05-08
  .venv/bin/python -m analytics.scan_decade_breakouts --asof 2026-05-08 --proximity-pct 5
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import NamedTuple, Optional

import pandas as pd

from analytics.decade_breakouts import decade_breakout_state
from analytics.scan_universe import _resolve_parquet, load_universe_meta


ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = ROOT / "data" / "ohlcv"
OHLCV_BHAV_DIR = ROOT / "data" / "ohlcv_bhav"
UNIVERSE_CSV = ROOT / "data" / "universe_1000cr.csv"

SCAN_SOFT_WARN_SECONDS = 30.0


class DecadeBreakoutScanResult(NamedTuple):
    """Returned by scan_decade_breakouts — DataFrame of eligible setups + scan metadata."""
    df: pd.DataFrame
    asof: str
    n_scanned: int
    n_eligible: int
    skipped_no_data: int
    skipped_no_asof: int
    skipped_short_history: int
    skipped_touched: int
    skipped_too_far: int
    scan_duration_seconds: float


def scan_decade_breakouts(
    asof_date: str | pd.Timestamp,
    proximity_pct: float = 2.0,
    lookback_years: int = 10,
    min_history_years: int = 11,
    top_n: int = 50,
    sector_filter: Optional[list[str]] = None,
    ohlcv_dir: Optional[Path] = None,
    bhav_dir: Optional[Path] = None,
) -> DecadeBreakoutScanResult:
    """
    Scan every parquet in ohlcv_dir for decade-breakout setups.

    DataFrame columns:
      ticker, company, sector, close, day_change_pct,
      H_old, H_old_date, H_old_age_years, gap_pct,
      H_recent, H_recent_date, status
    """
    start_ts = time.time()
    if ohlcv_dir is None:
        ohlcv_dir = OHLCV_DIR
    asof = pd.Timestamp(asof_date)
    meta = load_universe_meta()

    if bhav_dir is None:
        bhav_dir = OHLCV_BHAV_DIR if (ohlcv_dir == OHLCV_DIR and OHLCV_BHAV_DIR.exists()) else None

    yf_tickers = {p.stem for p in ohlcv_dir.glob("*.parquet") if not p.stem.startswith("_")}
    bv_tickers = (
        {p.stem for p in bhav_dir.glob("*.parquet") if not p.stem.startswith("_")}
        if bhav_dir is not None and bhav_dir.exists() else set()
    )
    all_tickers = sorted(yf_tickers | bv_tickers)

    universe_filter = None
    if UNIVERSE_CSV.exists() and ohlcv_dir == OHLCV_DIR:
        try:
            universe_filter = set(pd.read_csv(UNIVERSE_CSV)["SYMBOL"].astype(str))
        except Exception:
            pass

    sector_set = set(sector_filter) if sector_filter else None
    rows: list[dict] = []
    n_scanned = 0
    skipped_no_data = skipped_no_asof = 0
    skipped_short = skipped_touched = skipped_far = 0

    for ticker in all_tickers:
        if universe_filter is not None and ticker not in universe_filter:
            continue
        n_scanned += 1

        p = _resolve_parquet(ticker, asof, ohlcv_dir, bhav_dir=bhav_dir)
        if p is None:
            skipped_no_data += 1
            continue
        try:
            df = pd.read_parquet(p)
        except Exception:
            skipped_no_data += 1
            continue
        if asof not in df.index:
            skipped_no_asof += 1
            continue

        info = meta.get(ticker, {"company": ticker, "sector": "—"})
        if sector_set is not None and info["sector"] not in sector_set:
            continue

        try:
            ds = decade_breakout_state(
                df, asof, ticker=ticker,
                lookback_years=lookback_years,
                proximity_pct=proximity_pct,
                min_history_years=min_history_years,
            )
        except (ValueError, IndexError):
            skipped_no_data += 1
            continue

        if not ds.eligible:
            if "insufficient history" in ds.reason or "no bars older" in ds.reason:
                skipped_short += 1
            elif "touched in lookback" in ds.reason:
                skipped_touched += 1
            elif "too far" in ds.reason:
                skipped_far += 1
            else:
                skipped_no_data += 1
            continue

        today_idx = df.index.get_loc(asof)
        yesterday_close = (float(df.iloc[today_idx - 1]["close"])
                           if today_idx > 0 else ds.today_close)
        day_change_pct = (ds.today_close - yesterday_close) / yesterday_close * 100

        rows.append({
            "ticker": ticker,
            "company": info["company"],
            "sector": info["sector"],
            "close": ds.today_close,
            "day_change_pct": day_change_pct,
            "H_old": ds.H_old,
            "H_old_date": ds.H_old_date,
            "H_old_age_years": ds.H_old_age_years,
            "gap_pct": ds.gap_pct,
            "H_recent": ds.H_recent,
            "H_recent_date": ds.H_recent_date,
            "status": ds.status,
        })

    if rows:
        # "Broke today" first (sort desc lexically: "Broke today" > "Approaching"),
        # then closest gap first.
        out = (pd.DataFrame(rows)
               .sort_values(["status", "gap_pct"], ascending=[False, True])
               .head(top_n)
               .reset_index(drop=True))
    else:
        out = pd.DataFrame(columns=[
            "ticker", "company", "sector", "close", "day_change_pct",
            "H_old", "H_old_date", "H_old_age_years", "gap_pct",
            "H_recent", "H_recent_date", "status",
        ])

    duration = time.time() - start_ts
    if duration > SCAN_SOFT_WARN_SECONDS:
        import warnings
        warnings.warn(
            f"scan_decade_breakouts took {duration:.1f}s (soft threshold {SCAN_SOFT_WARN_SECONDS}s).",
            stacklevel=2,
        )

    return DecadeBreakoutScanResult(
        df=out,
        asof=str(asof.date()),
        n_scanned=n_scanned,
        n_eligible=len(rows),
        skipped_no_data=skipped_no_data,
        skipped_no_asof=skipped_no_asof,
        skipped_short_history=skipped_short,
        skipped_touched=skipped_touched,
        skipped_too_far=skipped_far,
        scan_duration_seconds=duration,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True, help="YYYY-MM-DD")
    ap.add_argument("--proximity-pct", type=float, default=2.0,
                    help="Alert when close ≥ H_old × (1 - this/100). Default 2.0.")
    ap.add_argument("--lookback-years", type=int, default=10,
                    help="Untouched-window length. Default 10.")
    ap.add_argument("--min-history-years", type=int, default=11,
                    help="Minimum stock history. Default 11 (gives 1y buffer over lookback).")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--sector", nargs="*")
    args = ap.parse_args()

    result = scan_decade_breakouts(
        asof_date=args.asof,
        proximity_pct=args.proximity_pct,
        lookback_years=args.lookback_years,
        min_history_years=args.min_history_years,
        top_n=args.top_n,
        sector_filter=args.sector,
    )

    print(f"\nScanned {result.n_scanned} tickers as of {result.asof}")
    print(f"  Eligible (within {args.proximity_pct}% of {args.lookback_years}y-old high): "
          f"{result.n_eligible}")
    print(f"  Skipped — short history (<{args.min_history_years}y): {result.skipped_short_history}")
    print(f"  Skipped — touched in last {args.lookback_years}y: {result.skipped_touched}")
    print(f"  Skipped — too far from H_old:    {result.skipped_too_far}")
    print(f"  Skipped — no data / no asof bar: "
          f"{result.skipped_no_data + result.skipped_no_asof}")
    print(f"  Duration: {result.scan_duration_seconds:.1f}s\n")

    if result.df.empty:
        print("(no eligible stocks)")
        return

    print(f"TOP {len(result.df)} DECADE-BREAKOUT WATCHLIST:")
    print(f"{'#':>2} {'TICKER':<12} {'Sector':<22} {'LTP':>10} {'H_old':>10} "
          f"{'Gap':>6} {'Age':>5} {'H_old set':<12} {'Status':<14}")
    print("-" * 115)
    for i, row in result.df.iterrows():
        sector = (row["sector"][:20] + "..") if len(row["sector"]) > 22 else row["sector"]
        h_old_date = pd.Timestamp(row["H_old_date"]).strftime("%Y-%m-%d")
        print(f"{i+1:>2} {row['ticker']:<12} {sector:<22} ₹{row['close']:>8.2f} "
              f"₹{row['H_old']:>8.2f} {row['gap_pct']:>5.1f}% {row['H_old_age_years']:>4.1f}y "
              f"{h_old_date:<12} {row['status']:<14}")


if __name__ == "__main__":
    main()
