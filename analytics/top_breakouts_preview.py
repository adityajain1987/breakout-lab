"""
Top Breakouts Today mockup — composes DESIGN.md Page 2 (Breakouts Today) into a static PNG.

Renders the EOD scan results as a sortable table view per the design spec.
Companion to stock_lookup_preview.py — that's Page 1, this is Page 2.

Run: .venv/bin/python -m analytics.top_breakouts_preview --asof 2026-04-30
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import pandas as pd

from analytics.scan_universe import scan_universe


ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"

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


def render_top_breakouts(
    asof_date: str,
    min_score: float = 30.0,
    min_volume_ratio: float = 1.5,
    require_above_50dma: bool = True,
    top_n: int = 20,
    output_path: Path | None = None,
) -> Path:
    """Render the Top Breakouts table mockup."""
    asof = pd.Timestamp(asof_date)

    result = scan_universe(
        asof_date=asof,
        min_score=min_score,
        min_volume_ratio=min_volume_ratio,
        require_above_50dma=require_above_50dma,
        top_n=top_n,
    )
    df = result.df

    fig = plt.figure(figsize=(18, max(8, 1.5 + 0.45 * (len(df) + 4))), facecolor=COLORS["bg"])
    gs = gridspec.GridSpec(
        nrows=4, ncols=1,
        height_ratios=[0.55, 0.45, max(2, 0.4 * len(df) + 0.3), 0.4],
        hspace=0.10,
        left=0.03, right=0.98, top=0.97, bottom=0.04,
    )

    # ---- Row 1: Header ----
    ax_h = fig.add_subplot(gs[0])
    ax_h.set_facecolor(COLORS["surface"])
    ax_h.set_xticks([]); ax_h.set_yticks([])
    for s in ax_h.spines.values():
        s.set_color(COLORS["border"])
    ax_h.text(0.01, 0.65, "Breakouts Today", transform=ax_h.transAxes,
              color=COLORS["text_primary"], fontsize=20, fontweight="bold", va="center")
    ax_h.text(0.01, 0.22, f"NSE 500 universe · asof {asof.date()} · sorted by composite score desc",
              transform=ax_h.transAxes, color=COLORS["text_secondary"], fontsize=11, va="center")
    ax_h.text(0.99, 0.5,
              f"{result.n_qualified} of {result.n_scanned} stocks qualified",
              transform=ax_h.transAxes, color=COLORS["accent"], fontsize=12,
              fontfamily="monospace", va="center", ha="right", fontweight="bold")

    # ---- Row 2: Filter strip ----
    ax_f = fig.add_subplot(gs[1])
    ax_f.set_facecolor(COLORS["bg"])
    ax_f.set_xticks([]); ax_f.set_yticks([])
    for s in ax_f.spines.values():
        s.set_color(COLORS["border"])
    filters = [
        f"min score: {min_score:.0f}",
        f"min volume ratio: {min_volume_ratio:.1f}×",
        f"above 50-DMA: {'Y' if require_above_50dma else 'any'}",
        f"market cap: 1000Cr+ (Nifty 500 universe)",
    ]
    ax_f.text(0.01, 0.5, "Filters: " + "  ·  ".join(filters),
              transform=ax_f.transAxes, color=COLORS["text_secondary"], fontsize=10, va="center")

    # ---- Row 3: Results table ----
    ax_t = fig.add_subplot(gs[2])
    ax_t.set_facecolor(COLORS["surface"])
    ax_t.set_xticks([]); ax_t.set_yticks([])
    for s in ax_t.spines.values():
        s.set_color(COLORS["border"])

    if df.empty:
        ax_t.text(0.5, 0.5,
                  "No breakouts matching filters today.\nTry lowering min score or volume ratio.",
                  transform=ax_t.transAxes, color=COLORS["text_secondary"],
                  fontsize=14, ha="center", va="center", style="italic")
    else:
        # Column layout (x positions normalized 0..1)
        cols = [
            ("#",       0.01, "right", 14),
            ("TICKER",  0.04, "left",  14),
            ("Sector",  0.13, "left",  14),
            ("LTP",     0.34, "right", 14),
            ("Chg%",    0.42, "right", 14),
            ("Levels",  0.50, "left",  14),
            ("Lvl ₹",   0.58, "right", 14),
            ("Vol×",    0.66, "right", 14),
            ("CIR%",    0.72, "right", 14),
            ("50d",     0.78, "left",  14),
            ("200d",    0.83, "left",  14),
            ("Score",   0.97, "right", 14),
        ]
        # Header row
        header_y = 0.96
        for label, x, align, _ in cols:
            ax_t.text(x, header_y, label, transform=ax_t.transAxes,
                      color=COLORS["text_secondary"], fontsize=10,
                      fontweight="bold", va="top", ha=align)
        ax_t.axhline(0.92, color=COLORS["border"], linewidth=0.5)

        # Data rows
        n = len(df)
        row_h = 0.86 / n if n > 0 else 0.86
        for i, row in df.iterrows():
            y = 0.88 - (i + 0.5) * row_h
            score_color = (COLORS["accent"] if row["breakout_score"] >= 70
                           else COLORS["flag"] if row["breakout_score"] >= 40
                           else COLORS["text_primary"])
            chg_color = COLORS["accent"] if row["day_change_pct"] >= 0 else COLORS["warn"]
            flags = ""
            if row["hvn_break"]: flags += "H"
            if row["swing_high_break"]: flags += "S"
            if row["cycle_high_break"]: flags += "C"
            flag_str = " ".join(list(flags)) if flags else "-"
            sector_short = (row["sector"][:18] + "..") if len(row["sector"]) > 20 else row["sector"]
            level_str = f"{row['level_broken']:.0f}" if row["level_broken"] else "-"
            ma_50 = ("✓", COLORS["accent"]) if row["above_50dma"] else ("✗", COLORS["warn"])
            ma_200 = ("✓", COLORS["accent"]) if row["above_200dma"] else ("✗", COLORS["warn"])

            row_data = [
                (f"{i+1}", COLORS["text_secondary"], "right"),
                (row["ticker"], COLORS["text_primary"], "left"),
                (sector_short, COLORS["text_secondary"], "left"),
                (f"₹{row['close']:,.2f}", COLORS["text_primary"], "right"),
                (f"{row['day_change_pct']:+.2f}%", chg_color, "right"),
                (flag_str, COLORS["text_primary"], "left"),
                (level_str, COLORS["text_secondary"], "right"),
                (f"{row['volume_ratio']:.1f}", COLORS["text_primary"], "right"),
                (f"{row['close_in_range_pct']:.0%}", COLORS["text_primary"], "right"),
                (ma_50[0], ma_50[1], "left"),
                (ma_200[0], ma_200[1], "left"),
                (f"{row['breakout_score']:.0f}", score_color, "right"),
            ]
            for (txt, col, align), (_, x, _, fs) in zip(row_data, cols):
                ax_t.text(x, y, txt, transform=ax_t.transAxes,
                          color=col, fontsize=10, fontfamily="monospace",
                          va="center", ha=align,
                          fontweight="bold" if col == score_color and row["breakout_score"] >= 70 else "normal")

    # ---- Row 4: Honest footer ----
    ax_foot = fig.add_subplot(gs[3])
    ax_foot.set_facecolor(COLORS["bg"])
    ax_foot.set_xticks([]); ax_foot.set_yticks([])
    for s in ax_foot.spines.values():
        s.set_color(COLORS["border"])
    foot_text = (
        f"Score is descriptive — composite of resistance breaks (HVN/Swing/Cycle) modulated by volume ratio, "
        f"close-in-range%, MA position. NOT a buy/sell recommendation. Click any row in production UI for "
        f"full Stock Lookup view (volume profile + breakout state + deals + honest disclosure label)."
    )
    ax_foot.text(0.01, 0.5, foot_text, transform=ax_foot.transAxes,
                 color=COLORS["text_secondary"], fontsize=9, va="center", style="italic")

    if output_path is None:
        REPORTS_DIR.mkdir(exist_ok=True)
        output_path = REPORTS_DIR / f"top_breakouts_{asof.date()}.png"
    fig.savefig(output_path, dpi=110, facecolor=COLORS["bg"], bbox_inches="tight")
    plt.close(fig)
    return output_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True)
    ap.add_argument("--min-score", type=float, default=30.0)
    ap.add_argument("--min-vol-ratio", type=float, default=1.5)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--include-below-50dma", action="store_true")
    args = ap.parse_args()

    out = render_top_breakouts(
        asof_date=args.asof,
        min_score=args.min_score,
        min_volume_ratio=args.min_vol_ratio,
        top_n=args.top_n,
        require_above_50dma=not args.include_below_50dma,
    )
    print(f"✓ Rendered → {out}")


if __name__ == "__main__":
    main()
