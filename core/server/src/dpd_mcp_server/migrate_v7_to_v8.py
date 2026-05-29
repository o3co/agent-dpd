"""Migrate a v0.8-shaped DPD database (user_version=7) to v0.9 (user_version=8).

v8 adds the note layer (#55):
  - notes — anchored long-form narrative. Anchors are polymorphic over
    nodes OR roots(=subgraph); anchor_id has no FK (validated in app code,
    like edges). At most one active note per (anchor_kind, anchor_id, kind),
    enforced by the partial unique index uniq_notes_active_anchor_kind.

The anchor_kind / kind / state CHECK constraints live in schema.sql for
fresh databases; on this ALTER-style upgrade path SQLite cannot add CHECK
constraints to a CREATE TABLE after the fact, but since `notes` is a brand
new table we CREATE it with the CHECKs intact here too. The closed taxonomies
are additionally enforced in app code (Storage.add_note), mirroring the
edges.layer / scope_root notes.

Atomicity:
  CREATE TABLE + CREATE INDEX + PRAGMA user_version bump run inside one
  BEGIN IMMEDIATE … COMMIT block. On error, ROLLBACK restores the v7 state
  and user_version stays at 7.

Idempotent: if user_version is already 8 (or newer), returns immediately.
CREATE TABLE/INDEX IF NOT EXISTS make a partial-state retry safe.

CLI: ``python -m dpd_mcp_server.migrate_v7_to_v8 path/to/db.sqlite``
"""
from __future__ import annotations

import sqlite3
import sys


def migrate(*, db_path: str) -> None:
    """Upgrade db at *db_path* from schema v7 to v8 in place."""
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 8:
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id           TEXT PRIMARY KEY,
                    session_id   TEXT NOT NULL REFERENCES sessions(id),
                    anchor_kind  TEXT NOT NULL
                                 CHECK (anchor_kind IN ('node','root')),
                    anchor_id    TEXT NOT NULL,
                    kind         TEXT NOT NULL CHECK (kind IN (
                        'narrative','caveat','external-analysis',
                        'rejected-alternative'
                    )),
                    text         TEXT NOT NULL,
                    state        TEXT NOT NULL DEFAULT 'active'
                                 CHECK (state IN ('active','archived')),
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uniq_notes_active_anchor_kind "
                "ON notes(anchor_kind, anchor_id, kind) WHERE state = 'active'"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_anchor "
                "ON notes(session_id, anchor_kind, anchor_id)"
            )
            conn.execute("PRAGMA user_version = 8")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _cli(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "Usage: python -m dpd_mcp_server.migrate_v7_to_v8 <db_path>",
            file=sys.stderr,
        )
        return 2
    db_path = argv[1]
    migrate(db_path=db_path)
    print(f"Migrated {db_path} to v0.9 (user_version=8)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
