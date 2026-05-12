"""
Render volume-profile PNGs for the 5 sample tickers.

Output:
  reports/profile_{TICKER}_{lookback}.png

Each PNG: candlestick-ish price chart on left, volume profile histogram on right.
POC / VAH / VAL marked. HVNs labelled.

Use this for visual sanity before trusting the algorithm. Compare against TradingView
for any one ticker × window to confirm POC is in the right place.

Run: .venv/bin/python analytics/preview_profiles.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from analytics.volume_profile import volume_profile_for_ticker

ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = ROOT / "data" / "ohlcv"
REPORTS_DIR = ROOT / "reports"

# Tickers + window per ticker (chosen to be informative for each stock)
SAMPLES = [
    {"ticker": "RELIANCE",  "start": "2024-01-01", "end": "2026-04-30", "label": "RELIANCE 2024-2026"},
    {"ticker": "NESTLEIND", "start": "2024-01-01", "end": "2026-04-30", "label": "NESTLEIND 2024-2026 (post-split)"},
    {"ticker": "KOTAKBANK", "start": "2024-01-01", "end": "2026-04-30", "label": "KOTAKBANK 2024-2026"},
    {"ticker": "MAZDOCK",   "start": "2024-01-01", "end": "2026-04-30", "label": "MAZDOCK 2024-2026 (multi-bagger)"},
    {"ticker": "GROWW",     "start": "2025-11-12", "end": "2026-04-30", "label": "GROWW IPO → today (115d, below VP threshold)"},
]


def render_one(ticker: str, start: str, end: str, label: str, out: Path) -> None:
    vp = volume_profile_for_ticker(ticker, start=start, end=end, bin_width_pct=0.005)
    df = pd.read_parquet(OHLCV_DIR / f"{ticker}.parquet").loc[start:end]

    fig, (ax_price, ax_vp) = plt.subplots(
        1, 2, figsize=(14, 7), gridspec_kw={"width_ratios": [3, 1]}, sharey=True
    )

    # --- Left: simple price chart with H-L bars ---
    ax_price.fill_between(df.index, df["low"], df["high"], color="#666666", alpha=0.4, linewidth=0)
    ax_price.plot(df.index, df["close"], color="#e6edf3", linewidth=0.8)

    # POC / VAH / VAL horizontal lines
    ax_price.axhline(vp.poc, color="#ffaa00", linewidth=1.2, label=f"POC ₹{vp.poc:.2f}")
    ax_price.axhline(vp.vah, color="#00d68f", linewidth=0.8, linestyle="--", label=f"VAH ₹{vp.vah:.2f}")
    ax_price.axhline(vp.val, color="#00d68f", linewidth=0.8, linestyle="--", label=f"VAL ₹{vp.val:.2f}")

    # HVNs (subtle dotted lines)
    for hvn in vp.hvns:
        ax_price.axhline(hvn, color="#ff3d71", linewidth=0.4, linestyle=":", alpha=0.5)

    ax_price.set_title(label, fontsize=12, color="#e6edf3")
    ax_price.set_ylabel("Price (₹, split+div adjusted)", color="#e6edf3")
    ax_price.legend(loc="upper left", fontsize=8, facecolor="#11161d", edgecolor="#1f2630", labelcolor="#e6edf3")
    ax_price.grid(True, color="#1f2630", linewidth=0.4, alpha=0.5)
    ax_price.set_facecolor("#0a0e14")
    ax_price.tick_params(colors="#8b949e")

    # --- Right: volume profile (horizontal bars) ---
    bins = vp.bins
    if not bins.empty:
        # Highlight value area
        in_va = (bins["price_bin_mid"] >= vp.val) & (bins["price_bin_mid"] <= vp.vah)
        colors = ["#00d68f" if v else "#666666" for v in in_va]
        # POC bar in amber
        poc_idx = bins["volume"].idxmax()
        colors[poc_idx] = "#ffaa00"

        ax_vp.barh(
            bins["price_bin_mid"],
            bins["volume"],
            height=vp.bin_width * 0.95 if vp.bin_width > 0 else 0.5,
            color=colors,
            edgecolor="none",
        )

    ax_vp.set_title(
        f"Vol profile · {len(bins)} bins · POC=₹{vp.poc:.2f}\n"
        f"HVNs={len(vp.hvns)} · LVNs={len(vp.lvns)} · {vp.n_days}d",
        fontsize=10, color="#e6edf3",
    )
    ax_vp.set_xlabel("Volume (shares)", color="#e6edf3")
    ax_vp.grid(True, color="#1f2630", linewidth=0.4, alpha=0.5, axis="x")
    ax_vp.set_facecolor("#0a0e14")
    ax_vp.tick_params(colors="#8b949e")

    fig.patch.set_facecolor("#0a0e14")
    fig.tight_layout()
    fig.savefig(out, dpi=110, facecolor="#0a0e14")
    plt.close(fig)
    print(f"  ✓ {out.name}  ({vp})")


def main() -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    print(f"Rendering {len(SAMPLES)} volume profiles → {REPORTS_DIR}/")
    for s in SAMPLES:
        out = REPORTS_DIR / f"profile_{s['ticker']}.png"
        try:
            render_one(s["ticker"], s["start"], s["end"], s["label"], out)
        except Exception as e:
            print(f"  ✗ {s['ticker']} failed: {e}")
    print(f"\nDone. Open with: open {REPORTS_DIR}/")


if __name__ == "__main__":
    main()
