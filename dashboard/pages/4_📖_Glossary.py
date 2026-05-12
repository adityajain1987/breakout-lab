"""Page 4 — Glossary. Plain-English definitions of every term."""
import streamlit as st

st.set_page_config(page_title="Glossary", page_icon="📖", layout="wide")
st.title("📖 Glossary")
st.caption("Plain-English definitions of every term that appears on the dashboard. Future-Amit will forget how to read this in 3 months — this page is for him.")

st.markdown("""
### Volume Profile terms

**HVN (High-Volume Node)** — a price level where lots of trading volume occurred over the
lookback window. Often acts as support/resistance because many traders have positions
there. *Reactive context, not a forecast — the level forms because price was there, not
the other way round.*

**LVN (Low-Volume Node)** — a price level with comparatively little trading volume.
Often resolves quickly when price reaches it (no sticky inventory).

**POC (Point of Control)** — the single price bin with the most volume in the lookback
window. The "centre of gravity" — price tends to revisit it.

**Value Area (VAH / VAL)** — the range around POC that contains 70% of total volume.
VAH = upper bound, VAL = lower bound. Outside the value area = price exploring; inside =
price digesting.

**Bin width** — how finely we slice prices for the histogram. Default 0.5% × mid-price
(adapts to the stock's price level — a ₹50 stock gets ₹0.25 bins, a ₹5000 stock gets ₹25).
Selectable 0.25% (finer) or 1.0% (coarser).

---

### Breakout terms

**HVN break** — today's close crossed UP through a high-volume node from the lookback
profile, AND yesterday's close was at-or-below it. Means accumulated overhead inventory
got absorbed.

**Swing high break (20d)** — today's close exceeded the highest high of the prior 20
trading days (excluding yesterday). Short-term momentum trigger.

**Cycle high break (52w)** — today's close exceeded the highest high of the prior 252
trading days. Largest-move setup historically (Minervini-style).

**Composite score (0-100)** — weighted sum: `(0.4×HVN + 0.3×swing + 0.3×cycle) × 100`,
modulated by volume ratio, close-in-range %, and MA position. ≥70 = strong; 30-69 =
moderate; <30 = filter out.

**Volume ratio** — today's volume / mean of past 20 days' volume. > 2× = elevated
participation; < 1× = low conviction.

**Close-in-range % (CIR)** — `(close - low) / (high - low)`. Top 25% of day's range =
strong close (buyers in control); bottom 25% = weak (sellers).

---

### Risk + position-sizing terms

**ATR (Average True Range)** — a 14-day average of daily true range. Measures volatility.
Used to size stops dynamically — a volatile stock gets a wider stop than a calm one.

**R-multiple** — profit/loss in units of risk. If you risked ₹1,000 (entry to stop) and
made ₹2,500, that's +2.5R. Lets you compare trades on different stocks at different
prices apples-to-apples.

**EV per trade (R)** — Expected Value per trade = `(hit% × avg win R) - (loss% × avg loss R)`.
Above +0.2R = robust to retail noise. Below = the edge gets eaten by slippage in real life.

**1% risk per trade** — the position size formula: `qty = (1% × account) / (entry - stop)`.
Cap losses at 1% of account per trade. Win or lose, you live to trade tomorrow.

---

### Portfolio + backtest terms

**CAGR (Compound Annual Growth Rate)** — annualised return. ₹100k → ₹192k over 5 years
= 14% CAGR.

**Max drawdown** — largest peak-to-trough fall in equity, as a %. -28% means at the worst
point you were down 28% from your previous high. Drawdown psychology > drawdown math —
holding through -28% takes nerve.

**Sharpe ratio** — risk-adjusted return = `(daily return mean / daily return std) × √252`.
> 1.0 = good; > 2.0 = excellent. Mostly meaningful for long backtests; noisy on small samples.

**Hit rate** — % of trades that closed positive. Sub-50% is fine if avg win > avg loss.

**Win/loss asymmetry** — `avg win R / |avg loss R|`. > 1.0 means winning trades are
bigger than losing ones — the math that makes positive-EV strategies survive sub-50% hit
rates.

---

### Deals terms

**Bulk deal** — any single trade > 0.5% of a company's total shares. NSE publishes the
named counterparty (broker / fund / individual). T+1 disclosure.

**Block deal** — single trade ≥ ₹10Cr or 5L shares, transacted in two daily windows
(9:00-9:15 AM, 2:05-2:20 PM). NSE publishes named counterparty. T+1 disclosure.

**Cross-deal** — same broker on both sides of a bulk. Often warehouse trades or internal
rebalancing — doesn't represent real demand transfer between distinct parties.

**T+1 disclosure** — bulk and block deals are reported one trading day AFTER they happen.
For backtesting, this means deals data must be shifted forward by 1 trading day to avoid
look-ahead bias.

**"Remaining X% of volume is anonymous"** — every deals panel says this. NSE only publishes
deals that exceed the bulk/block thresholds. The other 99%+ of daily volume is anonymous —
we cannot tell who bought or sold. Anyone claiming "of today's 1L volume, 60k was FII" for
a stock without a 60k bulk deal is fabricating data.

---

### Universe + data quality terms

**1000Cr+ universe** — Indian large/mid-caps. We use the Nifty 500 constituent list as
a strict superset (smallest member is ~₹5000Cr). Filters out small-caps where micro-
structure noise dominates the breakout signal.

**Auto-adjusted price** — Yahoo Finance's `auto_adjust=True` retroactively divides
historical prices by all subsequent dividend yields and split ratios. So a stock that
paid ₹500 in dividends since 2020 will show 2020 prices ₹500 lower than what they
actually were. Today's price still matches NSE quote. Volume and percentage moves are
correct on this scale.

**Quarantine flag** — an automated data-quality check emitted a warning. Tier 1 = data
corruption risk (split anomaly, DUMMY ticker). Tier 2 = signal-distortion day (circuit
hit, F&O expiry). Tier 3 = universe-build filter (recent IPO, suspended). See the
expander on the Stock Lookup page for any flags on the current ticker.

**Sacred holdout** — a backtest data window we do NOT touch until the training data has
proven a strategy clears its gate. Once opened, the holdout cannot be re-used — using it
twice would be peeking-at-the-test, which contaminates our ability to claim "we proved
this works on unseen data."
""")
