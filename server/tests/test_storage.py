"""Tests for dpd_mcp_server.storage."""

from __future__ import annotations

import sqlite3

import pytest

from dpd_mcp_server.storage import Storage


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
        scope="decompose-propagate.protocol",
        label="exploration",
        now="2026-05-20T10:00:00Z",
    )

    row = storage.get_session(session_id="ses_abc")
    assert row["scope"] == "decompose-propagate.protocol"
    assert row["label"] == "exploration"
    assert row["started_at"] == "2026-05-20T10:00:00Z"
    assert row["updated_at"] == "2026-05-20T10:00:00Z"
    assert row["focus_node_id"] is None


def test_list_sessions_empty(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    assert storage.list_sessions(scope="some.scope") == []


def test_list_sessions_filters_by_scope(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_a", scope="alpha", label=None,
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_session(
        session_id="ses_b", scope="beta", label=None,
        now="2026-05-20T10:01:00Z",
    )
    storage.insert_session(
        session_id="ses_c", scope="alpha", label=None,
        now="2026-05-20T10:02:00Z",
    )

    rows = storage.list_sessions(scope="alpha")

    assert {r["id"] for r in rows} == {"ses_a", "ses_c"}


def test_list_sessions_scope_none_returns_top_level_only(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_top", scope=None, label=None,
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_session(
        session_id="ses_sub", scope="some.scope", label=None,
        now="2026-05-20T10:01:00Z",
    )

    rows = storage.list_sessions(scope=None)

    assert [r["id"] for r in rows] == ["ses_top"]


def test_list_sessions_sorted_by_updated_at_desc(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_old", scope="x", label=None,
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_session(
        session_id="ses_mid", scope="x", label=None,
        now="2026-05-20T11:00:00Z",
    )
    storage.insert_session(
        session_id="ses_new", scope="x", label=None,
        now="2026-05-20T12:00:00Z",
    )

    rows = storage.list_sessions(scope="x")

    assert [r["id"] for r in rows] == ["ses_new", "ses_mid", "ses_old"]


def test_insert_root_touches_session_updated_at(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T11:00:00Z",
    )

    row = storage.get_session(session_id="ses_1")
    assert row["updated_at"] == "2026-05-20T11:00:00Z"


def test_insert_node_touches_session_updated_at(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:30:00Z",
    )
    storage.insert_node(
        node_id="q1", session_id="ses_1", node_type="question",
        text="?", parent_id="root_a", parent_kind="root",
        now="2026-05-20T11:30:00Z",
    )

    row = storage.get_session(session_id="ses_1")
    assert row["updated_at"] == "2026-05-20T11:30:00Z"


def test_insert_node_under_parent_touches_session_updated_at(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:30:00Z",
    )
    storage.insert_node_under_parent(
        node_id="q1", session_id="ses_1",
        node_type="question", text="?",
        parent_id="root_a", now="2026-05-20T12:00:00Z",
    )

    row = storage.get_session(session_id="ses_1")
    assert row["updated_at"] == "2026-05-20T12:00:00Z"


def test_close_node_touches_session_updated_at(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:30:00Z",
    )
    storage.insert_node(
        node_id="q1", session_id="ses_1", node_type="question",
        text="?", parent_id="root_a", parent_kind="root",
        now="2026-05-20T11:00:00Z",
    )
    storage.close_node(
        session_id="ses_1", node_id="q1",
        closure_reason="resolved",
        now="2026-05-20T13:00:00Z",
    )

    row = storage.get_session(session_id="ses_1")
    assert row["updated_at"] == "2026-05-20T13:00:00Z"


def test_list_sessions_reflects_graph_activity_recency(tmp_db_path: str) -> None:
    """Sessions with graph activity rank ahead of newer-but-idle ones."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_old", scope="x", label=None,
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_session(
        session_id="ses_newer_but_idle", scope="x", label=None,
        now="2026-05-20T11:00:00Z",
    )
    # ses_old gets activity AFTER ses_newer_but_idle was created.
    storage.insert_root(
        root_id="root_a", session_id="ses_old", topic="t",
        now="2026-05-20T12:00:00Z",
    )

    rows = storage.list_sessions(scope="x")

    assert [r["id"] for r in rows] == ["ses_old", "ses_newer_but_idle"]


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


def test_insert_node_under_parent_atomic_classify(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:00:00Z",
    )

    parent_kind = storage.insert_node_under_parent(
        node_id="q1", session_id="ses_1",
        node_type="question", text="?",
        parent_id="root_a", now="2026-05-20T10:00:00Z",
    )

    assert parent_kind == "root"
    row = storage.get_node(session_id="ses_1", node_id="q1")
    assert row["parent_kind"] == "root"


def test_insert_node_under_parent_raises_when_parent_missing(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )

    with pytest.raises(ValueError):
        storage.insert_node_under_parent(
            node_id="x", session_id="ses_1",
            node_type="question", text="?",
            parent_id="missing", now="2026-05-20T10:00:00Z",
        )


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


# ---------------------------------------------------------------------------
# Phase 2.5: set_focus
# ---------------------------------------------------------------------------


def test_set_focus_updates_session_focus_node_id(tmp_db_path: str) -> None:
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

    storage.set_focus(
        session_id="ses_1", node_id="q1", now="2026-05-20T11:00:00Z",
    )

    row = storage.get_session(session_id="ses_1")
    assert row["focus_node_id"] == "q1"
    assert row["updated_at"] == "2026-05-20T11:00:00Z"


def test_set_focus_can_clear_with_none(tmp_db_path: str) -> None:
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
    storage.set_focus(
        session_id="ses_1", node_id="q1", now="2026-05-20T11:00:00Z",
    )

    storage.set_focus(
        session_id="ses_1", node_id=None, now="2026-05-20T12:00:00Z",
    )

    row = storage.get_session(session_id="ses_1")
    assert row["focus_node_id"] is None
    assert row["updated_at"] == "2026-05-20T12:00:00Z"


def test_set_focus_raises_when_node_missing(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )

    with pytest.raises(ValueError, match="node"):
        storage.set_focus(
            session_id="ses_1", node_id="ghost",
            now="2026-05-20T11:00:00Z",
        )


def test_set_focus_raises_when_session_missing(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)

    with pytest.raises(ValueError, match="session"):
        storage.set_focus(
            session_id="ses_missing", node_id=None,
            now="2026-05-20T11:00:00Z",
        )


# ---------------------------------------------------------------------------
# Phase 2.5: set_root_lifecycle
# ---------------------------------------------------------------------------


def test_set_root_lifecycle_archives_root(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:00:00Z",
    )

    result = storage.set_root_lifecycle(
        session_id="ses_1", root_id="root_a",
        lifecycle="archived", now="2026-05-20T11:00:00Z",
    )

    assert result is True
    active = storage.list_active_roots(session_id="ses_1")
    assert active == []
    session_row = storage.get_session(session_id="ses_1")
    assert session_row["updated_at"] == "2026-05-20T11:00:00Z"


def test_set_root_lifecycle_returns_false_when_root_missing(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )

    result = storage.set_root_lifecycle(
        session_id="ses_1", root_id="root_ghost",
        lifecycle="archived", now="2026-05-20T11:00:00Z",
    )

    assert result is False


def test_set_root_lifecycle_rejects_invalid_value(tmp_db_path: str) -> None:
    import sqlite3 as _sqlite3
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:00:00Z",
    )

    with pytest.raises(_sqlite3.IntegrityError):
        storage.set_root_lifecycle(
            session_id="ses_1", root_id="root_a",
            lifecycle="bogus", now="2026-05-20T11:00:00Z",
        )


# ---------------------------------------------------------------------------
# Phase 2.5: list_open_nodes
# ---------------------------------------------------------------------------


def test_list_open_nodes_empty_session(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )

    assert storage.list_open_nodes(session_id="ses_1") == []


def test_list_open_nodes_returns_only_open_nodes(tmp_db_path: str) -> None:
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
        now="2026-05-20T10:01:00Z",
    )
    storage.insert_node(
        node_id="q2", session_id="ses_1", node_type="question",
        text="?", parent_id="root_a", parent_kind="root",
        now="2026-05-20T10:02:00Z",
    )
    storage.close_node(
        session_id="ses_1", node_id="q1",
        closure_reason="resolved", now="2026-05-20T10:03:00Z",
    )

    rows = storage.list_open_nodes(session_id="ses_1")

    assert [r["id"] for r in rows] == ["q2"]


def test_list_open_nodes_restricted_to_root_subtree(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="A",
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_root(
        root_id="root_b", session_id="ses_1", topic="B",
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_node(
        node_id="qa", session_id="ses_1", node_type="question",
        text="?", parent_id="root_a", parent_kind="root",
        now="2026-05-20T10:01:00Z",
    )
    storage.insert_node(
        node_id="qb", session_id="ses_1", node_type="question",
        text="?", parent_id="root_b", parent_kind="root",
        now="2026-05-20T10:02:00Z",
    )

    rows = storage.list_open_nodes(session_id="ses_1", root_id="root_a")

    assert [r["id"] for r in rows] == ["qa"]


# ---------------------------------------------------------------------------
# Phase 2.5: add_edge / list_edges
# ---------------------------------------------------------------------------


def test_set_focus_rejects_node_from_different_session(tmp_db_path: str) -> None:
    """set_focus must reject a node that exists in a *different* session,
    even if the bare id happens to be valid somewhere in the database."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_a", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_session(
        session_id="ses_b", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_a", topic="t",
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_node(
        node_id="n1", session_id="ses_a", node_type="question",
        text="?", parent_id="root_a", parent_kind="root",
        now="2026-05-20T10:01:00Z",
    )

    with pytest.raises(ValueError, match="node"):
        storage.set_focus(
            session_id="ses_b", node_id="n1",
            now="2026-05-20T11:00:00Z",
        )


def _seed_two_endpoints(storage: Storage) -> None:
    """Seed session ses_1 with root_a and two question nodes n1, n2."""
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_node(
        node_id="n1", session_id="ses_1", node_type="question",
        text="?", parent_id="root_a", parent_kind="root",
        now="2026-05-20T10:01:00Z",
    )
    storage.insert_node(
        node_id="n2", session_id="ses_1", node_type="answer",
        text="!", parent_id="n1", parent_kind="node",
        now="2026-05-20T10:02:00Z",
    )


def test_add_edge_round_trips_through_list(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)

    edge_id = storage.add_edge(
        session_id="ses_1",
        from_node="n1", to_node="n2",
        edge_type="requires", reason="dep",
        now="2026-05-20T11:00:00Z",
    )

    rows = storage.list_edges(session_id="ses_1")
    assert len(rows) == 1
    assert rows[0]["id"] == edge_id
    assert rows[0]["from_node"] == "n1"
    assert rows[0]["to_node"] == "n2"
    assert rows[0]["type"] == "requires"
    assert rows[0]["reason"] == "dep"


def test_add_edge_allows_root_endpoints(tmp_db_path: str) -> None:
    """Roots are valid edge endpoints (e.g., derived_from a root)."""
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)

    storage.add_edge(
        session_id="ses_1",
        from_node="n2", to_node="root_a",
        edge_type="derived_from", reason=None,
        now="2026-05-20T11:00:00Z",
    )

    rows = storage.list_edges(session_id="ses_1")
    assert len(rows) == 1
    assert rows[0]["from_node"] == "n2"
    assert rows[0]["to_node"] == "root_a"


def test_add_edge_rejects_unknown_from_node(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)

    with pytest.raises(ValueError, match="from_node"):
        storage.add_edge(
            session_id="ses_1",
            from_node="ghost", to_node="n2",
            edge_type="requires", reason=None,
            now="2026-05-20T11:00:00Z",
        )


def test_add_edge_rejects_unknown_to_node(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)

    with pytest.raises(ValueError, match="to_node"):
        storage.add_edge(
            session_id="ses_1",
            from_node="n1", to_node="ghost",
            edge_type="requires", reason=None,
            now="2026-05-20T11:00:00Z",
        )


def test_add_edge_touches_session_updated_at(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)

    storage.add_edge(
        session_id="ses_1",
        from_node="n1", to_node="n2",
        edge_type="requires", reason=None,
        now="2026-05-20T11:30:00Z",
    )

    session = storage.get_session(session_id="ses_1")
    assert session["updated_at"] == "2026-05-20T11:30:00Z"


def test_get_root_round_trips(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="Architecture",
        now="2026-05-20T10:01:00Z",
    )

    row = storage.get_root(session_id="ses_1", root_id="root_a")

    assert row is not None
    assert row["topic"] == "Architecture"
    assert row["lifecycle"] == "active"


def test_get_root_returns_none_when_missing(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    assert storage.get_root(session_id="ses_1", root_id="root_ghost") is None


# ---------------------------------------------------------------------------
# Phase 2.5: resolve_hypothesis_branch (atomic composite)
# ---------------------------------------------------------------------------


def _seed_hypothesis_branch(storage: Storage) -> None:
    """Build root_a with 3 open hypothesis children: h1, h2, h3."""
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-20T10:00:00Z",
    )
    for nid in ["h1", "h2", "h3"]:
        storage.insert_node(
            node_id=nid, session_id="ses_1", node_type="hypothesis",
            text=f"option {nid}", parent_id="root_a", parent_kind="root",
            now="2026-05-20T10:01:00Z",
        )


def test_resolve_hypothesis_branch_closes_target_resolved_and_siblings_rejected(
    tmp_db_path: str,
) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    result = storage.resolve_hypothesis_branch(
        session_id="ses_1", hyp_id="h2",
        decision_id="d1", decision_text="adopt h2",
        rationale_id="r1", rationale_text="best fit",
        now="2026-05-20T11:00:00Z",
    )

    assert result["hyp_id"] == "h2"
    assert result["decision_id"] == "d1"
    assert result["rationale_id"] == "r1"
    assert result["closed_siblings"] == ["h1", "h3"]
    assert isinstance(result["derived_from_edge_id"], int)

    h2 = storage.get_node(session_id="ses_1", node_id="h2")
    assert h2["status"] == "closed"
    assert h2["closure_reason"] == "resolved"

    h1 = storage.get_node(session_id="ses_1", node_id="h1")
    h3 = storage.get_node(session_id="ses_1", node_id="h3")
    assert h1["closure_reason"] == "rejected"
    assert h3["closure_reason"] == "rejected"

    d1 = storage.get_node(session_id="ses_1", node_id="d1")
    assert d1["type"] == "decision"
    assert d1["status"] == "closed"
    assert d1["closure_reason"] == "resolved"
    assert d1["parent_id"] == "root_a"
    assert d1["parent_kind"] == "root"

    r1 = storage.get_node(session_id="ses_1", node_id="r1")
    assert r1["type"] == "rationale"
    assert r1["parent_id"] == "d1"
    assert r1["parent_kind"] == "node"


def test_resolve_hypothesis_branch_without_rationale(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    result = storage.resolve_hypothesis_branch(
        session_id="ses_1", hyp_id="h1",
        decision_id="d1", decision_text="adopt h1",
        rationale_id=None, rationale_text=None,
        now="2026-05-20T11:00:00Z",
    )

    assert result["rationale_id"] is None
    assert storage.get_node(session_id="ses_1", node_id="d1") is not None


def test_resolve_hypothesis_branch_raises_when_target_missing(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    with pytest.raises(ValueError, match="hypothesis"):
        storage.resolve_hypothesis_branch(
            session_id="ses_1", hyp_id="h_ghost",
            decision_id="d1", decision_text="x",
            rationale_id=None, rationale_text=None,
            now="2026-05-20T11:00:00Z",
        )


def test_resolve_hypothesis_branch_rejects_non_hypothesis_target(tmp_db_path: str) -> None:
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
        now="2026-05-20T10:01:00Z",
    )

    with pytest.raises(ValueError, match="not 'hypothesis'"):
        storage.resolve_hypothesis_branch(
            session_id="ses_1", hyp_id="q1",
            decision_id="d1", decision_text="x",
            rationale_id=None, rationale_text=None,
            now="2026-05-20T11:00:00Z",
        )


def test_resolve_hypothesis_branch_leaves_closed_siblings_alone(tmp_db_path: str) -> None:
    """Already-closed sibling hypotheses are not re-closed."""
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)
    storage.close_node(
        session_id="ses_1", node_id="h1",
        closure_reason="invalidated", now="2026-05-20T10:30:00Z",
    )

    result = storage.resolve_hypothesis_branch(
        session_id="ses_1", hyp_id="h2",
        decision_id="d1", decision_text="x",
        rationale_id=None, rationale_text=None,
        now="2026-05-20T11:00:00Z",
    )

    # Only h3 is auto-rejected; h1 keeps its earlier closure_reason.
    assert result["closed_siblings"] == ["h3"]
    h1 = storage.get_node(session_id="ses_1", node_id="h1")
    assert h1["closure_reason"] == "invalidated"


def test_list_edges_filters_by_from_node(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)
    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="n2",
        edge_type="requires", reason=None,
        now="2026-05-20T10:01:00Z",
    )
    storage.add_edge(
        session_id="ses_1", from_node="n2", to_node="root_a",
        edge_type="derived_from", reason=None,
        now="2026-05-20T10:02:00Z",
    )

    rows = storage.list_edges(session_id="ses_1", from_node="n1")

    assert len(rows) == 1
    assert rows[0]["from_node"] == "n1"
    assert rows[0]["to_node"] == "n2"


def test_list_edges_filters_by_to_node(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)
    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="n2",
        edge_type="requires", reason=None,
        now="2026-05-20T10:01:00Z",
    )
    storage.add_edge(
        session_id="ses_1", from_node="root_a", to_node="n2",
        edge_type="blocks", reason=None,
        now="2026-05-20T10:02:00Z",
    )
    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="root_a",
        edge_type="derived_from", reason=None,
        now="2026-05-20T10:03:00Z",
    )

    rows = storage.list_edges(session_id="ses_1", to_node="n2")

    assert [r["from_node"] for r in rows] == ["n1", "root_a"]


def test_list_unblocked_open_nodes_empty_session(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-20T10:00:00Z"
    )

    assert storage.list_unblocked_open_nodes(session_id="ses_1") == []


def test_list_unblocked_open_nodes_returns_all_when_no_edges(tmp_db_path: str) -> None:
    """No edges = no blockers, so all open nodes are unblocked."""
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)

    rows = storage.list_unblocked_open_nodes(session_id="ses_1")

    assert {r["id"] for r in rows} == {"n1", "n2"}


