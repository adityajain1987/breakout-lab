"""
Breakout Lab — Streamlit dashboard entry point.

Run: cd ~/Desktop/Claude/breakout-lab && .venv/bin/streamlit run dashboard/app.py

Pages live in dashboard/pages/ — Streamlit auto-discovers them.
Filename prefix controls sidebar order; emoji becomes the page icon.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent.parent

st.set_page_config(
    page_title="Breakout Lab",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 Breakout Lab")
st.caption("Honest research dashboard. Volume profile + breakout state + named-counterparty deals for any 1000Cr+ NSE stock. Decide-yourself tool — no buy/sell signals.")

# Sitewide disclaimer banner — visible on every page-load via this landing page
# and reinforced on the Range Scanner page footer. Required for public hosting.
st.markdown(
    """
<div style="background:#11161d; border:2px solid #ffaa00; border-radius:6px;
            padding:14px 18px; margin:12px 0;">
  <span style="color:#ffaa00; font-weight:700; font-size:16px;">⚠️ Research & educational tool only</span>
  <div style="color:#e6edf3; font-size:14px; margin-top:6px; line-height:1.5;">
    This tool is <b>not registered with SEBI</b> as a Research Analyst or Investment Advisor.
    Nothing on this dashboard is a buy/sell recommendation. All data is public
    (NSE, Yahoo Finance) — you are the only person responsible for any trade you take.
    <b>Use at your own risk.</b>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown("---")

col1, col2 = st.columns([2, 1])

with col1:
    st.markdown("""
    ### What this is

    A research tool for **Amit** (retail trader, runs his own book). Ranked by
    **honest context**, not predictions or signals.

    For any NSE stock with cached data, you get:
    - Volume-by-price histogram (where the inventory sits)
    - Breakout state today (HVN / 20d swing / 52w cycle, composite score)
    - NSE-disclosed bulk + block deals with named counterparty
    - Always-visible "remaining X% of volume is anonymous" label

    No fake FII numbers. No buy/sell calls. Backtest stats are descriptive,
    not prescriptive.
    """)

    st.markdown("### How to use")
    st.markdown("""
    - **Stock Lookup** — drill into one ticker for full context
    - **Breakouts Today** — EOD scan ranked by composite breakout score
    - **Backtest Playground** — see how candidate strategies have performed
    - **Glossary** — plain-English definitions of every term
    """)

with col2:
    st.markdown("### Status")

    # Universe stats
    universe_csv = ROOT / "data" / "universe_1000cr.csv"
    if universe_csv.exists():
        import pandas as pd
        n_universe = len(pd.read_csv(universe_csv))
        st.metric("Universe size", f"{n_universe} tickers", help="Nifty 500 EQ-series — strict superset of 1000Cr+ market cap stocks.")
    else:
        st.warning("Universe CSV not built. Run `python -m data.build_universe`")

    # OHLCV stats
    ohlcv_dir = ROOT / "data" / "ohlcv"
    if ohlcv_dir.exists():
        n_parquets = len(list(ohlcv_dir.glob("*.parquet")))
        st.metric("OHLCV parquets cached", f"{n_parquets}", help="Per-ticker daily bars, 5+ years history each, split + dividend adjusted.")

    # Deals stats
    deals_db = ROOT / "deals" / "deals.db"
    if deals_db.exists():
        with sqlite3.connect(deals_db) as conn:
            n_deals, n_dates = conn.execute("SELECT COUNT(*), COUNT(DISTINCT date) FROM deals").fetchone()
        st.metric("Disclosed deals stored", f"{n_deals}", help=f"Across {n_dates} trading days. NSE bulk + block deals only — most stocks on most days have zero disclosures.")

    # Quarantine stats
    q_db = ROOT / "quarantine" / "quarantine.db"
    if q_db.exists():
        with sqlite3.connect(q_db) as conn:
            n_flags = conn.execute("SELECT COUNT(*) FROM flags").fetchone()[0]
        st.metric("Quarantine flags", f"{n_flags:,}", help="Edge-case detection across the universe — splits, IPOs, circuit hits, F&O expiries, suspended periods.")

st.markdown("---")
st.markdown("""
**Honest data note:** All prices shown are split + dividend adjusted (yfinance `auto_adjust=True`).
This means historical prices have been back-adjusted; today's price matches NSE quote, but
older prices may look lower than what you'd see on a non-adjusted chart. Sale prices,
volume profiles, and percentage moves are correct on the adjusted scale.

**This tool is not registered with SEBI as a Research Analyst service.** It exists as a
research and educational resource only. Backtest stats are descriptive (what happened in
historical data), never prescriptive. No buy/sell calls are made. Every viewer is
responsible for their own trading decisions.
""")
