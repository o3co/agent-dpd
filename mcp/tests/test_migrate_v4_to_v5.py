"""Migration from v0.3.1 schema (user_version=4) to v0.3.2 (user_version=5).

Adds: subgraphs_fts virtual table + backfill of existing closed/archived
subgraphs.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dpd_mcp_server.storage import Storage


def _seed_v4_db_with_closed_subgraph(db_path: str) -> str:
    """Open as v5, seed one closed subgraph, then downgrade to v4 (drop FTS)."""
    storage = Storage.open(db_path)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_v4', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_v4', 'ses_v4', 'r', 'active', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('n_s', 'ses_v4', 'start', 'V4 start NEEDLE-X', 'closed', "
            "'root_v4', 'root', 'closed', ?, ?, ?)",
            (now, now, now),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, paired_for, achievement_conditions, state, closed_at, "
            "created_at, updated_at) "
            "VALUES ('n_e', 'ses_v4', 'end', 'V4 end NEEDLE-Y', 'closed', "
            "'n_s', 'node', 'n_s', 'done', 'closed', ?, ?, ?)",
            (now, now, now),
        )
        # Drop FTS + reset version so we look like a real v4 DB.
        conn.execute("DROP TABLE subgraphs_fts")
        conn.execute("PRAGMA user_version = 4")
    return db_path


def test_fresh_v4_db_gets_subgraphs_fts(tmp_db_path: str) -> None:
    from dpd_mcp_server.migrate_v4_to_v5 import migrate

    db_path = _seed_v4_db_with_closed_subgraph(tmp_db_path)
    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='subgraphs_fts'"
            )
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert names == {"subgraphs_fts"}
    assert version == 5


def test_backfill_indexes_existing_closed_subgraphs(tmp_db_path: str) -> None:
    from dpd_mcp_server.migrate_v4_to_v5 import migrate

    db_path = _seed_v4_db_with_closed_subgraph(tmp_db_path)
    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT start_node_id, anchor_text FROM subgraphs_fts "
            "WHERE start_node_id = 'n_s'"
        ).fetchone()
    assert row is not None
    assert "NEEDLE-X" in row[1]
    assert "NEEDLE-Y" in row[1]


def test_migrate_is_idempotent(tmp_db_path: str) -> None:
    from dpd_mcp_server.migrate_v4_to_v5 import migrate

    db_path = _seed_v4_db_with_closed_subgraph(tmp_db_path)
    migrate(db_path=db_path)
    migrate(db_path=db_path)
    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = 'n_s'"
        ).fetchone()[0]
    assert count == 1


def test_skips_active_subgraphs(tmp_db_path: str) -> None:
    from dpd_mcp_server.migrate_v4_to_v5 import migrate

    storage = Storage.open(tmp_db_path)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_a', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_a', 'ses_a', 'r', 'active', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, created_at, updated_at) "
            "VALUES ('n_active', 'ses_a', 'start', 'Active', 'open', "
            "'root_a', 'root', 'active', ?, ?)",
            (now, now),
        )
        conn.execute("DROP TABLE subgraphs_fts")
        conn.execute("PRAGMA user_version = 4")

    migrate(db_path=tmp_db_path)

    with sqlite3.connect(tmp_db_path) as conn:
        count = conn.execute("SELECT count(*) FROM subgraphs_fts").fetchone()[0]
    assert count == 0