def test_list_unblocked_open_nodes_excludes_blocked_nodes(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)
    # n2 is blocked by n1 (open). n2 should NOT be in unblocked list.
    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="n2",
        edge_type="blocks", reason=None,
        now="2026-05-20T10:03:00Z",
    )

    rows = storage.list_unblocked_open_nodes(session_id="ses_1")

    assert [r["id"] for r in rows] == ["n1"]


def test_list_unblocked_open_nodes_includes_after_blocker_closes(tmp_db_path: str) -> None:
    """Once the blocker closes, the blocked node becomes unblocked."""
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)
    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="n2",
        edge_type="blocks", reason=None,
        now="2026-05-20T10:03:00Z",
    )

    storage.close_node(
        session_id="ses_1", node_id="n1",
        closure_reason="resolved", now="2026-05-20T11:00:00Z",
    )

    rows = storage.list_unblocked_open_nodes(session_id="ses_1")

    assert [r["id"] for r in rows] == ["n2"]


def test_list_unblocked_open_nodes_respects_custom_blocker_edge_type(tmp_db_path: str) -> None:
    """Default blocker edge type is 'blocks'; caller can override."""
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)
    # n1 'requires' n2 — in this caller's vocabulary, "requires" is what blocks.
    storage.add_edge(
        session_id="ses_1", from_node="n2", to_node="n1",
        edge_type="requires", reason=None,
        now="2026-05-20T10:03:00Z",
    )

    # With default 'blocks' type, no blocker is recognized.
    default_rows = storage.list_unblocked_open_nodes(session_id="ses_1")
    assert {r["id"] for r in default_rows} == {"n1", "n2"}

    # With 'requires', n1 is blocked by open n2.
    rows = storage.list_unblocked_open_nodes(
        session_id="ses_1", blocker_edge_type="requires",
    )
    assert [r["id"] for r in rows] == ["n2"]


