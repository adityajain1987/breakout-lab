"""
SQLite store for quarantine flags.

Schema:
  flags(id, date, symbol, check_name, severity, tier, details, fetched_at)
  - date NULLABLE: a check that applies to a date range / all history (e.g., DUMMY ticker)
  - symbol NULLABLE: a date-level fact that applies to all tickers (e.g., F&O expiry)
  - UNIQUE(date, symbol, check_name) → re-running sweeps is idempotent

Severity vocabulary:
  'tier1' — Must-pass before analytics. Data-corruption risk.
  'tier2' — Flag don't exclude. Distorts signal on specific days.
  'tier3' — Universe-build filter. Stock-level disqualification.
  'tier4' — Tag in metadata only. Real but distinguishable.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


# Sentinel used in the symbol column when a flag is a date-level fact applying to ALL
# tickers (e.g. F&O expiry day). We avoid NULL because SQLite UNIQUE constraints treat
# NULL as distinct, which would let date-level facts re-insert on every sweep.
ALL_SYMBOLS = "__ALL__"

SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL DEFAULT '__NA__',
    symbol      TEXT NOT NULL DEFAULT '__ALL__',
    check_name  TEXT NOT NULL,
    severity    TEXT NOT NULL,
    tier        INTEGER NOT NULL,
    details     TEXT,
    fetched_at  TEXT NOT NULL,
    UNIQUE(date, symbol, check_name)
);
CREATE INDEX IF NOT EXISTS idx_q_symbol ON flags(symbol);
CREATE INDEX IF NOT EXISTS idx_q_date ON flags(date);
CREATE INDEX IF NOT EXISTS idx_q_tier ON flags(tier);
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


def insert_flags(path: Path, flags: list[dict]) -> int:
    """Bulk insert flags. Dedupes via UNIQUE constraint. Returns count actually inserted.

    Each flag dict must have: check_name, severity, tier, details
    Optional: date (str ISO), symbol (str)

    None values for date/symbol are converted to sentinels ('__NA__' for date, '__ALL__'
    for symbol) so the UNIQUE constraint dedupes properly on re-runs (SQLite UNIQUE treats
    NULL as distinct, which would defeat dedup).
    """
    if not flags:
        return 0
    init_db(path)
    fetched_at = datetime.utcnow().isoformat(timespec="seconds")
    inserted = 0
    with sqlite3.connect(path) as conn:
        cur = conn.cursor()
        for f in flags:
            date_val = f.get("date") if f.get("date") is not None else "__NA__"
            sym_val = f.get("symbol") if f.get("symbol") is not None else ALL_SYMBOLS
            try:
                cur.execute(
                    """
                    INSERT INTO flags (date, symbol, check_name, severity, tier, details, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        date_val, sym_val,
                        f["check_name"], f["severity"], int(f["tier"]),
                        f.get("details", ""), fetched_at,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
    return inserted


def query_flags(
    path: Path,
    symbol: Optional[str] = None,
    date: Optional[str] = None,
    tier: Optional[int] = None,
    check_name: Optional[str] = None,
) -> pd.DataFrame:
    """Query flags. Symbol query also returns date-level facts (symbol='__ALL__')."""
    if not path.exists():
        return pd.DataFrame()
    sql = "SELECT date, symbol, check_name, severity, tier, details, fetched_at FROM flags WHERE 1=1"
    params: list = []
    if symbol is not None:
        # Match per-ticker flags AND date-level flags (sentinel symbol)
        sql += " AND (symbol = ? OR symbol = ?)"
        params.append(symbol)
        params.append(ALL_SYMBOLS)
    if date is not None:
        # Match date-specific flags AND ticker-level flags (sentinel date)
        sql += " AND (date = ? OR date = ?)"
        params.append(date)
        params.append("__NA__")
    if tier is not None:
        sql += " AND tier = ?"
        params.append(tier)
    if check_name is not None:
        sql += " AND check_name = ?"
        params.append(check_name)
    sql += " ORDER BY tier, date DESC, symbol"
    with sqlite3.connect(path) as conn:
        return pd.read_sql(sql, conn, params=params)


def summary(path: Path) -> pd.DataFrame:
    """Group by check_name, severity → flag counts. For 'how dirty is our data?' reports."""
    if not path.exists():
        return pd.DataFrame()
    sql = """
        SELECT check_name, tier, severity,
               COUNT(*) as n_flags,
               COUNT(DISTINCT symbol) as n_symbols,
               COUNT(DISTINCT date) as n_dates
        FROM flags
        GROUP BY check_name, tier, severity
        ORDER BY tier, n_flags DESC
    """
    with sqlite3.connect(path) as conn:
        return pd.read_sql(sql, conn)
