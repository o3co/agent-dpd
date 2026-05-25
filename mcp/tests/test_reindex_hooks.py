"""Tests for Storage._reindex_subgraph and mutation-path FTS hooks."""

from __future__ import annotations

import sqlite3

import pytest

from dpd_mcp_server.storage import Storage


def _seed_closed_subgraph(storage: Storage) -> tuple[str, str, str]:
    """Insert a minimal session + root + closed start/end pair.

    Returns (session_id, start_id, end_id).
    """
    now = "2026-05-23T00:00:00Z"
    sid, rid, sn, en = "ses_r1", "root_r1", "node_start_r1", "node_end_r1"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES (?, ?, ?)",
            (sid, now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES (?, ?, ?, 'active', ?)",
            (rid, sid, "test root", now),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES (?, ?, 'start', 'Start text here', 'closed', ?, 'root', "
            "'closed', ?, ?, ?)",
            (sn, sid, rid, now, now, now),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, paired_for, achievement_conditions, state, closed_at, "
            "created_at, updated_at) "
            "VALUES (?, ?, 'end', 'End text here', 'closed', ?, 'node', ?, "
            "'condition X met', 'closed', ?, ?, ?)",
            (en, sid, sn, sn, now, now, now),
        )
    return sid, sn, en


def test_reindex_subgraph_inserts_one_row(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    sid, sn, _en = _seed_closed_subgraph(storage)

    storage._reindex_subgraph(start_node_id=sn)

    with storage.connect() as conn:
        count = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?",
            (sn,),
        ).fetchone()[0]
        row = conn.execute(
            "SELECT start_node_id, session_id, anchor_text, body_text, "
            "journey_text FROM subgraphs_fts WHERE start_node_id = ?",
            (sn,),
        ).fetchone()
    assert count == 1
    assert row[0] == sn and row[1] == sid
    assert "Start text here" in row[2]
    assert "End text here" in row[2]
    assert "condition X met" in row[2]


def test_reindex_subgraph_is_idempotent(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _sid, sn, _en = _seed_closed_subgraph(storage)

    storage._reindex_subgraph(start_node_id=sn)
    storage._reindex_subgraph(start_node_id=sn)
    storage._reindex_subgraph(start_node_id=sn)

    with storage.connect() as conn:
        count = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?",
            (sn,),
        ).fetchone()[0]
    assert count == 1


def test_reindex_subgraph_skips_active_state(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_a', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_a', 'ses_a', 'r', 'active', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, created_at, updated_at) "
            "VALUES ('start_a', 'ses_a', 'start', 'Active start', 'open', "
            "'root_a', 'root', 'active', ?, ?)",
            (now, now),
        )

    storage._reindex_subgraph(start_node_id="start_a")

    with storage.connect() as conn:
        count = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = 'start_a'"
        ).fetchone()[0]
    assert count == 0


def test_reindex_subgraph_groups_by_node_type(tmp_db_path: str) -> None:
    """body_text gets decision/rationale/answer/evidence/resolution; rest go to journey_text."""
    storage = Storage.open(tmp_db_path)
    sid, sn, en = _seed_closed_subgraph(storage)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        # decision under end (body)
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('node_d', ?, 'decision', 'BODY decision text', 'closed', "
            "?, 'node', 'closed', ?, ?, ?)",
            (sid, en, now, now, now),
        )
        # hypothesis under end (journey)
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('node_h', ?, 'hypothesis', 'JOURNEY hypothesis text', "
            "'closed', ?, 'node', 'closed', ?, ?, ?)",
            (sid, en, now, now, now),
        )

    storage._reindex_subgraph(start_node_id=sn)

    with storage.connect() as conn:
        row = conn.execute(
            "SELECT body_text, journey_text FROM subgraphs_fts "
            "WHERE start_node_id = ?",
            (sn,),
        ).fetchone()
    assert "BODY decision text" in row[0]
    assert "BODY decision text" not in row[1]
    assert "JOURNEY hypothesis text" in row[1]
    assert "JOURNEY hypothesis text" not in row[0]


