"""Tests for prgp_mcp_server.storage."""

from __future__ import annotations

import sqlite3

import pytest

from prgp_mcp_server.storage import Storage


def test_open_creates_required_tables(tmp_db_path: str) -> None:
    Storage.open(tmp_db_path)

    with sqlite3.connect(tmp_db_path) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {"sessions", "roots", "nodes", "edges"} <= names


def test_open_enables_wal_mode(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    with storage.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_insert_session_round_trips_through_get(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_abc",
        scope="problem-graph.protocol",
        label="exploration",
        now="2026-05-20T10:00:00Z",
    )

    row = storage.get_session(session_id="ses_abc")
    assert row["scope"] == "problem-graph.protocol"
    assert row["label"] == "exploration"
    assert row["started_at"] == "2026-05-20T10:00:00Z"
    assert row["updated_at"] == "2026-05-20T10:00:00Z"
    assert row["focus_node_id"] is None


def test_insert_root_and_list_active_roots(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a",
        session_id="ses_1",
        topic="MCP architecture",
        now="2026-05-20T10:01:00Z",
    )
    storage.insert_root(
        root_id="root_b",
        session_id="ses_1",
        topic="Storage choice",
        now="2026-05-20T10:02:00Z",
    )

    active = storage.list_active_roots(session_id="ses_1")

    assert [r["id"] for r in active] == ["root_a", "root_b"]
    assert active[0]["lifecycle"] == "active"
    assert active[0]["topic"] == "MCP architecture"


def test_insert_node_and_get_node_round_trip(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a",
        session_id="ses_1",
        topic="t",
        now="2026-05-20T10:00:00Z",
    )

    storage.insert_node(
        node_id="q1",
        session_id="ses_1",
        node_type="question",
        text="Should we use MCP?",
        parent_id="root_a",
        parent_kind="root",
        now="2026-05-20T10:05:00Z",
    )

    row = storage.get_node(session_id="ses_1", node_id="q1")

    assert row is not None
    assert row["type"] == "question"
    assert row["text"] == "Should we use MCP?"
    assert row["status"] == "open"
    assert row["closure_reason"] is None
    assert row["parent_id"] == "root_a"
    assert row["parent_kind"] == "root"


def test_get_node_returns_none_when_missing(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    assert storage.get_node(session_id="ses_1", node_id="missing") is None


def test_close_node_marks_status_and_reason(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a",
        session_id="ses_1",
        topic="t",
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_node(
        node_id="q1",
        session_id="ses_1",
        node_type="question",
        text="?",
        parent_id="root_a",
        parent_kind="root",
        now="2026-05-20T10:00:00Z",
    )

    result = storage.close_node(
        session_id="ses_1",
        node_id="q1",
        closure_reason="resolved",
        now="2026-05-20T11:00:00Z",
    )

    assert result is True
    row = storage.get_node(session_id="ses_1", node_id="q1")
    assert row["status"] == "closed"
    assert row["closure_reason"] == "resolved"
    assert row["updated_at"] == "2026-05-20T11:00:00Z"


def test_close_node_returns_false_when_node_missing(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )

    result = storage.close_node(
        session_id="ses_1",
        node_id="does_not_exist",
        closure_reason="resolved",
        now="2026-05-20T11:00:00Z",
    )

    assert result is False


def test_walk_subtree_returns_descendants_depth_first(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a",
        session_id="ses_1",
        topic="t",
        now="2026-05-20T10:00:00Z",
    )
    # Tree:
    # root_a
    # ├── q1
    # │   └── a1
    # └── q2
    storage.insert_node(node_id="q1", session_id="ses_1", node_type="question",
                        text="?", parent_id="root_a", parent_kind="root",
                        now="2026-05-20T10:01:00Z")
    storage.insert_node(node_id="a1", session_id="ses_1", node_type="answer",
                        text="!", parent_id="q1", parent_kind="node",
                        now="2026-05-20T10:02:00Z")
    storage.insert_node(node_id="q2", session_id="ses_1", node_type="question",
                        text="?", parent_id="root_a", parent_kind="root",
                        now="2026-05-20T10:03:00Z")

    nodes = storage.walk_subtree(session_id="ses_1", root_id="root_a")
    ids = [n["id"] for n in nodes]

    assert ids == ["q1", "a1", "q2"]


def test_walk_subtree_handles_deep_chain(tmp_db_path: str) -> None:
    """Regression: previously hit Python's recursion limit at ~1000 deep."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:00:00Z",
    )

    depth = 2000
    parent_id = "root_a"
    parent_kind = "root"
    for i in range(depth):
        node_id = f"n{i:04d}"
        storage.insert_node(
            node_id=node_id, session_id="ses_1", node_type="question",
            text=f"node {i}", parent_id=parent_id, parent_kind=parent_kind,
            now=f"2026-05-20T10:{i // 3600:02d}:{i % 3600:04d}Z",
        )
        parent_id = node_id
        parent_kind = "node"

    nodes = storage.walk_subtree(session_id="ses_1", root_id="root_a")
    assert len(nodes) == depth
    assert nodes[0]["id"] == "n0000"
    assert nodes[-1]["id"] == f"n{depth - 1:04d}"


def test_insert_node_rejects_invalid_type_at_storage_layer(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:00:00Z",
    )
    import sqlite3 as _sqlite3
    with pytest.raises(_sqlite3.IntegrityError):
        storage.insert_node(
            node_id="bad", session_id="ses_1",
            node_type="bogus_type", text="?",
            parent_id="root_a", parent_kind="root",
            now="2026-05-20T10:00:00Z",
        )


def test_close_node_rejects_invalid_closure_reason_at_storage_layer(
    tmp_db_path: str,
) -> None:
    """The DB-level CHECK constraint enforces closure_reason vocabulary."""
    import sqlite3 as _sqlite3
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_node(
        node_id="q1", session_id="ses_1", node_type="question",
        text="?", parent_id="root_a", parent_kind="root",
        now="2026-05-20T10:00:00Z",
    )

    with pytest.raises(_sqlite3.IntegrityError):
        storage.close_node(
            session_id="ses_1", node_id="q1",
            closure_reason="bogus_reason",
            now="2026-05-20T11:00:00Z",
        )
