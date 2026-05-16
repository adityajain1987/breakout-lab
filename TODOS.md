# Breakout Lab — Priority Queue

**Purpose:** Priority-tagged todos. P0 = blocking next phase gate. P1 = important but not blocking. P2 = nice-to-have / future.
**Read alongside:** `CLAUDE.md`, `STATUS.md`, `DESIGN.md`, `docs/Phase1_OfficeHours.md`
**Last updated:** 2026-05-16

---

## Operations — Daily refresh recovery ✅ SHIPPED 2026-05-16

### ~~OPS.1 — Make Bhavcopy build incremental~~ ✅ SHIPPED 2026-05-16
**What broke:** May 11 refresh hit "Too many open files" mid-Bhavcopy build → quarantine + publish failed → launchd throttled (exit 78) → May 12-15 never ran → live URL stuck at 2026-05-08 for 8 days.
**What landed:** `data/build_bhavcopy_parquets.py` `build()` rewritten to incremental-by-default. Reads newest existing parquet mtime, processes only newer raw CSVs, appends + dedupes per-ticker. New runtime: 39.4s for 5 trading days × 2,452 tickers (was ~6,718s). FD usage stays well under macOS 256 limit. `--full` flag preserved for forced rebuild.
**Verification:** end-to-end manual catch-up (6/6 steps OK), live URL serves 2026-05-15 confirmed via cache-busted curl. launchd reloaded — next scheduled run Mon May 18 4:30 PM IST.

---

## Phase 7 — Decade Breakouts ✅ SHIPPED 2026-05-13

### ~~P7.1 — Decade-breakout analytics module + tests~~ ✅ SHIPPED 2026-05-13
**What landed:** `analytics/decade_breakouts.py` (pure function + `DecadeBreakoutState` dataclass) + `analytics/test_decade_breakouts.py` (14/14 passing). Definition: `H_old = max(intraday High) > 10y ago`, `H_recent = max(High) in last 10y excluding today`, eligible iff `H_recent < H_old` AND `close ≥ H_old × (1 − proximity_pct/100)`. Status flags: "Approaching" (close < H_old) and "Broke today" (close ≥ H_old, first time in 10+ years).

### ~~P7.2 — Universe scan + Streamlit page~~ ✅ SHIPPED 2026-05-13
**What landed:** `analytics/scan_decade_breakouts.py` (CLI + `DecadeBreakoutScanResult` NamedTuple) + `dashboard/pages/7_🚀_Decade_Breakouts.py`. Sliders for proximity_pct (0.5-50%), lookback_years (5-20), min_history_years, sector multi-select. Click-through to Stock Lookup. Sort: "Broke today" first, then closest-gap "Approaching". Real-data hits at 10% proximity: SAIL (8.4% gap, 18.4y untouched since Dec 2007), GMRAIRPORT, J&KBANK, DLF, BAJAJFINSV, UNIONBANK — classic 2007-2010 bubble names. Full scan ~1 sec.

### ~~P7.3 — Daily refresh integration~~ ✅ SHIPPED 2026-05-13
**What landed:** `step_decade_breakouts_scan()` added to `daily_refresh.py`, runs after quarantine sweep. Two passes (2% strict + 10% wide); persists `data/decade_breakouts_latest.parquet`. Failure visible in daily summary.

### P7.future — Click-through chart overlay (DEFERRED — nice-to-have)
**What's missing:** when you click a row in Page 7, it jumps to Stock Lookup but does not draw a horizontal line at `H_old` on the chart. Same pattern as the existing `show_range_bands` session-state flag for the Range Scanner — add `show_decade_high` flag + render H_old via `add_hline()`. Estimate ~20 min.

### P7.future — Backtest the decade-breakout setup (DEFERRED)
**Why:** Phase 7 currently makes no claim about whether trading these has historical edge — the page disclaimer is explicit about this. A small backtest (entry: close ≥ H_old after ≥ 10y untouched; exit: 1R stop / 2R-3R target / time stop 60-90 days) on the 2014-2023 universe would tell us whether the setup deserves more than research-context framing. Same holdout discipline as Phase 3.

---