def test_mark_reached_reindexes_subgraph(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_m', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_m', 'ses_m', 'r', 'active', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, created_at, updated_at) "
            "VALUES ('n_s', 'ses_m', 'start', 'Start about REINDEX-TEST topic', "
            "'open', 'root_m', 'root', 'active', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, paired_for, achievement_conditions, state, created_at, "
            "updated_at) "
            "VALUES ('n_e', 'ses_m', 'end', 'End for REINDEX-TEST', 'open', "
            "'n_s', 'node', 'n_s', 'all done', 'active', ?, ?)",
            (now, now),
        )

    # FTS should be empty before mark_reached (subgraph is active)
    with storage.connect() as conn:
        count_before = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = 'n_s'"
        ).fetchone()[0]
    assert count_before == 0

    storage.mark_reached(session_id="ses_m", end_node_id="n_e", now=now)

    with storage.connect() as conn:
        count_after = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = 'n_s'"
        ).fetchone()[0]
        anchor = conn.execute(
            "SELECT anchor_text FROM subgraphs_fts WHERE start_node_id = 'n_s'"
        ).fetchone()[0]
    assert count_after == 1
    assert "REINDEX-TEST" in anchor


def test_bulk_import_subgraph_reindexes_archived(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_b', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_b', 'ses_b', 'r', 'active', ?)",
            (now,),
        )

    storage.bulk_import_subgraph(
        session_id="ses_b",
        root_id="root_b",
        nodes=[
            {
                "id": "imp_s", "type": "start", "text": "Imported start KEYWORD-A",
                "parent_id": "root_b", "parent_kind": "root",
            },
            {
                "id": "imp_e", "type": "end", "text": "Imported end KEYWORD-B",
                "parent_id": "imp_s", "parent_kind": "node",
                "paired_for": "imp_s",
                "achievement_conditions": "imported done",
            },
        ],
        edges=[],
        provenance="imported",
        state="archived",
        now=now,
    )

    with storage.connect() as conn:
        anchor = conn.execute(
            "SELECT anchor_text FROM subgraphs_fts WHERE start_node_id = 'imp_s'"
        ).fetchone()
    assert anchor is not None
    assert "KEYWORD-A" in anchor[0]
    assert "KEYWORD-B" in anchor[0]


def test_delete_subgraph_removes_fts_row(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    sid, sn, en = _seed_closed_subgraph(storage)
    storage._reindex_subgraph(start_node_id=sn)

    # Pre-condition: FTS row exists
    with storage.connect() as conn:
        before = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?",
            (sn,),
        ).fetchone()[0]
    assert before == 1

    # Transition through closed → deletable so delete_subgraph accepts.
    now2 = "2026-05-23T00:01:00Z"
    storage.dump_persist_subgraph(
        session_id=sid, start_node_id=sn, destination=None, now=now2
    )
    storage.delete_subgraph(session_id=sid, start_node_id=sn, now=now2)

    with storage.connect() as conn:
        after = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?",
            (sn,),
        ).fetchone()[0]
    assert after == 0


def test_force_delete_start_node_removes_fts_row(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    sid, sn, _en = _seed_closed_subgraph(storage)
    storage._reindex_subgraph(start_node_id=sn)

    with storage.connect() as conn:
        before = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?",
            (sn,),
        ).fetchone()[0]
    assert before == 1

    now2 = "2026-05-23T00:02:00Z"
    storage.force_delete_node(session_id=sid, node_id=sn, now=now2)

    with storage.connect() as conn:
        after = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?",
            (sn,),
        ).fetchone()[0]
    assert after == 0


def test_force_delete_non_start_node_keeps_fts_row(tmp_db_path: str) -> None:
    """force_delete on a non-start node should not touch the FTS row.

    (The subgraph identity is the start_node_id, which still exists.)
    """
    storage = Storage.open(tmp_db_path)
    sid, sn, en = _seed_closed_subgraph(storage)
    storage._reindex_subgraph(start_node_id=sn)
    now2 = "2026-05-23T00:03:00Z"

    storage.force_delete_node(session_id=sid, node_id=en, now=now2)

    with storage.connect() as conn:
        after = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?",
            (sn,),
        ).fetchone()[0]
    assert after == 1


def test_force_delete_non_start_child_reindexes_parent_subgraph(tmp_db_path: str) -> None:
    """Deleting a non-start child of a closed subgraph must rebuild the FTS row
    so deleted text no longer matches find_similar.

    Codex P2 #2: previously the FTS row was kept but stale.
    """
    storage = Storage.open(tmp_db_path)
    sid, sn, en = _seed_closed_subgraph(storage)
    now2 = "2026-05-24T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('child_d', ?, 'decision', 'UNIQUE-MARKER-XYZ to be deleted', "
            "'closed', ?, 'node', 'closed', ?, ?, ?)",
            (sid, en, now2, now2, now2),
        )
    storage._reindex_subgraph(start_node_id=sn)

    # Pre: the unique marker is in the FTS body_text (decision = body type)
    with storage.connect() as conn:
        body_before = conn.execute(
            "SELECT body_text FROM subgraphs_fts WHERE start_node_id = ?", (sn,),
        ).fetchone()[0]
    assert "UNIQUE-MARKER-XYZ" in body_before

    # Force delete the child node
    storage.force_delete_node(session_id=sid, node_id="child_d", now=now2)

    # Post: FTS row still exists for the start (subgraph identity preserved)
    # BUT the deleted child's text is no longer in body_text (reindexed)
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT body_text FROM subgraphs_fts WHERE start_node_id = ?", (sn,),
        ).fetchone()
    assert row is not None  # FTS row still present
    assert "UNIQUE-MARKER-XYZ" not in row[0]  # stale text gone


