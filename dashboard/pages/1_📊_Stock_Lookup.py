"""Page 1 — Stock Lookup. Per DESIGN.md, the heart of the app."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from analytics.volume_profile import volume_profile  # noqa: E402
from analytics.breakout_detector import breakout_state  # noqa: E402
from analytics.range_detector import range_state  # noqa: E402
from deals.store import query_deals, disclosed_volume_pct  # noqa: E402
from quarantine.store import query_flags  # noqa: E402
from watchlist.store import add as wl_add, remove as wl_remove, is_watched  # noqa: E402


OHLCV_DIR = ROOT / "data" / "ohlcv"
OHLCV_BHAV_DIR = ROOT / "data" / "ohlcv_bhav"
UNIVERSE_CSV = ROOT / "data" / "universe_1000cr.csv"
DEALS_DB = ROOT / "deals" / "deals.db"
Q_DB = ROOT / "quarantine" / "quarantine.db"

LOOKBACK_OPTIONS = {
    "1W":  7, "1M":  30, "3M":  90,
    "6M":  180, "1Y":  365, "2Y":  730, "5Y": 1825,
}

st.set_page_config(page_title="Stock Lookup", page_icon="📊", layout="wide")
st.title("📊 Stock Lookup")


# ---- Sidebar controls ----
@st.cache_data
def list_available_tickers() -> list[str]:
    return sorted(p.stem for p in OHLCV_DIR.glob("*.parquet") if not p.stem.startswith("_"))


@st.cache_data
def load_universe_meta() -> dict:
    if not UNIVERSE_CSV.exists():
        return {}
    df = pd.read_csv(UNIVERSE_CSV)
    return {row["SYMBOL"]: {"company": row["COMPANY"], "sector": row["SECTOR"]}
            for _, row in df.iterrows()}


tickers = list_available_tickers()
meta = load_universe_meta()

with st.sidebar:
    st.markdown("### Lookup")
    # Default to the ticker passed via session_state (e.g. from Breakouts Today click-through),
    # else MAZDOCK, else first ticker.
    if "lookup_ticker" in st.session_state and st.session_state["lookup_ticker"] in tickers:
        default_idx = tickers.index(st.session_state["lookup_ticker"])
    elif "MAZDOCK" in tickers:
        default_idx = tickers.index("MAZDOCK")
    else:
        default_idx = 0
    ticker = st.selectbox(
        "Ticker",
        options=tickers,
        index=default_idx,
        help="Any NSE-listed symbol with cached OHLCV data. Stock Lookup accepts ANY ticker, not just 1000Cr+.",
    )
    # Clear the session_state hint after consuming it so manual changes stick
    if "lookup_ticker" in st.session_state and ticker != st.session_state["lookup_ticker"]:
        del st.session_state["lookup_ticker"]

    lookback_label = st.radio(
        "Lookback window",
        options=list(LOOKBACK_OPTIONS.keys()),
        index=3,  # 6M default per DESIGN.md
        horizontal=True,
        help="Volume profile lookback. Shorter = more recent S/R levels; longer = more historical context.",
    )

    bin_pct = st.select_slider(
        "Volume profile bin width",
        options=[0.0025, 0.005, 0.01],
        value=0.005,
        format_func=lambda x: f"{x*100:.2f}% of mid-price",
        help="Bin width as fraction of mid-price. 0.5% default. Capped at 1 NSE tick (₹0.05) min, 100 bins max.",
    )

    # Range band overlay (Phase 6). Defaults ON when arrived via Range Scanner click-through.
    show_range_bands = st.checkbox(
        "Show range bands (if detected)",
        value=st.session_state.get("show_range_bands", False),
        help="Overlay shaded resistance + support zones on the chart when a horizontal trading range is detected.",
    )
    st.session_state["show_range_bands"] = show_range_bands

    st.markdown("---")
    st.markdown("### Watchlist")
    if is_watched(ticker):
        if st.button(f"❌ Remove {ticker} from watchlist", use_container_width=True):
            wl_remove(ticker)
            st.success(f"Removed {ticker}")
            st.rerun()
    else:
        wl_notes = st.text_input("Notes (optional)", value="", key=f"wl_notes_{ticker}")
        if st.button(f"⭐ Add {ticker} to watchlist", use_container_width=True):
            wl_add(ticker, notes=wl_notes)
            st.success(f"Added {ticker}")
            st.rerun()


# ---- Load data ----
@st.cache_data
def load_ohlcv(ticker: str) -> pd.DataFrame:
    return pd.read_parquet(OHLCV_DIR / f"{ticker}.parquet")


df = load_ohlcv(ticker)
asof = df.index[-1]

today_close = float(df.loc[asof, "close"])
yesterday_close = float(df["close"].iloc[-2])
day_change_pct = (today_close - yesterday_close) / yesterday_close * 100

# Phase 6 — range detection (only if the user asked for the overlay)
rs = None
if show_range_bands:
    try:
        rs = range_state(df, asof, ticker=ticker, quarantine_db=Q_DB if Q_DB.exists() else None)
    except Exception:
        rs = None

# Auto-expand chart window when range bands are shown and a range is detected,
# so the user sees ALL the touches (not just the ones in the last 6 months).
window_auto_expanded = False
if rs is not None and rs.qualified:
    all_touch_dates = list(rs.resistance_touch_dates) + list(rs.support_touch_dates)
    if all_touch_dates:
        earliest_touch = min(all_touch_dates)
        # Pad: 30 days before the earliest touch
        range_start = earliest_touch - pd.Timedelta(days=30)
        selected_lookback_start = asof - pd.Timedelta(days=LOOKBACK_OPTIONS[lookback_label])
        # Only auto-expand if the range window is WIDER than the user's selection
        if range_start < selected_lookback_start:
            window_start = range_start
            window_auto_expanded = True
        else:
            window_start = selected_lookback_start
    else:
        window_start = asof - pd.Timedelta(days=LOOKBACK_OPTIONS[lookback_label])
else:
    window_start = asof - pd.Timedelta(days=LOOKBACK_OPTIONS[lookback_label])

df_window = df.loc[window_start:asof]

if len(df_window) < 5:
    st.error(f"Not enough data in window for {ticker}. Try a longer lookback.")
    st.stop()

# Compute analytics (volume profile uses the same window the candlestick chart shows)
vp = volume_profile(df_window, bin_width_pct=bin_pct)
bs = breakout_state(df, asof)

# Notify the user if we auto-expanded the chart so they don't think the selector broke
if window_auto_expanded:
    expanded_days = (asof - window_start).days
    st.info(
        f"📐 Chart auto-expanded to **{expanded_days} days** so you can see all "
        f"{rs.resistance_touches + rs.support_touches} touches in the range. "
        f"Sidebar lookback ({lookback_label}) overridden because the range is longer. "
        f"Toggle off 'Show range bands' to revert."
    )

# Bhavcopy enrichment — delivery % (institutional signal not in yfinance)
bhav_path = OHLCV_BHAV_DIR / f"{ticker}.parquet"
bhav_today = None
bhav_window_avg_deliv = None
if bhav_path.exists():
    try:
        bhav_df = pd.read_parquet(bhav_path)
        if asof in bhav_df.index and "deliv_per" in bhav_df.columns:
            bhav_today = bhav_df.loc[asof]
        # Average delivery % over the window (institutional behavior trend)
        bhav_in_window = bhav_df.loc[window_start:asof]
        if "deliv_per" in bhav_in_window.columns and len(bhav_in_window) > 0:
            bhav_window_avg_deliv = float(bhav_in_window["deliv_per"].mean())
    except Exception:
        pass


# ---- Header strip ----
m = meta.get(ticker, {})
hcols = st.columns([2, 3, 2, 2, 2, 2, 2])
hcols[0].markdown(f"### {ticker}")
hcols[1].markdown(f"**{m.get('company', ticker)}**")
hcols[2].metric("LTP", f"₹{today_close:,.2f}", delta=f"{day_change_pct:+.2f}%")
hcols[3].markdown(f"**Sector**  \n{m.get('sector', '—')}")
hcols[4].markdown(f"**As of**  \n{asof.date()}")
hcols[5].markdown(f"**Window**  \n{lookback_label} ({len(df_window)}d)")
# Bhavcopy delivery metrics (institutional signal)
if bhav_today is not None:
    today_deliv_pct = float(bhav_today["deliv_per"]) if pd.notna(bhav_today.get("deliv_per")) else None
    avg_str = f" · {lookback_label} avg {bhav_window_avg_deliv:.1f}%" if bhav_window_avg_deliv else ""
    if today_deliv_pct is not None:
        delta_vs_avg = today_deliv_pct - (bhav_window_avg_deliv or today_deliv_pct)
        hcols[6].metric(
            "Delivery %", f"{today_deliv_pct:.1f}%",
            delta=f"{delta_vs_avg:+.1f} pts vs avg" if bhav_window_avg_deliv else None,
            help=f"Today's delivery % from NSE Bhavcopy. High delivery = institutional accumulation. "
                 f"Window average: {bhav_window_avg_deliv:.1f}%" if bhav_window_avg_deliv else
                 "Today's delivery % from NSE Bhavcopy. Bhavcopy bonus signal not available from yfinance."
        )

st.markdown("---")


# ---- Main grid: price chart + volume profile ----
fig = make_subplots(
    rows=1, cols=2,
    column_widths=[0.7, 0.3],
    shared_yaxes=True,
    horizontal_spacing=0.01,
)

# Candlestick
fig.add_trace(
    go.Candlestick(
        x=df_window.index,
        open=df_window["open"], high=df_window["high"],
        low=df_window["low"], close=df_window["close"],
        name="Price",
        increasing=dict(line=dict(color="#00d68f"), fillcolor="#00d68f"),
        decreasing=dict(line=dict(color="#ff3d71"), fillcolor="#ff3d71"),
    ),
    row=1, col=1,
)
# POC / VAH / VAL lines — staggered annotation positions so labels don't overlap
# when prices are close (common bug: POC at ₹524, VAL at ₹516 → labels collide)
fig.add_hline(y=vp.poc, line=dict(color="#ffaa00", width=1.2),
              annotation_text=f"POC ₹{vp.poc:.2f}",
              annotation_position="top left",
              annotation_font=dict(size=10, color="#ffaa00"),
              row=1, col=1)
fig.add_hline(y=vp.vah, line=dict(color="#00d68f", width=0.8, dash="dash"),
              annotation_text=f"VAH ₹{vp.vah:.2f}",
              annotation_position="left",
              annotation_font=dict(size=9, color="#00d68f"),
              row=1, col=1)
fig.add_hline(y=vp.val, line=dict(color="#00d68f", width=0.8, dash="dash"),
              annotation_text=f"VAL ₹{vp.val:.2f}",
              annotation_position="bottom left",
              annotation_font=dict(size=9, color="#00d68f"),
              row=1, col=1)

# Phase 6 range lines — clean dashed blue lines (matching the user's Mahindra
# reference chart). No shaded fill: keeps the chart readable, avoids color collision
# with candle red/green, and doesn't imply a buy/sell signal (which a green/red
# zone subconsciously does). Tolerance is shown in the caption below the chart,
# not on the chart itself.
if rs is not None and rs.qualified:
    # Resistance — dashed blue line at the cluster mean, label on the right
    fig.add_hline(
        y=rs.resistance_mean,
        line=dict(color="#5b8def", width=1.8, dash="dash"),
        annotation_text=f"Ceiling ₹{rs.resistance_mean:,.0f} · {rs.resistance_touches} touches",
        annotation_position="top right",
        annotation_font=dict(color="#5b8def", size=11),
        row=1, col=1,
    )
    # Support — dashed blue line at the cluster mean, label on the right
    fig.add_hline(
        y=rs.support_mean,
        line=dict(color="#5b8def", width=1.8, dash="dash"),
        annotation_text=f"Floor ₹{rs.support_mean:,.0f} · {rs.support_touches} touches",
        annotation_position="bottom right",
        annotation_font=dict(color="#5b8def", size=11),
        row=1, col=1,
    )

    # Touch markers — small triangles at each touch date/price.
    # Down-pointing triangle at each ceiling touch (price went up to here, then down).
    # Up-pointing triangle at each floor touch (price went down to here, then up).
    if rs.resistance_touch_dates:
        fig.add_trace(
            go.Scatter(
                x=rs.resistance_touch_dates,
                y=rs.resistance_touch_prices,
                mode="markers",
                marker=dict(symbol="triangle-down", size=11, color="#5b8def",
                            line=dict(color="#0a0e14", width=1.5)),
                name=f"Ceiling touches ({rs.resistance_touches})",
                hovertemplate="<b>Ceiling touch</b><br>%{x|%b %d, %Y}<br>High: ₹%{y:,.2f}<extra></extra>",
                showlegend=False,
            ),
            row=1, col=1,
        )
    if rs.support_touch_dates:
        fig.add_trace(
            go.Scatter(
                x=rs.support_touch_dates,
                y=rs.support_touch_prices,
                mode="markers",
                marker=dict(symbol="triangle-up", size=11, color="#5b8def",
                            line=dict(color="#0a0e14", width=1.5)),
                name=f"Floor touches ({rs.support_touches})",
                hovertemplate="<b>Floor touch</b><br>%{x|%b %d, %Y}<br>Low: ₹%{y:,.2f}<extra></extra>",
                showlegend=False,
            ),
            row=1, col=1,
        )

# Volume profile horizontal bars
in_va = (vp.bins["price_bin_mid"] >= vp.val) & (vp.bins["price_bin_mid"] <= vp.vah)
poc_idx = vp.bins["volume"].idxmax() if not vp.bins.empty else -1
bar_colors = ["#00d68f" if v else "#3d444d" for v in in_va]
if poc_idx >= 0:
    bar_colors[poc_idx] = "#ffaa00"
fig.add_trace(
    go.Bar(
        x=vp.bins["volume"], y=vp.bins["price_bin_mid"],
        orientation="h", marker=dict(color=bar_colors, line=dict(width=0)),
        name="Volume profile", showlegend=False,
        hovertemplate="₹%{y:.2f}<br>vol %{x:,.0f}<extra></extra>",
    ),
    row=1, col=2,
)

fig.update_xaxes(title_text="Date", row=1, col=1, rangeslider=dict(visible=False),
                 gridcolor="#1f2630")
fig.update_xaxes(title_text="Volume", row=1, col=2, gridcolor="#1f2630")
fig.update_yaxes(title_text="Price (₹, split+div adjusted)", row=1, col=1,
                 gridcolor="#1f2630")
fig.update_layout(
    height=550,
    paper_bgcolor="#0a0e14", plot_bgcolor="#0a0e14",
    font=dict(color="#e6edf3"),
    margin=dict(l=10, r=10, t=10, b=10),
    showlegend=False,
)
st.plotly_chart(fig, use_container_width=True)

# Phase 6 — range summary in plain English + a "what am I looking at" expander
if rs is not None and rs.qualified:
    # Maturity in plain English
    maturity_text = {
        "Emerging": "newer (9–12 months old)",
        "Established": "well-established (1–2 years old)",
        "Major": "very mature (2+ years old)",
    }.get(rs.maturity_tag, rs.maturity_tag)

    # Status in plain English
    if rs.status == "Recent Breakout":
        direction_word = "above the ceiling" if rs.breakout_direction == "up" else "below the floor"
        status_text = f"**broke {direction_word} {rs.breakout_days_ago} trading days ago**"
    elif rs.status == "In-Range":
        status_text = "**still inside the range** today"
    else:
        status_text = f"status: {rs.status}"

    # Confidence stars described
    star_explanations = [
        "structure (peaks line up)",
        "volume confirms the levels",
        "touches spread across 9+ months",
        "level has acted as BOTH ceiling and floor at different times (role reversal)",
    ]
    stars_signals = star_explanations[:rs.stars]

    tolerance_pct = ((rs.resistance_upper - rs.resistance_mean) / rs.resistance_mean * 100
                     if rs.resistance_upper else 0)

    # Compute touch date spans for clarity
    r_dates = sorted(rs.resistance_touch_dates) if rs.resistance_touch_dates else []
    s_dates = sorted(rs.support_touch_dates) if rs.support_touch_dates else []
    r_span = (f"between **{r_dates[0]:%b %Y}** and **{r_dates[-1]:%b %Y}**"
              if r_dates else "")
    s_span = (f"between **{s_dates[0]:%b %Y}** and **{s_dates[-1]:%b %Y}**"
              if s_dates else "")

    st.markdown(
        f"#### 📐 Range detected: ₹{rs.support_mean:,.0f} (floor) – ₹{rs.resistance_mean:,.0f} (ceiling)"
    )
    st.markdown(
        f"This pattern has been in place for **{rs.range_duration_days} days** "
        f"({maturity_text}). The stock {status_text}. "
        f"Last touch was **{rs.last_touch_days_ago} days ago**."
    )
    st.markdown(
        f"- 🔵 **Ceiling (₹{rs.resistance_mean:,.0f}):** {rs.resistance_touches} touches, "
        f"{r_span} — shown as ▽ markers on the chart.\n"
        f"- 🔵 **Floor (₹{rs.support_mean:,.0f}):** {rs.support_touches} touches, "
        f"{s_span} — shown as △ markers on the chart."
    )
    st.caption(
        f"**What's a touch?** Price went UP to the ceiling (or DOWN to the floor) "
        f"and then turned around — a 'kiss and reject.' A touch is NOT a breakout "
        f"(when price goes through and keeps going). **Wiggle room:** wicks up to "
        f"**±{tolerance_pct:.1f}%** of the level still count — i.e., a wick to "
        f"₹{rs.resistance_upper:,.0f} or ₹{rs.resistance_lower:,.0f} still counts "
        f"as a ceiling touch."
    )

    # Confidence breakdown
    confidence_cols = st.columns([3, 1])
    with confidence_cols[0]:
        st.markdown(
            f"**Confidence: {'★' * rs.stars}{'☆' * (4 - rs.stars)}** ({rs.stars} out of 4 signals agree)"
        )
        for s in stars_signals:
            st.markdown(f"  ✓ {s}")
    with confidence_cols[1]:
        extras = []
        if rs.round_number_flag:
            extras.append("💰 Lands on a round-number level (psychologically watched)")
        if rs.quarantine_flag:
            extras.append("⚠️ Heads up: this stock has data-quality flags (corporate actions, IPO date, etc.) — verify before trusting")
        if extras:
            st.markdown("**Extra notes:**")
            for e in extras:
                st.markdown(f"  {e}")

    # "What am I looking at?" expander — explains every visual element + the jargon
    with st.expander("📖 How to read this chart (click to expand)"):
        st.markdown("""