## P0 — Phase 1: Data layer

### ~~P0.0 — Sample-data prefetch (5 tickers)~~ ✅ SHIPPED 2026-05-01
**What landed:** `data/fetch_samples.py` + 6 parquets in `data/ohlcv/` (RELIANCE 2304, NESTLEIND 2304, KOTAKBANK 2304, MAZDOCK 1373, GROWW 115, _NSEI 2299). Env setup at `.venv/`. NESTLEIND split verified, GROWW partial-history exclusion case verified. See `STATUS.md` decision log 2026-05-01 for full notes.

### ~~P0.1 — 1000Cr+ universe builder~~ ✅ SHIPPED 2026-05-01
**What landed:** `data/build_universe.py` — pulls Nifty 500 list from NSE archives (`https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv`), filters to EQ-series, outputs `data/universe_1000cr.csv` (503 tickers) + dated snapshot `data/universe_history/2026-05-01.csv`.
**Design simplification:** Used Nifty 500 list AS the universe instead of computing mcap from Bhavcopy × shares outstanding. Justification: smallest current Nifty 500 stock is well above ₹5,000Cr — strict superset of "1000Cr+". Saved ~2 hours of mcap-computation plumbing with zero accuracy loss.
**Sector coverage:** Financial Services 101, Capital Goods 62, Healthcare 49, Auto 37, Consumer Services 29, FMCG 28, IT 27, Chemicals 26, Metals 20, Energy 19 — typical Indian large/mid-cap distribution.

### ~~P0.2 — Extend yfinance fetcher to 1000Cr+ universe~~ ✅ SHIPPED 2026-05-01
**What landed:** `data/fetch_universe.py` — generalisation of `fetch_samples.py`, reads `data/universe_1000cr.csv` and pulls every ticker. Resumable (skips existing parquets), logs to `data/fetch_universe_log.csv`. Run via background process.
**Bhavcopy cross-check:** deferred to P0.7 — momentum-dashboard already validated yfinance against Bhavcopy on the overlapping 200-ticker NIFTY 200 set with 78% exact + remaining as clean splits. We're trusting that result for now; will spot-check on the ~300 new tickers in P0.7.
**Known failures:** DUMMYVEDL4 (Vedanta demerger artifact — same as momentum-dashboard saw). Filter at universe-build stage in next refresh.

### ~~P0.3a — Bulk + block deals scraper (forward-only)~~ ✅ SHIPPED 2026-05-01
**What landed:** `deals/scraper.py` (static archive fetcher) + `deals/store.py` (SQLite, dedupe-on-insert, T+1 shift helper, `disclosed_volume_pct` math) + `deals/test_store.py` (12/12 passing). Daily cron-style polling. SYNGENE demo: 5.6% disclosed across 22-day window with the honest "Remaining 94.4% is anonymous" label. See `STATUS.md` decision log 2026-05-01 for full notes.

### P0.3b — Historical deals backfill (DEFERRED — needs alternative approach)
**Why deferred:** NSE's `/api/historical/cm/equity/bulk-deals` JSON endpoint is broken (returns 404 even with full Chrome TLS impersonation via curl_cffi; same issue via the maintained `nsepython` library). NSE either moved or removed this API.
**Options to investigate (not blocking Phase 1):**
- Scrape the JS-rendered deals report page via Playwright (heavy, fragile)
- Manual download from NSE UI's CSV export → bulk import script (one-time effort, gives complete history)
- Paid feed (NSEpy commercial, Refinitiv) — overkill for personal-use research tool
- Wait for NSE to restore the API or find an alternate endpoint via DevTools inspection on the live UI
**Workaround in the meantime:** the daily forward-only scraper accumulates over time. After ~6 months of polling, we have a useful window for Stock Lookup demos. Backtest-time deals (the Phase 3 use case) can be filled in later when historical access is solved.
**Estimate when unblocked:** 0.5–1 day depending on chosen approach.

