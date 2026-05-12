"""Watchlist store tests — schema, add/remove idempotency, notes update, list ordering."""
from __future__ import annotations

from pathlib import Path
import time

import pytest

from watchlist.store import add, remove, is_watched, update_notes, list_all, init_db


def test_add_then_is_watched(tmp_path: Path):
    db = tmp_path / "w.db"
    add("RELIANCE", path=db)
    assert is_watched("RELIANCE", path=db)
    assert not is_watched("NOTTHERE", path=db)


def test_add_normalizes_to_uppercase(tmp_path: Path):
    db = tmp_path / "w.db"
    add("reliance", notes="oil major", path=db)
    assert is_watched("RELIANCE", path=db)


def test_add_is_idempotent_keeps_added_at_updates_notes(tmp_path: Path):
    db = tmp_path / "w.db"
    add("X", notes="first note", path=db)
    add("X", notes="updated note", path=db)
    df = list_all(path=db)
    assert len(df) == 1
    assert df.iloc[0]["notes"] == "updated note"


def test_remove_silent_if_absent(tmp_path: Path):
    db = tmp_path / "w.db"
    init_db(db)
    remove("NOTTHERE", path=db)  # should not raise
    add("X", path=db)
    remove("X", path=db)
    assert not is_watched("X", path=db)


def test_update_notes_separately(tmp_path: Path):
    db = tmp_path / "w.db"
    add("X", notes="initial", path=db)
    update_notes("X", "revised", path=db)
    df = list_all(path=db)
    assert df.iloc[0]["notes"] == "revised"


def test_list_all_orders_newest_first(tmp_path: Path):
    db = tmp_path / "w.db"
    add("FIRST", path=db)
    time.sleep(1.1)  # ensure different added_at timestamp (precision: seconds)
    add("SECOND", path=db)
    df = list_all(path=db)
    assert list(df["symbol"]) == ["SECOND", "FIRST"]


def test_list_all_empty_when_db_missing(tmp_path: Path):
    db = tmp_path / "missing.db"
    df = list_all(path=db)
    assert df.empty
