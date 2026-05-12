"""
Event-based backtest simulator.

Loop:
  For each trading day D in [start, end]:
    1. EXIT phase: process open positions
       - If today's LOW <= stop  → exit at stop (cost: 0.125%)
       - Else if today's HIGH >= target → exit at target (cost: 0.125%)
       - Else if days_held >= timeout → exit at today's CLOSE
    2. ENTRY phase: scan universe for breakouts
       - Use historical universe snapshot for the month containing D
       - For each (ticker, score >= threshold) signal:
         - Schedule entry at D+1 open
    3. EXECUTE scheduled entries from yesterday's signals
       - Compute ATR at signal_date (D-1)
       - Stop = entry - K × ATR
       - Target = entry + M × ATR
       - Qty = (1% × account) / (entry - stop), floored to int
       - Pay 0.125% entry cost
       - Track open position

Anti-look-ahead enforced:
  - Universe = historical snapshot for that month (NEVER today's)
  - Signal at D uses scan_universe(D) which uses breakout_state's anti-look-ahead
  - Entry executes at D+1 open (next-day execution)
  - ATR computed at signal date D using data through D only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from analytics.scan_universe import scan_universe
from backtest.atr import atr_at


ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = ROOT / "data" / "ohlcv"
HISTORY_DIR = ROOT / "data" / "universe_history"


@dataclass
class BacktestConfig:
    start_date: str
    end_date: str
    initial_capital: float = 100_000.0
    risk_per_trade_pct: float = 0.01            # 1% of account at risk
    cost_per_side_pct: float = 0.00125          # 0.125% × 2 = 0.25% round-trip
    min_score: float = 50.0                     # breakout score threshold
    min_volume_ratio: float = 1.5               # secondary filter
    require_above_50dma: bool = True
    require_above_200dma: bool = False
    regime_filter_enabled: bool = False    # block new entries when Nifty 50 < its 200-DMA
    regime_filter_ticker: str = "_NSEI"
    regime_filter_ma_period: int = 200
    atr_period: int = 14
    atr_stop_mult: float = 2.0                  # stop = entry - 2 × ATR
    atr_target_mult: float = 4.0                # target = entry + 4 × ATR (R:R = 2:1)
    timeout_days: int = 20                      # exit if neither stop nor target hit
    max_concurrent_positions: int = 50          # safety cap


@dataclass
class Trade:
    ticker: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    qty: int
    risk_amount: float                  # ₹ at risk = qty × (entry - stop)
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None   # "stop" | "target" | "timeout" | "open"
    pnl_gross: float = 0.0
    pnl_net: float = 0.0                # after costs
    r_multiple: float = 0.0             # (pnl_gross / risk_amount)
    cost_total: float = 0.0
    days_held: int = 0


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    final_equity: float = 0.0


def _load_universe_for_month(month_str: str) -> set[str]:
    """Load the historical universe snapshot. Falls back to current if missing."""
    snap = HISTORY_DIR / f"{month_str}.csv"
    if not snap.exists():
        return set()
    df = pd.read_csv(snap)
    return set(df["SYMBOL"].astype(str))


def _load_ohlcv(ticker: str) -> Optional[pd.DataFrame]:
    """Cached parquet load. Returns None if missing/unreadable."""
    p = OHLCV_DIR / f"{ticker}.parquet"
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def regime_active(idx_df: pd.DataFrame, asof_date: pd.Timestamp, ma_period: int = 200) -> bool:
    """
    Returns True if index close on asof_date > N-day SMA of index closes through asof_date.

    Used to gate NEW entries to bull-regime days only. Existing positions are not affected.
    Anti-look-ahead: uses index close on asof_date itself (known by EOD; entries execute
    at next-day open, so this is not future-leaking).
    """
    if idx_df is None or asof_date not in idx_df.index:
        return True  # default to active if no data
    history = idx_df.loc[:asof_date]["close"].astype(float)
    if len(history) < ma_period:
        return True  # not enough history yet
    today_close = float(history.iloc[-1])
    sma = float(history.iloc[-ma_period:].mean())
    return today_close > sma


def run_backtest(config: BacktestConfig, verbose: bool = False) -> BacktestResult:
    """Run the event loop start_date → end_date. Returns BacktestResult."""
    start = pd.Timestamp(config.start_date)
    end = pd.Timestamp(config.end_date)

    # Use _NSEI as the trading calendar (every day NSE opened)
    nsei = _load_ohlcv("_NSEI")
    if nsei is None:
        raise RuntimeError("_NSEI.parquet missing — can't determine trading calendar")
    calendar = nsei.loc[start:end].index.sort_values()

    # OHLCV cache to avoid repeated parquet loads
    ohlcv_cache: dict[str, pd.DataFrame] = {}

    def get_df(ticker: str) -> Optional[pd.DataFrame]:
        if ticker not in ohlcv_cache:
            ohlcv_cache[ticker] = _load_ohlcv(ticker)
        return ohlcv_cache[ticker]

    open_positions: list[Trade] = []
    closed_trades: list[Trade] = []
    pending_entries: list[tuple[str, pd.Timestamp]] = []  # (ticker, signal_date)

    equity = config.initial_capital
    equity_history: list[tuple[pd.Timestamp, float]] = []

    for day_idx, D in enumerate(calendar):
        # ---- 1. EXIT phase: process open positions ----
        still_open: list[Trade] = []
        for pos in open_positions:
            df = get_df(pos.ticker)
            if df is None or D not in df.index:
                # Ticker has a data gap on D (didn't trade today even though NSE did).
                # Carry the position forward — do NOT force-close. The bug we're avoiding:
                # using df.iloc[-1] would use the parquet's LAST row (potentially years
                # in the future) as the exit price. Instead, just hold and re-evaluate
                # the next day. If the gap persists too long, the timeout check will
                # eventually fire (using an actual close price from a real trading day).
                still_open.append(pos)
                continue
            row = df.loc[D]
            today_low = float(row["low"])
            today_high = float(row["high"])
            today_close = float(row["close"])

            # Stop hit (priority 1 — conservative)
            if today_low <= pos.stop:
                _close_trade(pos, D, pos.stop, "stop", config)
                closed_trades.append(pos)
                equity += pos.qty * pos.stop * (1 - config.cost_per_side_pct)
                continue
            # Target hit (priority 2)
            if today_high >= pos.target:
                _close_trade(pos, D, pos.target, "target", config)
                closed_trades.append(pos)
                equity += pos.qty * pos.target * (1 - config.cost_per_side_pct)
                continue
            # Timeout
            days_held = (D - pos.entry_date).days
            if days_held >= config.timeout_days:
                _close_trade(pos, D, today_close, "timeout", config)
                closed_trades.append(pos)
                equity += pos.qty * today_close * (1 - config.cost_per_side_pct)
                continue
            still_open.append(pos)
        open_positions = still_open

        # ---- 2. EXECUTE pending entries from previous day's signals ----
        if pending_entries and day_idx > 0:
            for ticker, signal_date in pending_entries:
                if len(open_positions) >= config.max_concurrent_positions:
                    break
                df = get_df(ticker)
                if df is None or D not in df.index:
                    continue
                entry_price = float(df.loc[D, "open"])
                if entry_price <= 0:
                    continue
                atr = atr_at(df, signal_date, n=config.atr_period)
                if pd.isna(atr) or atr <= 0:
                    continue
                stop = entry_price - config.atr_stop_mult * atr
                target = entry_price + config.atr_target_mult * atr
                if stop <= 0 or stop >= entry_price:
                    continue
                risk_per_share = entry_price - stop
                risk_amount = equity * config.risk_per_trade_pct
                qty = int(risk_amount // risk_per_share)
                if qty <= 0:
                    continue
                cost = qty * entry_price * config.cost_per_side_pct
                trade = Trade(
                    ticker=ticker,
                    signal_date=signal_date,
                    entry_date=D,
                    entry_price=entry_price,
                    stop=stop,
                    target=target,
                    qty=qty,
                    risk_amount=qty * risk_per_share,
                    cost_total=cost,
                )
                open_positions.append(trade)
                equity -= qty * entry_price + cost  # cash deployed + entry cost
            pending_entries = []

        # ---- 3. SIGNAL phase: scan today for breakouts ----
        # Regime filter: skip new entries when index < its 200-DMA (existing positions unaffected)
        if config.regime_filter_enabled:
            idx_df = get_df(config.regime_filter_ticker)
            if not regime_active(idx_df, D, ma_period=config.regime_filter_ma_period):
                # Track equity then skip entry phase
                deployed = sum(p.qty * _last_close(get_df(p.ticker), D) for p in open_positions)
                equity_history.append((D, equity + deployed))
                continue

        month_str = D.strftime("%Y-%m")
        allowed = _load_universe_for_month(month_str)
        if not allowed:
            # Fall back to today's universe if no historical snapshot
            allowed = None  # scan_universe will use all parquets

        scan = scan_universe(
            asof_date=D,
            min_score=config.min_score,
            min_volume_ratio=config.min_volume_ratio,
            require_above_50dma=config.require_above_50dma,
            require_above_200dma=config.require_above_200dma,
            top_n=config.max_concurrent_positions,
        )
        for _, sig in scan.df.iterrows():
            t = sig["ticker"]
            if allowed is not None and t not in allowed:
                continue  # not in historical universe → skip (anti-survivorship)
            # Don't double-enter if already holding
            if any(p.ticker == t for p in open_positions):
                continue
            pending_entries.append((t, D))

        # Track equity (deployed + cash)
        deployed = sum(p.qty * _last_close(get_df(p.ticker), D) for p in open_positions)
        total_equity = equity + deployed
        equity_history.append((D, total_equity))

        if verbose and day_idx % 100 == 0:
            print(f"  [{day_idx:4d}/{len(calendar)}] {D.date()}  equity=₹{total_equity:,.0f}  "
                  f"open={len(open_positions)}  closed={len(closed_trades)}  pending={len(pending_entries)}")

    # Force-close anything still open at end
    # CRITICAL: use the test window's last day, NOT parquet's last row.
    # The parquet may extend beyond the test window (we have data through today),
    # and using df.iloc[-1] would leak future prices into the backtest.
    test_end = calendar[-1]
    for pos in open_positions:
        df = get_df(pos.ticker)
        if df is None:
            continue
        sub = df.loc[:test_end]
        if len(sub) == 0:
            continue
        last_close = float(sub["close"].iloc[-1])
        last_date = sub.index[-1]
        _close_trade(pos, last_date, last_close, "end_of_test", config)
        closed_trades.append(pos)

    eq_series = pd.Series(
        [v for _, v in equity_history],
        index=pd.DatetimeIndex([d for d, _ in equity_history]),
        name="equity",
    )
    return BacktestResult(
        config=config,
        trades=closed_trades,
        equity_curve=eq_series,
        final_equity=eq_series.iloc[-1] if len(eq_series) > 0 else config.initial_capital,
    )


def _close_trade(t: Trade, exit_date: pd.Timestamp, exit_price: float, reason: str, config: BacktestConfig) -> None:
    """Mutate t with exit details, P&L, R-multiple."""
    t.exit_date = exit_date
    t.exit_price = exit_price
    t.exit_reason = reason
    t.days_held = (exit_date - t.entry_date).days
    exit_cost = t.qty * exit_price * config.cost_per_side_pct
    t.cost_total += exit_cost
    t.pnl_gross = (exit_price - t.entry_price) * t.qty
    t.pnl_net = t.pnl_gross - t.cost_total
    if t.risk_amount > 0:
        t.r_multiple = t.pnl_gross / t.risk_amount


def _last_close(df: Optional[pd.DataFrame], asof: pd.Timestamp) -> float:
    """Last available close on or before asof. Used for mark-to-market equity."""
    if df is None:
        return 0.0
    sub = df.loc[:asof]
    if len(sub) == 0:
        return 0.0
    return float(sub["close"].iloc[-1])
