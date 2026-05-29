"""Migration from v0.3 schema (user_version=3) to v0.3.1 schema (user_version=4).

Adds: sessions.mode, pool_items.rejected_at/rejected_reason/text_hash,
nodes.provenance, idx_pool_rejected partial index.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dpd_mcp_server.storage import Storage
from dpd_mcp_server.migrate_v3_to_v4 import migrate


def _seed_v3_db(db_path: str) -> None:
    """Create a fresh v4 DB via Storage.open(), then seed it with one session,
    one root, one node, and one pool item.

    The resulting DB is in v4 shape.  Callers that need a genuine v3-shaped DB
    must follow this with ``_downgrade_to_v3(db_path)`` to strip v4 columns and
    reset user_version to 3.
    """
    storage = Storage.open(db_path)
    now = "2026-05-22T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, scope, label, started_at, updated_at) "
            "VALUES ('ses_1', 'test', NULL, ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, scope, scope_root, topic, lifecycle, spawned_at) "
            "VALUES ('root_1', 'ses_1', 'test', 0, 'test topic', 'active', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, "
            "parent_id, parent_kind, created_at, updated_at) "
            "VALUES ('node_1', 'ses_1', 'question', 'test q', 'open', "
            "'root_1', 'root', ?, ?)",
            (now, now),
        )
        # Insert a pool item
        conn.execute(
            "INSERT INTO pool_items (id, scope_root_id, text, created_at) "
            "VALUES ('pool_1', 'root_1', 'pool text', ?)",
            (now,),
        )


def _downgrade_to_v3(db_path: str) -> None:
    """Forcibly downgrade a v4 DB to v3 by dropping new columns and resetting user_version.

    SQLite does not support DROP COLUMN directly (pre-3.35), so we rebuild tables.
    This lets us test the migration on a genuine v3-shaped database.
    """
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA foreign_keys = OFF;

        -- Rebuild sessions without 'mode'
        CREATE TABLE sessions_v3 (
            id           TEXT PRIMARY KEY,
            scope        TEXT,
            label        TEXT,
            started_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            focus_node_id TEXT
        );
        INSERT INTO sessions_v3 SELECT id, scope, label, started_at, updated_at, focus_node_id
        FROM sessions;
        DROP TABLE sessions;
        ALTER TABLE sessions_v3 RENAME TO sessions;

        -- Rebuild nodes without 'provenance'
        CREATE TABLE nodes_v3 (
            id              TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL,
            type            TEXT NOT NULL,
            text            TEXT NOT NULL,
            status          TEXT NOT NULL,
            closure_reason  TEXT,
            parent_id       TEXT NOT NULL,
            parent_kind     TEXT NOT NULL,
            paired_for      TEXT,
            achievement_conditions TEXT,
            achievement_conditions_satisfied INTEGER NOT NULL DEFAULT 0,
            state           TEXT NOT NULL DEFAULT 'active',
            archived_at     TEXT,
            closed_at       TEXT,
            deletable_at    TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );
        INSERT INTO nodes_v3 SELECT id, session_id, type, text, status, closure_reason,
            parent_id, parent_kind, paired_for, achievement_conditions,
            achievement_conditions_satisfied, state, archived_at, closed_at,
            deletable_at, created_at, updated_at
        FROM nodes;
        DROP TABLE nodes;
        ALTER TABLE nodes_v3 RENAME TO nodes;

        -- Rebuild pool_items without rejected_at, rejected_reason, text_hash
        CREATE TABLE pool_items_v3 (
            id                TEXT PRIMARY KEY,
            scope_root_id     TEXT NOT NULL,
            origin_session_id TEXT,
            text              TEXT NOT NULL,
            origin_turn       TEXT,
            created_at        TEXT NOT NULL,
            elevated_to       TEXT,
            elevated_at       TEXT,
            dropped_at        TEXT,
            tags              TEXT
        );
        INSERT INTO pool_items_v3 SELECT id, scope_root_id, origin_session_id, text,
            origin_turn, created_at, elevated_to, elevated_at, dropped_at, tags
        FROM pool_items;
        DROP TABLE pool_items;
        ALTER TABLE pool_items_v3 RENAME TO pool_items;

        -- Drop the v4 index
        DROP INDEX IF EXISTS idx_pool_rejected;

        PRAGMA user_version = 3;
        PRAGMA foreign_keys = ON;
    """)
    conn.commit()
    conn.close()


def test_v3_db_migrated_to_v4(tmp_db_path: str) -> None:
    """A v3 DB is upgraded to v4 by migrate()."""
    _seed_v3_db(tmp_db_path)
    _downgrade_to_v3(tmp_db_path)

    migrate(db_path=tmp_db_path)

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 4, f"expected user_version=4, got {version}"

    ses_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert "mode" in ses_cols

    node_cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    assert "provenance" in node_cols

    pool_cols = {r["name"] for r in conn.execute("PRAGMA table_info(pool_items)")}
    assert "rejected_at" in pool_cols
    assert "rejected_reason" in pool_cols
    assert "text_hash" in pool_cols

    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_pool_rejected'"
    ).fetchone()
    assert idx is not None

    conn.close()


