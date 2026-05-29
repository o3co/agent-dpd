"""Migrate a v0.2-shaped DPD database to the v0.3 model in place.

v0.3 introduces scope-level singleton root + per-old-root Start nodes.
Existing v0.2 nodes are re-parented to their Start node; old root rows
are retained for historical reference with migrated_to_start_id set.

Idempotent: running twice is a no-op for already-migrated rows.

CLI: ``python -m dpd_mcp_server.migrate_v2_to_v3 path/to/db.sqlite``
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone

from .ids import root_id as _new_root_id, node_id as _new_node_id


def migrate(*, db_path: str, now: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # 1. Collect all distinct scopes from sessions (NULL → top-level).
        scopes_rows = conn.execute(
            "SELECT DISTINCT scope FROM sessions"
        ).fetchall()
        scopes = [r["scope"] for r in scopes_rows]

        for scope in scopes:
            # Resolve scope_root for this scope. NULL scope (top-level) uses
            # the sentinel string '' (empty) so the UNIQUE index applies.
            scope_key = scope if scope is not None else ""

            existing_sr = conn.execute(
                "SELECT id FROM roots WHERE scope = ? AND scope_root = 1",
                (scope_key,),
            ).fetchone()
            if existing_sr is None:
                sr_id = _new_root_id()
                conn.execute(
                    "INSERT INTO roots (id, session_id, scope, scope_root, "
                    "topic, lifecycle, spawned_at, last_focused_at) "
                    "VALUES (?, NULL, ?, 1, ?, 'active', ?, NULL)",
                    (sr_id, scope_key, f"{scope_key or 'top-level'} scope root", now),
                )
            else:
                sr_id = existing_sr["id"]

            # 2. For each old root in this scope (NOT scope_root), create Start node.
            # Filter old roots whose session belongs to this scope.
            if scope is None:
                old_roots = list(conn.execute(
                    "SELECT r.* FROM roots r "
                    "JOIN sessions s ON r.session_id = s.id "
                    "WHERE r.scope_root = 0 AND s.scope IS NULL "
                    "AND r.migrated_to_start_id IS NULL"
                ))
            else:
                old_roots = list(conn.execute(
                    "SELECT r.* FROM roots r "
                    "JOIN sessions s ON r.session_id = s.id "
                    "WHERE r.scope_root = 0 AND s.scope = ? "
                    "AND r.migrated_to_start_id IS NULL",
                    (scope,),
                ))

            for old in old_roots:
                start_id = _new_node_id()
                # Map old root.lifecycle → Start node state.
                state = "closed" if old["lifecycle"] == "archived" else "active"
                archived_at_value = now if state == "closed" else None
                closed_at_value = now if state == "closed" else None
                conn.execute(
                    "INSERT INTO nodes "
                    "(id, session_id, type, text, status, closure_reason, "
                    "parent_id, parent_kind, paired_for, "
                    "achievement_conditions, achievement_conditions_satisfied, "
                    "state, archived_at, closed_at, deletable_at, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, 'start', ?, 'open', NULL, ?, 'root', NULL, "
                    "NULL, 0, ?, ?, ?, NULL, ?, ?)",
                    (start_id, old["session_id"], old["topic"], sr_id,
                     state, archived_at_value, closed_at_value, now, now),
                )
                # Re-parent all direct children of the old root.
                conn.execute(
                    "UPDATE nodes SET parent_id = ?, parent_kind = 'node', "
                    "updated_at = ? "
                    "WHERE session_id = ? AND parent_id = ? AND parent_kind = 'root'",
                    (start_id, now, old["session_id"], old["id"]),
                )
                # Mark old root migrated.
                conn.execute(
                    "UPDATE roots SET migrated_to_start_id = ? WHERE id = ?",
                    (start_id, old["id"]),
                )
        # Spec §7.2 step 4: initialize state column from status column.
        # SQLite DEFAULT only fires on INSERT; existing v0.2 rows got 'active'
        # via ALTER TABLE ADD COLUMN — manually sync from status here.
        conn.execute(
            "UPDATE nodes SET state = 'closed' WHERE status = 'closed' AND state = 'active'"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cli(argv: list[str]) -> int:
    """Command-line entry: ``python -m dpd_mcp_server.migrate_v2_to_v3 <db_path>``."""
    if len(argv) != 2:
        print(
            "Usage: python -m dpd_mcp_server.migrate_v2_to_v3 <db_path>",
            file=sys.stderr,
        )
        return 2
    db_path = argv[1]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    migrate(db_path=db_path, now=now)
    print(f"Migrated {db_path} to v0.3 (user_version=3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
