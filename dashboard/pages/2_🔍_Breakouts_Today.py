"""Page 2 — Breakouts Today. EOD scan over the 1000Cr+ universe."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from analytics.scan_universe import scan_universe  # noqa: E402


OHLCV_DIR = ROOT / "data" / "ohlcv"
NSEI = OHLCV_DIR / "_NSEI.parquet"

st.set_page_config(page_title="Breakouts Today", page_icon="🔍", layout="wide")
st.title("🔍 Breakouts Today")
st.caption("EOD scan over the Nifty 500 universe. Ranked by composite breakout score.")


# Determine the latest scan date from index parquet
@st.cache_data
def latest_trading_day() -> pd.Timestamp:
    df = pd.read_parquet(NSEI)
    return df.index[-1]


latest = latest_trading_day()


with st.sidebar:
    st.markdown("### Filters")
    asof_date = st.date_input(
        "Scan as-of date",
        value=latest.date(),
        max_value=latest.date(),
        help="Date to evaluate breakouts on. Defaults to most recent trading day.",
    )
    min_score = st.slider("Min composite score", 0, 100, 30, step=5,
                          help="Composite of HVN/Swing/Cycle breaks × volume × close-in-range × MA filter. 0-100 scale.")
    min_vol = st.slider("Min volume ratio (× 20d avg)", 1.0, 5.0, 1.5, step=0.1,
                        help="Today's volume / mean of past 20 days. Filter out low-conviction signals.")
    require_50dma = st.checkbox("Above 50-DMA only", value=True,
                                help="Ignore breakouts on stocks in short-term downtrends.")
    require_200dma = st.checkbox("Above 200-DMA only", value=False,
                                 help="Stricter filter — only stocks in long-term uptrends.")
    top_n = st.number_input("Top N to show", 5, 100, 20)


# Run scan (cached on inputs so re-renders are instant)
@st.cache_data
def cached_scan(asof: str, min_score: float, min_vol: float, req_50: bool, req_200: bool, top_n: int):
    return scan_universe(
        asof_date=asof, min_score=min_score, min_volume_ratio=min_vol,
        require_above_50dma=req_50, require_above_200dma=req_200, top_n=top_n,
    )


with st.spinner("Scanning Nifty 500 universe..."):
    result = cached_scan(str(asof_date), min_score, min_vol, require_50dma, require_200dma, top_n)


# Header summary
hcols = st.columns(4)
hcols[0].metric("Universe scanned", f"{result.n_scanned}", help="Tickers with parquet data on this date.")
hcols[1].metric("Qualified", f"{result.n_qualified}", help="Pass min score + min volume + MA filter.")
hcols[2].metric("Filtered out (vol)", f"{result.filtered_vol:,}", help="Below min volume ratio.")
hcols[3].metric("Filtered out (MA)", f"{result.filtered_ma:,}", help="Below the required moving averages.")

st.markdown("---")


# Results table
if result.df.empty:
    st.warning("No breakouts matched the filters. Try lowering the min score or volume ratio.")
else:
    df = result.df.copy()
    # Build readable flag string
    def flag_str(row):
        f = []
        if row["hvn_break"]: f.append("H")
        if row["swing_high_break"]: f.append("S")
        if row["cycle_high_break"]: f.append("C")
        return "+".join(f) or "-"
    df["levels"] = df.apply(flag_str, axis=1)
    df["50d"] = df["above_50dma"].apply(lambda b: "✓" if b else "✗")
    df["200d"] = df["above_200dma"].apply(lambda b: "✓" if b else "✗")

    show = df[["ticker", "company", "sector", "close", "day_change_pct", "levels",
               "level_broken", "volume_ratio", "close_in_range_pct",
               "50d", "200d", "breakout_score"]]

    st.caption("👇 Click any row to open it in **Stock Lookup** with full context.")

    event = st.dataframe(
        show,
        column_config={
            "ticker":              "Ticker",
            "company":             st.column_config.TextColumn("Company", width="medium"),
            "sector":              st.column_config.TextColumn("Sector", width="small"),
            "close":               st.column_config.NumberColumn("LTP", format="₹%.2f"),
            "day_change_pct":      st.column_config.NumberColumn("Chg%", format="%+.2f%%"),
            "levels":              st.column_config.TextColumn("Levels", help="H = HVN, S = 20d swing high, C = 52w cycle high — which resistance type(s) broke today"),
            "level_broken":        st.column_config.NumberColumn("Lvl ₹", format="₹%.0f"),
            "volume_ratio":        st.column_config.NumberColumn("Vol×", format="%.1f", help="Today's volume / 20d average"),
            "close_in_range_pct":  st.column_config.ProgressColumn("CIR%", min_value=0.0, max_value=1.0, format="%.0f%%", help="Close-in-range — (close - low) / (high - low)"),
            "50d":                 "50d",
            "200d":                "200d",
            "breakout_score":      st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d", help="Composite 0-100. Color in chart legend: green ≥70, orange 30-69."),
        },
        hide_index=True, use_container_width=True, height=600,
        on_select="rerun", selection_mode="single-row",
    )

    # If the user clicked a row, jump to Stock Lookup with that ticker pre-selected.
    if event.selection.rows:
        row_idx = event.selection.rows[0]
        clicked_ticker = show.iloc[row_idx]["ticker"]
        st.session_state["lookup_ticker"] = clicked_ticker
        st.switch_page("pages/1_📊_Stock_Lookup.py")

    st.caption(
        "Score is descriptive — composite of resistance breaks (HVN/Swing/Cycle) modulated "
        "by volume ratio, close-in-range%, MA position. **NOT a buy/sell recommendation.**"
    )
