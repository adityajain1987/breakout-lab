"""
Scan all cached tickers for breakouts as of a given date — the engine behind
the "Breakouts Today" page (DESIGN.md page 2).

Composes the existing tested `breakout_state` across every parquet in data/ohlcv/.
Returns a ScanResult (NamedTuple) with both the ranked DataFrame and scan metadata.

Run: .venv/bin/python -m analytics.scan_universe --asof 2026-04-30
     .venv/bin/python -m analytics.scan_universe --asof 2026-04-30 --min-score 50 --top-n 30
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import NamedTuple, Optional

import pandas as pd

from analytics.breakout_detector import breakout_state


class ScanResult(NamedTuple):
    """Returned by scan_universe — DataFrame of qualified breakouts + scan metadata."""
    df: pd.DataFrame
    asof: str
    n_scanned: int
    n_qualified: int
    skipped_no_data: int
    skipped_no_asof: int
    filtered_score: int
    filtered_ma: int
    filtered_vol: int


ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = ROOT / "data" / "ohlcv"
OHLCV_BHAV_DIR = ROOT / "data" / "ohlcv_bhav"
UNIVERSE_CSV = ROOT / "data" / "universe_1000cr.csv"


def _resolve_parquet(ticker: str, asof: pd.Timestamp, primary_dir: Path,
                     bhav_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Pick the BEST parquet for this ticker × asof.

    Priority:
      1. yfinance if it has the asof bar (we backfill yfinance to 2005 for deep history,
         which the new decadal-high detector needs ≥5000 bars to fire).
      2. Bhavcopy if yfinance is stale today (yfinance India lags 1-3 days at NSE close).
      3. Whichever has any data (caller will skip if neither has today's bar).
    """
    bhav = (bhav_dir or OHLCV_BHAV_DIR) / f"{ticker}.parquet"
    yf = primary_dir / f"{ticker}.parquet"

    # 1. Prefer yfinance — it has 21 years of history; decadal detector needs depth.
    if yf.exists():
        try:
            df = pd.read_parquet(yf, columns=["close"])
            if asof in df.index:
                return yf
        except Exception:
            pass

    # 2. Bhavcopy fallback for staleness.
    if bhav.exists():
        try:
            df = pd.read_parquet(bhav, columns=["close"])
            if asof in df.index:
                return bhav
        except Exception:
            pass

    # 3. Neither has the asof bar.
    return yf if yf.exists() else (bhav if bhav.exists() else None)


def load_universe_meta() -> dict[str, dict]:
    """Build {SYMBOL: {company, sector}} lookup."""
    if not UNIVERSE_CSV.exists():
        return {}
    df = pd.read_csv(UNIVERSE_CSV)
    return {row["SYMBOL"]: {"company": row["COMPANY"], "sector": row["SECTOR"]}
            for _, row in df.iterrows()}


