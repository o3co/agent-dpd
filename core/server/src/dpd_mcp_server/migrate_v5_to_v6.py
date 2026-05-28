"""Migrate a v0.3.2-shaped DPD database (user_version=5) to v0.5 (user_version=6).

v6 adds:
  - nodes.severity (TEXT, NULLABLE) — optional proposer-assigned severity
    field used by the §4.5 natural-pause proposal to group/sort items
    (e.g., {'logical', 'surface', 'cosmetic'} for question nodes).

Atomicity:
  ALTER TABLE + PRAGMA user_version bump run inside one BEGIN IMMEDIATE …
  COMMIT block. On error, ROLLBACK restores the original v5 state and
  user_version stays at 5.

Idempotent: if user_version is already 6 (or newer), returns immediately.
A defensive check on the existing column list also makes a partial-state
retry safe (e.g. ALTER succeeded but the version bump didn't).

CLI: ``python -m dpd_mcp_server.migrate_v5_to_v6 path/to/db.sqlite``
"""
from __future__ import annotations

import sqlite3
import sys


def migrate(*, db_path: str) -> None:
    """Upgrade db at *db_path* from schema v5 to v6 in place."""
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 6:
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            existing_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(nodes)")
            }
            if "severity" not in existing_cols:
                conn.execute("ALTER TABLE nodes ADD COLUMN severity TEXT")
            conn.execute("PRAGMA user_version = 6")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _cli(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "Usage: python -m dpd_mcp_server.migrate_v5_to_v6 <db_path>",
            file=sys.stderr,
        )
        return 2
    db_path = argv[1]
    migrate(db_path=db_path)
    print(f"Migrated {db_path} to v0.5 (user_version=6)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
