"""Migration from v0.8 schema (user_version=7) to v0.9 (user_version=8).

Adds:
  - notes table (anchored long-form narrative, #55)
  - uniq_notes_active_anchor_kind partial unique index
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dpd_mcp_server.migrate_v7_to_v8 import migrate
from dpd_mcp_server.storage import Storage


def _downgrade_to_v7(db_path: str) -> None:
    """Open at the latest schema, then strip the v8 additions and stamp v7."""
    Storage.open(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP INDEX IF EXISTS uniq_notes_active_anchor_kind")
        conn.execute("DROP INDEX IF EXISTS idx_notes_anchor")
        conn.execute("DROP TABLE IF EXISTS notes")
        conn.execute("PRAGMA user_version = 7")


def test_migrate_creates_notes_table(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    _downgrade_to_v7(db_path)

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(notes)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert {
        "id", "session_id", "anchor_kind", "anchor_id", "kind",
        "text", "state", "created_at", "updated_at",
    } <= cols
    assert version == 8


def test_migrate_creates_active_unique_index(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    _downgrade_to_v7(db_path)

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        index_names = {
            r[1] for r in conn.execute("PRAGMA index_list(notes)")
        }
    assert "uniq_notes_active_anchor_kind" in index_names


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    Storage.open(db_path)  # Storage.open migrates all the way to the latest

    migrate(db_path=db_path)  # v7→v8 must no-op (version already ≥ 8)

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 9


def test_migrate_preserves_existing_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    now = "2026-05-29T00:00:00Z"
    Storage.open(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('s_m', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('r_m', 's_m', 't', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, created_at, updated_at) "
            "VALUES ('n_a', 's_m', 'question', 'a', 'open', 'r_m', "
            "'root', 'active', ?, ?)", (now, now),
        )
    _downgrade_to_v7(db_path)
    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        node = conn.execute(
            "SELECT text FROM nodes WHERE id = 'n_a'"
        ).fetchone()
    assert node["text"] == "a"
