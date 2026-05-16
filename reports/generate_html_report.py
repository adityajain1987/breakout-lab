"""
Self-contained HTML daily report — for sharing with Amit (or saving to disk).

Renders:
  1. Header: project name, asof date, key universe stats
  2. Top breakouts today: full table with score / vol / CIR / MA filters
  3. Featured stock lookups: top 3 breakouts get a mini Stock Lookup section each
       (price chart + volume profile + breakout state + deals if any)
  4. Honest disclaimers: SEBI, "decide yourself", "not a recommendation"

Output: reports/breakout_lab_{YYYY-MM-DD}.html
  - Single self-contained file (Plotly inline, no CDN dependency)
  - No Python / Streamlit needed to view — open in any browser
  - Easy to email / WhatsApp / file-share
  - Stays out of SEBI gray zone IF labelled as personal-use research notes

Run: .venv/bin/python -m reports.generate_html_report
     .venv/bin/python -m reports.generate_html_report --asof 2026-04-30 --top 5
     .venv/bin/python -m reports.generate_html_report --no-features  # table only, no featured lookups
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analytics.scan_universe import scan_universe, _resolve_parquet  # noqa: E402
from analytics.scan_ranges import scan_ranges  # noqa: E402
from analytics.scan_decade_breakouts import scan_decade_breakouts  # noqa: E402
from analytics.volume_profile import volume_profile  # noqa: E402
from analytics.breakout_detector import breakout_state  # noqa: E402
from deals.store import query_deals, disclosed_volume_pct  # noqa: E402

OHLCV_DIR = ROOT / "data" / "ohlcv"
UNIVERSE_CSV = ROOT / "data" / "universe_1000cr.csv"
DEALS_DB = ROOT / "deals" / "deals.db"
REPORTS_DIR = ROOT / "reports"

COLORS = {
    "bg":             "#0a0e14",
    "surface":        "#11161d",
    "border":         "#1f2630",
    "text_primary":   "#e6edf3",
    "text_secondary": "#8b949e",
    "accent":         "#00d68f",
    "warn":           "#ff3d71",
    "flag":           "#ffaa00",
}


def latest_trading_day() -> pd.Timestamp:
    """Return the most recent trading day available across BOTH data sources.
    yfinance India lags 1-3 days; Bhavcopy is same-day. Use the freshest."""
    candidates = []
    yf_nsei = OHLCV_DIR / "_NSEI.parquet"
    if yf_nsei.exists():
        candidates.append(pd.read_parquet(yf_nsei, columns=["close"]).index[-1])
    bhav_dir = ROOT / "data" / "ohlcv_bhav"
    if bhav_dir.exists():
        # Use any large-cap with reliable daily data (BHEL has been verified through today)
        for sentinel in ("RELIANCE", "BHEL", "TCS", "INFY"):
            p = bhav_dir / f"{sentinel}.parquet"
            if p.exists():
                candidates.append(pd.read_parquet(p, columns=["close"]).index[-1])
                break
    if not candidates:
        raise RuntimeError("No data sources available to determine latest trading day")
    return max(candidates)


def _score_color(score: float) -> str:
    if score >= 70: return COLORS["accent"]
    if score >= 30: return COLORS["flag"]
    return COLORS["text_secondary"]


def _build_stock_lookup_block(ticker: str, asof: pd.Timestamp, lookback_days: int = 180) -> str:
    """Return HTML block for one featured stock — chart + key stats + deals snippet.

    Prefers Bhavcopy parquet (always same-day) when available, falls back to yfinance.
    Mirrors scan_universe's `_resolve_parquet` logic — without this the whole block
    silently degrades to a "No bar" placeholder when yfinance India is laggy.
    """
    # Use the SAME resolver scan_universe uses, otherwise the featured-card score
    # (computed here) can disagree with the table score (computed by scan_universe).
    # ApolloHosp on 2026-05-08 was 100 in the table (yfinance bar) vs 37 in the card
    # (bhavcopy bar) when the priorities differed.
    parquet = _resolve_parquet(ticker, asof, OHLCV_DIR,
                               bhav_dir=ROOT / "data" / "ohlcv_bhav")
    if parquet is None or not parquet.exists():
        return f"<div class='card'>No bar for {ticker} on {asof.date()} (data lag)</div>"
    try:
        df = pd.read_parquet(parquet)
    except Exception:
        return f"<div class='card'>Cannot read parquet for {ticker}</div>"
    if asof not in df.index:
        return f"<div class='card'>No bar for {ticker} on {asof.date()} (data lag)</div>"

    window_start = asof - pd.Timedelta(days=lookback_days)
    df_window = df.loc[window_start:asof]
    if len(df_window) < 30:
        return f"<div class='card'>Insufficient window data for {ticker}</div>"

    today_close = float(df.loc[asof, "close"])
    yesterday_close = float(df["close"].iloc[df.index.get_loc(asof) - 1])
    day_change_pct = (today_close - yesterday_close) / yesterday_close * 100

    vp = volume_profile(df_window, bin_width_pct=0.005)
    bs = breakout_state(df, asof)

    deals_df = query_deals(DEALS_DB, symbol=ticker,
                           start=str(window_start.date()), end=str(asof.date()))
    label_data = disclosed_volume_pct(deals_df, df_window)

    # TradingView Lightweight Charts: candlestick with POC/VAH/VAL as priceLines.
    # The original volume-profile *histogram* sidebar is dropped — lightweight-charts
    # doesn't render horizontal volume-by-price natively. POC/VAH/VAL still convey the
    # high-volume nodes through coloured horizontal lines. The full histogram view stays
    # in the Streamlit dashboard for deep work.
    chart_html = _render_tradingview_chart(
        chart_id=f"chart_{ticker}",
        df=df_window,
        kind="candle",
        price_lines=[
            {"price": float(vp.poc), "color": COLORS["flag"], "title": f"POC ₹{vp.poc:.2f}",
             "lineWidth": 2, "lineStyle": 0},
            {"price": float(vp.vah), "color": COLORS["accent"], "title": f"VAH ₹{vp.vah:.2f}",
             "lineWidth": 1, "lineStyle": 2},
            {"price": float(vp.val), "color": COLORS["accent"], "title": f"VAL ₹{vp.val:.2f}",
             "lineWidth": 1, "lineStyle": 2},
        ],
        height=420,
    )

    # Breakout state HTML
    flags = []
    if bs.hvn_break: flags.append("HVN")
    if bs.swing_high_break: flags.append("20d SWING")
    if bs.cycle_high_break: flags.append("52w CYCLE")
    flag_text = " + ".join(flags) if flags else "no fresh breaks today"
    score_color = _score_color(bs.breakout_score)
    chg_color = COLORS["accent"] if day_change_pct >= 0 else COLORS["warn"]

    deals_html = ""
    if not deals_df.empty:
        deals_df_show = deals_df.copy()
        deals_df_show["value_cr"] = (deals_df_show["quantity"] * deals_df_show["price"] / 1e7).round(1)
        rows = []
        for _, r in deals_df_show.head(8).iterrows():
            side_color = COLORS["accent"] if r["side"] == "BUY" else COLORS["warn"]
            rows.append(f"<tr><td>{r['date']}</td><td>{r['deal_type']}</td>"
                        f"<td>{r['client'][:40]}</td>"
                        f"<td style='color:{side_color}'>{r['side']}</td>"
                        f"<td>{int(r['quantity']):,}</td>"
                        f"<td>₹{r['price']:.2f}</td><td>{r['value_cr']:.1f}</td></tr>")
        deals_html = (
            "<div class='deals-table'><h4>Disclosed deals (window)</h4>"
            "<table><thead><tr><th>Date</th><th>Type</th><th>Client</th>"
            "<th>Side</th><th>Qty</th><th>Price</th><th>₹ Cr</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )

    return f"""
