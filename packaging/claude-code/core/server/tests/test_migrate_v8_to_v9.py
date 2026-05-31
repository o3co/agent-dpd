"""Migration from v0.x schema (user_version=8) to user_version=9 (#63).

v9 rebuilds the `nodes` table WITHOUT the `type` CHECK constraint, moving the
node-type vocabulary enforcement to the code-defined Storage.NODE_TYPES
frozenset (mirroring how edges.type carries no CHECK). All other columns,
CHECKs, defaults, and indexes are preserved.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dpd_mcp_server.migrate_v8_to_v9 import migrate
from dpd_mcp_server.storage import Storage

# Current `nodes` DDL WITH the type CHECK — the pre-v9 (v8) shape. Used to
# reconstruct a genuine v8 database from a latest-schema one.
_NODES_V8_DDL = """
CREATE TABLE nodes_v8 (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    type            TEXT NOT NULL CHECK (type IN (
        'question','plan','hypothesis','goal','problem',
        'answer','action','verification','decision','resolution',
        'evidence','constraint','assumption','rationale','risk',
        'start','end'
    )),
    text            TEXT NOT NULL,
    provenance      TEXT NOT NULL DEFAULT 'grounded'
        CHECK (provenance IN ('grounded', 'inferred', 'imported', 'manual')),
    status          TEXT NOT NULL CHECK (status IN ('open','closed')),
    closure_reason  TEXT
        CHECK (closure_reason IS NULL OR
               closure_reason IN ('resolved','rejected','invalidated')),
    parent_id       TEXT NOT NULL,
    parent_kind     TEXT NOT NULL CHECK (parent_kind IN ('root','node')),
    paired_for      TEXT REFERENCES nodes(id),
    achievement_conditions TEXT,
    achievement_conditions_satisfied INTEGER NOT NULL DEFAULT 0
        CHECK (achievement_conditions_satisfied IN (0,1)),
    state           TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','archived','closed','deletable','gone')),
    severity        TEXT,
    archived_at     TEXT,
    closed_at       TEXT,
    deletable_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""


def _downgrade_to_v8(db_path: str) -> None:
    """Open at the latest schema, then rebuild `nodes` WITH the type CHECK and
    stamp user_version=8 — i.e. reconstruct a genuine pre-#63 database."""
    Storage.open(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            _NODES_V8_DDL
            + ";\n"
            + """
            INSERT INTO nodes_v8 SELECT * FROM nodes;
            DROP TABLE nodes;
            ALTER TABLE nodes_v8 RENAME TO nodes;
            CREATE INDEX IF NOT EXISTS idx_nodes_session ON nodes(session_id);
            CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
            CREATE INDEX IF NOT EXISTS idx_nodes_state ON nodes(session_id, state);
            PRAGMA user_version = 8;
            """
        )


def _seed_node(db_path: str) -> None:
    """Insert one session + node through the public API at latest schema."""
    s = Storage.open(db_path)
    s.insert_session(session_id="ses_x", scope=None, label="L",
                     now="2026-06-01T00:00:00Z")
    s.insert_node(node_id="node_x", session_id="ses_x", node_type="decision",
                  text="kept", parent_id="ses_x", parent_kind="root",
                  now="2026-06-01T00:00:00Z")


def test_migrate_bumps_version_and_drops_type_check(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    _downgrade_to_v8(db_path)

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        # Raw INSERT (bypassing Storage) of a type the OLD CHECK rejected now
        # succeeds — direct proof the DB-level CHECK is gone. The app-code
        # NODE_TYPES guard still rejects it on the Storage path (tested
        # separately in test_node_type_vocab).
        conn.execute(
            "INSERT INTO nodes "
            "(id, session_id, type, text, status, parent_id, parent_kind, "
            " created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("raw1", "ses_raw", "zzz_not_a_type", "t", "open", "p", "root",
             "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z"),
        )


def test_migrate_preserves_existing_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    _seed_node(db_path)
    _downgrade_to_v8(db_path)

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM nodes WHERE id='node_x'").fetchone()
    assert row is not None
    assert row["type"] == "decision"
    assert row["text"] == "kept"


def test_migrate_recreates_indexes(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    _downgrade_to_v8(db_path)

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        idx = {r[1] for r in conn.execute("PRAGMA index_list(nodes)")}
    assert {"idx_nodes_session", "idx_nodes_parent", "idx_nodes_state"} <= idx


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    Storage.open(db_path)  # already at latest (>= 9)

    migrate(db_path=db_path)  # must no-op

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9


def test_migrate_leaves_fk_integrity_clean(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.sqlite")
    _seed_node(db_path)
    _downgrade_to_v8(db_path)

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert violations == []


def test_migrate_handles_alter_upgraded_column_order(tmp_path: Path) -> None:
    # Databases that reached v8 via the migration chain have `severity` as the
    # LAST column (added by ALTER TABLE in v5→v6), not in the schema.sql position
    # (before archived_at). A positional `SELECT *` copy would shift every value
    # after `state` into the wrong column. Reproduce that physical order and
    # assert the rebuild maps by name (data intact, no shift).
    from dpd_mcp_server.migrate_v5_to_v6 import migrate as _migrate_v5_to_v6

    db_path = str(tmp_path / "graph.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions(
            id TEXT PRIMARY KEY, scope TEXT, label TEXT,
            started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            focus_node_id TEXT, mode TEXT);
        CREATE TABLE nodes(
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL,
            text TEXT NOT NULL,
            provenance TEXT NOT NULL DEFAULT 'grounded',
            status TEXT NOT NULL, closure_reason TEXT,
            parent_id TEXT NOT NULL, parent_kind TEXT NOT NULL, paired_for TEXT,
            achievement_conditions TEXT,
            achievement_conditions_satisfied INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'active',
            archived_at TEXT, closed_at TEXT, deletable_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        PRAGMA user_version = 5;
        """
    )
    conn.commit()
    conn.close()

    _migrate_v5_to_v6(db_path=db_path)  # ALTER ADD COLUMN severity -> appended LAST

    with sqlite3.connect(db_path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(nodes)")]
        assert cols[-1] == "severity", "fixture must reproduce severity-last order"
        # Seed a node with distinctive values so a column shift is detectable.
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, severity, archived_at, created_at, updated_at) "
            "VALUES ('n1','s','decision','t','open','p','root','SEV',NULL,'CREATED','UPDATED')"
        )
        conn.execute("PRAGMA user_version = 8")  # v6→v7/v7→v8 don't touch node cols

    migrate(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM nodes WHERE id='n1'").fetchone()
    # With a buggy positional copy, severity ('SEV') would land in updated_at.
    assert row["severity"] == "SEV"
    assert row["created_at"] == "CREATED"
    assert row["updated_at"] == "UPDATED"
    assert row["archived_at"] is None


def test_storage_open_forward_ports_v8_to_v9(tmp_path: Path) -> None:
    # A genuine v8 DB opened cold through Storage.open must reach v9 (proves the
    # dispatch chain wires _migrate_v8_to_v9 in).
    db_path = str(tmp_path / "graph.sqlite")
    _downgrade_to_v8(db_path)

    Storage.open(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