**The blue dashed lines (NEW — from the Range Scanner):**
- 🔵 **Top blue dashed line = Ceiling (Resistance).** Price has bumped against this level multiple times and turned back down.
- 🔵 **Bottom blue dashed line = Floor (Support).** Price has fallen to this level multiple times and bounced back up.
- The "touches" count tells you how many times each level has been tested.
- The "wiggle room" caption above tells you how forgiving the algorithm is — a wick that pokes through by a small amount still counts.

**The volume-profile lines (already on the chart):**
- 🟡 **Orange solid line = POC (Point of Control).** The single price where the MOST trading volume happened in your lookback window. Often acts as a magnet for price.
- 🟢 **Green dashed lines = VAH / VAL (Value Area High / Low).** The price band where ~70% of all volume happened. A "value zone" — price spent most of its time here.

**The right side of the chart:**
- That horizontal histogram shows **how much trading volume happened at each price**. Tall bars = popular price, short bars = price went through quickly. The orange bar = POC.

**Why this matters:** when the blue dashed line (Range Scanner level) sits near a volume peak (orange POC or green VAH/VAL), that's two independent signals agreeing — *"this is a real level."* Much stronger than either alone.
""")
elif show_range_bands and (rs is None or not rs.qualified):
    reason = rs.reason if rs is not None else "range_state errored"
    st.info(f"📐 **No qualifying range found** for {ticker} on {asof.date()}.\n\nReason: *{reason}*")


# ---- Honest deals label (always visible, flag color) ----
deals_df = query_deals(DEALS_DB, symbol=ticker,
                       start=str(window_start.date()), end=str(asof.date()))
label_data = disclosed_volume_pct(deals_df, df_window)

st.markdown(
    f"""
