"""
Stock Lookup mockup — composes the full DESIGN.md Page 1 layout into a static PNG.

This is a VISUAL DESIGN VALIDATION tool, not the production UI (that's Phase 4 Streamlit).
Renders the exact layout we'd ship — header, price+volume chart, volume profile, deals
table with the always-visible "rest is anonymous" label, breakout state card — so we can
eyeball the composition for any ticker × asof_date before committing to interactive UI.

Run examples:
  .venv/bin/python -m analytics.stock_lookup_preview --ticker MAZDOCK --asof 2025-04-29
  .venv/bin/python -m analytics.stock_lookup_preview --ticker SYNGENE --asof 2026-04-30

Output: reports/stock_lookup_{TICKER}_{asof}.png
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import pandas as pd

from analytics.volume_profile import volume_profile
from analytics.breakout_detector import breakout_state
from deals.store import query_deals, disclosed_volume_pct


ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = ROOT / "data" / "ohlcv"
DEALS_DB = ROOT / "deals" / "deals.db"
REPORTS_DIR = ROOT / "reports"

# Static metadata for our mockup tickers (would come from universe builder in production)
TICKER_META = {
    "RELIANCE":  {"company": "Reliance Industries Ltd",  "sector": "Energy",        "fno": True,  "mcap_cr": 1_900_000},
    "NESTLEIND": {"company": "Nestle India Ltd",         "sector": "FMCG",          "fno": True,  "mcap_cr": 240_000},
    "KOTAKBANK": {"company": "Kotak Mahindra Bank Ltd",  "sector": "Banking",       "fno": True,  "mcap_cr": 380_000},
    "MAZDOCK":   {"company": "Mazagon Dock Shipbuilders","sector": "Defence",       "fno": True,  "mcap_cr": 110_000},
    "GROWW":     {"company": "Groww (Billionbrains)",    "sector": "Capital Mkts",  "fno": False, "mcap_cr": 130_000},
    "SYNGENE":   {"company": "Syngene International Ltd","sector": "Pharma CRO",    "fno": True,  "mcap_cr": 19_500},
}

# Color tokens from DESIGN.md
COLORS = {
    "bg":             "#0a0e14",
    "surface":        "#11161d",
    "border":         "#1f2630",
    "text_primary":   "#e6edf3",
    "text_secondary": "#8b949e",
    "accent":         "#00d68f",
    "warn":           "#ff3d71",
    "flag":           "#ffaa00",
    "muted":          "#3d444d",
}


def render_stock_lookup(
    ticker: str,
    asof_date: str,
    lookback_days: int = 126,
    output_path: Path | None = None,
) -> Path:
    """Render the full Stock Lookup mockup PNG for one ticker × asof_date."""
    asof = pd.Timestamp(asof_date)

    # ---------- Load + slice data ----------
    parquet = OHLCV_DIR / f"{ticker}.parquet"
    if not parquet.exists():
        raise FileNotFoundError(f"No OHLCV parquet for {ticker}")
    df_full = pd.read_parquet(parquet)
    if asof not in df_full.index:
        raise ValueError(f"asof {asof.date()} not in {ticker} index")

    window_start = asof - pd.Timedelta(days=lookback_days * 1.5)  # approx for calendar→trading
    df_window = df_full.loc[window_start:asof]
    today_row = df_full.loc[asof]
    yesterday_close = float(df_full.loc[:asof].iloc[-2]["close"])
    today_close = float(today_row["close"])
    day_change_pct = (today_close - yesterday_close) / yesterday_close * 100

    # ---------- Compute analytics ----------
    vp = volume_profile(df_window, bin_width_pct=0.005)
    bs = breakout_state(df_full, asof)

    # Deals in the window
    deals_df = query_deals(
        DEALS_DB,
        symbol=ticker,
        start=str(window_start.date()),
        end=str(asof.date()),
    )
    label_data = disclosed_volume_pct(deals_df, df_window)

    # ---------- Compose figure ----------
    fig = plt.figure(figsize=(18, 12), facecolor=COLORS["bg"])
    gs = gridspec.GridSpec(
        nrows=4, ncols=16,
        height_ratios=[0.6, 5.5, 0.7, 4.2],
        hspace=0.35, wspace=0.6,
        left=0.04, right=0.97, top=0.96, bottom=0.04,
    )

    # ---- Row 1: Header strip ----
    ax_header = fig.add_subplot(gs[0, :])
    ax_header.set_facecolor(COLORS["surface"])
    ax_header.set_xticks([]); ax_header.set_yticks([])
    for spine in ax_header.spines.values():
        spine.set_color(COLORS["border"])
    meta = TICKER_META.get(ticker, {"company": ticker, "sector": "—", "fno": False, "mcap_cr": 0})
    chg_color = COLORS["accent"] if day_change_pct >= 0 else COLORS["warn"]
    chg_sign = "+" if day_change_pct >= 0 else ""
    header_parts = [
        (f"{ticker}", COLORS["text_primary"], 22, "bold", 0.01),
        (meta["company"], COLORS["text_secondary"], 13, "normal", 0.10),
        (f"₹{today_close:,.2f}", COLORS["text_primary"], 18, "bold", 0.32),
        (f"{chg_sign}{day_change_pct:+.2f}%", chg_color, 14, "bold", 0.40),
        (f"Mcap  ₹{meta['mcap_cr']:,} Cr", COLORS["text_secondary"], 12, "normal", 0.50),
        (f"Sector  {meta['sector']}", COLORS["text_secondary"], 12, "normal", 0.65),
        (f"F&O  {'Y' if meta['fno'] else 'N'}", COLORS["text_secondary"], 12, "normal", 0.78),
        (f"asof {asof.date()}  ·  {lookback_days}d window", COLORS["text_secondary"], 11, "normal", 0.86),
    ]
    for txt, col, sz, w, x in header_parts:
        ax_header.text(x, 0.5, txt, transform=ax_header.transAxes,
                       color=col, fontsize=sz, fontweight=w,
                       fontfamily="monospace" if "₹" in txt or "%" in txt or txt.startswith(ticker) else "sans-serif",
                       va="center")

    # ---- Row 2: Price chart (left) + Volume profile (right) ----
    ax_price = fig.add_subplot(gs[1, :11])
    ax_vp = fig.add_subplot(gs[1, 11:], sharey=ax_price)

    # Price chart: H-L bars + close line
    ax_price.fill_between(df_window.index, df_window["low"], df_window["high"],
                          color=COLORS["muted"], alpha=0.5, linewidth=0)
    ax_price.plot(df_window.index, df_window["close"],
                  color=COLORS["text_primary"], linewidth=0.9)

    # POC + VAH + VAL lines
    ax_price.axhline(vp.poc, color=COLORS["flag"], linewidth=1.2, label=f"POC ₹{vp.poc:.2f}")
    ax_price.axhline(vp.vah, color=COLORS["accent"], linewidth=0.7, linestyle="--", label=f"VAH ₹{vp.vah:.2f}")
    ax_price.axhline(vp.val, color=COLORS["accent"], linewidth=0.7, linestyle="--", label=f"VAL ₹{vp.val:.2f}")

    # Mark today with a vertical line + dot
    ax_price.axvline(asof, color=COLORS["text_secondary"], linewidth=0.4, alpha=0.5)
    ax_price.scatter([asof], [today_close], s=80, color=COLORS["flag"],
                     edgecolors=COLORS["text_primary"], zorder=5, linewidth=1)

    ax_price.set_facecolor(COLORS["bg"])
    ax_price.tick_params(colors=COLORS["text_secondary"], labelsize=9)
    for spine in ax_price.spines.values():
        spine.set_color(COLORS["border"])
    ax_price.grid(True, color=COLORS["border"], linewidth=0.4, alpha=0.4)
    ax_price.set_ylabel("Price (₹, split+div adjusted)", color=COLORS["text_primary"], fontsize=10)
    ax_price.legend(loc="upper left", fontsize=8, facecolor=COLORS["surface"],
                    edgecolor=COLORS["border"], labelcolor=COLORS["text_primary"])

    # Volume profile histogram (right)
    if not vp.bins.empty:
        in_va = (vp.bins["price_bin_mid"] >= vp.val) & (vp.bins["price_bin_mid"] <= vp.vah)
        bar_colors = [COLORS["accent"] if v else COLORS["muted"] for v in in_va]
        poc_idx = vp.bins["volume"].idxmax()
        bar_colors[poc_idx] = COLORS["flag"]
        ax_vp.barh(vp.bins["price_bin_mid"], vp.bins["volume"],
                   height=vp.bin_width * 0.95 if vp.bin_width > 0 else 0.5,
                   color=bar_colors, edgecolor="none")
    ax_vp.set_facecolor(COLORS["bg"])
    ax_vp.tick_params(colors=COLORS["text_secondary"], labelsize=8)
    ax_vp.set_yticklabels([])  # share with price chart
    for spine in ax_vp.spines.values():
        spine.set_color(COLORS["border"])
    ax_vp.set_xlabel("Volume (shares, binned by 0.5% × mid_price)", color=COLORS["text_secondary"], fontsize=9)
    ax_vp.set_title(f"Volume Profile · {len(vp.bins)} bins · {vp.n_days}d",
                    color=COLORS["text_primary"], fontsize=10, loc="left")
    ax_vp.grid(True, color=COLORS["border"], linewidth=0.4, alpha=0.4, axis="x")

    # ---- Row 3: Honest deals label (always visible, flag color, non-dismissable) ----
    ax_label = fig.add_subplot(gs[2, :])
    ax_label.set_facecolor(COLORS["surface"])
    ax_label.set_xticks([]); ax_label.set_yticks([])
    for spine in ax_label.spines.values():
        spine.set_color(COLORS["flag"])
        spine.set_linewidth(1.5)
    ax_label.text(0.01, 0.5, "⚠", transform=ax_label.transAxes,
                  color=COLORS["flag"], fontsize=22, va="center")
    ax_label.text(0.04, 0.5, label_data["label"], transform=ax_label.transAxes,
                  color=COLORS["text_primary"], fontsize=11, va="center", wrap=True)

    # ---- Row 4: Deals table (left) + Breakout state card (right) ----
    ax_deals = fig.add_subplot(gs[3, :11])
    ax_deals.set_facecolor(COLORS["surface"])
    ax_deals.set_xticks([]); ax_deals.set_yticks([])
    for spine in ax_deals.spines.values():
        spine.set_color(COLORS["border"])
    ax_deals.text(0.01, 0.94, f"Disclosed deals (window {window_start.date()} → {asof.date()})",
                  transform=ax_deals.transAxes, color=COLORS["text_primary"],
                  fontsize=12, fontweight="bold", va="top")

    if not deals_df.empty:
        # Render as text table
        col_x = [0.01, 0.13, 0.20, 0.50, 0.58, 0.72, 0.86]
        headers = ["Date", "Type", "Client", "Side", "Qty", "Price", "₹ Cr"]
        for i, (h, x) in enumerate(zip(headers, col_x)):
            ax_deals.text(x, 0.78, h, transform=ax_deals.transAxes,
                          color=COLORS["text_secondary"], fontsize=10,
                          fontweight="bold", va="top")
        ax_deals.axhline(0.74, color=COLORS["border"], linewidth=0.5)
        for i, (_, r) in enumerate(deals_df.head(8).iterrows()):
            y = 0.66 - i * 0.085
            client_short = (r["client"][:30] + "…") if len(r["client"]) > 32 else r["client"]
            value_cr = r["quantity"] * r["price"] / 1e7
            side_color = COLORS["accent"] if r["side"] == "BUY" else COLORS["warn"]
            row_vals = [
                (str(r["date"])[:10], COLORS["text_primary"]),
                (r["deal_type"], COLORS["text_secondary"]),
                (client_short, COLORS["text_primary"]),
                (r["side"], side_color),
                (f"{int(r['quantity']):,}", COLORS["text_primary"]),
                (f"₹{r['price']:,.2f}", COLORS["text_primary"]),
                (f"{value_cr:,.1f}", COLORS["text_primary"]),
            ]
            for (txt, col), x in zip(row_vals, col_x):
                ax_deals.text(x, y, txt, transform=ax_deals.transAxes,
                              color=col, fontsize=9, fontfamily="monospace", va="top")
    else:
        ax_deals.text(0.5, 0.4,
                      "No NSE-disclosed bulk or block deals in this window.",
                      transform=ax_deals.transAxes, color=COLORS["text_secondary"],
                      fontsize=12, ha="center", va="center", style="italic")
        ax_deals.text(0.5, 0.30,
                      "(Most large-cap days have zero disclosed deals — only trades > 0.5% of company shares qualify)",
                      transform=ax_deals.transAxes, color=COLORS["text_secondary"],
                      fontsize=9, ha="center", va="center", style="italic")

    # Breakout state card (right of deals)
    ax_bs = fig.add_subplot(gs[3, 11:])
    ax_bs.set_facecolor(COLORS["surface"])
    ax_bs.set_xticks([]); ax_bs.set_yticks([])
    for spine in ax_bs.spines.values():
        spine.set_color(COLORS["border"])
    ax_bs.text(0.05, 0.94, "Breakout state today", transform=ax_bs.transAxes,
               color=COLORS["text_primary"], fontsize=12, fontweight="bold", va="top")
    score_color = (COLORS["accent"] if bs.breakout_score >= 60
                   else COLORS["flag"] if bs.breakout_score >= 30
                   else COLORS["text_secondary"])
    ax_bs.text(0.05, 0.74, f"Score {bs.breakout_score:.0f}", transform=ax_bs.transAxes,
               color=score_color, fontsize=28, fontweight="bold",
               fontfamily="monospace", va="top")
    flags = []
    if bs.hvn_break: flags.append("HVN")
    if bs.swing_high_break: flags.append("20d SWING")
    if bs.cycle_high_break: flags.append("52w CYCLE")
    flag_text = " + ".join(flags) if flags else "no fresh breaks today"
    ax_bs.text(0.05, 0.55, f"Breaks: {flag_text}", transform=ax_bs.transAxes,
               color=COLORS["text_primary"], fontsize=11, va="top")
    ax_bs.text(0.05, 0.46, f"Volume:  {bs.volume_ratio:.2f}× the 20d avg",
               transform=ax_bs.transAxes, color=COLORS["text_secondary"], fontsize=10,
               fontfamily="monospace", va="top")
    ax_bs.text(0.05, 0.38, f"Close:   {bs.close_in_range_pct:.0%} of day's range",
               transform=ax_bs.transAxes, color=COLORS["text_secondary"], fontsize=10,
               fontfamily="monospace", va="top")
    ma_50 = "above" if bs.above_50dma else "below"
    ma_200 = "above" if bs.above_200dma else "below"
    ax_bs.text(0.05, 0.30, f"MAs:     {ma_50} 50dma · {ma_200} 200dma",
               transform=ax_bs.transAxes, color=COLORS["text_secondary"], fontsize=10,
               fontfamily="monospace", va="top")
    if bs.level_broken is not None:
        ax_bs.text(0.05, 0.22, f"Level broken: ₹{bs.level_broken:,.2f}",
                   transform=ax_bs.transAxes, color=COLORS["accent"], fontsize=10,
                   fontfamily="monospace", va="top")
    ax_bs.text(0.05, 0.10, "(Score is descriptive, not prescriptive — backtest stat, not a recommendation)",
               transform=ax_bs.transAxes, color=COLORS["text_secondary"],
               fontsize=8, va="top", style="italic")

    # ---- Save ----
    if output_path is None:
        REPORTS_DIR.mkdir(exist_ok=True)
        output_path = REPORTS_DIR / f"stock_lookup_{ticker}_{asof.date()}.png"
    fig.savefig(output_path, dpi=110, facecolor=COLORS["bg"], bbox_inches="tight")
    plt.close(fig)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--asof", required=True)
    parser.add_argument("--lookback", type=int, default=126)
    args = parser.parse_args()

    out = render_stock_lookup(args.ticker, args.asof, args.lookback)
    print(f"✓ Rendered → {out}")


if __name__ == "__main__":
    main()
