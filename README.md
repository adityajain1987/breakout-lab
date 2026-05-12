# Breakout Lab

Honest research dashboard for one Indian stock trader. Volume profile, breakout state,
and named-counterparty deals for any 1000Cr+ NSE stock. **Decide-yourself tool — no
buy/sell signals.**

Built by Aditya for Amit Bhai. Not for distribution. Personal use only.

---

## What it shows

For any NSE-listed ticker with cached data:

- **Volume-by-price histogram** — where the inventory sits over your chosen window
- **Breakout state today** — HVN / 20-day swing / 52-week cycle, with composite score
- **NSE-disclosed bulk + block deals** — named counterparty, with the always-visible
  *"remaining X% of volume is anonymous"* label
- **Quarantine flags** — data-quality warnings (splits, IPOs, circuit days, suspended periods)

For your daily routine:

- **Breakouts Today** — EOD scan over the Nifty 500 universe, ranked by composite score (click any row to jump to Stock Lookup)
- **Backtest Playground** — test strategy ideas on any historical window
- **Glossary** — plain-English definitions of every term
- **Watchlist** — your saved tickers with notes + live breakout state (add via Stock Lookup sidebar)

---

## What it does NOT do

- **No auto buy/sell signals.** The breakout score is descriptive, not prescriptive —
  a backtest stat, not a recommendation.
- **No fake FII numbers.** NSE doesn't publish per-stock FII flow at retail level.
  We show only the trades that NSE actually discloses (bulk > 0.5% of company shares,
  block ≥ ₹10Cr / 5L shares) and label the rest "anonymous."
- **No public deployment.** Run locally. Distributing signals would require SEBI
  Research Analyst registration in India.

---

## Run it (one command)

```bash
cd breakout-lab
./setup.sh
```

That single command:
1. Verifies Python 3.11 is installed
2. Creates a Python virtual environment (`.venv/`)
3. Installs all dependencies
4. Builds the Nifty 500 universe
5. Fetches ~500 OHLCV parquets (5-15 min, resumable)
6. Builds 76 monthly historical universe snapshots
7. Pulls today's bulk + block deals
8. Runs the quarantine sweep
9. Schedules daily refresh (Mon-Fri 4:30 PM via macOS launchd)
10. Optionally opens the dashboard

**Total time: ~10-20 minutes (mostly the OHLCV fetch).** After it finishes, the dashboard is at `http://localhost:8501/` and refreshes automatically every weekday after market close.

If you'd rather run things manually:

```bash
.venv/bin/python -m data.build_universe
.venv/bin/python -m data.fetch_universe          # 5-15 min
.venv/bin/python -m data.build_universe_history
.venv/bin/python -m deals.scraper                # ~1 sec
.venv/bin/python -m quarantine.run_sweep         # ~3-5 min
./setup_daily_refresh.sh                         # macOS launchd
.venv/bin/streamlit run dashboard/app.py         # opens dashboard
```

Daily refresh manually (if not using launchd):

```bash
.venv/bin/python daily_refresh.py             # full: deals + OHLCV + quarantine
.venv/bin/python daily_refresh.py --quick     # deals + quarantine only (faster)
```

Schedule controls:

```bash
./setup_daily_refresh.sh --status   # is the launchd job installed?
./setup_daily_refresh.sh --remove   # uninstall it
```

---

## Daily workflow

1. After 4 PM IST, run `python daily_refresh.py` (or schedule it via cron / launchd)
2. Open the dashboard
3. **Breakouts Today** — see the day's qualified breakouts, ranked
4. Click into any interesting ticker via **Stock Lookup** for the full context
5. Decide manually whether to act

---

## Honest data note

All prices are **split + dividend adjusted** (yfinance `auto_adjust=True`). Today's
price matches the NSE quote, but historical prices have been back-adjusted — they
may look lower than what you'd see on a non-adjusted chart. Volume profile,
percentage moves, and breakout signals are correct on this scale.

---

## Project structure

```
breakout-lab/
├── README.md                    ← you are here (Amit-facing)
├── CLAUDE.md                    ← engineer-facing project pointer
├── STATUS.md                    ← decision log + phase status
├── TODOS.md                     ← priority queue
├── DESIGN.md                    ← UI design spec
├── docs/
│   └── Phase1_OfficeHours.md    ← Phase 1 design pressure-test
├── requirements.txt
├── daily_refresh.py             ← runs all 3 daily jobs
│
├── data/                        ← OHLCV parquets, universe CSVs
│   ├── universe_1000cr.csv      ← Nifty 500 list (the universe)
│   ├── universe_history/        ← monthly point-in-time snapshots 2020+
│   ├── ohlcv/                   ← per-ticker daily bars (parquet)
│   ├── build_universe.py
│   ├── fetch_universe.py
│   └── build_universe_history.py
│
├── analytics/                   ← pure analytical primitives
│   ├── volume_profile.py        ← POC / VAH / VAL / HVN / LVN
│   ├── breakout_detector.py     ← 3-resistance composite score
│   └── scan_universe.py         ← daily scan engine
│
├── deals/                       ← NSE bulk + block deals
│   ├── scraper.py               ← daily fetcher (forward-only)
│   ├── store.py                 ← SQLite + dedupe + T+1 helper
│   └── deals.db                 ← accumulated deals
│
├── quarantine/                  ← data-quality flags
│   ├── checks.py                ← 5 check functions
│   ├── store.py                 ← SQLite flag store
│   ├── run_sweep.py             ← run all checks
│   └── quarantine.db            ← accumulated flags
│
├── backtest/                    ← event-based simulator
│   ├── simulator.py             ← the event loop
│   ├── atr.py                   ← Wilder ATR
│   ├── metrics.py               ← EV / CAGR / DD / asymmetry
│   ├── report.py                ← markdown + equity curve PNG
│   └── run.py                   ← CLI with sacred-holdout protection
│
├── dashboard/                   ← Streamlit MVP
│   ├── app.py                   ← landing page
│   └── pages/
│       ├── 1_📊_Stock_Lookup.py
│       ├── 2_🔍_Breakouts_Today.py
│       ├── 3_🧪_Backtest_Playground.py
│       └── 4_📖_Glossary.py
│
└── reports/                     ← generated artifacts (PNGs, MDs)
```

---

## What didn't work, honestly

The **breakout score as an auto-execution signal failed the +0.2R EV gate** on the
2020-2024 train window (best result +0.145R after one allowed parameter tune). The
project's original framing was always "research tool, decide yourself" — the backtest
just gave us empirical confirmation that auto-execution wouldn't work.

The 2025-2026 holdout window remains **sacred and untouched**. It will be opened
only if we ever materially redesign the strategy (different exit logic, sector RS
filter, ensemble with other signals).

What this means in practice: use the dashboard as **context for your own decisions**.
The composite score tells you "this setup historically hit X% of the time" — useful
information for the human in the loop. It doesn't tell you what to do.

---

## Disclaimers

- This tool is **not** registered as a SEBI Research Analyst service.
- It is built for **personal use only**.
- Do not share signals or screenshots with anyone — distribution would require
  SEBI RA registration in India.
- Past performance is not predictive of future results.
- The composite breakout score is a researched signal, not a recommendation to act.