<div style="background:#11161d; border:2px solid #ffaa00; border-radius:4px;
            padding:12px 16px; margin:8px 0;">
  <span style="color:#ffaa00; font-size:18px; margin-right:8px;">⚠</span>
  <span style="color:#e6edf3;">{label_data['label']}</span>
</div>
""",
    unsafe_allow_html=True,
)


# ---- Two-column: Deals table | Breakout state card ----
deal_col, bs_col = st.columns([7, 3])

with deal_col:
    st.markdown("#### Disclosed deals (window)")
    if deals_df.empty:
        st.info("No NSE-disclosed bulk or block deals in this window. Most large-cap days have zero disclosed deals — only trades > 0.5% of company shares qualify as bulk, and ≥₹10Cr / 5L shares as block.")
    else:
        deals_df = deals_df.copy()
        deals_df["value_cr"] = deals_df["quantity"] * deals_df["price"] / 1e7
        st.dataframe(
            deals_df[["date", "deal_type", "client", "side", "quantity", "price", "value_cr"]],
            column_config={
                "date":      st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
                "deal_type": st.column_config.TextColumn("Type", help="bulk = >0.5% of company shares; block = ≥₹10Cr or 5L shares"),
                "client":    st.column_config.TextColumn("Client", width="large"),
                "side":      st.column_config.TextColumn("Side", help="Buyer (BUY) or seller (SELL) of the disclosed counterparty"),
                "quantity":  st.column_config.NumberColumn("Qty", format="%d"),
                "price":     st.column_config.NumberColumn("Price", format="₹%.2f"),
                "value_cr":  st.column_config.NumberColumn("₹ Cr", format="%.1f", help="Quantity × price / 10M"),
            },
            hide_index=True, use_container_width=True,
        )

with bs_col:
    st.markdown("#### Breakout state today")
    score_color = ("#00d68f" if bs.breakout_score >= 60
                   else "#ffaa00" if bs.breakout_score >= 30
                   else "#8b949e")
    st.markdown(
        f"<h1 style='color:{score_color}; margin:0; font-family:monospace;'>{bs.breakout_score:.0f}</h1>",
        unsafe_allow_html=True,
    )
    flags = []
    if bs.hvn_break: flags.append("HVN")
    if bs.swing_high_break: flags.append("20d SWING")
    if bs.cycle_high_break: flags.append("52w CYCLE")
    flag_text = " + ".join(flags) if flags else "no fresh breaks today"
    st.markdown(f"**Breaks:** {flag_text}")
    st.markdown(f"**Volume:** {bs.volume_ratio:.2f}× the 20-day average", help="Today's volume / mean of past 20 days. >2× = elevated participation.")
    st.markdown(f"**Close in range:** {bs.close_in_range_pct:.0%} of day's H-L", help="(close - low) / (high - low). Top 25% = strong close.")
    ma_50 = "🟢 above" if bs.above_50dma else "🔴 below"
    ma_200 = "🟢 above" if bs.above_200dma else "🔴 below"
    st.markdown(f"**50-DMA:** {ma_50}  \n**200-DMA:** {ma_200}")
    if bs.level_broken is not None:
        st.markdown(f"**Level broken:** ₹{bs.level_broken:,.2f}")
    st.caption("Score is descriptive, not prescriptive — a backtest stat, not a recommendation.")


# ---- Quarantine flags collapsible ----
with st.expander(f"Quarantine flags for {ticker} (data quality audit)"):
    flags = query_flags(Q_DB, symbol=ticker)
    if flags.empty:
        st.write("✓ No flags. Data is clean for this ticker (no splits anomalies, recent IPO status, suspended periods, or DUMMY pattern).")
    else:
        st.dataframe(
            flags[["date", "check_name", "tier", "details"]],
            column_config={
                "date":       st.column_config.DateColumn("Date"),
                "check_name": "Check",
                "tier":       st.column_config.NumberColumn("Tier", help="1=must-pass, 2=flag, 3=universe filter, 4=tag"),
                "details":    st.column_config.TextColumn("Details", width="large"),
            },
            hide_index=True, use_container_width=True,
        )
