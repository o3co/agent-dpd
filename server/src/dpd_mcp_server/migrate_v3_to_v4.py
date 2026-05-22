"""Migrate a v0.3-shaped DPD database (user_version=3) to v0.3.1 (user_version=4).

v0.3.1 introduces:
  - sessions.mode          (TEXT, nullable — legacy rows get NULL)
  - pool_items.rejected_at (TEXT, nullable)
  - pool_items.rejected_reason (TEXT, nullable)
  - pool_items.text_hash   (TEXT, nullable)
  - nodes.provenance       (TEXT NOT NULL DEFAULT 'grounded'
                            CHECK provenance IN ('grounded','inferred','imported','manual'))
  - idx_pool_rejected      partial index on pool_items

Atomicity:
  All DDL runs inside an explicit BEGIN ... COMMIT transaction. We set
  isolation_level=None so Python's sqlite3 module does NOT auto-commit DDL
  statements (which it does in legacy mode), and instead all ALTER/CREATE/
  DROP/RENAME participate in the same transaction. On error, ROLLBACK
  restores the original v3 state — verified by
  test_migrate_v3_to_v4::test_migration_rolls_back_on_partial_failure.

  PRAGMA foreign_keys is a per-connection toggle and cannot be modified
  inside a transaction (sqlite limitation), so it is set OFF before BEGIN
  and restored ON after COMMIT/ROLLBACK.

Idempotent: if user_version is already 4, returns immediately.

CLI: ``python -m dpd_mcp_server.migrate_v3_to_v4 path/to/db.sqlite``
"""
from __future__ import annotations

import sqlite3
import sys


def migrate(*, db_path: str) -> None:
    """Upgrade db at *db_path* from schema v3 to v4 in place.

    Idempotent: calling on a v4 (or newer) database is a no-op.

    Atomic: uses ``isolation_level=None`` + explicit ``BEGIN IMMEDIATE`` /
    ``COMMIT`` so DDL statements participate in the transaction. On error,
    ``ROLLBACK`` restores the original v3 state.
    """
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # manual tx control — required for DDL atomicity
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 4:
            return

        # PRAGMA foreign_keys must be set OUTSIDE a transaction.
        # OFF here for the nodes table rebuild (DROP + RENAME); restored after.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 1. sessions: add mode column (nullable, legacy rows get NULL)
            conn.execute("ALTER TABLE sessions ADD COLUMN mode TEXT")

            # 2. pool_items: add rejected_at, rejected_reason, text_hash
            conn.execute("ALTER TABLE pool_items ADD COLUMN rejected_at TEXT")
            conn.execute("ALTER TABLE pool_items ADD COLUMN rejected_reason TEXT")
            conn.execute("ALTER TABLE pool_items ADD COLUMN text_hash TEXT")

            # 3. nodes: rebuild table to add provenance with NOT NULL + CHECK.
            #    SQLite ALTER TABLE ADD COLUMN can add a column with a DEFAULT
            #    and NOT NULL, but CHECK constraints on ADD COLUMN are only
            #    enforced on new inserts in some builds, not on the existing
            #    data. Rebuilding guarantees the CHECK is part of the table
            #    definition and will be enforced for all future writes.
            conn.execute("""
                CREATE TABLE nodes_v4 (
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
                    archived_at     TEXT,
                    closed_at       TEXT,
                    deletable_at    TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                )
            """)
            conn.execute("""
                INSERT INTO nodes_v4
                    (id, session_id, type, text, provenance, status, closure_reason,
                     parent_id, parent_kind, paired_for, achievement_conditions,
                     achievement_conditions_satisfied, state, archived_at, closed_at,
                     deletable_at, created_at, updated_at)
                SELECT id, session_id, type, text, 'grounded', status, closure_reason,
                       parent_id, parent_kind, paired_for, achievement_conditions,
                       achievement_conditions_satisfied, state, archived_at, closed_at,
                       deletable_at, created_at, updated_at
                FROM nodes
            """)
            conn.execute("DROP TABLE nodes")
            conn.execute("ALTER TABLE nodes_v4 RENAME TO nodes")

            # 4. Partial index for active pool items (not rejected).
            #    Keys (scope_root_id, created_at) accelerate the common query
            #    "list active pool items in scope ordered by recency". Spec §9.5
            #    documents (rejected_at) keys but those would be degenerate
            #    (all NULL under the partial filter); see I1/M1 notes.
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pool_rejected
                    ON pool_items(scope_root_id, created_at)
                    WHERE rejected_at IS NULL
            """)

            conn.execute("PRAGMA user_version = 4")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def _cli(argv: list[str]) -> int:
    """Command-line entry: ``python -m dpd_mcp_server.migrate_v3_to_v4 <db_path>``."""
    if len(argv) != 2:
        print(
            "Usage: python -m dpd_mcp_server.migrate_v3_to_v4 <db_path>",
            file=sys.stderr,
        )
        return 2
    db_path = argv[1]
    migrate(db_path=db_path)
    print(f"Migrated {db_path} to v0.3.1 (user_version=4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
