"""Migrate a v0.3.1-shaped DPD database (user_version=4) to v0.3.2 (user_version=5).

v0.3.2 introduces:
  - subgraphs_fts virtual table (FTS5, tokenize='trigram')
  - Backfill: every existing start node with state IN ('closed', 'archived')
    gets an FTS row written via Storage._reindex_subgraph.

Idempotent: if user_version is already 5, returns immediately.

CLI: ``python -m dpd_mcp_server.migrate_v4_to_v5 path/to/db.sqlite``
"""
from __future__ import annotations

import sqlite3
import sys


def migrate(*, db_path: str) -> None:
    """Upgrade db at *db_path* from schema v4 to v5 in place.

    Idempotent: calling on a v5 (or newer) database is a no-op.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 5:
            return

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
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
    finally:
        conn.close()

    # Backfill via Storage so we reuse the same composition logic as the
    # mutation hooks. Imported here to avoid an import cycle at module top.
    from .storage import Storage
    storage = Storage(db_path)
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM nodes WHERE type = 'start' "
            "AND state IN ('closed', 'archived')"
        ).fetchall()
    for r in rows:
        storage._reindex_subgraph(start_node_id=r["id"])


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