<div class="featured-stock">
  <div class="featured-header">
    <div class="featured-ticker">
      <h2>{ticker}</h2>
      <div class="muted">{ticker} · {bs.asof_date.date()}</div>
    </div>
    <div class="featured-price">
      <div class="ltp">₹{today_close:,.2f}</div>
      <div class="chg" style="color:{chg_color}">{day_change_pct:+.2f}%</div>
    </div>
    <div class="featured-score" style="color:{score_color}">{bs.breakout_score:.0f}</div>
  </div>

  <div class="chart-container">
    {chart_html}
  </div>

  <div class="honest-label">
    ⚠ {label_data['label']}
  </div>

  <div class="featured-meta">
    <div class="meta-row"><strong>Breaks:</strong> {flag_text}</div>
    <div class="meta-row"><strong>Volume:</strong> {bs.volume_ratio:.2f}× the 20-day average</div>
    <div class="meta-row"><strong>Close in range:</strong> {bs.close_in_range_pct:.0%} of day's H-L</div>
    <div class="meta-row"><strong>50-DMA:</strong> {'above 🟢' if bs.above_50dma else 'below 🔴'} ·
                          <strong>200-DMA:</strong> {'above 🟢' if bs.above_200dma else 'below 🔴'}</div>
    {f"<div class='meta-row'><strong>Level broken:</strong> ₹{bs.level_broken:,.2f}</div>" if bs.level_broken else ""}
  </div>

  {deals_html}
</div>
"""


def _resolve_parquet_for_chart(ticker: str, asof: pd.Timestamp):
    """Return DataFrame with the asof bar, or None.

    Mirrors scan_universe._resolve_parquet's priority order (yfinance first because
    decade detector needs 21y depth, bhavcopy fallback for staleness). Returns the
    loaded DataFrame, not the path, so callers don't re-read.
    """
    parquet = _resolve_parquet(ticker, asof, OHLCV_DIR,
                               bhav_dir=ROOT / "data" / "ohlcv_bhav")
    if parquet is None or not parquet.exists():
        return None
    try:
        df = pd.read_parquet(parquet)
        return df if asof in df.index else None
    except Exception:
        return None


def _render_tradingview_chart(
    chart_id: str, df: pd.DataFrame, *,
    kind: str = "candle",            # "candle" or "line"
    price_lines: Optional[list[dict]] = None,
    markers: Optional[list[dict]] = None,
    height: int = 380,
) -> str:
    """Render one TradingView Lightweight Charts widget as inline HTML+JS.

    df: OHLCV with DatetimeIndex (columns: open, high, low, close required for candles;
        only close required for line).
    kind: 'candle' for candlestick, 'line' for closing-line (used on long histories
          where candles become illegible).
    price_lines: list of {price: float, color: str, title: str, lineStyle: int} dicts.
                 lineStyle 0=solid, 1=dotted, 2=dashed.
    markers: list of {time: 'YYYY-MM-DD', position: str, color: str, shape: str, text: str}
             dicts. Used to mark events on the chart (e.g. peak date).

    The library is loaded once globally via the <script> tag in the report head.
    Each chart call here is self-contained inline JS.
    """
    df = df.sort_index()
    if kind == "candle":
        data = [
            {"time": idx.strftime("%Y-%m-%d"),
             "open": round(row["open"], 4), "high": round(row["high"], 4),
             "low": round(row["low"], 4), "close": round(row["close"], 4)}
            for idx, row in df.iterrows()
        ]
    else:  # line
        data = [
            {"time": idx.strftime("%Y-%m-%d"), "value": round(row["close"], 4)}
            for idx, row in df.iterrows()
        ]

    price_lines = price_lines or []
    markers = markers or []
    data_json = json.dumps(data)
    price_lines_json = json.dumps(price_lines)
    markers_json = json.dumps(markers)

    add_series = (
        "chart.addCandlestickSeries({"
        f"upColor:'{COLORS['accent']}', downColor:'{COLORS['warn']}',"
        f"borderUpColor:'{COLORS['accent']}', borderDownColor:'{COLORS['warn']}',"
        f"wickUpColor:'{COLORS['accent']}', wickDownColor:'{COLORS['warn']}'"
        "})"
        if kind == "candle" else
        "chart.addLineSeries({"
        f"color:'{COLORS['accent']}', lineWidth:2"
        "})"
    )

    return f"""