def test_existing_nodes_get_grounded_provenance(tmp_db_path: str) -> None:
    """After migration, pre-existing nodes have provenance='grounded'."""
    _seed_v3_db(tmp_db_path)
    _downgrade_to_v3(tmp_db_path)

    migrate(db_path=tmp_db_path)

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    nodes = conn.execute("SELECT id, provenance FROM nodes").fetchall()
    assert len(nodes) > 0
    for node in nodes:
        assert node["provenance"] == "grounded", (
            f"node {node['id']!r} has provenance={node['provenance']!r}"
        )
    conn.close()


def test_existing_sessions_get_null_mode(tmp_db_path: str) -> None:
    """Legacy sessions have mode=NULL after migration (= heuristic detection target)."""
    _seed_v3_db(tmp_db_path)
    _downgrade_to_v3(tmp_db_path)

    migrate(db_path=tmp_db_path)

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    sessions = conn.execute("SELECT id, mode FROM sessions").fetchall()
    assert len(sessions) > 0
    for ses in sessions:
        assert ses["mode"] is None, (
            f"session {ses['id']!r} has mode={ses['mode']!r}, expected NULL"
        )
    conn.close()


def test_migrate_is_idempotent(tmp_db_path: str) -> None:
    """Running migrate twice on a v4 DB is a no-op (no error, version stays 4)."""
    _seed_v3_db(tmp_db_path)
    _downgrade_to_v3(tmp_db_path)

    migrate(db_path=tmp_db_path)
    migrate(db_path=tmp_db_path)  # second call: must be no-op

    conn = sqlite3.connect(tmp_db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 4
    conn.close()


def test_migration_rolls_back_on_partial_failure(tmp_db_path: str) -> None:
    """If migration fails mid-way, DB is rolled back to v3 state.

    Triggers failure by injecting a v3 row whose ``closure_reason`` violates a
    v4 CHECK constraint. The migration's INSERT INTO nodes_v4 SELECT raises
    IntegrityError after several ALTER TABLE statements have already executed.
    Verifies that explicit BEGIN ... COMMIT atomicity rolls back the prior
    DDL — without it (Python sqlite3 legacy mode auto-commits DDL), the
    sessions.mode / pool_items.rejected_* columns would persist with
    user_version still 3, leaving the database unrecoverable on next open.
    """
    import pytest

    _seed_v3_db(tmp_db_path)
    _downgrade_to_v3(tmp_db_path)

    # Inject a v3 row that violates v4 CHECK (closure_reason IN
    # ('resolved','rejected','invalidated')). v3 nodes table (rebuilt by
    # _downgrade_to_v3) intentionally has no CHECK on closure_reason, so the
    # bogus value sits there until the v4 migration's INSERT trips on it.
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE nodes SET closure_reason = 'bogus_value' WHERE id = 'node_1'"
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.IntegrityError):
        migrate(db_path=tmp_db_path)

    # Verify DB rolled back to v3 state — no half-applied changes.
    conn = sqlite3.connect(tmp_db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 3, (
            f"user_version should remain 3 after rollback, got {version}"
        )

        ses_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert "mode" not in ses_cols, (
            f"sessions.mode should not persist after rollback, cols={ses_cols}"
        )

        pool_cols = {r[1] for r in conn.execute("PRAGMA table_info(pool_items)")}
        for col in ("rejected_at", "rejected_reason", "text_hash"):
            assert col not in pool_cols, (
                f"pool_items.{col} should not persist after rollback, cols={pool_cols}"
            )

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "nodes_v4" not in tables, (
            f"orphan nodes_v4 must not persist after rollback, tables={tables}"
        )

        node_cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)")}
        assert "provenance" not in node_cols, (
            f"nodes.provenance should not persist after rollback, cols={node_cols}"
        )
    finally:
        conn.close()


def test_cli_entry_point_migrates_db(
    tmp_db_path: str, capsys
) -> None:
    """`python -m dpd_mcp_server.migrate_v3_to_v4 <db>` actually invokes migrate().

    Regression guard: prior to v0.3.1 the module had no ``__main__`` block, so
    the documented CLI silently no-op'd. This test invokes _cli() with the
    same argv form as ``python -m`` to verify the migration is executed.
    """
    from dpd_mcp_server.migrate_v3_to_v4 import _cli

    _seed_v3_db(tmp_db_path)
    _downgrade_to_v3(tmp_db_path)

    # Simulate `python -m dpd_mcp_server.migrate_v3_to_v4 <db_path>`.
    rc = _cli(["dpd_mcp_server.migrate_v3_to_v4", tmp_db_path])
    assert rc == 0

    conn = sqlite3.connect(tmp_db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 4
    finally:
        conn.close()

    captured = capsys.readouterr()
    assert "Migrated" in captured.out


def test_cli_entry_point_usage_error(capsys) -> None:
    """`python -m dpd_mcp_server.migrate_v3_to_v4` (no args) prints usage + exit 2."""
    from dpd_mcp_server.migrate_v3_to_v4 import _cli

    rc = _cli(["dpd_mcp_server.migrate_v3_to_v4"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "Usage" in captured.err