### ~~P0.4 — Volume profile module~~ ✅ SHIPPED 2026-05-01
**What landed:** `analytics/volume_profile.py` (core + dataclass) + `analytics/test_volume_profile.py` (13/13 passing) + `analytics/preview_profiles.py` (5 PNGs in `reports/`). Algorithm: 0.5% × mid_price binning (capped at 1 tick / 100 bins), uniform volume distribution across daily H-L range, greedy 70% value-area expansion, scipy-free HVN/LVN peak detection. RELIANCE preview manually verified. Two bugs caught + fixed by tests on first run. See `STATUS.md` decision log 2026-05-01 for full notes.
**Deferred to TradingView cross-check:** unit tests use synthetic answers + real-data sanity bounds. A spot-check against TradingView's volume profile on 1 ticker × window will confirm POC matches within 1 bin. Run when convenient — not blocking P0.5.

### ~~P0.5 — Breakout detector (3 resistance types)~~ ✅ SHIPPED 2026-05-01
**What landed:** `analytics/breakout_detector.py` (BreakoutState dataclass + breakout_state core + scan_breakouts range API) + `analytics/test_breakout_detector.py` (18/18 passing). Three resistance types (HVN/swing/cycle) each with anti-look-ahead window (excludes yesterday's bar so continuation days don't re-fire). Composite score 0-100 with tunable weights + vol/range/MA multipliers. Real-data scan: MAZDOCK 2024-2025 → 16 breakouts above score 40, top 3 perfect-100 align with known inflection points. See `STATUS.md` decision log 2026-05-01.
**Deferred to TradingView spot-check:** the "10 manually verified historical breakouts" gate from the original spec is partly met (real MAZDOCK scan + synthetic edge cases). Full spot-check against TradingView for 5 winners + 5 losers can run as a one-off audit before Phase 4 ships.

### ~~P0.6 — Historical universe builder~~ ✅ SHIPPED 2026-05-01 (with documented limitation)
**What landed:** `data/build_universe_history.py` — 76 monthly snapshots in `data/universe_history/{YYYY-MM}.csv` from 2020-01 to 2026-04. 5 unit tests passing.
**Honest scope adjustment:** NSE doesn't expose historical Nifty 500 membership at any clean URL (probed multiple patterns, all dead or returning index-level not constituent-level data). Used pragmatic approximation: today's universe filtered by tickers-with-data-in-month. Excludes recent IPOs from pre-existence months (kills worst bias). Still includes survivors that have remained in Nifty 500 throughout. Bias direction: OPTIMISTIC (failure cases under-represented). Same compromise momentum-dashboard accepted, now explicit and queryable. True point-in-time backfill = P0.6b (deferred — needs paid feed or NSE constituent-history API restoration).

### ~~P0.7 — Edge-case quarantine~~ ✅ SHIPPED 2026-05-01 (5 of 12 checks; 4 deferred with reason)
**What landed:** `quarantine/` package — `store.py` (SQLite + UNIQUE-constraint dedupe), `checks.py` (5 pure check functions), `run_sweep.py` (CLI), `test_quarantine.py` (19 tests). `quarantine.db` populated with **1,306 flags across 499 tickers**.
**Checks implemented:**
- Tier 1: `check_split_anomaly` (10 flags — VEDL/ABFRL demergers, YESBANK crisis, INDIAMART bonus all caught) | `check_dummy_ticker` (DUMMY* regex + all-zero-volume)
- Tier 2: `check_circuit_hits` (1,155 flags — heuristic: 5/10/20% move + volume drop to <50% of 20d avg) | `is_fno_expiry` helper (105 dates flagged as symbol-NULL)
- Tier 3: `check_recent_ipo` (32 flags — auto-exclude < 250 trading days) | `check_suspended_periods` (4 flags — ≥5 consecutive zero-volume days)
**Schema choice:** flags table uses NULLable `date` AND `symbol` so the same table holds ticker-level facts (DUMMY), date-level facts (F&O expiry), AND ticker × date events (circuit hits). Query returns matching flags whether input is just-symbol, just-date, or both.

### ~~P0.7c — F&O expiry NULL-symbol dedupe~~ ✅ SHIPPED 2026-05-02
**What landed:** Replaced NULL with sentinels `'__ALL__'` (symbol) and `'__NA__'` (date) in the schema with NOT NULL DEFAULT. UNIQUE constraint now properly dedupes date-level facts. Updated `query_flags` to treat sentinels as "applies to all" in joins. Re-ran sweep twice to verify: identical totals (1306 flags), no duplication on F&O expiry rows. 19/19 tests passing.