def test_list_unblocked_open_nodes_restricted_to_root(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)
    storage.insert_root(
        root_id="root_b", session_id="ses_1", topic="B",
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_node(
        node_id="m1", session_id="ses_1", node_type="question",
        text="?", parent_id="root_b", parent_kind="root",
        now="2026-05-20T10:01:00Z",
    )

    rows = storage.list_unblocked_open_nodes(
        session_id="ses_1", root_id="root_a",
    )

    # Only root_a's descendants: n1, n2 (m1 belongs to root_b).
    assert {r["id"] for r in rows} == {"n1", "n2"}


def test_resolve_hypothesis_branch_creates_derived_from_edge(tmp_db_path: str) -> None:
    """The decision node should be linked to the accepted hypothesis via a
    'derived_from' edge automatically (so the source of the decision is
    structurally discoverable, not just textually implied)."""
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    storage.resolve_hypothesis_branch(
        session_id="ses_1", hyp_id="h2",
        decision_id="d1", decision_text="adopt h2",
        rationale_id=None, rationale_text=None,
        now="2026-05-20T11:00:00Z",
    )

    edges = storage.list_edges(session_id="ses_1")
    assert len(edges) == 1
    assert edges[0]["from_node"] == "d1"
    assert edges[0]["to_node"] == "h2"
    assert edges[0]["type"] == "derived_from"


def test_resolve_hypothesis_branch_rejects_already_closed_hypothesis(tmp_db_path: str) -> None:
    """A retry or accidental re-call on a closed hypothesis must fail loud,
    not overwrite closure_reason and create a duplicate decision."""
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)
    # First accept resolves h1, closes h2/h3 rejected, inserts d1.
    storage.resolve_hypothesis_branch(
        session_id="ses_1", hyp_id="h1",
        decision_id="d1", decision_text="adopt h1",
        rationale_id=None, rationale_text=None,
        now="2026-05-20T11:00:00Z",
    )

    # Second call on the same (now-closed) h1 must raise.
    with pytest.raises(ValueError, match="open"):
        storage.resolve_hypothesis_branch(
            session_id="ses_1", hyp_id="h1",
            decision_id="d2", decision_text="reaccept",
            rationale_id=None, rationale_text=None,
            now="2026-05-20T12:00:00Z",
        )

    # Equally, a previously-rejected sibling must not be acceptable.
    with pytest.raises(ValueError, match="open"):
        storage.resolve_hypothesis_branch(
            session_id="ses_1", hyp_id="h2",
            decision_id="d3", decision_text="late switch",
            rationale_id=None, rationale_text=None,
            now="2026-05-20T13:00:00Z",
        )

    # Confirm no duplicate decision was inserted.
    assert storage.get_node(session_id="ses_1", node_id="d2") is None
    assert storage.get_node(session_id="ses_1", node_id="d3") is None
