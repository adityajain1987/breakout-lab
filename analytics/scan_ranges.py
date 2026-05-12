"""
Scan all cached tickers for horizontal trading ranges as of a given date.

Companion to analytics/scan_universe.py — same shape, opposite question.
  scan_universe: "which stocks are BREAKING OUT today?"
  scan_ranges:   "which stocks are still IN a range right now?"

Composes the existing tested `range_state` across every parquet in data/ohlcv/.
Returns a RangeScanResult (NamedTuple) with both the ranked DataFrame and scan metadata.

Run: .venv/bin/python -m analytics.scan_ranges --asof 2026-04-30
     .venv/bin/python -m analytics.scan_ranges --asof 2026-04-30 --min-stars 3 --status in-range
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import NamedTuple, Optional

import pandas as pd

from analytics.range_detector import range_state
from analytics.scan_universe import _resolve_parquet, load_universe_meta  # reuse helpers


ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = ROOT / "data" / "ohlcv"
OHLCV_BHAV_DIR = ROOT / "data" / "ohlcv_bhav"
UNIVERSE_CSV = ROOT / "data" / "universe_1000cr.csv"
Q_DB = ROOT / "quarantine" / "quarantine.db"


# Soft-warning threshold per plan-eng-review Section 4. Not blocking — just logged.
SCAN_SOFT_WARN_SECONDS = 30.0


class RangeScanResult(NamedTuple):
    """DataFrame of qualified ranges + scan metadata."""
    df: pd.DataFrame
    asof: str
    n_scanned: int
    n_qualified: int
    skipped_no_data: int
    skipped_no_asof: int
    skipped_short_history: int
    skipped_no_pair: int
    skipped_quarantine_invalidated: int
    filtered_stars: int
    filtered_status: int
    filtered_maturity: int
    filtered_stale: int
    scan_duration_seconds: float


def scan_ranges(
    asof_date: str | pd.Timestamp,
    min_stars: int = 1,
    status_filter: str = "all",          # "all" | "in-range" | "breakout"
    maturity_filter: Optional[list[str]] = None,  # subset of ["Emerging","Established","Major"]
    sector_filter: Optional[list[str]] = None,
    max_stale_days: int = 90,            # drop ranges whose last touch is older than this
    top_n: int = 50,
    ohlcv_dir: Optional[Path] = None,
    bhav_dir: Optional[Path] = None,
    quarantine_db: Optional[Path] = None,
    **detector_kwargs,
) -> RangeScanResult:
    """
    Scan every parquet in ohlcv_dir for horizontal ranges as of asof_date.

    Filters:
      min_stars       — drop ranges with star count below this (1-4)
      status_filter   — "all" / "in-range" / "breakout"
      maturity_filter — keep only these maturity tags
      sector_filter   — keep only these sectors

    detector_kwargs passes through to range_state (e.g., atr_tolerance_mult).
    """
    start_ts = time.time()
    if ohlcv_dir is None:
        ohlcv_dir = OHLCV_DIR
    asof = pd.Timestamp(asof_date)
    meta = load_universe_meta()

    if quarantine_db is None:
        quarantine_db = Q_DB if Q_DB.exists() else None

    # Same data-source resolution as scan_universe
    if bhav_dir is None:
        bhav_dir = OHLCV_BHAV_DIR if (ohlcv_dir == OHLCV_DIR and OHLCV_BHAV_DIR.exists()) else None

    yf_tickers = {p.stem for p in ohlcv_dir.glob("*.parquet") if not p.stem.startswith("_")}
    bv_tickers = (
        {p.stem for p in bhav_dir.glob("*.parquet") if not p.stem.startswith("_")}
        if bhav_dir is not None and bhav_dir.exists() else set()
    )
    all_tickers = sorted(yf_tickers | bv_tickers)

    # 1000Cr+ universe filter (only when scanning real data)
    universe_filter = None
    if UNIVERSE_CSV.exists() and ohlcv_dir == OHLCV_DIR:
        try:
            universe_filter = set(pd.read_csv(UNIVERSE_CSV)["SYMBOL"].astype(str))
        except Exception:
            pass

    rows: list[dict] = []
    n_scanned = 0
    skipped_no_data = 0
    skipped_no_asof = 0
    skipped_short_history = 0
    skipped_no_pair = 0
    skipped_quar = 0
    filtered_stars = 0
    filtered_status = 0
    filtered_maturity = 0
    filtered_stale = 0

    maturity_set = set(maturity_filter) if maturity_filter else None
    sector_set = set(sector_filter) if sector_filter else None

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

        # Sector filter (cheap — apply BEFORE expensive range_state)
        info = meta.get(ticker, {"company": ticker, "sector": "—"})
        if sector_set is not None and info["sector"] not in sector_set:
            continue

        try:
            rs = range_state(df, asof, ticker=ticker, quarantine_db=quarantine_db,
                             **detector_kwargs)
        except (ValueError, IndexError):
            skipped_no_data += 1
            continue

        if not rs.qualified:
            # Classify why for telemetry
            if "insufficient history" in rs.reason:
                skipped_short_history += 1
            elif "invalidated" in rs.reason or "Tier 1" in rs.reason:
                skipped_quar += 1
            else:
                skipped_no_pair += 1
            continue

        if rs.stars < min_stars:
            filtered_stars += 1
            continue
        if status_filter == "in-range" and rs.status != "In-Range":
            filtered_status += 1
            continue
        if status_filter == "breakout" and rs.status != "Recent Breakout":
            filtered_status += 1
            continue
        if maturity_set is not None and rs.maturity_tag not in maturity_set:
            filtered_maturity += 1
            continue
        if rs.last_touch_days_ago > max_stale_days:
            filtered_stale += 1
            continue

        # Today's close + day change for the row
        today_idx = df.index.get_loc(asof)
        today_close = float(df.iloc[today_idx]["close"])
        yesterday_close = float(df.iloc[today_idx - 1]["close"]) if today_idx > 0 else today_close
        day_change_pct = (today_close - yesterday_close) / yesterday_close * 100

        rows.append({
            "ticker": ticker,
            "company": info["company"],
            "sector": info["sector"],
            "close": today_close,
            "day_change_pct": day_change_pct,
            "resistance": rs.resistance_mean,
            "support": rs.support_mean,
            "width_pct": rs.width_pct_of_price,
            "stars": rs.stars,
            "round_number": rs.round_number_flag,
            "role_reversal": rs.role_reversal_flag,
            "volume_confirmed": rs.volume_node_confirmed,
            "duration_days": rs.range_duration_days,
            "last_touch_days_ago": rs.last_touch_days_ago,
            "maturity": rs.maturity_tag,
            "status": rs.status,
            "breakout_direction": rs.breakout_direction,
            "breakout_days_ago": rs.breakout_days_ago,
            "quarantine_flag": rs.quarantine_flag,
        })

    if rows:
        out = (pd.DataFrame(rows)
               .sort_values(["stars", "last_touch_days_ago", "duration_days"],
                            ascending=[False, True, False])
               .head(top_n)
               .reset_index(drop=True))
    else:
        out = pd.DataFrame(columns=[
            "ticker", "company", "sector", "close", "day_change_pct",
            "resistance", "support", "width_pct", "stars", "round_number",
            "role_reversal", "volume_confirmed", "duration_days", "last_touch_days_ago",
            "maturity", "status", "breakout_direction", "breakout_days_ago",
            "quarantine_flag",
        ])

    duration = time.time() - start_ts
    if duration > SCAN_SOFT_WARN_SECONDS:
        # Soft warning per plan-eng-review — log it but do not fail.
        import warnings
        warnings.warn(
            f"scan_ranges took {duration:.1f}s (soft threshold {SCAN_SOFT_WARN_SECONDS}s). "
            f"Investigate if this trends upward.",
            stacklevel=2,
        )

    return RangeScanResult(
        df=out,
        asof=str(asof.date()),
        n_scanned=n_scanned,
        n_qualified=len(rows),
        skipped_no_data=skipped_no_data,
        skipped_no_asof=skipped_no_asof,
        skipped_short_history=skipped_short_history,
        skipped_no_pair=skipped_no_pair,
        skipped_quarantine_invalidated=skipped_quar,
        filtered_stars=filtered_stars,
        filtered_status=filtered_status,
        filtered_maturity=filtered_maturity,
        filtered_stale=filtered_stale,
        scan_duration_seconds=duration,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True, help="YYYY-MM-DD")
    ap.add_argument("--min-stars", type=int, default=1, help="1-4")
    ap.add_argument("--status", choices=["all", "in-range", "breakout"], default="all")
    ap.add_argument("--maturity", choices=["Emerging", "Established", "Major"], nargs="*")
    ap.add_argument("--sector", nargs="*")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--tolerance-mult", type=float, default=1.5,
                    help="ATR-tolerance multiplier (0.5=tight, 1.5=medium, 3.0=loose)")
    ap.add_argument("--max-stale-days", type=int, default=90,
                    help="Drop ranges with last touch older than this many days")
    args = ap.parse_args()

    result = scan_ranges(
        asof_date=args.asof,
        min_stars=args.min_stars,
        status_filter=args.status,
        maturity_filter=args.maturity,
        sector_filter=args.sector,
        max_stale_days=args.max_stale_days,
        top_n=args.top_n,
        atr_tolerance_mult=args.tolerance_mult,
    )

    print(f"\nScanned {result.n_scanned} tickers as of {result.asof}")
    print(f"  Qualified (passed all filters): {result.n_qualified}")
    print(f"  Filtered out: stars {result.filtered_stars}, status {result.filtered_status}, "
          f"maturity {result.filtered_maturity}, stale {result.filtered_stale}")
    print(f"  Skipped (no range found): short history {result.skipped_short_history}, "
          f"no pair {result.skipped_no_pair}, quarantine invalidated "
          f"{result.skipped_quarantine_invalidated}")
    print(f"  Skipped (no data): no asof {result.skipped_no_asof}, "
          f"no data {result.skipped_no_data}")
    print(f"  Duration: {result.scan_duration_seconds:.1f}s\n")

    if result.df.empty:
        print("(no ranges matched filters)")
        return

    print(f"TOP {len(result.df)} RANGES:")
    print(f"{'#':>2} {'TICKER':<12} {'Sector':<22} {'LTP':>8} {'R':>8} {'S':>8} {'W%':>5} "
          f"{'★':>4} {'Maturity':<11} {'Status':<16} {'Dur':>5} {'Last':>5}")
    print("-" * 120)
    for i, row in result.df.iterrows():
        stars_str = "★" * int(row["stars"])
        sector = (row["sector"][:20] + "..") if len(row["sector"]) > 22 else row["sector"]
        status_str = row["status"]
        if row["status"] == "Recent Breakout":
            status_str = f"BO {row['breakout_direction']} ({int(row['breakout_days_ago'])}d)"
        flags = []
        if row.get("round_number"): flags.append("💰")
        if row.get("role_reversal"): flags.append("↻")
        if row.get("volume_confirmed"): flags.append("📊")
        if row.get("quarantine_flag"): flags.append("⚠")
        flag_str = "".join(flags)
        print(f"{i+1:>2} {row['ticker']:<12} {sector:<22} ₹{row['close']:>6.0f} "
              f"₹{row['resistance']:>6.0f} ₹{row['support']:>6.0f} {row['width_pct']:>4.0f}% "
              f"{stars_str:>4} {row['maturity']:<11} {status_str:<16} {int(row['duration_days']):>4}d "
              f"{int(row['last_touch_days_ago']):>4}d {flag_str}")


if __name__ == "__main__":
    main()
