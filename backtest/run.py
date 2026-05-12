"""
Backtest CLI entrypoint with HOLDOUT PROTECTION.

Sacred holdout: 2025-01-01 onwards is locked. Any backtest extending beyond 2024-12-31
requires explicit --open-holdout flag. Once opened, the holdout cannot be re-used for
parameter tuning (this is honor-system; the user must self-enforce).

Usage:
  .venv/bin/python -m backtest.run --start 2024-04-01 --end 2024-04-30          # smoke test
  .venv/bin/python -m backtest.run --start 2020-01-01 --end 2024-12-31          # train window
  .venv/bin/python -m backtest.run --start 2025-01-01 --end 2026-04-30 --open-holdout
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from backtest.simulator import BacktestConfig, run_backtest
from backtest.metrics import compute_metrics
from backtest.report import write_report


HOLDOUT_BOUNDARY = pd.Timestamp("2025-01-01")
ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--label", default=None, help="Label for output files. Defaults to start_end.")
    ap.add_argument("--min-score", type=float, default=50.0)
    ap.add_argument("--min-vol-ratio", type=float, default=1.5)
    ap.add_argument("--include-below-50dma", action="store_true")
    ap.add_argument("--require-above-200dma", action="store_true",
                    help="Stricter trend filter — only enter on stocks above their 200-DMA.")
    ap.add_argument("--regime-filter", action="store_true",
                    help="Block NEW entries when Nifty 50 close < its 200-DMA (existing positions unaffected). "
                         "Tests the hypothesis that the strategy is regime-dependent.")
    ap.add_argument("--atr-stop", type=float, default=2.0)
    ap.add_argument("--atr-target", type=float, default=4.0)
    ap.add_argument("--timeout-days", type=int, default=20)
    ap.add_argument("--initial-capital", type=float, default=100_000.0)
    ap.add_argument("--max-positions", type=int, default=50)
    ap.add_argument("--open-holdout", action="store_true",
                    help="Required to run backtest extending past 2024-12-31. SACRED.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    end_ts = pd.Timestamp(args.end)
    if end_ts >= HOLDOUT_BOUNDARY and not args.open_holdout:
        sys.exit(
            f"\n❌ HOLDOUT PROTECTION TRIGGERED\n"
            f"  end date {args.end} >= holdout boundary {HOLDOUT_BOUNDARY.date()}\n"
            f"  Re-run with --open-holdout to proceed (cannot be undone).\n"
        )

    config = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.initial_capital,
        min_score=args.min_score,
        min_volume_ratio=args.min_vol_ratio,
        require_above_50dma=not args.include_below_50dma,
        require_above_200dma=args.require_above_200dma,
        regime_filter_enabled=args.regime_filter,
        atr_stop_mult=args.atr_stop,
        atr_target_mult=args.atr_target,
        timeout_days=args.timeout_days,
        max_concurrent_positions=args.max_positions,
    )

    label = args.label or f"{args.start}_to_{args.end}"
    print(f"\n=== Backtest: {label} ===")
    print(f"  Min score: {args.min_score}, vol≥{args.min_vol_ratio}×, above50dma={config.require_above_50dma}")
    print(f"  ATR stop {args.atr_stop}× / target {args.atr_target}× / timeout {args.timeout_days}d")
    print(f"  Capital ₹{args.initial_capital:,.0f}, max {args.max_positions} concurrent positions")
    print(f"  Costs: 0.25% round-trip\n")

    t0 = time.time()
    result = run_backtest(config, verbose=args.verbose)
    elapsed = time.time() - t0
    print(f"\n  ⏱  Backtest done in {elapsed:.1f}s ({elapsed/60:.1f} min)")

    metrics = compute_metrics(result.trades, result.equity_curve, config.initial_capital)
    paths = write_report(result, metrics, REPORTS_DIR, label)

    print(f"\n  📊 EV per trade: {metrics.ev_per_trade_r:+.3f}R  (gate: > 0.2R)")
    print(f"  📈 CAGR {metrics.cagr:.2%}  ·  Max DD {metrics.max_drawdown:.1%}  ·  "
          f"Trades {metrics.n_trades}  ·  Hit {metrics.hit_rate:.1%}")
    print(f"\n  Report: {paths['md']}")
    print(f"  Equity: {paths['png']}")


if __name__ == "__main__":
    main()
