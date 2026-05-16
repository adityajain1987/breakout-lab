"""Page 7 — Decade Breakouts. Phase 7 (2026-05-13).

Pre-breakout watchlist for stocks approaching a >10-year-old high that has been
strictly untouched (not even intraday) for the entire lookback window.

User spec:
  "Last high was 100 for example and it's trading below 100 only for the last 10 years
   like 70-80 range and it didn't touch the high 100, not once intraday or any way.
   We need to pop if they come close to the high 100 maybe 1 or 2% before."

Companion to Page 2 (Breakouts Today):
  Page 2 fires the day the level breaks.
  Page 7 fires BEFORE — while there's still entry headroom.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from analytics.scan_decade_breakouts import scan_decade_breakouts  # noqa: E402


OHLCV_DIR = ROOT / "data" / "ohlcv"
NSEI = OHLCV_DIR / "_NSEI.parquet"


st.set_page_config(page_title="Decade Breakouts", page_icon="🚀", layout="wide")
st.title("🚀 Decade Breakouts")
st.caption(
    "Stocks approaching a high that was set **>10 years ago** and **never touched since** "
    "— not even intraday. Pre-breakout watchlist. **Research context only — not buy/sell signals.**"
)


@st.cache_data
def latest_trading_day() -> pd.Timestamp:
    df = pd.read_parquet(NSEI)
    return df.index[-1]


@st.cache_data
def list_sectors() -> list[str]:
    csv = ROOT / "data" / "universe_1000cr.csv"
    if not csv.exists():
        return []
    df = pd.read_csv(csv)
    return sorted(s for s in df["SECTOR"].dropna().unique())


latest = latest_trading_day()
sectors = list_sectors()


# ---- Sidebar filters ----
with st.sidebar:
    st.markdown("### Filters")
    asof_date = st.date_input(
        "Scan as-of date", value=latest.date(), max_value=latest.date(),
        help="Date to evaluate against. Defaults to most recent trading day.",
    )

    proximity_pct = st.slider(
        "How close to the high (alert window, %)",
        min_value=0.5, max_value=50.0, value=2.0, step=0.5,
        help="Alert when today's close is within this % below the old high. "
             "User default = 2% (catches it 1-2% before the break).",
    )

    lookback_years = st.slider(
        "Untouched window (years)",
        min_value=5, max_value=20, value=10, step=1,
        help="The high must have been UNTOUCHED for this many years. "
             "User default = 10. Try 7 (looser) or 15 (stricter).",
    )

    min_history_years = st.slider(
        "Minimum stock history (years)",
        min_value=lookback_years, max_value=25, value=max(lookback_years + 1, 11), step=1,
        help="Stocks with less history are excluded. Must be ≥ lookback window.",
    )

    sector_choices = st.multiselect(
        "Sector (optional)",
        options=sectors, default=[],
        help="Leave empty for all sectors.",
    )

    top_n = st.number_input("Top N to show", 5, 200, 50)


# ---- Run scan (cached on inputs so re-renders are instant) ----
@st.cache_data
def cached_scan(asof: str, proximity_pct: float, lookback_years: int,
                min_history_years: int, sector_tuple: tuple[str, ...], top_n: int):
    return scan_decade_breakouts(
        asof_date=asof,
        proximity_pct=proximity_pct,
        lookback_years=lookback_years,
        min_history_years=min_history_years,
        sector_filter=list(sector_tuple) if sector_tuple else None,
        top_n=top_n,
    )


with st.spinner(f"Scanning Nifty 500 for {lookback_years}y-untouched highs..."):
    result = cached_scan(
        str(asof_date), proximity_pct, lookback_years,
        min_history_years, tuple(sector_choices), top_n,
    )


# ---- Header summary ----
hcols = st.columns(4)
hcols[0].metric("Universe scanned", f"{result.n_scanned}",
                help="Tickers with parquet data on this date.")
hcols[1].metric("On watchlist", f"{result.n_eligible}",
                help=f"Within {proximity_pct}% of an untouched {lookback_years}y high.")
hcols[2].metric("Touched in window", f"{result.skipped_touched}",
                help=f"Disqualified — broke or tested the old high in the last {lookback_years}y.")
hcols[3].metric("Too far from H", f"{result.skipped_too_far}",
                help=f"Have a clean {lookback_years}y-untouched high but currently >"
                     f"{proximity_pct}% below it. Loosen the slider to see them.")

st.caption(f"Scan time: {result.scan_duration_seconds:.1f}s · "
           f"Short history excluded: {result.skipped_short_history}")
st.markdown("---")


# ---- Results table ----
if result.df.empty:
    st.warning(
        f"No stocks within {proximity_pct}% of a {lookback_years}y-untouched high. "
        f"Try widening the alert window (e.g. 5% or 10%) to see who's approaching."
    )
else:
    df = result.df.copy()
    df["H_old_date_str"] = pd.to_datetime(df["H_old_date"]).dt.strftime("%Y-%m-%d")
    df["status_icon"] = df["status"].map({"Broke today": "🚀 Broke today",
                                          "Approaching": "📍 Approaching"})
    df["gap_str"] = df.apply(
        lambda r: f"{r['gap_pct']:+.1f}%" if r['gap_pct'] >= 0 else f"{r['gap_pct']:+.1f}% (above)",
        axis=1,
    )

    show = df[["ticker", "company", "sector", "close", "day_change_pct",
               "H_old", "H_old_date_str", "H_old_age_years", "gap_pct",
               "status_icon"]]

    # ---- Click-through to Stock Lookup ----
    st.markdown("### 👇 Open a stock in Stock Lookup")
    pick_cols = st.columns([3, 1])
    with pick_cols[0]:
        ticker_options = df["ticker"].tolist()
        picked_ticker = st.selectbox(
            "Pick a ticker from the results below",
            options=ticker_options,
            index=0 if ticker_options else None,
            label_visibility="collapsed",
            help="Or click the left edge of any row below — both work.",
        )
    with pick_cols[1]:
        if st.button("📊 Open chart →", type="primary", use_container_width=True):
            st.session_state["lookup_ticker"] = picked_ticker
            st.switch_page("pages/1_📊_Stock_Lookup.py")

    st.caption("💡 You can also click the **left edge** of any row in the table — same result.")

    event = st.dataframe(
        show,
        column_config={
            "ticker":     "Ticker",
            "company":    st.column_config.TextColumn("Company", width="medium"),
            "sector":     st.column_config.TextColumn("Sector", width="small"),
            "close":      st.column_config.NumberColumn("LTP", format="₹%.2f"),
            "day_change_pct": st.column_config.NumberColumn("Chg%", format="%+.2f%%"),
            "H_old":      st.column_config.NumberColumn(
                "H (old high)", format="₹%.2f",
                help=f"Highest intraday print >{lookback_years}y ago. Untouched since."),
            "H_old_date_str": st.column_config.TextColumn(
                "H set on", help="Date the old high was printed."),
            "H_old_age_years": st.column_config.NumberColumn(
                "Age", format="%.1fy", help="Years since the old high was set."),
            "gap_pct":    st.column_config.NumberColumn(
                "Gap", format="%+.1f%%",
                help="(H_old − close) / H_old × 100. Positive = below the high."),
            "status_icon": st.column_config.TextColumn(
                "Status", width="medium",
                help="🚀 Broke today: close ≥ H_old (first time in 10+ years). "
                     "📍 Approaching: close < H_old, within alert window."),
        },
        hide_index=True, use_container_width=True, height=600,
        on_select="rerun", selection_mode="single-row",
    )

    if event.selection.rows:
        row_idx = event.selection.rows[0]
        clicked_ticker = show.iloc[row_idx]["ticker"]
        st.session_state["lookup_ticker"] = clicked_ticker
        st.switch_page("pages/1_📊_Stock_Lookup.py")


# ---- How to read this ----
with st.expander("📖 How to read this page"):
    st.markdown(f"""
