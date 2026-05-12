"""Backtest report — markdown summary + equity curve PNG."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from backtest.metrics import Metrics
from backtest.simulator import BacktestResult


COLORS = {
    "bg":             "#0a0e14",
    "surface":        "#11161d",
    "border":         "#1f2630",
    "text_primary":   "#e6edf3",
    "text_secondary": "#8b949e",
    "accent":         "#00d68f",
    "warn":           "#ff3d71",
    "flag":           "#ffaa00",
}


def write_report(result: BacktestResult, metrics: Metrics, output_dir: Path, label: str) -> dict:
    """Write markdown report + equity curve PNG. Returns paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = output_dir / f"backtest_{label}_{ts}.md"
    png_path = output_dir / f"backtest_{label}_{ts}_equity.png"

    # ---- equity curve PNG ----
    fig, ax = plt.subplots(figsize=(14, 7), facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])
    ax.plot(result.equity_curve.index, result.equity_curve.values,
            color=COLORS["accent"], linewidth=1.2, label="Strategy equity")
    ax.axhline(metrics.starting_equity, color=COLORS["text_secondary"],
               linewidth=0.6, linestyle=":", label=f"Starting capital ₹{metrics.starting_equity:,.0f}")
    # Underwater shading (drawdown)
    peak = result.equity_curve.cummax()
    dd = (result.equity_curve - peak) / peak * 100
    ax2 = ax.twinx()
    ax2.fill_between(dd.index, dd.values, 0, color=COLORS["warn"], alpha=0.15)
    ax2.set_ylabel("Drawdown %", color=COLORS["warn"], fontsize=9)
    ax2.tick_params(colors=COLORS["warn"], labelsize=8)
    ax2.set_facecolor(COLORS["bg"])

    ax.set_title(f"Backtest equity — {label}  ({result.equity_curve.index[0].date()} → {result.equity_curve.index[-1].date()})",
                 color=COLORS["text_primary"], fontsize=12)
    ax.set_ylabel("Equity (₹)", color=COLORS["text_primary"])
    ax.tick_params(colors=COLORS["text_secondary"])
    for s in ax.spines.values():
        s.set_color(COLORS["border"])
    ax.grid(True, color=COLORS["border"], linewidth=0.4, alpha=0.5)
    ax.legend(loc="upper left", facecolor=COLORS["surface"],
              edgecolor=COLORS["border"], labelcolor=COLORS["text_primary"], fontsize=9)
    fig.tight_layout()
    fig.savefig(png_path, dpi=110, facecolor=COLORS["bg"])
    plt.close(fig)

    # ---- markdown ----
    cfg = result.config
    rep = []
    rep.append(f"# Backtest report — {label}")
    rep.append("")
    rep.append(f"**Generated:** {datetime.now().isoformat(timespec='seconds')}")
    rep.append(f"**Window:** {cfg.start_date} → {cfg.end_date}")
    rep.append(f"**Equity curve:** `{png_path.name}`")
    rep.append("")
    rep.append("## Configuration")
    rep.append("")
    rep.append(f"- Initial capital: ₹{cfg.initial_capital:,.0f}")
    rep.append(f"- Risk per trade: {cfg.risk_per_trade_pct:.1%}")
    rep.append(f"- Cost per side: {cfg.cost_per_side_pct:.4%} (round-trip {2 * cfg.cost_per_side_pct:.4%})")
    rep.append(f"- Min breakout score: {cfg.min_score}")
    rep.append(f"- Min volume ratio: {cfg.min_volume_ratio}×")
    rep.append(f"- Above 50DMA filter: {cfg.require_above_50dma}")
    rep.append(f"- ATR period: {cfg.atr_period}d, stop {cfg.atr_stop_mult}× ATR, target {cfg.atr_target_mult}× ATR")
    rep.append(f"- Timeout: {cfg.timeout_days}d")
    rep.append("")
    rep.append("## Headline numbers")
    rep.append("")
    rep.append(f"- **EV per trade: {metrics.ev_per_trade_r:+.3f} R**  ← the gate metric")
    rep.append(f"- Trades: {metrics.n_trades}  (wins {metrics.n_wins}, losses {metrics.n_losses})")
    rep.append(f"- Hit rate: {metrics.hit_rate:.1%}")
    rep.append(f"- Avg win: {metrics.avg_win_r:+.2f}R ({metrics.avg_win_pct:+.1%})  ·  "
               f"Avg loss: {metrics.avg_loss_r:+.2f}R ({metrics.avg_loss_pct:+.1%})")
    if metrics.win_loss_asymmetry is not None:
        rep.append(f"- Win/loss asymmetry: {metrics.win_loss_asymmetry:.2f}× "
                   "(< 1 = winning more often than losing big; > 1 = winning bigger than losing)")
    rep.append(f"- Days held — winners: {metrics.avg_days_held_winners:.1f}d, losers: {metrics.avg_days_held_losers:.1f}d")
    rep.append("")
    rep.append("## Portfolio metrics")
    rep.append("")
    rep.append(f"- Starting equity: ₹{metrics.starting_equity:,.0f}")
    rep.append(f"- Ending equity:   ₹{metrics.ending_equity:,.0f}")
    rep.append(f"- Total net P&L:   ₹{metrics.total_pnl_net:,.0f}")
    rep.append(f"- CAGR:            {metrics.cagr:.2%}")
    rep.append(f"- Max drawdown:    -{metrics.max_drawdown:.1%}")
    if metrics.sharpe_annual is not None:
        rep.append(f"- Sharpe (annual): {metrics.sharpe_annual:.2f}")
    rep.append("")
    rep.append("## Exit reasons")
    rep.append("")
    for reason, n in sorted((metrics.exit_reason_counts or {}).items(), key=lambda x: -x[1]):
        pct = n / metrics.n_trades * 100 if metrics.n_trades > 0 else 0
        rep.append(f"- {reason}: {n} ({pct:.1f}%)")
    rep.append("")
    rep.append("## Top 10 wins (by R)")
    rep.append("")
    rep.append("| Ticker | Entry → Exit | Days | R | Net ₹ |")
    rep.append("|---|---|---:|---:|---:|")
    sorted_trades = sorted(result.trades, key=lambda t: -t.r_multiple)
    for t in sorted_trades[:10]:
        rep.append(f"| {t.ticker} | {t.entry_date.date()} → {t.exit_date.date() if t.exit_date else '?'} | "
                   f"{t.days_held} | {t.r_multiple:+.2f} | ₹{t.pnl_net:,.0f} |")
    rep.append("")
    rep.append("## Top 10 losses (by R)")
    rep.append("")
    rep.append("| Ticker | Entry → Exit | Days | R | Net ₹ |")
    rep.append("|---|---|---:|---:|---:|")
    for t in sorted_trades[-10:][::-1]:
        rep.append(f"| {t.ticker} | {t.entry_date.date()} → {t.exit_date.date() if t.exit_date else '?'} | "
                   f"{t.days_held} | {t.r_multiple:+.2f} | ₹{t.pnl_net:,.0f} |")

    md_path.write_text("\n".join(rep) + "\n")
    return {"md": md_path, "png": png_path}
