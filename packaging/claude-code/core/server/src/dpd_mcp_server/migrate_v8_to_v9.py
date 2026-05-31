"""Migrate a v0.x DPD database (user_version=8) to user_version=9.

v9 (#63) rebuilds the ``nodes`` table WITHOUT the ``type`` CHECK constraint.
Node-type enforcement moves from the DB CHECK to the code-defined
``Storage.NODE_TYPES`` frozenset (mirroring how ``edges.type`` carries no
CHECK), so future vocabulary additions are frozenset edits, not migrations.
Every other column, CHECK, default, and index on ``nodes`` is preserved.

Atomicity:
  The table rebuild (CREATE/INSERT/DROP/RENAME) + index recreation +
  PRAGMA user_version bump run inside one BEGIN IMMEDIATE … COMMIT block. On
  error, ROLLBACK restores the v8 state and user_version stays at 8.

  PRAGMA foreign_keys is a per-connection toggle and cannot be modified inside
  a transaction (sqlite limitation), so it is set OFF before BEGIN and restored
  ON after COMMIT/ROLLBACK — required because ``nodes.paired_for`` (self-FK) and
  ``pool_items.elevated_to`` reference ``nodes(id)`` and must not be enforced
  mid-rebuild. This mirrors migrate_v3_to_v4.

Idempotent: if user_version is already 9 (or newer), returns immediately.

CLI: ``python -m dpd_mcp_server.migrate_v8_to_v9 path/to/db.sqlite``
"""
from __future__ import annotations

import sqlite3
import sys

# Logical column order of the `nodes` table (fresh schema.sql order). The copy
# below lists these explicitly on BOTH sides so the migration is robust to a
# different *physical* column order in the source table — databases that reached
# v8 via the migration chain have `severity` appended LAST (it was added by
# `ALTER TABLE ADD COLUMN` in migrate_v5_to_v6), not in the schema.sql position.
# A positional `SELECT *` would shift those rows into the wrong columns.
_NODES_COLUMNS = (
    "id, session_id, type, text, provenance, status, closure_reason, "
    "parent_id, parent_kind, paired_for, achievement_conditions, "
    "achievement_conditions_satisfied, state, severity, "
    "archived_at, closed_at, deletable_at, created_at, updated_at"
)

# Current `nodes` DDL MINUS the `type` CHECK — `type` becomes a plain
# `TEXT NOT NULL`. Every other column / CHECK / default is identical to the v8
# table.
_NODES_V9_DDL = """
CREATE TABLE nodes_new (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    type            TEXT NOT NULL,
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


def migrate(*, db_path: str) -> None:
    """Upgrade db at *db_path* from schema v8 to v9 in place.

    Idempotent: calling on a v9 (or newer) database is a no-op.
    """
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # manual tx control — required for DDL atomicity
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 9:
            return

        # PRAGMA foreign_keys must be toggled OUTSIDE a transaction; OFF here for
        # the nodes table rebuild (DROP + RENAME with self-FK + inbound FK).
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(_NODES_V9_DDL)
            # Copy by explicit column name (NOT SELECT *) so a source table whose
            # physical column order differs from schema.sql (severity-last on
            # ALTER-upgraded DBs) maps correctly instead of shifting values.
            conn.execute(
                f"INSERT INTO nodes_new ({_NODES_COLUMNS}) "
                f"SELECT {_NODES_COLUMNS} FROM nodes"
            )
            conn.execute("DROP TABLE nodes")
            conn.execute("ALTER TABLE nodes_new RENAME TO nodes")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_session ON nodes(session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_state "
                "ON nodes(session_id, state)"
            )
            conn.execute("PRAGMA user_version = 9")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def _cli(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "Usage: python -m dpd_mcp_server.migrate_v8_to_v9 <db_path>",
            file=sys.stderr,
        )
        return 2
    db_path = argv[1]
    migrate(db_path=db_path)
    print(f"Migrated {db_path} to user_version=9")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
