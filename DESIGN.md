# Breakout Lab — Design Spec

**Purpose:** Visual + behavioral spec for the dashboard UI. Goal-state — what we're building toward in Phase 4+. Not what exists today.
**Read alongside:** `CLAUDE.md`, `STATUS.md`, `TODOS.md`
**Last updated:** 2026-05-01

---

## Aesthetic

**Bloomberg-lite. Terminal-honest. Numbers over decoration.**

Same family as `momentum-dashboard`. Built for one user (Amit) who reads it daily and doesn't need to be sold to.

**Anti-patterns rejected:**
- Glossy gradients, glow effects, hero illustrations
- Confidence bars without sample-size disclosure
- "AI-powered" marketing copy anywhere
- Fake urgency (countdown timers, red flashing borders)
- Animations beyond fade-in on data refresh
- Anything that looks like a Chartink-style cluttered grid

---

## Visual tokens (shared with momentum-dashboard)

| Token | Value | Use |
|---|---|---|
| `bg` | `#0a0e14` | Page background |
| `surface` | `#11161d` | Card background |
| `border` | `#1f2630` | Card border, dividers |
| `text-primary` | `#e6edf3` | Numbers, headlines |
| `text-secondary` | `#8b949e` | Labels, metadata |
| `accent` | `#00d68f` | Positive (above level, gains) |
| `warn` | `#ff3d71` | Negative (below level, losses) |
| `flag` | `#ffaa00` | Caveat ("anonymous", "estimate", "earnings day") |
| `mono` | JetBrains Mono | All numbers |
| `sans` | Inter | Labels and prose |

Spacing scale: 4 / 8 / 12 / 16 / 24 / 32 px. Nothing else.
Radius: 4px on cards, 2px on inputs.

---

## Pages (Phase 4+)

### 1. Stock Lookup (home)

The heart of the app. One ticker at a time.

**Header strip:**
- Ticker | Company | LTP | day change | Mcap (Cr) | Sector | F&O Y/N | Earnings date if within 14d (`flag` colour)

**Lookback selector:** 1W · 1M · 3M · 6M (default) · 1Y · 2Y · 5Y
(Buttons, not dropdown — one-click switch.)

**Main grid:**
- LEFT (70%): candlestick price chart with daily volume bars below
- RIGHT (30%): volume profile — horizontal histogram, price-binned. POC marked. Value area shaded. HVN/LVN labelled.

**Below grid:**
- **Deals panel:** table of bulk + block deals in the lookback window. Columns: date | type (bulk/block) | client | side | qty | price | % of day's volume.
- **Always-visible label** above the table (`flag` colour, non-dismissable):
  > *"This shows only NSE-disclosed bulk + block deals (named counterparty). The remaining XX% of volume in this period is anonymous — NSE does not publish per-stock FII flow."*
- **Breakout state card:** is the stock currently breaking out? Shows volume ratio, close-in-range %, MA position, breakout score.
  - Score is descriptive: *"Score 78. Setups in this band hit target 54% historically. Sample: 312."*
  - Never *"BUY"* / *"recommended entry"*.

**Tooltips on every number** (memory aids):
- Hover *POC ₹523* → *"Point of Control. Price bin with the most traded volume in your selected window. Often acts as support / resistance."*
- Hover *Score 78* → *"Composite of volume ratio (3.2x), close in top 18% of day, above 50-DMA, above 200-DMA. Historically, scores in this band hit target 54% of the time on backtest. Sample: 312 trades."*
- Hover *Bulk deal* → *"NSE-disclosed: any trade > 0.5% of company shares. Reported T+1."*
- Hover *Volume ratio 3.2x* → *"Today's volume divided by 20-day average. > 2x = above-average participation."*

### 2. Breakouts Today

EOD scan across the full 1000Cr+ universe. Table view, sortable.

**Columns:** TICKER | sector | LTP | day change | level broken | volume ratio | close-in-range % | breakout score | above 50/200-DMA