### P0.7b — Remaining quarantine checks (DEFERRED — need data we don't have yet)
- Bhavcopy holiday-gap detection — needs `data/fetch_bhavcopy.py` (separate ~0.5 day)
- Block deal time-window flag — needs intraday timestamps (NSE CSV doesn't include them; would need to scrape NSE's JS-rendered deals page)
- Index-inclusion day tags — needs NSE index reconstitution history
- Earnings day tags — needs earnings calendar source (screener.in or trendlyne)
**Estimate when unblocked:** 1 day combined.

---

## P1 — Phase 2-3: Backtest

### ~~P1.1 — Event-based backtest simulator~~ ✅ SHIPPED 2026-05-02
**What landed:** `backtest/` package (atr, simulator, metrics, report, run + CLI) + 7 unit tests including the critical `test_force_close_uses_test_window_end_not_parquet_end` (locks in the bug we caught). Cumulative 79 tests passing.
**TRAIN result:** v3 with min_score=70 = EV +0.145R (gate +0.2R). **Failed.** See STATUS decision log 2026-05-02 for full analysis.
**Two look-ahead bugs caught + fixed by test-driven build:** force_close path and data_gap path both used `df.iloc[-1]["close"]` (parquet end) instead of test-window-bounded last close.

### ~~P1.2 — TRAIN tuning (3 principled variants)~~ ✅ SHIPPED 2026-05-02
**What landed:** Tested V_premium (selective entry), V_long_hold (asymmetric exit), V_combo (both stacked) in parallel on full TRAIN 2020-2024. V_long_hold +0.258R and V_combo +0.299R both cleared the +0.2R gate. The asymmetric-exit insight (atr_stop 1.5, atr_target 5, timeout 40d) was the meaningful change — entry filter alone barely moved the needle. See STATUS decision log 2026-05-02 for full results.

### ~~P1.3 — HOLDOUT gate~~ ✅ SHIPPED 2026-05-02 — STRATEGY KILLED
**What landed:** Opened HOLDOUT (2025-01-01 → 2026-04-30) on V_combo (highest train EV). Result: **EV -0.059R, CAGR -5.88%** — strategy collapsed to negative on unseen data. Hit rate dropped from 35.9% to 29.3% (regime-dependent failure: 2025-2026 was choppier than train period). Sacred holdout has now been spent. Strategy definitively killed for auto-execution.

