"""
Backtest metrics — computed from list of closed Trades + equity curve.

Output shape matches momentum-dashboard for report-template compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    n_trades: int
    n_wins: int
    n_losses: int
    hit_rate: float                 # 0..1
    avg_win_pct: float              # mean win as % of entry
    avg_loss_pct: float             # mean loss as % of entry (negative)
    avg_win_r: float                # mean winning R multiple
    avg_loss_r: float               # mean losing R multiple (negative)
    ev_per_trade_r: float           # expected value per trade in R units
    total_pnl_net: float
    starting_equity: float
    ending_equity: float
    cagr: float                     # 0..1
    max_drawdown: float             # 0..1 (positive number, e.g. 0.15 = -15%)
    sharpe_annual: Optional[float] = None
    win_loss_asymmetry: Optional[float] = None  # avg_win / |avg_loss|
    avg_days_held_winners: float = 0.0
    avg_days_held_losers: float = 0.0
    exit_reason_counts: dict = None


def compute_metrics(trades: list, equity_curve: pd.Series, starting_equity: float) -> Metrics:
    """Compute all metrics from closed trades + equity curve."""
    if not trades:
        return Metrics(
            n_trades=0, n_wins=0, n_losses=0, hit_rate=0.0,
            avg_win_pct=0.0, avg_loss_pct=0.0, avg_win_r=0.0, avg_loss_r=0.0,
            ev_per_trade_r=0.0, total_pnl_net=0.0,
            starting_equity=starting_equity, ending_equity=starting_equity,
            cagr=0.0, max_drawdown=0.0, exit_reason_counts={},
        )

    pnl_net = np.array([t.pnl_net for t in trades])
    r_mult = np.array([t.r_multiple for t in trades])
    pct_returns = np.array([
        (t.exit_price - t.entry_price) / t.entry_price for t in trades
    ])
    days_held = np.array([t.days_held for t in trades])

    wins_mask = pnl_net > 0
    n_wins = int(wins_mask.sum())
    n_losses = int(len(trades) - n_wins)
    hit_rate = n_wins / len(trades) if len(trades) > 0 else 0.0

    avg_win_pct = float(pct_returns[wins_mask].mean()) if n_wins > 0 else 0.0
    avg_loss_pct = float(pct_returns[~wins_mask].mean()) if n_losses > 0 else 0.0
    avg_win_r = float(r_mult[wins_mask].mean()) if n_wins > 0 else 0.0
    avg_loss_r = float(r_mult[~wins_mask].mean()) if n_losses > 0 else 0.0
    ev_per_trade_r = float(r_mult.mean())

    avg_days_held_winners = float(days_held[wins_mask].mean()) if n_wins > 0 else 0.0
    avg_days_held_losers = float(days_held[~wins_mask].mean()) if n_losses > 0 else 0.0

    total_pnl_net = float(pnl_net.sum())
    ending_equity = float(equity_curve.iloc[-1]) if len(equity_curve) > 0 else starting_equity

    # CAGR — annualised return from start to end
    if len(equity_curve) > 1:
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        years = days / 365.25
        cagr = (ending_equity / starting_equity) ** (1 / max(years, 0.01)) - 1 if years > 0 else 0.0
    else:
        cagr = 0.0

    # Max drawdown from peak
    if len(equity_curve) > 1:
        peak = equity_curve.cummax()
        dd_series = (peak - equity_curve) / peak
        max_drawdown = float(dd_series.max())
    else:
        max_drawdown = 0.0

    # Sharpe — daily returns of equity curve, annualised
    sharpe_annual: Optional[float] = None
    if len(equity_curve) > 30:
        daily_ret = equity_curve.pct_change().dropna()
        if daily_ret.std() > 0:
            sharpe_annual = float((daily_ret.mean() / daily_ret.std()) * np.sqrt(252))

    win_loss_asymmetry = None
    if avg_loss_r < 0:
        win_loss_asymmetry = avg_win_r / abs(avg_loss_r)

    exit_counts: dict = {}
    for t in trades:
        exit_counts[t.exit_reason or "unknown"] = exit_counts.get(t.exit_reason or "unknown", 0) + 1

    return Metrics(
        n_trades=len(trades),
        n_wins=n_wins,
        n_losses=n_losses,
        hit_rate=hit_rate,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        avg_win_r=avg_win_r,
        avg_loss_r=avg_loss_r,
        ev_per_trade_r=ev_per_trade_r,
        total_pnl_net=total_pnl_net,
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        cagr=cagr,
        max_drawdown=max_drawdown,
        sharpe_annual=sharpe_annual,
        win_loss_asymmetry=win_loss_asymmetry,
        avg_days_held_winners=avg_days_held_winners,
        avg_days_held_losers=avg_days_held_losers,
        exit_reason_counts=exit_counts,
    )