<div id="{chart_id}" style="width:100%; height:{height}px; background:{COLORS['bg']};"></div>
<script>
(function() {{
  const el = document.getElementById('{chart_id}');
  if (!el) return;
  if (typeof LightweightCharts === 'undefined') {{
    el.innerHTML = '<div style="padding:20px;color:#ff3d71">⚠ TradingView Lightweight Charts library failed to load</div>';
    return;
  }}
  // Hidden tabs (display:none) give clientWidth=0. Fall back to a sensible default,
  // then snap to real width via ResizeObserver when the panel becomes visible.
  const initialWidth = el.clientWidth > 0 ? el.clientWidth : 800;
  let chart;
  try {{
    chart = LightweightCharts.createChart(el, {{
      width: initialWidth, height: {height},
      layout: {{ background: {{ color: '{COLORS['bg']}' }}, textColor: '{COLORS['text_primary']}' }},
      grid: {{ vertLines: {{ color: '{COLORS['border']}' }}, horzLines: {{ color: '{COLORS['border']}' }} }},
      rightPriceScale: {{ borderColor: '{COLORS['border']}' }},
      timeScale: {{ borderColor: '{COLORS['border']}', timeVisible: false, secondsVisible: false }},
      crosshair: {{ mode: 1 }},
    }});
    const series = {add_series};
    series.setData({data_json});
    const priceLines = {price_lines_json};
    priceLines.forEach(function(pl) {{
      series.createPriceLine({{
        price: pl.price, color: pl.color, lineWidth: pl.lineWidth || 1,
        lineStyle: pl.lineStyle == null ? 2 : pl.lineStyle,
        axisLabelVisible: true, title: pl.title || ''
      }});
    }});
    const markers = {markers_json};
    if (markers.length > 0) series.setMarkers(markers);
    chart.timeScale().fitContent();
  }} catch (e) {{
    console.error('Chart render failed for {chart_id}:', e);
    el.innerHTML = '<div style="padding:20px;color:#ff3d71">⚠ Chart render error: ' + e.message + '</div>';
    return;
  }}
  // Resize on window resize + when the element becomes visible (e.g. user switches tab)
  function resize() {{ if (el.clientWidth > 0) chart.applyOptions({{ width: el.clientWidth }}); }}
  window.addEventListener('resize', resize);
  if (typeof ResizeObserver !== 'undefined') {{
    new ResizeObserver(resize).observe(el);
  }}
}})();
</script>
"""


def _build_range_chart_block(row, asof: pd.Timestamp) -> str:
    """Chart for one Active Ranges row — price + dashed support/resistance lines.

    Window: 3 years (long enough to see the range form, short enough to read).
    """
    ticker = row["ticker"]
    df = _resolve_parquet_for_chart(ticker, asof)
    if df is None:
        return f"<div class='card'>No bar for {ticker} on {asof.date()}</div>"

    window_start = asof - pd.Timedelta(days=3 * 365)
    df_window = df.loc[window_start:asof]
    if len(df_window) < 60:
        return f"<div class='card'>Insufficient window for {ticker}</div>"

    today_close = float(df.loc[asof, "close"])
    yesterday_close = float(df["close"].iloc[df.index.get_loc(asof) - 1])
    day_change_pct = (today_close - yesterday_close) / yesterday_close * 100
    chg_color = COLORS["accent"] if day_change_pct >= 0 else COLORS["warn"]

    chart_html = _render_tradingview_chart(
        chart_id=f"range_{ticker}",
        df=df_window,
        kind="candle",
        price_lines=[
            {"price": float(row["resistance"]), "color": "#5b8def",
             "title": f"R ₹{row['resistance']:,.0f}", "lineWidth": 2, "lineStyle": 2},
            {"price": float(row["support"]), "color": "#5b8def",
             "title": f"S ₹{row['support']:,.0f}", "lineWidth": 2, "lineStyle": 2},
        ],
        height=380,
    )

    stars = "★" * int(row["stars"])
    status_str = row["status"]
    if row["status"] == "Recent Breakout":
        status_str = f"BO {row['breakout_direction']} ({int(row['breakout_days_ago'])}d ago)"
    width_pct = row["width_pct"]

    return f"""
