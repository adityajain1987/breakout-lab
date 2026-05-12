"""
Page 3 — Backtest Playground.

Two modes:
  1. View canned results from full TRAIN runs (5-year backtests, pre-computed)
  2. Run a custom backtest on a SHORT window (≤ 90 days, ≤ 60 sec runtime)

Holdout protection: window end past 2024-12-31 requires explicit "open holdout" checkbox.
Holdout is sacred — opening it cannot be undone. The TRAIN run already failed the +0.2R
gate, so opening holdout now would just confirm the kill rather than validate an edge.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backtest.simulator import BacktestConfig, run_backtest  # noqa: E402
from backtest.metrics import compute_metrics  # noqa: E402

REPORTS_DIR = ROOT / "reports"
HOLDOUT_BOUNDARY = pd.Timestamp("2025-01-01").date()

st.set_page_config(page_title="Backtest Playground", page_icon="🧪", layout="wide")
st.title("🧪 Backtest Playground")

tab1, tab2 = st.tabs(["📊 Canned TRAIN runs", "⚙️ Custom short backtest"])

# =========================================================================
# Tab 1 — canned results from the full 5-year TRAIN runs
# =========================================================================
with tab1:
    st.markdown("### TRAIN 2020-2024 — three runs (latest tune leftmost)")
    st.markdown("""
    Per office-hours discipline, ONE parameter tune is allowed before declaring the
    strategy passes/fails the gate. The score-tighten attempt didn't move the needle.
    """)

    train_data = pd.DataFrame([
        {"Run": "v1 (default config)",                "Window": "TRAIN 2020-24", "Min score": 50, "Trades": 5478, "Hit%": 45.4,
         "Avg win R": 1.32, "Avg loss R": -0.85, "EV/trade R": 0.136, "CAGR%": 13.95, "Max DD%": 28.0},
        {"Run": "v3 (score=70 tune)",                 "Window": "TRAIN 2020-24", "Min score": 70, "Trades": 4315, "Hit%": 45.8,
         "Avg win R": 1.34, "Avg loss R": -0.84, "EV/trade R": 0.145, "CAGR%": 15.61, "Max DD%": 24.1},
        {"Run": "V_premium (selective entry)",        "Window": "TRAIN 2020-24", "Min score": 60, "Trades": 4776, "Hit%": 46.1,
         "Avg win R": 1.32, "Avg loss R": -0.85, "EV/trade R": 0.152, "CAGR%": 16.16, "Max DD%": 25.0},
        {"Run": "V_long_hold (asymmetric exit)",      "Window": "TRAIN 2020-24", "Min score": 50, "Trades": 4568, "Hit%": 34.9,
         "Avg win R": 2.50, "Avg loss R": -0.97, "EV/trade R": 0.258, "CAGR%": 20.43, "Max DD%": 30.5},
        {"Run": "V_combo (selective + asymmetric)",   "Window": "TRAIN 2020-24", "Min score": 60, "Trades": 4096, "Hit%": 35.9,
         "Avg win R": 2.56, "Avg loss R": -0.97, "EV/trade R": 0.299, "CAGR%": 23.71, "Max DD%": 26.1},
        {"Run": "V_combo HOLDOUT (the kill)",         "Window": "HOLDOUT 2025-26", "Min score": 60, "Trades": 798, "Hit%": 29.3,
         "Avg win R": 2.11, "Avg loss R": -0.96, "EV/trade R": -0.059, "CAGR%": -5.88, "Max DD%": 17.6},
        {"Run": "V_combo + regime filter (closure test)", "Window": "HOLDOUT 2025-26", "Min score": 60, "Trades": 615, "Hit%": 27.6,
         "Avg win R": 2.18, "Avg loss R": -0.97, "EV/trade R": -0.097, "CAGR%": -6.40, "Max DD%": 10.0},
    ])
    st.dataframe(
        train_data,
        column_config={
            "EV/trade R": st.column_config.NumberColumn("EV/trade R", format="%+.3f", help="Expected value per trade in R units. Gate: > +0.2R"),
            "Hit%":       st.column_config.NumberColumn("Hit%", format="%.1f%%"),
            "CAGR%":      st.column_config.NumberColumn("CAGR%", format="%.2f%%"),
            "Max DD%":    st.column_config.NumberColumn("Max DD%", format="%.1f%%"),
        },
        hide_index=True, use_container_width=True,
    )

    st.error(
        "🛑 **HOLDOUT FAILED — strategy DEFINITIVELY killed.** Best train variant V_combo cleared "
        "the gate at +0.299R EV. Opened the holdout (one-shot, irreversible) — collapsed to "
        "**-0.059R EV** on 2025-2026 data. Hit rate dropped from 35.9% (train) to 29.3% (holdout). "
        "The sacred-holdout discipline did its job — caught a curve-fit BEFORE any real capital moved."
    )

    st.warning(
        "🔬 **Regime-filter closure test (post-kill, intellectual only):** added a Nifty 50 200-DMA "
        "regime filter to V_combo and re-ran on the spent holdout. Result: EV got **WORSE**, not "
        "better (-0.097R vs -0.059R). The hypothesis 'strategy is regime-dependent, just needs a "
        "regime filter' was wrong. The breakout signal isn't predictive even in confirmed bull "
        "regimes during 2025-2026. Future strategy redesigns need fundamentally different signals "
        "(multi-day confirmation, sector RS, cross-asset signals, mean reversion, ensemble), not "
        "parameter tweaks on this one. **The sacred holdout is now spent and cannot be re-used.**"
    )

    # Show the most recent equity curve PNG if it exists
    pngs = sorted(REPORTS_DIR.glob("backtest_TRAIN_2020_2024_v3_*_equity.png"), reverse=True)
    if pngs:
        st.markdown("### Equity curve (v3 — latest TRAIN)")
        st.image(str(pngs[0]), use_container_width=True)

    # Show the most recent markdown report
    mds = sorted(REPORTS_DIR.glob("backtest_TRAIN_2020_2024_v3_*.md"), reverse=True)
    if mds:
        with st.expander("Full markdown report (v3 TRAIN)"):
            st.markdown(mds[0].read_text())


# =========================================================================
# Tab 2 — custom short backtest (interactive)
# =========================================================================
with tab2:
    st.markdown("### Custom backtest — short windows only")
    st.markdown(
        "Run an event-based breakout backtest on a window you choose. Limited to **90 days max** "
        "to keep runtime under ~60 seconds. For full 5-year runs, use the CLI: "
        "`python -m backtest.run --start 2024-01-01 --end 2024-12-31`"
    )

    col_a, col_b = st.columns(2)
    with col_a:
        start = st.date_input("Start", value=date(2024, 4, 1),
                              min_value=date(2020, 1, 1),
                              max_value=date.today())
    with col_b:
        end = st.date_input("End", value=date(2024, 4, 30),
                            min_value=date(2020, 1, 1),
                            max_value=date.today())

    if (pd.Timestamp(end) - pd.Timestamp(start)).days > 90:
        st.warning("Window > 90 days. Use CLI for long backtests; this page caps interactive runs at 90d.")

    holdout_open = False
    if pd.Timestamp(end).date() >= HOLDOUT_BOUNDARY:
        holdout_open = st.checkbox(
            "⚠️ I understand the holdout (2025+) is sacred and opening it is a one-shot decision",
            value=False,
            help="The TRAIN gate already failed; opening holdout now just confirms the kill. Only check if you're sure."
        )
        if not holdout_open:
            st.error(f"End date {end} crosses holdout boundary {HOLDOUT_BOUNDARY}. Check the box above to proceed.")

    st.markdown("#### Strategy params")
    p1, p2, p3 = st.columns(3)
    with p1:
        min_score_b = st.slider("Min breakout score", 0, 100, 50, step=5)
        min_vol_b = st.slider("Min volume ratio (×)", 1.0, 5.0, 1.5, step=0.1)
        above_50_b = st.checkbox("Above 50-DMA filter", value=True)
    with p2:
        atr_stop = st.slider("Stop = N × ATR", 0.5, 5.0, 2.0, step=0.5)
        atr_target = st.slider("Target = N × ATR", 1.0, 10.0, 4.0, step=0.5)
        timeout_d = st.slider("Timeout days", 5, 60, 20, step=5)
    with p3:
        capital = st.number_input("Initial capital ₹", value=100_000, step=10_000)
        max_pos = st.slider("Max concurrent positions", 5, 100, 50, step=5)
        st.metric("Reward:Risk ratio", f"{atr_target/atr_stop:.1f}:1", help="Target distance / stop distance")

    if st.button("▶ Run backtest", type="primary"):
        if pd.Timestamp(end).date() >= HOLDOUT_BOUNDARY and not holdout_open:
            st.error("Cannot run — holdout protection.")
        elif (pd.Timestamp(end) - pd.Timestamp(start)).days > 90:
            st.error("Window > 90 days. Reduce range or use CLI.")
        else:
            cfg = BacktestConfig(
                start_date=str(start), end_date=str(end),
                initial_capital=float(capital),
                min_score=float(min_score_b), min_volume_ratio=float(min_vol_b),
                require_above_50dma=above_50_b,
                atr_stop_mult=float(atr_stop), atr_target_mult=float(atr_target),
                timeout_days=int(timeout_d), max_concurrent_positions=int(max_pos),
            )
            with st.spinner(f"Running backtest {start} → {end}..."):
                result = run_backtest(cfg)
                metrics = compute_metrics(result.trades, result.equity_curve, cfg.initial_capital)

            r1, r2, r3, r4 = st.columns(4)
            r1.metric("EV per trade (R)", f"{metrics.ev_per_trade_r:+.3f}",
                      help="Gate: > +0.2R for live capital")
            r2.metric("Trades", f"{metrics.n_trades}")
            r3.metric("Hit rate", f"{metrics.hit_rate:.1%}")
            r4.metric("CAGR", f"{metrics.cagr:.2%}")

            r5, r6, r7, r8 = st.columns(4)
            r5.metric("Avg win", f"{metrics.avg_win_r:+.2f}R")
            r6.metric("Avg loss", f"{metrics.avg_loss_r:+.2f}R")
            r7.metric("Max DD", f"-{metrics.max_drawdown:.1%}")
            r8.metric("Ending equity", f"₹{metrics.ending_equity:,.0f}",
                      delta=f"{metrics.ending_equity - cfg.initial_capital:+,.0f}")

            if not result.equity_curve.empty:
                st.line_chart(result.equity_curve, height=300)

            with st.expander(f"Exit reasons ({metrics.n_trades} trades)"):
                if metrics.exit_reason_counts:
                    er_df = pd.DataFrame(
                        [(k, v, f"{v/metrics.n_trades*100:.1f}%") for k, v in metrics.exit_reason_counts.items()],
                        columns=["Exit reason", "Count", "Pct"],
                    )
                    st.dataframe(er_df, hide_index=True)

            st.caption("Backtest results are descriptive. Past performance is not predictive of future results. The composite breakout score is a researched signal, not a recommendation to act.")
