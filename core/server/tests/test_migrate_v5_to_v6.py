"""Migration from v0.3.2 schema (user_version=5) to v0.4 (user_version=6).

Adds: nodes.severity (TEXT, NULLABLE).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dpd_mcp_server.migrate_v5_to_v6 import migrate
from dpd_mcp_server.storage import Storage


def _downgrade_to_v5(db_path: str) -> None:
    """Open as v6, then drop severity and stamp user_version=5 to simulate v5."""
    Storage.open(db_path)
    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)")}
        if "severity" in cols:
            # SQLite supports DROP COLUMN since 3.35; fall back to a rebuild
            # if the runtime is older.
            try:
                conn.execute("ALTER TABLE nodes DROP COLUMN severity")
            except sqlite3.OperationalError:
                conn.executescript(
                    "BEGIN;"
                    "CREATE TABLE nodes_new AS SELECT id, session_id, type, text, "
                    "provenance, status, closure_reason, parent_id, parent_kind, "
                    "paired_for, achievement_conditions, "
                    "achievement_conditions_satisfied, state, archived_at, "
                    "closed_at, deletable_at, created_at, updated_at FROM nodes;"
                    "DROP TABLE nodes;"
                    "ALTER TABLE nodes_new RENAME TO nodes;"
                    "COMMIT;"
                )
        conn.execute("PRAGMA user_version = 5")


def test_migrate_adds_severity_column(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    _downgrade_to_v5(db_path)

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert "severity" in cols
    assert version == 6


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    Storage.open(db_path)  # already v6

    migrate(db_path=db_path)  # should no-op

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 6


def test_migrate_preserves_existing_node_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    now = "2026-05-27T00:00:00Z"
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
            "VALUES ('n_m', 's_m', 'question', 'preserved', 'open', 'r_m', "
            "'root', 'active', ?, ?)", (now, now),
        )
    _downgrade_to_v5(db_path)
    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT text, severity FROM nodes WHERE id = 'n_m'"
        ).fetchone()
    assert row["text"] == "preserved"
    assert row["severity"] is None
