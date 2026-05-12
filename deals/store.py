"""
SQLite store for NSE bulk + block deals.

Schema baked-in dedupe via UNIQUE constraint on the natural key. Insert is idempotent —
re-running today's fetch produces zero new rows (good for cron resilience).

Functions:
  init_db(path)              — create schema if missing
  insert_deals(path, df)     — bulk insert, returns count actually inserted (after dedup)
  query_deals(path, ...)     — fetch by symbol × window
  shift_for_backtest(df)     — add `available_date` = deal_date + 1 trading day (T+1 anti-look-ahead)
  disclosed_volume_pct(deals_df, ohlcv_df, ...) — the honest-FII label math

Anti-look-ahead boundary: deals are T+1 disclosure. When joining to backtest, use
`available_date`, never `date`. Use `shift_for_backtest()` to enforce this.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,       -- YYYY-MM-DD
    symbol      TEXT NOT NULL,
    deal_type   TEXT NOT NULL,       -- 'bulk' or 'block'
    client      TEXT NOT NULL,
    side        TEXT NOT NULL,       -- 'BUY' or 'SELL'
    quantity    INTEGER NOT NULL,
    price       REAL NOT NULL,
    remarks     TEXT,
    fetched_at  TEXT NOT NULL,
    UNIQUE(date, symbol, deal_type, client, side, quantity, price)
);
CREATE INDEX IF NOT EXISTS idx_deals_symbol_date ON deals(symbol, date);
CREATE INDEX IF NOT EXISTS idx_deals_date ON deals(date);
"""


def init_db(path: Path) -> None:
    """Create schema if not present. Idempotent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


def insert_deals(path: Path, df: pd.DataFrame) -> int:
    """
    Insert deal rows. Dedupe via UNIQUE constraint — rows already present are silently skipped.
    Returns the count actually inserted (excludes duplicates).

    df must have columns: date, symbol, deal_type, client, side, quantity, price, remarks
    `date` must be ISO YYYY-MM-DD string.
    """
    if df.empty:
        return 0
    init_db(path)
    fetched_at = datetime.utcnow().isoformat(timespec="seconds")
    inserted = 0
    with sqlite3.connect(path) as conn:
        cur = conn.cursor()
        for _, row in df.iterrows():
            try:
                cur.execute(
                    """
                    INSERT INTO deals (date, symbol, deal_type, client, side, quantity, price, remarks, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["date"], row["symbol"], row["deal_type"], row["client"],
                        row["side"], int(row["quantity"]), float(row["price"]),
                        row.get("remarks", ""), fetched_at,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass  # duplicate — UNIQUE constraint kicked in
        conn.commit()
    return inserted


def query_deals(
    path: Path,
    symbol: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    deal_type: Optional[str] = None,
) -> pd.DataFrame:
    """Query deals filtered by symbol, date range, deal_type."""
    if not path.exists():
        return pd.DataFrame()
    sql = "SELECT date, symbol, deal_type, client, side, quantity, price, remarks FROM deals WHERE 1=1"
    params: list = []
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol)
    if start:
        sql += " AND date >= ?"
        params.append(start)
    if end:
        sql += " AND date <= ?"
        params.append(end)
    if deal_type:
        sql += " AND deal_type = ?"
        params.append(deal_type)
    sql += " ORDER BY date DESC, symbol, deal_type, side"
    with sqlite3.connect(path) as conn:
        df = pd.read_sql(sql, conn, params=params, parse_dates=["date"])
    return df


def shift_for_backtest(deals_df: pd.DataFrame, trading_calendar: Optional[pd.DatetimeIndex] = None) -> pd.DataFrame:
    """
    Add `available_date` column = deal_date + 1 trading day.

    Bulk + block deals are disclosed T+1, so a backtest "running on date D" must only see
    deals with available_date <= D. Use this column in any join to OHLCV history.

    If trading_calendar (a DatetimeIndex of NSE trading days) is provided, uses that for
    accuracy. Otherwise falls back to pandas BusinessDay (Sat/Sun off; ignores NSE holidays
    — close enough for Phase 1; refine when P0.7 holiday calendar lands).
    """
    out = deals_df.copy()
    if "date" not in out.columns:
        raise ValueError("deals_df missing 'date' column")
    deal_dates = pd.to_datetime(out["date"])

    if trading_calendar is not None:
        cal = pd.DatetimeIndex(trading_calendar).sort_values()
        # For each deal date, find the next trading day strictly after it
        idxs = cal.searchsorted(deal_dates.values, side="right")
        idxs = idxs.clip(max=len(cal) - 1)
        out["available_date"] = cal[idxs]
    else:
        out["available_date"] = deal_dates + pd.tseries.offsets.BDay(1)

    return out