<div class="featured-stock">
  <div class="featured-header">
    <div class="featured-ticker">
      <h2>{ticker}</h2>
      <div class="muted">{row['sector']} · {asof.date()}</div>
    </div>
    <div class="featured-price">
      <div class="ltp">₹{today_close:,.2f}</div>
      <div class="chg" style="color:{chg_color}">{day_change_pct:+.2f}%</div>
    </div>
    <div class="featured-score" style="color:{COLORS['flag']}; font-size:32px">{stars}</div>
  </div>
  <div class="chart-container">{chart_html}</div>
  <div class="featured-meta">
    <div class="meta-row"><strong>Range:</strong> ₹{row['support']:,.0f} – ₹{row['resistance']:,.0f}
         ({width_pct:.0f}% wide)</div>
    <div class="meta-row"><strong>Duration:</strong> {int(row['duration_days'])} days
         ({row['maturity']})</div>
    <div class="meta-row"><strong>Status:</strong> {status_str}</div>
  </div>
</div>
"""


def _build_decade_chart_block(row, asof: pd.Timestamp) -> str:
    """Chart for one Decade Breakouts row — full available history + H_old dashed line.

    Window: stock's full history (so the user can see the level has been untouched for 10+ years).
    Uses line chart (not candles) — candles are illegible on a 15-year window.
    """
    ticker = row["ticker"]
    df = _resolve_parquet_for_chart(ticker, asof)
    if df is None:
        return f"<div class='card'>No bar for {ticker} on {asof.date()}</div>"

    # Use the full history up to asof.
    df_window = df.loc[:asof]
    if len(df_window) < 60:
        return f"<div class='card'>Insufficient history for {ticker}</div>"

    today_close = float(df.loc[asof, "close"])
    yesterday_close = float(df["close"].iloc[df.index.get_loc(asof) - 1])
    day_change_pct = (today_close - yesterday_close) / yesterday_close * 100
    chg_color = COLORS["accent"] if day_change_pct >= 0 else COLORS["warn"]

    H_old = float(row["H_old"])
    H_old_date = pd.Timestamp(row["H_old_date"])

    # Line chart over full available history; H_old as a horizontal priceLine;
    # a triangle marker on the chart at H_old_date so the eye lands on the peak.
    chart_html = _render_tradingview_chart(
        chart_id=f"decade_{ticker}",
        df=df_window,
        kind="line",
        price_lines=[
            {"price": H_old, "color": "#5b8def",
             "title": f"H_old ₹{H_old:,.2f}", "lineWidth": 2, "lineStyle": 2},
        ],
        markers=[
            {"time": H_old_date.strftime("%Y-%m-%d"),
             "position": "aboveBar", "color": COLORS["flag"],
             "shape": "arrowDown", "text": f"Peak {H_old_date.date()}"},
        ],
        height=380,
    )

    gap_color = COLORS["accent"] if row["gap_pct"] < 0 else COLORS["text_primary"]
    status_html = ("🚀 Broke today" if row["status"] == "Broke today"
                   else "📍 Approaching")

    return f"""
<div class="featured-stock">
  <div class="featured-header">
    <div class="featured-ticker">
      <h2>{ticker}</h2>
      <div class="muted">{row['sector']} · {asof.date()}</div>
    </div>
    <div class="featured-price">
      <div class="ltp">₹{today_close:,.2f}</div>
      <div class="chg" style="color:{chg_color}">{day_change_pct:+.2f}%</div>
    </div>
    <div class="featured-score" style="color:{gap_color}; font-size:36px">{row['gap_pct']:+.1f}%</div>
  </div>
  <div class="chart-container">{chart_html}</div>
  <div class="featured-meta">
    <div class="meta-row"><strong>H_old:</strong> ₹{H_old:,.2f}
         (set {H_old_date.date()}, {row['H_old_age_years']:.1f} years ago)</div>
    <div class="meta-row"><strong>Gap to high:</strong>
         <span style="color:{gap_color}">{row['gap_pct']:+.1f}%</span></div>
    <div class="meta-row"><strong>Status:</strong> {status_html}</div>
  </div>