### P1.future — Full Bhavcopy migration (DEFERRED — needs corporate-actions handler)
**What's done:** Bhavcopy fetcher + parquet builder + cross-check shipped 2026-05-03. 1,565 trading days backfilled, 3,128 per-ticker parquets in `data/ohlcv_bhav/`. Today's prices match yfinance exactly. Delivery % feature exposed in Stock Lookup header.
**What's left:** make Bhavcopy the PRIMARY source for everything (not just delivery %). Requires:
- NSE corporate actions fetcher (`data/fetch_corp_actions.py`) — pulls split + bonus + special-dividend history from `https://nsearchives.nseindia.com/content/equities/CA_LAST_24_MONTHS.csv` and predecessor archives
- Backward adjustment math: apply split + bonus ratios to historical Bhavcopy prices so they're comparable for backtest
- Switch `analytics/scan_universe.py`, `backtest/simulator.py`, etc. to read from `data/ohlcv_bhav/` instead of `data/ohlcv/`
- Delete or archive the yfinance parquets after verification
- Re-run TRAIN backtest to verify identical EV (sanity check that the migration didn't break anything)
**Estimate:** 1-2 days focused work.
**Why not now:** today's analytics + dashboard work correctly with the current setup. The Bhavcopy migration is a code-quality + data-integrity upgrade, not a user-facing feature gap. Ship the dashboard to Amit first, gather Phase 5 friction feedback, THEN decide if the migration is worth the time.

### P1.future — Strategy redesign (DEFERRED — needs fundamentally new signal + new holdout)
**Why deferred:** Holdout 2025-2026 is spent per discipline. Closure test (V_combo + regime filter on spent holdout) DISPROVED the obvious "fix" — adding Nifty 200-DMA regime filter made EV WORSE not better (-0.097R vs -0.059R). The breakout signal itself isn't predictive in this window, even in confirmed bull regimes. **Parameter tweaks won't save this strategy family.**
**Hypotheses worth testing (each requires fresh code + fresh holdout):**
- Multi-day breakout confirmation (wait 2-3 days, enter on pullback)
- Sector RS filter (only in leading sectors)
- Cross-asset confirmation (breakout + positive earnings revisions / FII inflow)
- Mean-reversion variant (fade extreme moves)
- Ensemble (multiple independent signals must align)
**Validation workflow:** code the new signal → backtest TRAIN (2020-2024) → if passes 0.2R gate → paper-trade fresh window (2026-05 onwards) → if positive 4-12 weeks → scale gradually. Backtest Playground in dashboard remains the right tool for the TRAIN phase.

### P1.2 — Backtest run on TRAIN window (2018-2023)
**Scope:**
- Run breakout strategy on training universe-history
- Report EV per trade in R, CAGR, max DD, hit rate, avg win/loss
- Sanity checks (look-ahead, math reconciliation, behaviour through 2018 mid-cap rout / 2020 COVID / 2022 inflation)
- Tune ONE filter at a time on train (don't curve-fit)
**Estimate:** 0.5 day after P1.1.

### P1.3 — Backtest gate on HOLDOUT (2024-2025)
**Why:** Sacred holdout. Same discipline as momentum-dashboard.
**Scope:**
- Open holdout via `--open-holdout` CLI flag (accidental peeking impossible)
- Gate: EV > 0.2R after costs
- Below = kill or rework
**Estimate:** 0.5 day after P1.2.

---

## P1 — Phase 4: UI MVP

### ~~P1.4 + P1.5 + P1.6 + P1.7 — Streamlit MVP (all 4 pages)~~ ✅ SHIPPED 2026-05-02
**What landed:** `dashboard/` package — `app.py` (landing) + 4 pages in `dashboard/pages/`:
- `1_📊_Stock_Lookup.py` — header strip + plotly candlestick+VP subplot + honest deals banner + deals table + breakout state card + quarantine flags expander; sidebar controls for ticker / lookback / bin width
- `2_🔍_Breakouts_Today.py` — sortable scan with ProgressColumn for CIR% and Score; full filter sidebar; 4-metric summary; cached on inputs
- `3_🧪_Backtest_Playground.py` — two tabs: canned TRAIN results (table + v3 equity curve + full markdown report) AND custom 90d-max interactive backtest with all params + holdout protection
- `4_📖_Glossary.py` — every term defined in plain English

`.streamlit/config.toml` themes the app to DESIGN.md Bloomberg-lite tokens. Plotly added to requirements (6.7.0). All pages HTTP 200 + import smoke OK. **Total time ~70 min vs estimated 4.5 days.**

**Run:** `cd ~/Desktop/Claude/breakout-lab && .venv/bin/streamlit run dashboard/app.py`

### ~~P1.4 — Streamlit shell + Stock Lookup page~~ (subsumed above)
**Scope:**
- Page 1: Stock Lookup — ticker search → price chart + volume profile (right-side histogram) + deals table + key stats
- Wire 6M default lookback selector (1W / 1M / 3M / 6M / 1Y / 2Y / 5Y)
- Wire bin-width selector (0.25% / 0.5% / 1.0% of mid-price) per P0.4
- **Disclosed-deals label format:** "Disclosed: X.X% of volume (Y of Z trading days, last [window])." If window < 1M, append "Switch to longer view for stable rate." Always visible in `flag` colour, non-dismissable.
- Stock Lookup accepts ANY NSE ticker with parquet data, not just 1000Cr+ (per Premise C resolution)
- Tooltips on every number (per DESIGN.md)
**Estimate:** 1.5 days.

### P1.5 — Breakouts Today page
**Scope:**
- Page 2: scan all 1000Cr+ universe at EOD → list breakout candidates with score
- Filters: above 50/200-DMA, volume ratio > 2x, close in top 25% of range, sector multi-select
- Click row → drill into Stock Lookup
**Estimate:** 1 day.

### P1.6 — Backtest Playground page
**Scope:**
- Page 3: tweak breakout thresholds (volume ratio, range %, MA filter, ATR stop/target multiples), see equity curve + EV/CAGR/DD update
- Read-only on holdout window (locked until Phase 3 gate passes)
- Save named configs to disk
**Estimate:** 1.5 days.

### P1.7 — Glossary page
**Scope:**
- Page 4: plain-English definitions for HVN, LVN, POC, VAH/VAL, breakout, R, EV, ATR, bulk deal, block deal, etc.
- Alphabetical, one paragraph each
**Estimate:** 0.5 day.

---

## P2 — Future / nice-to-have

### P2.1 — ATR-based suggested SL/target levels (descriptive only)
**Why:** Common question "where do I put stop?" — give the volatility-scaled math, not a recommendation.
**Scope:** On Stock Lookup, show *"If you entered here: 2×ATR stop = ₹X, 4×ATR target = ₹Y. (This is volatility math, not a recommendation.)"*

### P2.2 — Sector tagging + relative-strength overlay
**Why:** Breakouts in trending sectors win more.
**Scope:** Tag each ticker with NSE sector. On breakout scan, show sector RS vs Nifty.

### P2.3 — Options OI overlay (only if Amit asks)
**Why:** Max-pain and OI clusters are alternative resistance signals.
**Scope:** NSE option chain CSV for F&O stocks → overlay OI on price chart.

### P2.4 — Earnings-day flag
**Why:** Earnings breakouts are a different setup (event-driven, not technical).
**Scope:** Mark earnings dates on chart, flag in breakouts scan.

### P2.5 — Live intraday mode
**Why:** Currently EOD-only. If Amit wants intraday breakout alerts, plug into TradeScan WebSocket feed.
**Scope:** Reuse `tradescan` Kite Connect WebSocket. Phase 5+ only — don't build until daily-use phase proves the EOD version is sticky.

### P2.6 — Watchlist + notes journal
**Scope:** SQLite-backed watchlist, free-text notes per stock per date, tag breakouts Amit took action on (paper or real). Closes the learning loop.

---

## P6 — Range Scanner (planned 2026-05-12, plan-eng-review locked)

**What:** Add a sixth dashboard page that scans the Nifty 500 daily for **horizontal trading ranges** (rectangle patterns) lasting ≥ 9 months. Companion to "Breakouts Today" — same data, opposite lens (stocks that AREN'T breaking out, that are still oscillating between R and S).

**Why now:** Post-strategy-kill pivot. The dashboard's value is "research context, decide yourself." A Range Scanner is exactly that — surfaces structural setups without making buy/sell calls.

**Locked design (from plan-eng-review):**
- **Algorithm = Option G + D** — fast pre-filter (fractal swing pivots, scipy-free pandas) then volume-profile cross-check ONLY on candidates (~50 stocks). Graded 4-rank star scoring + 💰 round-number icon.
- **Star ranks:** ★ peaks line up · ★★ + volume node confirms · ★★★ + touches spread ≥9mo · ★★★★ + role reversal (R and S over time)
- **Tolerance:** ATR-normalized per stock, default 1.5×ATR, slider in UI (Tight 0.5× / Medium 1.5× / Loose 3×). ATR excludes circuit-hit days (per quarantine flags).
- **Width filter:** ≥ 1.5× annualized volatility (vol-normalized, not fixed %).
- **Maturity tag:** 9–12m (Emerging) / 12–24m (Established) / 24+m (Major base).
- **Status:** In-Range (close inside band) / Recent Breakout (close above R-band or below S-band in last 10 trading days). Text column, **no colored sticker, no ⚡ agreement icon** — neutral framing.
- **Right-edge handling:** centered fractal for historical pivots; one-sided look-back for the most recent 10 days (avoids the future-bar dependency).
- **Quarantine:** ⚠️ icon for flagged stocks (recent IPO, frequent circuit, suspended). Tier 1 split-anomaly flag inside lookback = range INVALIDATED.
- **Chart:** click row → existing Stock Lookup page with Plotly `add_hrect()` shaded R/S bands overlaid on candlestick + volume profile subplot.
- **Performance:** soft warning logged when scan > 30 sec (no build failure).
- **Disclaimer footer:** persistent on page — *"This shows what happened. It does NOT tell you what to do. No backtest validation yet."*

**Tickets:**

### P6.1 — `analytics/range_detector.py`
**Scope:** Pure functions returning `RangeState` dataclass. Implements: swing pivot detection (centered + one-sided), ATR-tolerance clustering, range pairing (R above S, concurrent in time, ≥3 touches each), vol-normalized width filter, volume-profile cross-check, 4-rank star scoring + 💰 icon, recent-breakout detection, maturity tag, Tier 1 quarantine invalidation, ATR cleanup excluding circuit days. Opens with ASCII pipeline diagram comment. ~350 LOC.
**Estimate:** 90 min.

### P6.2 — `analytics/test_range_detector.py`
**Scope:** ~28 unit tests covering each algorithm stage (synthetic data) + 5 real-data sanity tests (Mahindra/ITC/Bajaj Auto must detect; MAZDOCK/Reliance must NOT detect). Anti-look-ahead invariant test (critical). ~400 LOC.
**Estimate:** 60 min.

### P6.3 — `analytics/scan_ranges.py` + `analytics/test_scan_ranges.py`
**Scope:** Clones `analytics/scan_universe.py` pattern — full-universe scan returning `RangeScanResult` NamedTuple. Reuses `_resolve_parquet`. Filters: min stars, in-range vs breakout vs both, maturity multi-select, sector. CLI for manual runs. Soft 30s warning. ~150 + 200 LOC.
**Estimate:** 45 min.

### P6.4 — `dashboard/pages/6_📐_Range_Scanner.py`
**Scope:** Streamlit page. Sidebar: duration slider (9m default), tolerance preset (Tight/Medium/Loose), maturity multi-select, sector filter, status filter (All / In-Range / Recent Breakout), min stars. Main: 4-metric header, sortable table with `ProgressColumn` for stars, click-through to Stock Lookup. Persistent disclaimer footer. ~200 LOC.
**Estimate:** 60 min.

### P6.5 — Extend `dashboard/pages/1_📊_Stock_Lookup.py`
**Scope:** When a range is detected for the current ticker/asof, overlay shaded R/S bands on the existing candlestick subplot via `fig.add_hrect()`. Toggle in sidebar to show/hide. Falls back gracefully if no range detected.
**Estimate:** 30 min.

### P6.6 — Smoke tests + final verification
**Scope:** Run pytest across all packages (target: 91 + ~33 new = ~124 passing). Import-smoke each dashboard page. Run scan_ranges CLI on real universe and verify ~30-60 results.
**Estimate:** 15 min.

### P6.future — Earnings-day flag (DEFERRED — blocked on P0.7b)
Earnings days inside a range are structurally different from random-day touches. Flag them on the chart once P0.7b lands the earnings calendar source.

### P6.future — Range-breakout backtest (DEFERRED, optional)
If you ever want to validate the algorithm as a signal (not just research context), put it through the sacred-holdout discipline. Backtest range-breakout EV > 0.2R on 2018-2023 train, validate on a FRESH holdout (2026-05 onwards paper trading). Note: the 2025-2026 holdout is already spent.

**Phase 6 total: ~5 hours focused build time.**

---

## Estimate roll-up

- Phase 1 data layer: **~6.5 days** dev + scrape time (was 5; +0.25 P0.0, +0.25 P0.3, +0.25 P0.5, +0.5 P0.6, +0.75 P0.7)
- Phase 2 analytics: 2 days
- Phase 3 backtest: 3 days (was 2.5; +0.5 P1.1 honest re-scope)
- Phase 4 UI MVP: 4.5 days
- **Total to MVP: ~16 working days from today** (was ~14)
- Phase 5 daily use: 4 weeks of Amit using it

---

## Done

(Empty — Phase 0 only.)