def _make_failing_connect(real_connect, fail_on_sql: str):
    """Return a patched connect() context manager that raises OperationalError
    when the first SQL statement matching *fail_on_sql* is executed.

    The wrapper delegates all other operations to the real connection.
    Raising inside the ``with real_connect() as conn:`` block causes SQLite
    to roll back the transaction — verifying atomicity.
    """
    import contextlib

    _inject_once = {"active": True}

    class _FailingConn:
        def __init__(self, real_conn):
            self._conn = real_conn
            self._armed = True

        def execute(self, sql, params=()):
            if self._armed and fail_on_sql in sql:
                self._armed = False
                raise sqlite3.OperationalError(f"simulated failure on: {fail_on_sql!r}")
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    @contextlib.contextmanager
    def patched():
        with real_connect() as conn:
            if _inject_once["active"]:
                _inject_once["active"] = False
                yield _FailingConn(conn)
            else:
                yield conn

    return patched


def test_force_delete_start_node_is_atomic(tmp_db_path: str) -> None:
    """Bug B: FTS DELETE and node DELETE must be one atomic transaction.

    Simulate a failure during the node-delete phase; assert the FTS row
    is NOT pre-removed. (Today's bug: pre-removes FTS before node delete.)
    """
    storage = Storage.open(tmp_db_path)
    sid, sn, _en = _seed_closed_subgraph(storage)
    storage._reindex_subgraph(start_node_id=sn)

    with storage.connect() as conn:
        before = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?", (sn,),
        ).fetchone()[0]
    assert before == 1

    real_connect = storage.connect
    storage.connect = _make_failing_connect(real_connect, "DELETE FROM nodes")  # type: ignore[method-assign]

    with pytest.raises(sqlite3.OperationalError):
        storage.force_delete_node(session_id=sid, node_id=sn, now="2026-05-24T01:00:00Z")

    storage.connect = real_connect  # type: ignore[method-assign]

    # After failure: FTS row MUST still exist (atomic invariant)
    with storage.connect() as conn:
        after = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?", (sn,),
        ).fetchone()[0]
    assert after == 1, "FTS row was deleted despite node-delete failure (non-atomic)"


def test_delete_subgraph_is_atomic(tmp_db_path: str) -> None:
    """Bug B: delete_subgraph node delete + FTS DELETE must be atomic.

    If the FTS DELETE fails after the main delete commits, the FTS row
    dangles. This test simulates the inverse failure.
    """
    storage = Storage.open(tmp_db_path)
    sid, sn, _en = _seed_closed_subgraph(storage)
    storage._reindex_subgraph(start_node_id=sn)
    now2 = "2026-05-24T01:00:00Z"
    storage.dump_persist_subgraph(
        session_id=sid, start_node_id=sn, destination=None, now=now2
    )

    real_connect = storage.connect
    storage.connect = _make_failing_connect(real_connect, "DELETE FROM subgraphs_fts")  # type: ignore[method-assign]

    with pytest.raises(sqlite3.OperationalError):
        storage.delete_subgraph(session_id=sid, start_node_id=sn, now=now2)

    storage.connect = real_connect  # type: ignore[method-assign]

    # After failure: BOTH the node AND the FTS row must still exist
    # (atomic: either both gone, or both present).
    with storage.connect() as conn:
        node_count = conn.execute(
            "SELECT count(*) FROM nodes WHERE id = ?", (sn,),
        ).fetchone()[0]
        fts_count = conn.execute(
            "SELECT count(*) FROM subgraphs_fts WHERE start_node_id = ?", (sn,),
        ).fetchone()[0]
    assert node_count == 1 and fts_count == 1, (
        f"Atomicity violated: node={node_count}, fts={fts_count} "
        "(expected both 1, both 0, but not split)"
    )
