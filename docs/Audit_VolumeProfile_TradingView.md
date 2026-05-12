# Volume profile audit — TradingView cross-check

**Generated:** 2026-05-03T11:28:33

This audit verifies two things:
1. **Data integrity:** our parquet OHLCV matches NSE Bhavcopy (independent source) on sample dates.
2. **Volume profile correctness:** our POC / VAH / VAL values are computed correctly, with explicit instructions for manual TradingView comparison.

## Part 1 — Bhavcopy cross-check (data integrity)

Compares our parquet `close` and `volume` for 5 sample dates × 5 sample tickers against NSE's official daily Bhavcopy file.

**Key insight:** our prices are split-and-dividend adjusted (yfinance `auto_adjust=True`). Bhavcopy prices are RAW (un-adjusted). So we expect:
- Tickers with no recent corporate actions → exact match (close + volume both within 0.5%)
- Tickers with splits/dividends → close lower + volume higher in equal proportion (yfinance back-adjusts both); **turnover (close × volume) stays the same**

**Verdict rule:** if turnover differs by < 2% (allowing for small dividend rounding), the data is consistent — it's a split adjustment, not corruption. If turnover differs by > 2%, that's a real mismatch worth investigating.

| Date | Ticker | Our close | Bhavcopy close | Close Δ% | Our vol | Bhav vol | Vol Δ% | Turnover Δ% | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 2024-01-15 | RELIANCE | ₹1,383.88 | ₹2,788.25 | -50.37% | 8,610,594 | 4,305,297 | +100.00% | -0.73% | ADJUSTED |
| 2024-01-15 | MAZDOCK | ₹1,128.00 | ₹2,347.40 | -51.95% | 0 | 2,174,201 | -100.00% | -100.00% | DATA_GAP_yfinance_missed_day |
| 2024-01-15 | KOTAKBANK | ₹369.47 | ₹1,851.50 | -80.04% | 30,861,560 | 6,172,312 | +400.00% | -0.22% | ADJUSTED |
| 2024-01-15 | NESTLEIND | ₹1,244.68 | ₹2,547.55 | -51.14% | 1,787,668 | 893,834 | +100.00% | -2.28% | ADJUSTED |
| 2024-01-15 | SYNGENE | ₹731.39 | ₹734.10 | -0.37% | 1,001,921 | 1,001,921 | +0.00% | -0.37% | EXACT |
| 2024-04-15 | RELIANCE | ₹1,454.06 | ₹2,929.65 | -50.37% | 12,902,062 | 6,451,031 | +100.00% | -0.73% | ADJUSTED |
| 2024-04-15 | MAZDOCK | ₹1,056.23 | ₹2,146.60 | -50.80% | 2,462,544 | 1,231,272 | +100.00% | -1.59% | ADJUSTED |
| 2024-04-15 | KOTAKBANK | ₹358.82 | ₹1,798.15 | -80.04% | 21,599,340 | 4,319,868 | +400.00% | -0.22% | ADJUSTED |
| 2024-04-15 | NESTLEIND | ₹1,251.18 | ₹2,553.65 | -51.00% | 2,293,646 | 1,146,823 | +100.00% | -2.01% | ADJUSTED |
| 2024-04-15 | SYNGENE | ₹721.43 | ₹724.10 | -0.37% | 586,867 | 586,867 | +0.00% | -0.37% | EXACT |
| 2024-07-15 | RELIANCE | ₹1,585.49 | ₹3,194.45 | -50.37% | 5,329,688 | 2,664,844 | +100.00% | -0.73% | ADJUSTED |
| 2024-07-15 | MAZDOCK | ₹2,653.35 | ₹5,392.45 | -50.80% | 2,727,188 | 1,363,594 | +100.00% | -1.59% | ADJUSTED |
| 2024-07-15 | KOTAKBANK | ₹367.88 | ₹1,843.55 | -80.04% | 29,549,980 | 5,909,996 | +400.00% | -0.22% | ADJUSTED |
| 2024-07-15 | NESTLEIND | ₹1,276.39 | ₹2,605.10 | -51.00% | 695,922 | 347,961 | +100.00% | -2.01% | ADJUSTED |
| 2024-07-15 | SYNGENE | ₹743.11 | ₹744.55 | -0.19% | 942,601 | 942,601 | +0.00% | -0.19% | EXACT |
| 2024-10-15 | RELIANCE | ₹1,338.68 | ₹2,688.05 | -50.20% | 33,762,794 | 16,881,397 | +100.00% | -0.40% | ADJUSTED |
| 2024-10-15 | MAZDOCK | ₹2,172.86 | ₹4,403.15 | -50.65% | 10,001,486 | 5,000,743 | +100.00% | -1.30% | ADJUSTED |
| 2024-10-15 | KOTAKBANK | ₹378.60 | ₹1,895.20 | -80.02% | 21,477,465 | 4,295,493 | +400.00% | -0.12% | ADJUSTED |
| 2024-10-15 | NESTLEIND | ₹1,222.45 | ₹2,484.25 | -50.79% | 1,590,308 | 795,154 | +100.00% | -1.58% | ADJUSTED |
| 2024-10-15 | SYNGENE | ₹885.13 | ₹886.85 | -0.19% | 176,546 | 176,546 | +0.00% | -0.19% | EXACT |
| 2024-12-30 | RELIANCE | ₹1,205.88 | ₹1,210.70 | -0.40% | 8,818,766 | 8,818,766 | +0.00% | -0.40% | EXACT |
| 2024-12-30 | MAZDOCK | ₹2,252.47 | ₹2,269.05 | -0.73% | 1,907,300 | 1,907,300 | +0.00% | -0.73% | ADJUSTED |
| 2024-12-30 | KOTAKBANK | ₹347.74 | ₹1,740.70 | -80.02% | 27,513,860 | 5,502,772 | +400.00% | -0.12% | ADJUSTED |
| 2024-12-30 | NESTLEIND | ₹1,062.85 | ₹2,159.90 | -50.79% | 2,470,980 | 1,235,490 | +100.00% | -1.58% | ADJUSTED |
| 2024-12-30 | SYNGENE | ₹857.29 | ₹858.95 | -0.19% | 826,549 | 826,549 | +0.00% | -0.19% | EXACT |

