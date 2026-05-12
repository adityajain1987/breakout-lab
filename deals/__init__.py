"""NSE bulk + block deals — fetch, store, query.

Per Phase 1 office hours Item 6: this is the only honest "FII presence" signal we can show
at retail level. NSE publishes:
  - Bulk deals: any single trade > 0.5% of company shares (T+1 disclosure, named counterparty)
  - Block deals: single trade ≥ ₹10Cr or 5L shares, in two daily windows (T+1 disclosure)

Modules:
  scraper   — fetch today's bulk + block from NSE static archive (no auth needed)
  store     — SQLite schema + insert + dedupe + T+1 shift helper for backtest joins
"""
