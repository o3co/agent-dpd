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
