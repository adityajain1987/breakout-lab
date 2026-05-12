"""Page 5 — Watchlist. Personal saved tickers + breakout state at a glance."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from analytics.breakout_detector import breakout_state  # noqa: E402
from watchlist.store import add, remove, list_all, is_watched  # noqa: E402


OHLCV_DIR = ROOT / "data" / "ohlcv"
UNIVERSE_CSV = ROOT / "data" / "universe_1000cr.csv"


st.set_page_config(page_title="Watchlist", page_icon="⭐", layout="wide")
st.title("⭐ Watchlist")
st.caption("Personal saved tickers with current breakout state. Add from Stock Lookup sidebar, or use the form below.")

# Public-deploy notice: the watchlist DB is shared across everyone using this URL.
# Multi-user state isn't designed for — adds/removes are visible to all users and
# may be overwritten. For a private watchlist, run the project locally via setup.sh.
st.info(
    "ℹ️ **Shared watchlist (public version):** This list is visible to everyone "
    "using this URL. Your additions and removals affect what everyone sees. "
    "For a private watchlist, run Breakout Lab locally on your own machine "
    "(see [the project README](https://github.com/adityajain1987/breakout-lab))."
)


@st.cache_data
def list_available_tickers() -> list[str]:
    return sorted(p.stem for p in OHLCV_DIR.glob("*.parquet") if not p.stem.startswith("_"))


@st.cache_data
def universe_meta() -> dict:
    if not UNIVERSE_CSV.exists():
        return {}
    df = pd.read_csv(UNIVERSE_CSV)
    return {row["SYMBOL"]: {"company": row["COMPANY"], "sector": row["SECTOR"]} for _, row in df.iterrows()}


tickers = list_available_tickers()
meta = universe_meta()


# ---- Add form (sidebar) ----
with st.sidebar:
    st.markdown("### Add to watchlist")
    new_sym = st.selectbox("Ticker", options=[""] + tickers, index=0,
                           help="Pick from any NSE ticker with cached data.")
    new_notes = st.text_input("Notes (optional)", value="",
                              help="Free-form. e.g. 'wait for ₹500 retest', 'earnings 30-May'")
    if st.button("➕ Add"):
        if new_sym:
            add(new_sym, notes=new_notes)
            st.success(f"Added {new_sym} to watchlist")
            st.rerun()
        else:
            st.warning("Pick a ticker first.")


# ---- Main: show current watchlist with live breakout state ----
wl = list_all()

if wl.empty:
    st.info("Your watchlist is empty. Add tickers via the sidebar form, or open **Stock Lookup** and click '⭐ Add to watchlist' there.")
    st.stop()


# For each watched ticker, compute current breakout state.
# Always returns DataFrame with declared columns even for empty input (defensive — empty
# input occurred when Streamlit's st.stop() doesn't halt module-level execution in tests).
ENRICH_COLUMNS = ["symbol", "close", "day_change_pct", "breakout_score", "levels", "volume_ratio", "ma_status"]


@st.cache_data(ttl=3600)  # refresh once per hour
def enrich_with_state(symbols: list[str]) -> pd.DataFrame:
    rows = []
    for s in symbols:
        parquet = OHLCV_DIR / f"{s}.parquet"
        if not parquet.exists():
            rows.append({"symbol": s, "close": None, "day_change_pct": None,
                         "breakout_score": None, "levels": "?", "volume_ratio": None, "ma_status": "?"})
            continue
        try:
            df = pd.read_parquet(parquet)
            asof = df.index[-1]
            yest_close = float(df["close"].iloc[-2])
            close = float(df["close"].iloc[-1])
            chg = (close - yest_close) / yest_close * 100
            bs = breakout_state(df, asof)
            flags = []
            if bs.hvn_break: flags.append("H")
            if bs.swing_high_break: flags.append("S")
            if bs.cycle_high_break: flags.append("C")
            ma_str = ("✓" if bs.above_50dma else "✗") + "/" + ("✓" if bs.above_200dma else "✗")
            rows.append({
                "symbol": s, "close": close, "day_change_pct": chg,
                "breakout_score": bs.breakout_score,
                "levels": "+".join(flags) or "-",
                "volume_ratio": bs.volume_ratio,
                "ma_status": ma_str,
            })
        except Exception:
            rows.append({"symbol": s, "close": None, "day_change_pct": None,
                         "breakout_score": None, "levels": "err", "volume_ratio": None, "ma_status": "?"})
    return pd.DataFrame(rows, columns=ENRICH_COLUMNS)


with st.spinner(f"Computing breakout state for {len(wl)} watched tickers..."):
    state_df = enrich_with_state(list(wl["symbol"]))


# Merge watchlist metadata + computed state + universe info
merged = wl.merge(state_df, on="symbol", how="left")
merged["company"] = merged["symbol"].map(lambda s: meta.get(s, {}).get("company", s))
merged["sector"] = merged["symbol"].map(lambda s: meta.get(s, {}).get("sector", "—"))
merged["added_at"] = pd.to_datetime(merged["added_at"]).dt.date


# ---- Display ----
display = merged[["symbol", "company", "sector", "close", "day_change_pct",
                  "breakout_score", "levels", "volume_ratio", "ma_status",
                  "added_at", "notes"]].copy()

st.caption(f"👇 Click any row to open it in **Stock Lookup**. Use the buttons below the table to remove tickers.")

event = st.dataframe(
    display,
    column_config={
        "symbol":          "Ticker",
        "company":         st.column_config.TextColumn("Company", width="medium"),
        "sector":          st.column_config.TextColumn("Sector", width="small"),
        "close":           st.column_config.NumberColumn("LTP", format="₹%.2f"),
        "day_change_pct":  st.column_config.NumberColumn("Chg%", format="%+.2f%%"),
        "breakout_score":  st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
        "levels":          st.column_config.TextColumn("Levels", help="H=HVN S=20d swing C=52w cycle"),
        "volume_ratio":    st.column_config.NumberColumn("Vol×", format="%.1f"),
        "ma_status":       st.column_config.TextColumn("50/200d", help="✓ = above DMA, ✗ = below"),
        "added_at":        st.column_config.DateColumn("Added", format="YYYY-MM-DD"),
        "notes":           st.column_config.TextColumn("Notes", width="medium"),
    },
    hide_index=True, use_container_width=True,
    on_select="rerun", selection_mode="single-row",
)

# Click-through to Stock Lookup
if event.selection.rows:
    row_idx = event.selection.rows[0]
    clicked = display.iloc[row_idx]["symbol"]
    st.session_state["lookup_ticker"] = clicked
    st.switch_page("pages/1_📊_Stock_Lookup.py")


# ---- Remove buttons ----
st.markdown("---")
st.markdown("### Remove from watchlist")
if len(wl) > 0:
    cols = st.columns(min(5, len(wl)))
    for i, sym in enumerate(wl["symbol"]):
        if cols[i % 5].button(f"❌ {sym}", key=f"rm_{sym}"):
            remove(sym)
            st.success(f"Removed {sym}")
            st.rerun()
else:
    st.caption("(no tickers to remove — watchlist is empty)")

st.caption("Watchlist data persists in `watchlist/watchlist.db` (SQLite). Delete that file to wipe.")