</div>
"""


def _build_breakouts_table(scan_result, latest: pd.Timestamp) -> str:
    """HTML table for the full breakouts scan."""
    df = scan_result.df
    if df.empty:
        return "<p class='muted'>No breakouts matched the filters today.</p>"

    rows = []
    for i, r in df.iterrows():
        flags = ""
        if r["hvn_break"]: flags += "H"
        if r["swing_high_break"]: flags += "S"
        if r["cycle_high_break"]: flags += "C"
        if r.get("decadal_high_break", False): flags += "D"
        flag_str = " ".join(list(flags)) if flags else "-"
        score_color = _score_color(r["breakout_score"])
        chg_color = COLORS["accent"] if r["day_change_pct"] >= 0 else COLORS["warn"]
        ma_50 = "✓" if r["above_50dma"] else "✗"
        ma_200 = "✓" if r["above_200dma"] else "✗"
        ma_50_color = COLORS["accent"] if r["above_50dma"] else COLORS["warn"]
        ma_200_color = COLORS["accent"] if r["above_200dma"] else COLORS["warn"]
        rows.append(
            f"<tr>"
            f"<td>{i+1}</td>"
            f"<td><strong>{r['ticker']}</strong></td>"
            f"<td class='muted'>{r['sector']}</td>"
            f"<td>₹{r['close']:,.2f}</td>"
            f"<td style='color:{chg_color}'>{r['day_change_pct']:+.2f}%</td>"
            f"<td>{flag_str}</td>"
            f"<td>{r['level_broken']:.0f}</td>"
            f"<td>{r['volume_ratio']:.1f}×</td>"
            f"<td>{r['close_in_range_pct']:.0%}</td>"
            f"<td style='color:{ma_50_color}'>{ma_50}</td>"
            f"<td style='color:{ma_200_color}'>{ma_200}</td>"
            f"<td style='color:{score_color}; font-weight:bold'>{r['breakout_score']:.0f}</td>"
            f"</tr>"
        )
    return (
        "<table class='breakouts-table'><thead><tr>"
        "<th>#</th><th>Ticker</th><th>Sector</th><th>LTP</th><th>Chg%</th>"
        "<th>Levels</th><th>Lvl ₹</th><th>Vol×</th><th>CIR%</th>"
        "<th>50d</th><th>200d</th><th>Score</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _build_ranges_table(ranges_result) -> str:
    """HTML table for the Range Scanner — horizontal ranges currently in play."""
    df = ranges_result.df
    if df.empty:
        return ("<p class='muted'>No qualified ranges right now. The detector needs ≥9 months of "
                "back-and-forth between two horizontal levels with ≥3 touches each.</p>")

    rows = []
    for i, r in df.iterrows():
        stars = "★" * int(r["stars"])
        status_str = r["status"]
        if r["status"] == "Recent Breakout":
            status_str = f"BO {r['breakout_direction']} ({int(r['breakout_days_ago'])}d)"
            status_color = COLORS["accent"] if r["breakout_direction"] == "up" else COLORS["warn"]
        else:
            status_color = COLORS["text_primary"]
        flags = ""
        if r.get("round_number"): flags += "💰"
        if r.get("role_reversal"): flags += "↻"
        if r.get("volume_confirmed"): flags += "📊"
        if r.get("quarantine_flag"): flags += "⚠️"
        chg_color = COLORS["accent"] if r["day_change_pct"] >= 0 else COLORS["warn"]
        rows.append(
            f"<tr>"
            f"<td>{i+1}</td>"
            f"<td><strong>{r['ticker']}</strong></td>"
            f"<td class='muted'>{r['sector']}</td>"
            f"<td>₹{r['close']:,.2f}</td>"
            f"<td style='color:{chg_color}'>{r['day_change_pct']:+.2f}%</td>"
            f"<td>₹{r['support']:,.0f} – ₹{r['resistance']:,.0f}</td>"
            f"<td>{r['width_pct']:.0f}%</td>"
            f"<td style='color:{COLORS['flag']}'>{stars}</td>"
            f"<td>{int(r['duration_days'])}d</td>"
            f"<td class='muted'>{r['maturity']}</td>"
            f"<td style='color:{status_color}'>{status_str}</td>"
            f"<td>{flags or '-'}</td>"
            f"</tr>"
        )
    return (
        "<table class='breakouts-table'><thead><tr>"
        "<th>#</th><th>Ticker</th><th>Sector</th><th>LTP</th><th>Chg%</th>"
        "<th>Range</th><th>W%</th><th>★</th><th>Dur</th><th>Maturity</th>"
        "<th>Status</th><th>Flags</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _build_decade_breakouts_table(decade_result) -> str:
    """HTML table for the Decade Breakouts watchlist."""
    df = decade_result.df
    if df.empty:
        return ("<p class='muted'>No stocks within the proximity window of a 10-year-untouched high "
                "right now. Most 2010-era decade-base setups already broke out in 2023-24.</p>")

    rows = []
    for i, r in df.iterrows():
        status = r["status"]
        if status == "Broke today":
            status_html = f"<span style='color:{COLORS['accent']}'>🚀 Broke today</span>"
        else:
            status_html = "<span class='muted'>📍 Approaching</span>"
        h_old_date = pd.Timestamp(r["H_old_date"]).strftime("%Y-%m-%d")
        gap_color = COLORS["accent"] if r["gap_pct"] < 0 else COLORS["text_primary"]
        chg_color = COLORS["accent"] if r["day_change_pct"] >= 0 else COLORS["warn"]
        rows.append(
            f"<tr>"
            f"<td>{i+1}</td>"
            f"<td><strong>{r['ticker']}</strong></td>"
            f"<td class='muted'>{r['sector']}</td>"
            f"<td>₹{r['close']:,.2f}</td>"
            f"<td style='color:{chg_color}'>{r['day_change_pct']:+.2f}%</td>"
            f"<td>₹{r['H_old']:,.2f}</td>"
            f"<td class='muted'>{h_old_date}</td>"
            f"<td>{r['H_old_age_years']:.1f}y</td>"
            f"<td style='color:{gap_color}; font-weight:bold'>{r['gap_pct']:+.1f}%</td>"
            f"<td>{status_html}</td>"
            f"</tr>"
        )
    return (
        "<table class='breakouts-table'><thead><tr>"
        "<th>#</th><th>Ticker</th><th>Sector</th><th>LTP</th><th>Chg%</th>"
        "<th>H (old)</th><th>Set on</th><th>Age</th><th>Gap</th><th>Status</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def generate(asof: pd.Timestamp, top_n: int = 20, n_features: int = 3,
             min_score: float = 30.0, min_vol: float = 1.5,
             decade_proximity_pct: float = 10.0) -> Path:
    """Generate the HTML report. Returns path to saved file.

    Three tabs:
      🔍 Today's Breakouts — full scan + featured stock cards (existing default behaviour)
      📐 Active Ranges     — Range Scanner results
      🚀 Decade Breakouts  — stocks within decade_proximity_pct% of a >10y-untouched high
    """
    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"breakout_lab_{asof.date()}.html"

    # Run the three scans
    scan_result = scan_universe(
        asof_date=asof, min_score=min_score, min_volume_ratio=min_vol,
        require_above_50dma=True, top_n=top_n,
    )
    ranges_result = scan_ranges(
        asof_date=asof, min_stars=2, status_filter="all", top_n=top_n,
    )
    decade_result = scan_decade_breakouts(
        asof_date=asof, proximity_pct=decade_proximity_pct,
        lookback_years=10, min_history_years=11, top_n=top_n,
    )

    # Featured stock lookup blocks (still only for the Breakouts tab — the headline view)
    featured_html = ""
    if not scan_result.df.empty and n_features > 0:
        for _, r in scan_result.df.head(n_features).iterrows():
            featured_html += _build_stock_lookup_block(r["ticker"], asof)

    # Read universe stats for header
    universe_count = 0
    if UNIVERSE_CSV.exists():
        universe_count = len(pd.read_csv(UNIVERSE_CSV))

    table_html = _build_breakouts_table(scan_result, asof)
    ranges_table_html = _build_ranges_table(ranges_result)
    decade_table_html = _build_decade_breakouts_table(decade_result)

    # Featured chart blocks for the Ranges and Decade tabs — top 3 each.
    ranges_featured_html = ""
    if not ranges_result.df.empty:
        for _, r in ranges_result.df.head(3).iterrows():
            ranges_featured_html += _build_range_chart_block(r, asof)

    decade_featured_html = ""
    if not decade_result.df.empty:
        # Decade hits are rare — show every eligible stock (caps at top_n above).
        for _, r in decade_result.df.iterrows():
            decade_featured_html += _build_decade_chart_block(r, asof)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Breakout Lab — {asof.date()}</title>
<script src="https://unpkg.com/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    background: {COLORS['bg']}; color: {COLORS['text_primary']};
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 24px; line-height: 1.5;
  }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  h1, h2, h3, h4 {{ color: {COLORS['text_primary']}; margin-top: 0; }}
  h1 {{ font-size: 28px; }}
  h2 {{ font-size: 22px; border-bottom: 1px solid {COLORS['border']}; padding-bottom: 8px; }}
  .muted {{ color: {COLORS['text_secondary']}; font-size: 13px; }}
  .header-strip {{
    background: {COLORS['surface']}; border: 1px solid {COLORS['border']};
    border-radius: 4px; padding: 16px 20px; margin-bottom: 24px;
    display: flex; justify-content: space-between; flex-wrap: wrap; gap: 16px;
  }}
  .header-strip .stats {{ display: flex; gap: 32px; }}
  .header-strip .stat {{ font-size: 14px; }}
  .header-strip .stat strong {{ display: block; font-size: 22px; color: {COLORS['accent']}; font-family: monospace; }}
  table {{ width: 100%; border-collapse: collapse; font-family: monospace; font-size: 13px; }}
  table th, table td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid {COLORS['border']}; }}
  table th {{ color: {COLORS['text_secondary']}; font-weight: bold; background: {COLORS['surface']}; }}
  table tr:hover {{ background: {COLORS['surface']}; }}
  .breakouts-table td:nth-child(4), .breakouts-table td:nth-child(5),
  .breakouts-table td:nth-child(7), .breakouts-table td:nth-child(8),
  .breakouts-table td:nth-child(12) {{ text-align: right; }}

  .featured-stock {{
    background: {COLORS['surface']}; border: 1px solid {COLORS['border']};
    border-radius: 6px; padding: 20px; margin: 24px 0;
  }}
  .featured-header {{
    display: flex; align-items: center; gap: 24px; margin-bottom: 16px; flex-wrap: wrap;
  }}
  .featured-ticker h2 {{ margin: 0; font-size: 24px; border: none; padding: 0; }}
  .featured-price .ltp {{ font-size: 22px; font-family: monospace; font-weight: bold; }}
  .featured-price .chg {{ font-size: 16px; font-family: monospace; }}
  .featured-score {{ font-size: 56px; font-weight: bold; font-family: monospace; margin-left: auto; }}
  .featured-meta {{ margin: 16px 0; }}
  .meta-row {{ margin: 4px 0; font-family: monospace; font-size: 13px; }}

  .honest-label {{
    background: {COLORS['surface']}; border: 2px solid {COLORS['flag']};
    border-radius: 4px; padding: 10px 14px; margin: 12px 0;
    font-size: 13px; color: {COLORS['text_primary']};
  }}

  .deals-table {{ margin-top: 16px; }}
  .deals-table h4 {{ margin: 8px 0; font-size: 14px; }}
  .deals-table table {{ font-size: 12px; }}

  .disclaimer {{
    background: {COLORS['surface']}; border: 1px solid {COLORS['border']};
    border-radius: 4px; padding: 16px; margin: 32px 0 0; font-size: 13px; color: {COLORS['text_secondary']};
  }}
  .disclaimer strong {{ color: {COLORS['flag']}; }}

  /* How-to-read expandable section */
  details.how-to-read {{
    background: {COLORS['surface']}; border: 1px solid {COLORS['flag']};
    border-radius: 6px; padding: 12px 18px; margin: 16px 0 24px;
  }}
  details.how-to-read summary {{
    cursor: pointer; color: {COLORS['flag']}; font-weight: bold; font-size: 15px;
    padding: 4px 0; user-select: none;
  }}
  details.how-to-read summary:hover {{ opacity: 0.85; }}
  .how-to-content {{ margin-top: 14px; padding-top: 12px; border-top: 1px solid {COLORS['border']};
                     color: {COLORS['text_primary']}; font-size: 14px; }}
  .how-to-content h3 {{ font-size: 16px; color: {COLORS['accent']}; margin: 16px 0 8px; }}
  .how-to-content ul, .how-to-content ol {{ padding-left: 22px; line-height: 1.7; }}
  .how-to-content li {{ margin: 6px 0; }}
  .how-to-content p {{ line-height: 1.6; margin: 8px 0; }}

  /* Pure-CSS tabs (no JS). Three hidden radios drive three tab panels. */
  .tabs {{ margin: 24px 0; }}
  .tabs input[type="radio"] {{ display: none; }}
  .tab-nav {{ display: flex; gap: 4px; border-bottom: 2px solid {COLORS['border']}; }}
  .tab-nav label {{
    padding: 12px 20px; cursor: pointer;
    color: {COLORS['text_secondary']}; font-weight: 600; font-size: 15px;
    border: 2px solid transparent; border-bottom: none;
    border-radius: 6px 6px 0 0;
    transition: color 0.15s, background 0.15s;
  }}
  .tab-nav label:hover {{ color: {COLORS['text_primary']}; background: {COLORS['surface']}; }}
  .tab-panel {{ display: none; padding: 24px 0; }}
  /* Wire radio:checked → matching panel visible + matching label active */
  #tab-breakouts:checked ~ .tab-panels #panel-breakouts,
  #tab-ranges:checked ~ .tab-panels #panel-ranges,
  #tab-decade:checked ~ .tab-panels #panel-decade {{ display: block; }}
  #tab-breakouts:checked ~ .tab-nav label[for="tab-breakouts"],
  #tab-ranges:checked ~ .tab-nav label[for="tab-ranges"],
  #tab-decade:checked ~ .tab-nav label[for="tab-decade"] {{
    color: {COLORS['accent']}; border-color: {COLORS['border']};
    border-bottom: 2px solid {COLORS['bg']}; margin-bottom: -2px;
    background: {COLORS['surface']};
  }}
  .tab-counter {{
    display: inline-block; margin-left: 6px; padding: 2px 8px;
    background: {COLORS['border']}; color: {COLORS['text_primary']};
    border-radius: 10px; font-size: 12px; font-family: monospace;
  }}
</style>
</head>
<body>
<div class="container">

  <div class="header-strip">
    <div>
      <h1 style="margin:0;">📈 Breakout Lab</h1>
      <div class="muted">Daily research notes for personal use · {asof.date()}</div>
    </div>
    <div class="stats">
      <div class="stat"><span class="muted">Universe</span><strong>{universe_count}</strong></div>
      <div class="stat"><span class="muted">Scanned</span><strong>{scan_result.n_scanned}</strong></div>
      <div class="stat"><span class="muted">Qualified</span><strong>{scan_result.n_qualified}</strong></div>
      <div class="stat"><span class="muted">Featured</span><strong>{n_features}</strong></div>
    </div>
  </div>

  <details class="how-to-read" open>
    <summary>📖 How to read this report (click to collapse)</summary>
    <div class="how-to-content">
      <h3>The breakouts table — what each column means</h3>
      <ul>
        <li><strong>Levels (H S C D)</strong> — which type of resistance broke today.
          <strong>H</strong> = High-Volume Node (where shares accumulated historically).
          <strong>S</strong> = 20-day swing high (recent peak).
          <strong>C</strong> = 52-week cycle high (yearly peak).
          <strong>D</strong> = <em>20-year decadal high</em> — rare and structurally significant (think BHEL's
          May 2026 break of its 2008 peak after 18 years). When you see D, that's a real generational move.
          More letters = stronger signal.</li>
        <li><strong>Lvl ₹</strong> — the actual price level that broke.</li>
        <li><strong>Vol×</strong> — today's volume / 20-day average. <strong>2.0× = double normal volume</strong>; higher = more conviction.</li>
        <li><strong>CIR%</strong> — "Close-In-Range" — where today's close was in the day's high-low range.
          <strong>100% = closed at day's high (very strong)</strong>; 50% = middle; 0% = weak.</li>
        <li><strong>50d / 200d</strong> — ✓ = stock above its 50-day / 200-day moving average (uptrend); ✗ = below (downtrend).</li>
        <li><strong>Score</strong> — composite 0-100 combining all of the above. <span style="color:#00d68f">≥70 strong</span>, <span style="color:#ffaa00">30-69 moderate</span>.</li>
      </ul>

      <h3>The featured stock cards — three things to read</h3>
      <ol>
        <li><strong>The price chart (left)</strong> shows recent action. The horizontal lines are POC (orange = where most volume traded), VAH/VAL (green dashed = the 70%-volume range around POC). When price breaks above POC, often runs further; when it falls below, often retests POC as new resistance.</li>
        <li><strong>Volume profile lines on the chart</strong> — orange solid <strong>POC</strong> (Point of Control, the single price where most shares changed hands), green dashed <strong>VAH/VAL</strong> (the band where ~70% of volume happened). When price approaches POC from above, often acts as support; from below, often acts as resistance.</li>
        <li>The full sideways volume-profile <em>histogram</em> lives in the Streamlit dashboard — open it locally with <code>streamlit run dashboard/app.py</code> for deeper exploration.</li>
        <li><strong>The yellow "Disclosed" banner</strong> is the honest deals label. We only count the trades NSE publicly disclosed (bulk + block deals > size threshold). Most of every stock's volume is anonymous — no one knows who bought it. Anyone claiming to know per-stock FII flow is making it up.</li>
      </ol>

      <h3>What this report is NOT</h3>
      <p>It's <strong>NOT a buy list</strong>. The composite score tells you "this setup historically hit X% of the time on past data" — useful as research context, NOT a recommendation. The auto-execution version of this strategy was <strong>backtested and FAILED</strong> the gate (EV per trade -0.059R on unseen 2025-2026 data). Use this as one data point among many for your own decisions.</p>

      <p><em>Filters used to generate this scan: min score {min_score:.0f}, min volume {min_vol:.1f}×, above 50-DMA only, Nifty 500 universe (~500 large/mid-cap NSE stocks).</em></p>
    </div>
  </details>

  <div class="tabs">
    <input type="radio" name="tab" id="tab-breakouts" checked>
    <input type="radio" name="tab" id="tab-ranges">
    <input type="radio" name="tab" id="tab-decade">

    <div class="tab-nav">
      <label for="tab-breakouts">🔍 Today's Breakouts<span class="tab-counter">{scan_result.n_qualified}</span></label>
      <label for="tab-ranges">📐 Active Ranges<span class="tab-counter">{ranges_result.n_qualified}</span></label>
      <label for="tab-decade">🚀 Decade Breakouts<span class="tab-counter">{decade_result.n_eligible}</span></label>
    </div>

    <div class="tab-panels">
      <div class="tab-panel" id="panel-breakouts">
        <h2 style="border:none; padding:0;">🔍 Today's qualified breakouts</h2>
        <p class="muted">Filters: min score {min_score:.0f} · min volume {min_vol:.1f}× · above 50-DMA · Nifty 500 universe ·
           <span style="color:#ffaa00">Don't know what these columns mean? Click "How to read this report" above ☝️</span></p>
        {table_html}

        <h2 style="margin-top: 40px;">⭐ Featured (top {n_features} by score)</h2>
        {featured_html}
      </div>

      <div class="tab-panel" id="panel-ranges">
        <h2 style="border:none; padding:0;">📐 Active horizontal ranges</h2>
        <p class="muted">Rectangle patterns lasting ≥9 months. Companion to breakouts — same data, opposite lens.
           ★ structure · ★★ +volume confirms · ★★★ +9-month spread · ★★★★ +role reversal.</p>
        {ranges_table_html}

        <h2 style="margin-top: 40px;">⭐ Featured ranges (top 3 by ★ score)</h2>
        <p class="muted">Dashed blue lines = the support (S) and resistance (R) levels.
           3-year window shows how the band has formed.</p>
        {ranges_featured_html}
      </div>

      <div class="tab-panel" id="panel-decade">
        <h2 style="border:none; padding:0;">🚀 Decade Breakouts watchlist</h2>
        <p class="muted">Stocks within <strong>{decade_proximity_pct:.0f}%</strong> of a high set <strong>>10 years ago</strong>
           and untouched (not even intraday) for the entire window. Pre-breakout shortlist — catches the setup
           <em>before</em> the level breaks, not after.</p>
        {decade_table_html}

        <h2 style="margin-top: 40px;">⭐ Featured decade-breakout charts</h2>
        <p class="muted">Full price history. Dashed blue line = the old high (H_old). Dotted orange marker = the day it was set.
           The point of the chart: see with your own eyes that price has not crossed the line in 10+ years.</p>
        {decade_featured_html}
      </div>
    </div>
  </div>

  <div class="disclaimer">
    <p><strong>READ THIS BEFORE ACTING ON ANYTHING IN THIS REPORT.</strong></p>
    <p>This is a personal-use research document. It is <strong>NOT</strong> a buy/sell signal,
    investment advice, or a recommendation to act. The composite breakout score is a
    descriptive backtest statistic — it tells you "this setup has historically hit X% of the
    time" — not what to do.</p>
    <p>This tool is <strong>NOT</strong> registered as a SEBI Research Analyst service.
    The author makes no claim of edge or predictive accuracy. The breakout-as-trigger
    auto-execution version of this strategy was tested and <strong>FAILED the holdout gate</strong>
    (EV per trade -0.059R on 2025-2026 unseen data). Use as research context for your own
    decisions only. Past performance is not predictive.</p>
    <p><strong>Honest data note:</strong> All prices are split + dividend adjusted (yfinance
    auto_adjust=True). Today's price matches NSE quote; historical prices have been back-adjusted
    and may look lower than non-adjusted charts. The "remaining X% of volume is anonymous"
    label on each deals panel is non-negotiable — NSE does not publish per-stock FII flow at
    retail level, and we will never invent numbers we cannot see.</p>
    <p class="muted" style="margin-top: 12px;">Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ·
    Breakout Lab v1 · github.com/aditya/breakout-lab (private)</p>
  </div>

</div>
</body>
</html>
"""

    out_path.write_text(html)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="YYYY-MM-DD (default: latest trading day)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--features", type=int, default=3, help="Number of featured Stock Lookup blocks")
    ap.add_argument("--min-score", type=float, default=30.0)
    ap.add_argument("--min-vol", type=float, default=1.5)
    ap.add_argument("--decade-proximity-pct", type=float, default=10.0,
                    help="Proximity window for the Decade Breakouts tab. Default 10%.")
    ap.add_argument("--no-features", action="store_true")
    args = ap.parse_args()

    asof = pd.Timestamp(args.asof) if args.asof else latest_trading_day()
    n_features = 0 if args.no_features else args.features

    print(f"Generating HTML report for asof={asof.date()}, top={args.top}, features={n_features}...")
    out = generate(asof, top_n=args.top, n_features=n_features,
                   min_score=args.min_score, min_vol=args.min_vol,
                   decade_proximity_pct=args.decade_proximity_pct)
    print(f"\n✓ Wrote: {out}")
    print(f"  Open in browser: open {out}")


if __name__ == "__main__":
    main()