def disclosed_volume_pct(
    deals_df: pd.DataFrame,
    ohlcv_df: pd.DataFrame,
) -> dict:
    """
    Per office-hours Item 6: the honest deals math (revised after seeing real NSE data).

    NSE publishes ONE row per counterparty that exceeded the threshold. So:
      - Some bulk deals appear as 1 row (only one side qualified)
      - Some appear as 2 rows (both sides qualified — same underlying transaction)
      - Block deals: 1 row each

    Correct dedupe rule: a unique transaction = (date, symbol, quantity, price).
    Sum qty over deduplicated transactions. This counts each TRADE once,
    regardless of how many counterparties NSE chose to disclose.

    Returns dict with:
      disclosed_qty       — sum of unique-transaction quantities in window
      total_qty           — sum of OHLCV volume in window
      pct                 — disclosed_qty / total_qty
      n_disclosed_days    — distinct dates with at least one disclosure
      n_total_days        — trading days in window
      n_unique_deals      — count of unique transactions (after dedupe)
      n_raw_rows          — count of raw NSE-disclosed rows (before dedupe)
      label               — human-readable string for the UI deals panel
    """
    window_start = ohlcv_df.index.min()
    window_end = ohlcv_df.index.max()
    total_qty = int(ohlcv_df["volume"].sum())
    n_total_days = int(len(ohlcv_df))

    short_window_warning = " Switch to longer view for stable rate." if n_total_days < 22 else ""

    if deals_df.empty:
        return {
            "disclosed_qty": 0,
            "total_qty": total_qty,
            "pct": 0.0,
            "n_disclosed_days": 0,
            "n_total_days": n_total_days,
            "n_unique_deals": 0,
            "n_raw_rows": 0,
            "label": (
                f"Disclosed: 0.0% of volume (0 of {n_total_days} trading days, "
                f"{window_start.date()} → {window_end.date()}). "
                f"Remaining 100.0% of volume in this window is anonymous "
                f"(NSE does not publish per-stock FII flow).{short_window_warning}"
            ),
            "window_start": window_start,
            "window_end": window_end,
        }

    # Filter deals to the OHLCV window
    deal_dates = pd.to_datetime(deals_df["date"])
    in_window = (deal_dates >= window_start) & (deal_dates <= window_end)
    in_win = deals_df[in_window]

    # Dedupe: a transaction is uniquely identified by (date, symbol, qty, price).
    # Same (qty, price) on the same day = same trade reported from buy AND sell side.
    unique = in_win.drop_duplicates(subset=["date", "symbol", "quantity", "price"])
    disclosed_qty = int(unique["quantity"].sum())
    n_disclosed_days = int(pd.to_datetime(unique["date"]).dt.date.nunique())

    pct = (disclosed_qty / total_qty) if total_qty > 0 else 0.0
    anon_pct = 1.0 - pct

    label = (
        f"Disclosed: {pct * 100:.1f}% of volume "
        f"({n_disclosed_days} of {n_total_days} trading days, "
        f"{window_start.date()} → {window_end.date()}). "
        f"Remaining {anon_pct * 100:.1f}% of volume in this window is anonymous "
        f"(NSE does not publish per-stock FII flow).{short_window_warning}"
    )

    return {
        "disclosed_qty": disclosed_qty,
        "total_qty": total_qty,
        "pct": pct,
        "n_disclosed_days": n_disclosed_days,
        "n_total_days": n_total_days,
        "n_unique_deals": len(unique),
        "n_raw_rows": len(in_win),
        "label": label,
        "window_start": window_start,
        "window_end": window_end,
    }
