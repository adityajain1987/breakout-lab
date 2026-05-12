"""
SQLite-backed watchlist. Per-user (single user, no auth) personal saved-tickers list.

Schema:
  watchlist(symbol PRIMARY KEY, added_at, notes)

Functions:
  add(symbol, notes='')      — idempotent (UPSERT)
  remove(symbol)             — silent if absent
  list_all()                 — DataFrame[symbol, added_at, notes] sorted by added_at desc
  is_watched(symbol)         — bool
  update_notes(symbol, notes)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "watchlist" / "watchlist.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    symbol      TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT ''
);
"""


def init_db(path: Path = DEFAULT_DB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


def add(symbol: str, notes: str = "", path: Path = DEFAULT_DB) -> None:
    init_db(path)
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO watchlist (symbol, added_at, notes) VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET notes = excluded.notes
            """,
            (symbol.upper(), now, notes),
        )
        conn.commit()


def remove(symbol: str, path: Path = DEFAULT_DB) -> None:
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        conn.commit()


def is_watched(symbol: str, path: Path = DEFAULT_DB) -> bool:
    if not path.exists():
        return False
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
    return row is not None


def update_notes(symbol: str, notes: str, path: Path = DEFAULT_DB) -> None:
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE watchlist SET notes = ? WHERE symbol = ?", (notes, symbol.upper())
        )
        conn.commit()


def list_all(path: Path = DEFAULT_DB) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "added_at", "notes"])
    with sqlite3.connect(path) as conn:
        return pd.read_sql(
            "SELECT symbol, added_at, notes FROM watchlist ORDER BY added_at DESC",
            conn,
            parse_dates=["added_at"],
        )
