"""Migration from v0.2 (forest of roots per session) to v0.3 (scope_root + subgraph Start nodes)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dpd_mcp_server.storage import Storage
from dpd_mcp_server.migrate_v2_to_v3 import migrate


def _seed_v2_db(db_path: str) -> None:
    """Set up a v0.2-shaped database with 2 sessions in scope 'dpd'
    and 2 roots per session, each with one child node."""
    storage = Storage.open(db_path)
    with storage.connect() as conn:
        for i, scope in enumerate(("dpd", "dpd"), start=1):
            sid = f"ses_{i}"
            conn.execute(
                "INSERT INTO sessions (id, scope, label, started_at, updated_at) "
                "VALUES (?, ?, NULL, ?, ?)",
                (sid, scope, "2026-05-20T00:00:00Z", "2026-05-20T00:00:00Z"),
            )
            for j in (1, 2):
                rid = f"r_{i}_{j}"
                conn.execute(
                    "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
                    "VALUES (?, ?, ?, 'active', ?)",
                    (rid, sid, f"topic {i}.{j}", "2026-05-20T00:00:00Z"),
                )
                conn.execute(
                    "INSERT INTO nodes (id, session_id, type, text, status, "
                    "parent_id, parent_kind, created_at, updated_at) "
                    "VALUES (?, ?, 'question', ?, 'open', ?, 'root', ?, ?)",
                    (f"n_{i}_{j}", sid, f"q {i}.{j}", rid,
                     "2026-05-20T00:00:00Z", "2026-05-20T00:00:00Z"),
                )


def test_migrate_creates_scope_root_and_start_nodes(tmp_db_path: str) -> None:
    _seed_v2_db(tmp_db_path)
    storage = Storage.open(tmp_db_path)

    migrate(db_path=tmp_db_path, now="2026-05-22T00:00:00Z")

    with storage.connect() as conn:
        # scope_root exists for "dpd"
        sr = conn.execute(
            "SELECT * FROM roots WHERE scope = 'dpd' AND scope_root = 1"
        ).fetchone()
        assert sr is not None
        assert sr["session_id"] is None

        # 4 Start nodes created (one per old root), all under scope_root
        starts = list(conn.execute(
            "SELECT * FROM nodes WHERE type = 'start' AND parent_id = ? "
            "AND parent_kind = 'root' ORDER BY id",
            (sr["id"],),
        ))
        assert len(starts) == 4

        # Old roots are marked with migrated_to_start_id
        olds = list(conn.execute(
            "SELECT * FROM roots WHERE scope_root = 0 ORDER BY id"
        ))
        assert len(olds) == 4
        for old in olds:
            assert old["migrated_to_start_id"] is not None

        # Original child nodes are reparented to the new Start nodes
        # (their parent_id should now be a Start node id, parent_kind='node')
        reparented = list(conn.execute(
            "SELECT * FROM nodes WHERE type = 'question' ORDER BY id"
        ))
        for row in reparented:
            assert row["parent_kind"] == "node"
            # parent_id should be one of the Start ids
            assert row["parent_id"] in {s["id"] for s in starts}


def test_migrate_is_idempotent(tmp_db_path: str) -> None:
    """Running migrate twice must not duplicate scope_root or Start nodes."""
    _seed_v2_db(tmp_db_path)
    migrate(db_path=tmp_db_path, now="2026-05-22T00:00:00Z")
    migrate(db_path=tmp_db_path, now="2026-05-22T01:00:00Z")
    storage = Storage.open(tmp_db_path)
    with storage.connect() as conn:
        sr_count = conn.execute(
            "SELECT COUNT(*) FROM roots WHERE scope = 'dpd' AND scope_root = 1"
        ).fetchone()[0]
        assert sr_count == 1
        start_count = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE type = 'start'"
        ).fetchone()[0]
        assert start_count == 4