**Default sort:** breakout score descending.

**Filters above table:**
- Sector multi-select
- Min volume ratio (default 2x)
- Min market cap (default 1000Cr)
- Above 50-DMA only (default ON)
- Above 200-DMA only (default OFF)

**Click row → opens Stock Lookup for that ticker.**

**Empty state:** *"No breakouts matching filters today. Try lowering volume ratio or removing 50-DMA filter."*

### 3. Backtest Playground

Tweak rule thresholds, see strategy results update.

**Top: rule editor**
- Min volume ratio: slider 1.5–5x
- Min close-in-range %: slider 0–100%
- MA filter: none / above 50-DMA / above 200-DMA / both
- Stop: ATR multiplier (1–3)
- Target: ATR multiplier (2–6)
- Hold timeout: 5 / 10 / 20 / 40 days
- Window: TRAIN (2018-2023) / HOLDOUT
- Costs: 0.25% (locked, not editable)

**Below: results**
- Equity curve (overlay vs Nifty 50 buy-and-hold)
- KPI strip: EV per trade (R) | CAGR | max DD | hit rate | trades | avg win/loss R
- Trade list (collapsed; expand to see all entries with date, ticker, R outcome)

**Holdout window is read-only** — show greyed-out "Locked until train passes Phase 3 gate." Once unlocked, banner: *"Holdout opened on YYYY-MM-DD. Cannot be reused for tuning."*

### 4. Glossary

Plain-English definitions, alphabetical. One paragraph each. No jargon-on-jargon.

Entries (minimum): ATR, block deal, breakout, bulk deal, CAGR, drawdown, EV, HVN, LVN, POC, R-multiple, value area, VAH/VAL, volume profile, volume ratio.

---

## User aids (memory helpers)

Same philosophy as `momentum-dashboard`. Future-Amit will forget how to read this in 3 months.

1. **Hover-to-explain on every number.** No exceptions.
2. **Honest-precision labels.** Anything not directly measured is marked: "estimate", "backtest stat", "anonymous", "T+1 disclosure".
3. **Glossary always one click away** (footer link on every page).

**Deliberately excluded:**
- No first-visit modal
- No legal / SEBI disclaimer (personal use, not shared)
- No marketing or "Get started" tutorial
- No login

---

## Behavior rules

1. **No buy/sell buttons. Ever.** Dashboard shows context; trader executes manually elsewhere.
2. **Backtest stats are descriptive, not prescriptive.** *"This setup hit 54% historically"* — not *"buy this."*
3. **Volume profile is reactive context, not forecast.** UI tooltip says so.
4. **Honest deals label is non-dismissable.** Cannot be hidden, cannot be a small-print footnote.
5. **Holdout window stays locked** until Phase 3 gate passes. Period.

---

## Interactions

Minimal. Daily-glance + occasional deep-dive. Not a workbench.

- Click ticker anywhere → opens Stock Lookup
- Click chart in Stock Lookup → opens TradingView for that ticker in new tab (sanity cross-check)
- Hover any number → tooltip
- Lookback selector buttons (no dropdown — buttons stay one-click)
- Sortable tables (no drag-drop, no inline edit)

---

## Phase 4 build path

1. Streamlit shell + Stock Lookup page (1.5 days) — get the volume profile right first
2. Breakouts Today page (1 day) — reuse breakout detector module
3. Backtest Playground page (1.5 days) — wire to backtest module
4. Glossary page (0.5 day)
5. Iterate on data correctness for 1 week before any styling polish
6. Next.js port is OPTIONAL — only if Amit uses Streamlit version daily for 4+ weeks

---

## Validation

Before shipping any UI version: run Google's `design.md` linter per `RULES.md` standing rule.

```bash
PATH="/opt/homebrew/bin:$PATH" npx -y @google/design.md lint DESIGN.md
```

Prose-only files pass with 1 warning (no YAML frontmatter). Acceptable.
