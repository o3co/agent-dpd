"""Migrate a v0.3.1-shaped DPD database (user_version=4) to v0.3.2 (user_version=5).

v0.3.2 introduces:
  - subgraphs_fts virtual table (FTS5, tokenize='trigram')
  - Backfill: every existing start node with state IN ('closed', 'archived')
    gets an FTS row written via Storage._reindex_subgraph_on.

Atomicity:
  All DDL and backfill DML run inside an explicit BEGIN IMMEDIATE ... COMMIT
  transaction. We set isolation_level=None so Python's sqlite3 module does NOT
  auto-commit DDL statements, and instead CREATE VIRTUAL TABLE + all backfill
  INSERTs + PRAGMA user_version = 5 participate in the same transaction. On
  any error, ROLLBACK restores the original v4 state — user_version stays at 4
  and no FTS rows are written.

  Mirrors the atomicity pattern of migrate_v3_to_v4.py.

Idempotent: if user_version is already 5, returns immediately.

CLI: ``python -m dpd_mcp_server.migrate_v4_to_v5 path/to/db.sqlite``
"""
from __future__ import annotations

import sqlite3
import sys


def migrate(*, db_path: str) -> None:
    """Upgrade db at *db_path* from schema v4 to v5 in place.

    Idempotent: calling on a v5 (or newer) database is a no-op.

    Atomic: uses ``isolation_level=None`` + explicit ``BEGIN IMMEDIATE`` /
    ``COMMIT`` so DDL statements and backfill DML participate in the same
    transaction. On error, ``ROLLBACK`` restores the original v4 state —
    ``PRAGMA user_version`` stays at 4 and no FTS rows are persisted.
    """
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # manual tx control — required for DDL atomicity
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 5:
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS subgraphs_fts USING fts5(
                    start_node_id UNINDEXED,
                    session_id    UNINDEXED,
                    anchor_text,
                    body_text,
                    journey_text,
                    closed_at     UNINDEXED,
                    tokenize = 'trigram'
                )
            """)

            # Backfill inside the same transaction so a failure rolls back
            # the version bump AND any partial FTS rows.
            # Imported here to avoid an import cycle at module top.
            from .storage import Storage
            storage = Storage(db_path)
            rows = conn.execute(
                "SELECT id FROM nodes WHERE type = 'start' "
                "AND state IN ('closed', 'archived')"
            ).fetchall()
            for r in rows:
                storage._reindex_subgraph_on(conn, start_node_id=r["id"])

            conn.execute("PRAGMA user_version = 5")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _cli(argv: list[str]) -> int:
    """Command-line entry: ``python -m dpd_mcp_server.migrate_v4_to_v5 <db_path>``."""
    if len(argv) != 2:
        print(
            "Usage: python -m dpd_mcp_server.migrate_v4_to_v5 <db_path>",
            file=sys.stderr,
        )
        return 2
    db_path = argv[1]
    migrate(db_path=db_path)
    print(f"Migrated {db_path} to v0.3.2 (user_version=5)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
