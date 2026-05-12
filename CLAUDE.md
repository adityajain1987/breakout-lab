# Breakout Lab — Project Pointer

**Purpose:** Project-specific entry point. Identity, stack, rules, pointers. Cross-project rules live in `~/Desktop/Claude/MD Files/RULES.md`.
**Read alongside:** `STATUS.md`, `TODOS.md`, `DESIGN.md`
**Last updated:** 2026-05-01

---

## Fresh chat: read in this order

1. `~/Desktop/Claude/MD Files/RULES.md` — cross-project operating rules
2. `~/Desktop/Claude/MD Files/STATUS.md` — portfolio snapshot
3. This project's `STATUS.md` — current state + active phase
4. This project's `TODOS.md` — what to ship next
5. This project's `DESIGN.md` — UI spec
6. `~/Desktop/Claude/Github Repo Links/REFERENCES.md` — check before building from scratch

---

## Identity

**What:** Honest research dashboard. For any 1000Cr+ NSE stock, shows volume-by-price distribution, breakout state, and named-counterparty deals (bulk + block). Decide-yourself tool — no buy/sell calls.
**Who:** Amit (retail trader, runs his own book). Built by Aditya, used by Amit on his own machine.
**Stage:** Phase 0 — scaffold complete. Phase 1 (data layer) pending.
**North Star:** **Honest context per stock without faking what we can't see.** Every number on screen is either directly measured or marked as estimate / backtest stat.

---

## What it is NOT

- Not a signal service. Never displays "BUY at X / SL at Y / TGT at Z" as a recommendation.
- Not Chartink. We don't list 200 pre-baked screeners. We give deep volume + breakout context for one stock at a time.
- Not a forecast. Volume profile is reactive (the level forms after price has been there). UI says so.
- Not shared. Personal use only — no auth, no public deploy, no SEBI Research Analyst registration.
- Not a faked FII feed. We can't see who placed normal orders. We show only NSE-disclosed bulk + block deals and label the rest "anonymous."

---

## The 6 hard rules

1. **No auto buy/sell signals.** Show context; user decides. Backtest stats are descriptive ("this setup hit 54% historically"), never prescriptive ("buy this").
2. **Honest deals.** FII/DII panel shows only NSE-disclosed bulk + block deals with named counterparty. Always-visible label: "Remaining XX% of volume is anonymous (NSE does not publish per-stock FII flow)."
3. **1000Cr+ universe.** Hard filter at start. Below that, microstructure noise dominates the volume profile.
4. **Volume-profile lookback default = 6M.** Selectable 1W / 1M / 3M / 6M / 1Y / 2Y / 5Y. 5Y is for backtest, not for current S/R levels.
5. **Costs in from day one.** Backtest = 0.25% round-trip baked in. Pre-cost numbers are not reported anywhere.
6. **Sacred holdout.** Same discipline as momentum-dashboard: train on 2018-2023, hold out 2024-2025, do not peek at holdout until Phase 3 gate.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Match momentum-dashboard, share tooling |
| Data (OHLCV) | yfinance + parquet cache | Reuse fetcher from momentum-dashboard |
| Data (deals) | NSE public bulk/block CSVs | Daily, free, T+1 |
| Universe | NSE Bhavcopy + market cap filter | Recompute monthly |
| Backtest | pandas + numpy | Adapt momentum-dashboard simulator |
| UI MVP | Streamlit | 1-2 days to working |
| UI v2 | Next.js | Only if MVP earns it (Phase 5+) |
| Storage | parquet (OHLCV), SQLite (deals + journal), markdown (reports) | Right tool per job |

---

## Phase gates (do not skip)

| Phase | Exit criteria |
|---|---|
| 0. Scaffold | 4 MD files exist, folder structure ready, portfolio registered |
| 1. Data layer | 1000Cr+ universe built (~500 stocks), 5Y OHLCV cached, bulk/block scraper running, Bhavcopy cross-check passes |
| 2. Analytics | Volume profile module + breakout detector, both unit-tested against known historical cases |
| 3. Backtest | Breakout strategy EV > 0.2R after costs on 2024-2025 holdout |
| 4. UI MVP | Streamlit 4 pages, tooltips wired, runs locally |
| 5. Daily use | Amit uses 4 weeks, friction logged, iterate |

---

## Folder layout

```
breakout-lab/
├── CLAUDE.md          ← you are here
├── STATUS.md          ← phase + decision log
├── TODOS.md           ← priority queue
├── DESIGN.md          ← UI spec
├── data/              ← OHLCV parquet cache, universe CSVs
├── analytics/         ← volume profile + breakout detector
├── backtest/          ← strategy code, runner, metrics (adapted from momentum-dashboard)
├── deals/             ← bulk + block deals scraper + SQLite store
├── reports/           ← markdown reports + PNG outputs
├── dashboard/         ← Streamlit (Phase 4+)
└── docs/              ← long specs, runbooks, architecture
```

---

## Reuse from sister projects (don't rebuild the tyre)

| From | What we lift |
|---|---|
| `momentum-dashboard/data/fetch_yfinance.py` | OHLCV fetcher, parquet cache, resumable, Bhavcopy cross-check |
| `momentum-dashboard/backtest/{data,simulator,metrics,report}.py` | Backtest engine — adapt monthly-rebalance → event-based breakout |
| `momentum-dashboard/data/universe_nifty200.csv` | Starting universe; extend to all 1000Cr+ |
| `momentum-dashboard/DESIGN.md` aesthetic | Bloomberg-lite tokens, JetBrains Mono numbers, hover-to-explain tooltips |
| `tradescan` PDH alert logic | Reference for breakout-state detection patterns |
| `tradescan` NSE public API patterns | Reference for scraping bulk/block CSVs reliably |

---

## Skill routing

See `~/Desktop/Claude/MD Files/RULES.md` §Skill Routing for the canonical table. No project-specific overrides.