**The setup we're looking for** — a stock that:
1. Made a high years ago (e.g. ₹100 in 2008).
2. Spent the entire **{lookback_years}-year** window since trading below that level, never touching it once — not even intraday.
3. Has now climbed back to within **{proximity_pct}%** of that old high.

**Why this matters.** A decade-old high that never even got tested is a level the market has
collectively "forgotten." When price finally reclaims it, supply that built up at that level
during the long decline is often gone — sellers gave up. That's why these breakouts can move
fast once they go.

**Two statuses you'll see:**
- 📍 **Approaching** — close is below H_old but within the alert window. The watchlist.
- 🚀 **Broke today** — close crossed H_old for the first time in {lookback_years}+ years.

**What the screen does NOT do:**
- It does not validate that decade-breakouts have positive expectancy. No backtest yet.
- It does not consider sector, market regime, volume, or fundamentals.
- It does not predict whether a "Broke today" event will hold or fail.

Use it as a starting point for your own work, not as a buy signal.
    """)


# ---- Persistent disclaimer footer ----
st.markdown("---")
st.markdown(
    """
<div style="background:#11161d; border-left:4px solid #ffaa00; padding:12px 16px; margin-top:16px;">
  <span style="color:#ffaa00; font-weight:600;">⚠ Research context only</span><br>
  <span style="color:#e6edf3; font-size:14px;">
    This shows where price sits relative to old, untouched highs. It does <b>NOT</b> tell
    you what to do. No backtest validation yet — the algorithm finds the setup but does
    not measure whether trading these has historical edge. Use as visual context for your
    own decisions, alongside your existing process.
  </span>
</div>
""",
    unsafe_allow_html=True,
)
