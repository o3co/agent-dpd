"""Migrate a v0.5-shaped DPD database (user_version=6) to v0.6 (user_version=7).

v7 adds the proof-tree discipline schema (#42):
  - edges.layer (TEXT, NULLABLE) — epistemic classification of an edge:
    {'necessary', 'selective', 'invalid'}. NULL means the discipline was
    not applied to this edge. Orthogonal to edges.type (the relationship
    kind). The CHECK constraint lives in schema.sql for fresh databases;
    on ALTER-upgraded databases the closed taxonomy is enforced in app code
    (Storage.add_edge / set_edge_layer), mirroring the scope_root note.
  - edges.verification_priority (TEXT, NULLABLE) — {'critical','standard',
    'low'}; drives list_unverified_edges ordering. Same CHECK placement.
  - edge_verifications — append-only audit of external verification runs
    for necessary edges (1:many, supports re-verification history).

Atomicity:
  ALTER TABLE + CREATE TABLE + PRAGMA user_version bump run inside one
  BEGIN IMMEDIATE … COMMIT block. On error, ROLLBACK restores the v6 state
  and user_version stays at 6.

Idempotent: if user_version is already 7 (or newer), returns immediately.
Defensive column/table existence checks make a partial-state retry safe.

CLI: ``python -m dpd_mcp_server.migrate_v6_to_v7 path/to/db.sqlite``
"""
from __future__ import annotations

import sqlite3
import sys


def migrate(*, db_path: str) -> None:
    """Upgrade db at *db_path* from schema v6 to v7 in place."""
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 7:
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            edge_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(edges)")
            }
            if "layer" not in edge_cols:
                conn.execute("ALTER TABLE edges ADD COLUMN layer TEXT")
            if "verification_priority" not in edge_cols:
                conn.execute(
                    "ALTER TABLE edges ADD COLUMN verification_priority TEXT"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS edge_verifications (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    edge_id     INTEGER NOT NULL REFERENCES edges(id),
                    verified_by TEXT,
                    verified_at TEXT,
                    method      TEXT,
                    verdict     TEXT,
                    notes       TEXT,
                    prompt_hash TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_edge_verifications_edge "
                "ON edge_verifications(edge_id)"
            )
            conn.execute("PRAGMA user_version = 7")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _cli(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "Usage: python -m dpd_mcp_server.migrate_v6_to_v7 <db_path>",
            file=sys.stderr,
        )
        return 2
    db_path = argv[1]
    migrate(db_path=db_path)
    print(f"Migrated {db_path} to v0.6 (user_version=7)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
