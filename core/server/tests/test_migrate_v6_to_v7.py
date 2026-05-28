"""Migration from v0.5 schema (user_version=6) to v0.6 (user_version=7).

Adds:
  - edges.layer (TEXT, NULLABLE)
  - edges.verification_priority (TEXT, NULLABLE)
  - edge_verifications table
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dpd_mcp_server.migrate_v6_to_v7 import migrate
from dpd_mcp_server.storage import Storage


def _downgrade_to_v6(db_path: str) -> None:
    """Open as v7, then strip the v7 additions and stamp user_version=6."""
    Storage.open(db_path)
    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(edges)")}
        for col in ("layer", "verification_priority"):
            if col in cols:
                try:
                    conn.execute(f"ALTER TABLE edges DROP COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
        conn.execute("DROP TABLE IF EXISTS edge_verifications")
        conn.execute("PRAGMA user_version = 6")


def test_migrate_adds_edge_layer_and_priority_columns(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    _downgrade_to_v6(db_path)

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(edges)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert "layer" in cols
    assert "verification_priority" in cols
    assert version == 7


def test_migrate_creates_edge_verifications_table(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    _downgrade_to_v6(db_path)

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(edge_verifications)")}
    assert {"id", "edge_id", "verified_by", "verified_at", "method",
            "verdict", "notes", "prompt_hash"} <= cols


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    Storage.open(db_path)  # already v7

    migrate(db_path=db_path)  # should no-op

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 7


def test_migrate_preserves_existing_edge_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    now = "2026-05-28T00:00:00Z"
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
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, created_at, updated_at) "
            "VALUES ('n_b', 's_m', 'answer', 'b', 'open', 'n_a', "
            "'node', 'active', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO edges (session_id, from_node, to_node, type, reason, "
            "created_at) VALUES ('s_m', 'n_a', 'n_b', 'requires', 'dep', ?)",
            (now,),
        )
    _downgrade_to_v6(db_path)
    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT type, reason, layer, verification_priority "
            "FROM edges WHERE from_node = 'n_a'"
        ).fetchone()
    assert row["type"] == "requires"
    assert row["reason"] == "dep"
    assert row["layer"] is None
    assert row["verification_priority"] is None
