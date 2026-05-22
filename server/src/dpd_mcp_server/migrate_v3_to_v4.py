"""Migrate a v0.3-shaped DPD database (user_version=3) to v0.3.1 (user_version=4).

v0.3.1 introduces:
  - sessions.mode          (TEXT, nullable — legacy rows get NULL)
  - pool_items.rejected_at (TEXT, nullable)
  - pool_items.rejected_reason (TEXT, nullable)
  - pool_items.text_hash   (TEXT, nullable)
  - nodes.provenance       (TEXT NOT NULL DEFAULT 'grounded'
                            CHECK provenance IN ('grounded','inferred','imported','manual'))
  - idx_pool_rejected      partial index on pool_items

SQLite cannot add a NOT NULL column with CHECK constraint via simple ALTER TABLE
when the table already has rows unless a DEFAULT is provided. SQLite 3.37+
supports ADD COLUMN … NOT NULL DEFAULT … without rebuilding, but to be safe
and to guarantee the CHECK constraint is registered on older builds we rebuild
the nodes table (same approach used in migrate_v2_to_v3).

Idempotent: if user_version is already 4, returns immediately.
"""
from __future__ import annotations

import sqlite3


def migrate(*, db_path: str) -> None:
    """Upgrade db at *db_path* from schema v3 to v4 in place.

    Idempotent: calling on a v4 (or newer) database is a no-op.

    All changes run inside a single Python-managed transaction (BEGIN … COMMIT).
    On error, the transaction is rolled back automatically. This matches the
    approach used in migrate_v2_to_v3.py (individual conn.execute() calls)
    rather than executescript(), which issues an implicit COMMIT before each
    statement and therefore provides no atomicity guarantee.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 4:
            return

        # All changes in a single transaction for atomicity.
        conn.execute("PRAGMA foreign_keys = OFF")

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

        # 4. Partial index for active pool items (not rejected)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pool_rejected
                ON pool_items(scope_root_id, created_at)
                WHERE rejected_at IS NULL
        """)

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
