"""
Event-based backtest for the breakout strategy.

Per Phase 1 office hours Premise A: this is ~60% NEW code vs momentum-dashboard's
monthly-rebalance simulator. Metrics and report layers compose with the same shape
so output files look identical, but the event loop is fundamentally different:
  - Monthly rebal: rebalance to top N stocks every 21 trading days
  - Event-based:   enter on breakout signal, exit on ATR stop / ATR target / N-day timeout

Strict anti-look-ahead invariants:
  1. Universe for date D = `data/universe_history/{YYYY-MM}.csv` for that month (P0.6).
     NEVER use today's universe for historical scans — that's survivorship bias.
  2. Breakout signals at date D use breakout_state which already excludes day D from
     resistance windows (P0.5 anti-look-ahead).
  3. Entry executes at date D+1 OPEN — never at D close (which is when signal fires).
  4. ATR / stop / target computed at signal date D using data through D only.
  5. Deals data (if used) shifted by +1 day via deals.shift_for_backtest (P0.3a).

Sacred holdout: 2025-01-01 onwards is LOCKED until user passes --open-holdout flag.
Same discipline as momentum-dashboard. Once opened, it cannot be re-used for tuning.

Modules:
  atr        — Average True Range
  simulator  — the event loop (signals → entries → exits → trades)
  metrics    — EV per trade in R, CAGR, max DD, hit rate, win/loss asymmetry
  report     — markdown + equity curve PNG
  run        — CLI entrypoint with holdout protection
"""