def scan_universe(
    asof_date: str | pd.Timestamp,
    min_score: float = 30.0,
    require_above_50dma: bool = True,
    require_above_200dma: bool = False,
    min_volume_ratio: float = 1.5,
    top_n: int = 20,
    ohlcv_dir: Optional[Path] = None,
    bhav_dir: Optional[Path] = None,
) -> ScanResult:
    """
    Scan every parquet in ohlcv_dir for breakouts as of asof_date.
    Returns ScanResult(df, asof, n_scanned, n_qualified, ...).

    DataFrame columns:
      ticker, company, sector, close, day_change_pct, breakout_score,
      hvn_break, swing_high_break, cycle_high_break, level_broken,
      volume_ratio, close_in_range_pct, above_50dma, above_200dma,
      sma_50, sma_200
    """
    if ohlcv_dir is None:
        ohlcv_dir = OHLCV_DIR
    asof = pd.Timestamp(asof_date)
    meta = load_universe_meta()

    rows: list[dict] = []
    skipped_no_data = 0
    skipped_no_asof = 0
    filtered_score = 0
    filtered_ma = 0
    filtered_vol = 0

    # Universe = union of tickers across BOTH sources. Bhavcopy is preferred (fresher /
    # official NSE) but yfinance is fallback for any ticker missing from Bhavcopy.
    # Tests can pass bhav_dir=None or a tmp dir to isolate from real data.
    if bhav_dir is None:
        # Default behaviour: use real Bhavcopy ONLY when reading the real ohlcv dir.
        bhav_dir = OHLCV_BHAV_DIR if (ohlcv_dir == OHLCV_DIR and OHLCV_BHAV_DIR.exists()) else None

    yf_tickers = {p.stem for p in ohlcv_dir.glob("*.parquet") if not p.stem.startswith("_")}
    bv_tickers = (
        {p.stem for p in bhav_dir.glob("*.parquet") if not p.stem.startswith("_")}
        if bhav_dir is not None and bhav_dir.exists() else set()
    )
    all_tickers = sorted(yf_tickers | bv_tickers)

    # If a universe CSV exists, restrict to it (1000Cr+ filter). Tests pass an
    # isolated ohlcv_dir, so we skip the real CSV in that case.
    universe_filter = None
    if UNIVERSE_CSV.exists() and ohlcv_dir == OHLCV_DIR:
        try:
            universe_filter = set(pd.read_csv(UNIVERSE_CSV)["SYMBOL"].astype(str))
        except Exception:
            pass

    n_scanned_total = 0
    for ticker in all_tickers:
        if universe_filter is not None and ticker not in universe_filter:
            continue
        n_scanned_total += 1
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

        try:
            bs = breakout_state(df, asof)
        except (ValueError, IndexError):
            skipped_no_data += 1
            continue

        # Apply filters
        if require_above_50dma and not bs.above_50dma:
            filtered_ma += 1
            continue
        if require_above_200dma and not bs.above_200dma:
            filtered_ma += 1
            continue
        if bs.volume_ratio < min_volume_ratio:
            filtered_vol += 1
            continue
        if bs.breakout_score < min_score:
            filtered_score += 1
            continue

        # Compute day change (need yesterday's close)
        today_idx = df.index.get_loc(asof)
        yesterday_close = float(df.iloc[today_idx - 1]["close"])
        day_change_pct = (bs.close - yesterday_close) / yesterday_close * 100

        info = meta.get(ticker, {"company": ticker, "sector": "—"})
        rows.append({
            "ticker": ticker,
            "company": info["company"],
            "sector": info["sector"],
            "close": bs.close,
            "day_change_pct": day_change_pct,
            "breakout_score": bs.breakout_score,
            "hvn_break": bs.hvn_break,
            "swing_high_break": bs.swing_high_break,
            "cycle_high_break": bs.cycle_high_break,
            "decadal_high_break": bs.decadal_high_break,
            "level_broken": bs.level_broken,
            "volume_ratio": bs.volume_ratio,
            "close_in_range_pct": bs.close_in_range_pct,
            "above_50dma": bs.above_50dma,
            "above_200dma": bs.above_200dma,
            "sma_50": bs.sma_50,
            "sma_200": bs.sma_200,
        })

    if rows:
        out = pd.DataFrame(rows).sort_values("breakout_score", ascending=False).head(top_n).reset_index(drop=True)
    else:
        out = pd.DataFrame(columns=[
            "ticker", "company", "sector", "close", "day_change_pct", "breakout_score",
            "hvn_break", "swing_high_break", "cycle_high_break", "decadal_high_break",
            "level_broken", "volume_ratio", "close_in_range_pct", "above_50dma", "above_200dma",
            "sma_50", "sma_200",
        ])
    return ScanResult(
        df=out,
        asof=str(asof.date()),
        n_scanned=n_scanned_total,
        n_qualified=len(rows),
        skipped_no_data=skipped_no_data,
        skipped_no_asof=skipped_no_asof,
        filtered_score=filtered_score,
        filtered_ma=filtered_ma,
        filtered_vol=filtered_vol,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True, help="YYYY-MM-DD")
    ap.add_argument("--min-score", type=float, default=30.0)
    ap.add_argument("--min-vol-ratio", type=float, default=1.5)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--include-below-50dma", action="store_true")
    ap.add_argument("--require-above-200dma", action="store_true")
    args = ap.parse_args()

    result = scan_universe(
        asof_date=args.asof,
        min_score=args.min_score,
        min_volume_ratio=args.min_vol_ratio,
        top_n=args.top_n,
        require_above_50dma=not args.include_below_50dma,
        require_above_200dma=args.require_above_200dma,
    )
    print(f"\nScanned {result.n_scanned} tickers as of {result.asof}")
    print(f"  Qualified: {result.n_qualified}")
    print(f"  Filtered out: score<{args.min_score}: {result.filtered_score}, "
          f"MA filter: {result.filtered_ma}, vol<{args.min_vol_ratio}x: {result.filtered_vol}")
    print(f"  Skipped: no asof bar: {result.skipped_no_asof}, no data: {result.skipped_no_data}\n")

    if result.df.empty:
        print("(no breakouts matched filters)")
        return

    print(f"TOP {len(result.df)} BREAKOUTS:")
    print(f"{'#':>2} {'TICKER':<12} {'Sector':<28} {'Close':>9} {'Chg':>6} {'Lvls':>4} {'Vol×':>5} {'CIR':>4} {'Score':>5}")
    print("-" * 95)
    for i, row in result.df.iterrows():
        flags = []
        if row["hvn_break"]: flags.append("H")
        if row["swing_high_break"]: flags.append("S")
        if row["cycle_high_break"]: flags.append("C")
        if row.get("decadal_high_break", False): flags.append("D")
        flag_str = "+".join(flags) or "-"
        sector = (row["sector"][:25] + "..") if len(row["sector"]) > 27 else row["sector"]
        print(f"{i+1:>2} {row['ticker']:<12} {sector:<28} ₹{row['close']:>7.2f} {row['day_change_pct']:>+5.1f}% {flag_str:>4} {row['volume_ratio']:>4.1f}x {row['close_in_range_pct']:>4.0%} {row['breakout_score']:>5.1f}")


if __name__ == "__main__":
    main()