**Summary:** 6 EXACT, 18 ADJUSTED (split/div, turnover preserved), 1 DATA_GAP (yfinance missed single days), 0 MISMATCH (real corruption), 0 skipped (Bhavcopy unavailable).

**Verdict:** EXACT + ADJUSTED = data integrity confirmed. DATA_GAP cases are known-issue single-day gaps where yfinance missed NSE's trading day (close carries forward, vol=0); these slip past our 5-consecutive-day suspended_period quarantine. MISMATCH > 0 = real bug.

## Part 2 — Volume profile values (for TradingView manual cross-check)

For each sample (ticker × window), we compute our POC / VAH / VAL / HVN list and print them here. Open TradingView for the same ticker × date range and configure its volume profile as below to compare.

**TradingView setup to match our settings:**
- Open the daily chart for the ticker on TradingView
- Set the visible range to match the window dates below
- Add Volume Profile → 'Visible Range' (VPVR)
- In settings, set:
  - Number of Rows: ~50 (we use ~25-100 depending on price range, default 0.5% of mid-price)
  - Value Area Volume: 70%
  - Volume Type: Total Volume
- Compare the displayed POC, VAH, VAL to our values below

**Expected tolerance:** POC within 1-2 bins (our binning uses % of mid-price; TradingView uses fixed price spacing). Value area boundaries within similar tolerance. If POC differs by > 5 bins, that's worth investigating.

### RELIANCE 2024 calendar year

- Window: `2024-01-01` → `2024-12-31` (246 trading days)
- Bins: 58 bins of width ₹6.9843 each
- **POC:** ₹1458.63
- **VAH:** ₹1514.50
- **VAL:** ₹1339.90
- **HVNs (1):** ₹1458.63
- **LVNs (0):** (none)
- Total volume in window: 3,258,314,971 shares

### MAZDOCK 2024 (multi-bagger)

- Window: `2024-01-01` → `2024-12-31` (246 trading days)
- Bins: 100 bins of width ₹19.9998 each
- **POC:** ₹2133.41
- **VAH:** ₹2853.40
- **VAL:** ₹1553.42
- **HVNs (4):** ₹1113.42, ₹1553.42, ₹2133.41, ₹2433.41
- **LVNs (3):** ₹1273.42, ₹1813.41, ₹2353.41
- Total volume in window: 1,266,477,936 shares

### KOTAKBANK 2024

- Window: `2024-01-01` → `2024-12-31` (246 trading days)
- Bins: 46 bins of width ₹1.7401 each
- **POC:** ₹355.93
- **VAH:** ₹368.11
- **VAL:** ₹338.53
- **HVNs (1):** ₹354.19
- **LVNs (0):** (none)
- Total volume in window: 7,049,822,375 shares

### NESTLEIND 2024 (post 1:10 split)

- Window: `2024-01-01` → `2024-12-31` (246 trading days)
- Bins: 52 bins of width ₹6.0568 each
- **POC:** ₹1240.44
- **VAH:** ₹1301.01
- **VAL:** ₹1185.93
- **HVNs (2):** ₹1113.25, ₹1240.44
- **LVNs (1):** ₹1137.48
- Total volume in window: 472,826,484 shares

### SYNGENE last 7M

- Window: `2025-10-01` → `2026-04-30` (142 trading days)
- Bins: 100 bins of width ₹2.9895 each
- **POC:** ₹438.30
- **VAH:** ₹516.02
- **VAL:** ₹396.44
- **HVNs (3):** ₹408.40, ₹438.30, ₹453.24
- **LVNs (2):** ₹426.34, ₹450.25
- Total volume in window: 197,889,353 shares

---

## How to use this audit

1. **Trust check:** the Bhavcopy table above should be all EXACT or ADJUSTED. Any MISMATCH = real data bug, investigate immediately.
2. **Spot check (optional):** open TradingView for any one ticker × window above. Compare its visible-range volume profile POC to ours. If they're within 1-2 bins, we're aligned. If they differ a lot, the binning approach is the most likely source of difference (we use 0.5% × mid_price; TradingView uses fixed N rows spread linearly across the visible range).
3. **No expectation of pixel-perfect match:** different volume profile tools use different binning algorithms (TPO, TPO+volume, fixed range, visible range). Our implementation is the standard 'distribute daily volume uniformly across daily H-L range, accumulate into mid-price-% bins'. Reasonable tools should land in the same neighborhood; exact agreement isn't expected.

