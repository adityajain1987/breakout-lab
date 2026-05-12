"""Page 6 — Range Scanner. Per Phase 6 design (plan-eng-review 2026-05-12).

Companion to Page 2 (Breakouts Today) — same data, opposite lens.
Page 2: which stocks are BREAKING OUT today?
Page 6: which stocks are still IN a range right now?

The point: pure research context. No buy/sell signals. The disclaimer footer
makes the framing explicit.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from analytics.scan_ranges import scan_ranges  # noqa: E402


OHLCV_DIR = ROOT / "data" / "ohlcv"
NSEI = OHLCV_DIR / "_NSEI.parquet"

# Tolerance presets — ATR multipliers per plan-eng-review Section 1 Issue 4
TOLERANCE_PRESETS = {
    "Tight (~2% wiggle)": 0.5,
    "Medium (~4% wiggle)": 1.5,
    "Loose (~7% wiggle)": 3.0,
}

st.set_page_config(page_title="Range Scanner", page_icon="📐", layout="wide")
st.title("📐 Range Scanner")
st.caption(
    "Horizontal trading ranges (rectangle patterns) lasting ≥9 months. "
    "**Research context only — not buy/sell signals.**"
)


@st.cache_data
def latest_trading_day() -> pd.Timestamp:
    df = pd.read_parquet(NSEI)
    return df.index[-1]


@st.cache_data
def list_sectors() -> list[str]:
    """Read sectors from the 1000Cr+ universe CSV."""
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
        help="Date to evaluate the range on. Defaults to most recent trading day.",
    )

    tolerance_label = st.radio(
        "Wiggle room (tolerance)",
        options=list(TOLERANCE_PRESETS.keys()),
        index=1,  # Medium default
        help="How forgiving the algorithm is about wicks poking through the band. "
             "Per-stock auto-adjusted (multiplied by each stock's daily volatility).",
    )
    tolerance_mult = TOLERANCE_PRESETS[tolerance_label]

    min_stars = st.slider(
        "Minimum stars", 1, 4, 1, step=1,
        help="★ structure · ★★ + volume confirms · ★★★ + touches spread ≥9mo · ★★★★ + role reversal. Additive.",
    )

    status_filter = st.radio(
        "Status filter",
        options=["all", "in-range", "breakout"],
        format_func=lambda s: {"all": "All", "in-range": "🟢 Still in range",
                               "breakout": "📈 Recent breakout (last 10d)"}[s],
        help="Recent breakout = close went above resistance band OR below support band in the last 10 trading days.",
    )

    maturity_choices = st.multiselect(
        "Maturity",
        options=["Emerging", "Established", "Major"],
        default=["Emerging", "Established", "Major"],
        help="Emerging 9-12mo · Established 12-24mo · Major 24+mo. Older ranges = more structural weight.",
    )

    sector_choices = st.multiselect(
        "Sector (optional)",
        options=sectors,
        default=[],
        help="Leave empty for all sectors.",
    )

    max_stale_days = st.slider(
        "Drop ranges whose last touch is older than (days)", 30, 365, 90, step=15,
        help="Filter out 'historical' ranges where price has moved away. 90 days = actively-tested ranges.",
    )

    top_n = st.number_input("Top N to show", 5, 200, 50)


# ---- Run scan (cached on inputs so re-renders are instant) ----
@st.cache_data
def cached_scan(asof: str, tolerance_mult: float, min_stars: int, status_filter: str,
                maturity_tuple: tuple[str, ...], sector_tuple: tuple[str, ...],
                max_stale_days: int, top_n: int):
    return scan_ranges(
        asof_date=asof,
        min_stars=min_stars,
        status_filter=status_filter,
        maturity_filter=list(maturity_tuple) if maturity_tuple else None,
        sector_filter=list(sector_tuple) if sector_tuple else None,
        max_stale_days=max_stale_days,
        top_n=top_n,
        atr_tolerance_mult=tolerance_mult,
    )


with st.spinner(f"Scanning Nifty 500 universe for ranges (tolerance: {tolerance_label})..."):
    result = cached_scan(
        str(asof_date), tolerance_mult, min_stars, status_filter,
        tuple(maturity_choices), tuple(sector_choices), max_stale_days, top_n,
    )


# ---- Header summary ----
hcols = st.columns(4)
hcols[0].metric("Universe scanned", f"{result.n_scanned}",
                help="Tickers with parquet data on this date.")
hcols[1].metric("Qualified ranges", f"{result.n_qualified}",
                help="Pass all filters: stars, status, maturity, sector, staleness.")
hcols[2].metric("Filtered out", f"{result.filtered_stars + result.filtered_status + result.filtered_maturity + result.filtered_stale:,}",
                help=f"Stars: {result.filtered_stars}, status: {result.filtered_status}, "
                     f"maturity: {result.filtered_maturity}, stale: {result.filtered_stale}")
hcols[3].metric("Scan time", f"{result.scan_duration_seconds:.1f}s",
                help="Soft warning if > 30s. Stays well under that on Mahindra's M2/M4 machines.")

st.markdown("---")


# ---- Results table ----
if result.df.empty:
    st.warning("No ranges matched the filters. Try lowering min stars or loosening the wiggle room.")
else:
    df = result.df.copy()

    # Build display strings
    df["stars_str"] = df["stars"].apply(lambda n: "★" * int(n))
    df["band"] = df.apply(
        lambda r: f"₹{r['support']:.0f} – ₹{r['resistance']:.0f}", axis=1,
    )
    df["status_str"] = df.apply(
        lambda r: (f"BO {r['breakout_direction']} ({int(r['breakout_days_ago'])}d)"
                   if r["status"] == "Recent Breakout" else r["status"]),
        axis=1,
    )
    # Combined flags icon — keep visual neutral per plan-eng-review (no colored stickers)
    def _flag_icons(row):
        icons = []
        if row.get("round_number"): icons.append("💰")
        if row.get("role_reversal"): icons.append("↻")
        if row.get("volume_confirmed"): icons.append("📊")
        if row.get("quarantine_flag"): icons.append("⚠️")
        return "".join(icons)
    df["flags"] = df.apply(_flag_icons, axis=1)

    show = df[["ticker", "company", "sector", "close", "day_change_pct",
               "band", "width_pct", "stars", "stars_str",
               "duration_days", "maturity", "status_str", "last_touch_days_ago",
               "flags"]]

    # ---- Reliable click-through: dropdown + button (works no matter where you click) ----
    st.markdown("### 👇 Open a stock in Stock Lookup with bands drawn on the chart")
    pick_cols = st.columns([3, 1])
    with pick_cols[0]:
        ticker_options = df["ticker"].tolist()
        picked_ticker = st.selectbox(
            "Pick a ticker from the results below",
            options=ticker_options,
            index=0 if ticker_options else None,
            label_visibility="collapsed",
            help="Or click a row in the table below — both work.",
        )
    with pick_cols[1]:
        if st.button("📊 Open chart →", type="primary", use_container_width=True):
            st.session_state["lookup_ticker"] = picked_ticker
            st.session_state["show_range_bands"] = True
            st.switch_page("pages/1_📊_Stock_Lookup.py")

    st.caption("💡 You can also click the **left edge** of any row in the table (where the checkbox appears) — same result.")

    event = st.dataframe(
        show,
        column_config={
            "ticker":     "Ticker",
            "company":    st.column_config.TextColumn("Company", width="medium"),
            "sector":     st.column_config.TextColumn("Sector", width="small"),
            "close":      st.column_config.NumberColumn("LTP", format="₹%.2f"),
            "day_change_pct": st.column_config.NumberColumn("Chg%", format="%+.2f%%"),
            "band":       st.column_config.TextColumn(
                "Range", help="Support – Resistance (band midpoints)"),
            "width_pct":  st.column_config.NumberColumn(
                "W%", format="%.0f%%", help="Range width as % of current price"),
            "stars":      st.column_config.ProgressColumn(
                "★ Score", min_value=0, max_value=4, format="%d",
                help="★ structure · ★★ + volume · ★★★ + ≥9mo spread · ★★★★ + role reversal"),
            "stars_str":  st.column_config.TextColumn(
                "Rating", help="Visual star count"),
            "duration_days": st.column_config.NumberColumn(
                "Dur (d)", format="%d", help="Combined span from earliest to latest touch"),
            "maturity":   st.column_config.TextColumn(
                "Maturity", help="Emerging 9-12mo · Established 12-24mo · Major 24+mo"),
            "status_str": st.column_config.TextColumn(
                "Status", width="small",
                help="In-Range = close inside the band · BO up/down = close beyond band in last 10d"),
            "last_touch_days_ago": st.column_config.NumberColumn(
                "Last touch (d)", format="%d",
                help="Days since the latest R or S touch. Lower = range still actively tested."),
            "flags":      st.column_config.TextColumn(
                "Flags", help="💰 round number · ↻ role reversal · 📊 volume confirms · ⚠️ data-quality flag"),
        },
        hide_index=True, use_container_width=True, height=600,
        on_select="rerun", selection_mode="single-row",
    )

    # Click-through to Stock Lookup with the ticker pre-selected
    if event.selection.rows:
        row_idx = event.selection.rows[0]
        clicked_ticker = show.iloc[row_idx]["ticker"]
        st.session_state["lookup_ticker"] = clicked_ticker
        st.session_state["show_range_bands"] = True
        st.switch_page("pages/1_📊_Stock_Lookup.py")


# ---- Persistent disclaimer footer (per plan-eng-review Issue 1) ----
st.markdown("---")
st.markdown(
    """
<div style="background:#11161d; border-left:4px solid #ffaa00; padding:12px 16px; margin-top:16px;">
  <span style="color:#ffaa00; font-weight:600;">⚠ Research context only</span><br>
  <span style="color:#e6edf3; font-size:14px;">
    This shows what's happened in the price history. It does <b>NOT</b> tell you what to do.
    No backtest validation yet — the algorithm finds horizontal structure but does not
    measure whether trading these ranges has historical edge. Use as visual context for
    your own decisions, alongside your existing process.
  </span>
</div>
""",
    unsafe_allow_html=True,
)
