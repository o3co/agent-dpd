"""Tests for prgp_mcp_server.storage."""

from __future__ import annotations

import sqlite3

import pytest

from prgp_mcp_server.storage import Storage


def test_open_creates_required_tables(tmp_db_path: str) -> None:
    Storage.open(tmp_db_path)

    with sqlite3.connect(tmp_db_path) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {"sessions", "roots", "nodes", "edges"} <= names


def test_open_enables_wal_mode(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    with storage.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
