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


def test_list_edges_filters_by_edge_type(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)

    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="n2",
        edge_type="derived_from", reason=None, now="2026-05-21T10:00:00Z",
    )
    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="n2",
        edge_type="contradicts", reason=None, now="2026-05-21T10:01:00Z",
    )

    rows = storage.list_edges(session_id="ses_1", edge_type="derived_from")
    assert len(rows) == 1
    assert rows[0]["type"] == "derived_from"

    rows = storage.list_edges(session_id="ses_1", edge_type="contradicts")
    assert len(rows) == 1
    assert rows[0]["type"] == "contradicts"


def test_list_edges_edge_type_combines_with_endpoint_filters(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)

    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="n2",
        edge_type="derived_from", reason=None, now="2026-05-21T10:00:00Z",
    )
    storage.add_edge(
        session_id="ses_1", from_node="n2", to_node="n1",
        edge_type="derived_from", reason=None, now="2026-05-21T10:01:00Z",
    )

    rows = storage.list_edges(
        session_id="ses_1", from_node="n1", edge_type="derived_from",
    )
    assert len(rows) == 1
    assert rows[0]["from_node"] == "n1"


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


def test_list_unblocked_open_nodes_treats_active_root_as_blocker(tmp_db_path: str) -> None:
    """Active roots can serve as blockers (active root counts the same as
    open node from the blocked-set perspective)."""
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)
    # root_a (lifecycle=active) blocks n2 — n2 should NOT appear in unblocked.
    storage.add_edge(
        session_id="ses_1", from_node="root_a", to_node="n2",
        edge_type="blocks", reason=None,
        now="2026-05-20T10:03:00Z",
    )

    rows = storage.list_unblocked_open_nodes(session_id="ses_1")

    assert "n2" not in {r["id"] for r in rows}


def test_list_unblocked_open_nodes_treats_archived_root_as_unblocked(tmp_db_path: str) -> None:
    """An archived root no longer blocks. Closing the root branches via
    set_root_lifecycle('archived') must release nodes it was blocking."""
    storage = Storage.open(tmp_db_path)
    _seed_two_endpoints(storage)
    storage.add_edge(
        session_id="ses_1", from_node="root_a", to_node="n2",
        edge_type="blocks", reason=None,
        now="2026-05-20T10:03:00Z",
    )
    # Archive the blocker root.
    storage.set_root_lifecycle(
        session_id="ses_1", root_id="root_a",
        lifecycle="archived", now="2026-05-20T11:00:00Z",
    )

    rows = storage.list_unblocked_open_nodes(session_id="ses_1")

    assert "n2" in {r["id"] for r in rows}


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


# ---------------------------------------------------------------------------
# resolve_branch tests
# ---------------------------------------------------------------------------


def test_resolve_branch_all_true_closes_all_resolved(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)  # root_a + h1/h2/h3 all open hypothesis

    result = storage.resolve_branch(
        session_id="ses_1",
        parent_id="root_a",
        parent_kind="root",
        results=[
            {"node_id": "h1", "closure_reason": "resolved"},
            {"node_id": "h2", "closure_reason": "resolved"},
            {"node_id": "h3", "closure_reason": "resolved"},
        ],
        decision_id="d1",
        decision_text="all three confirmed",
        rationale_id=None,
        rationale_text=None,
        derived_from_node_ids=["h1", "h2", "h3"],
        now="2026-05-21T11:00:00Z",
    )

    # closed_nodes: list of full node dicts
    assert sorted(n["id"] for n in result["closed_nodes"]) == ["h1", "h2", "h3"]
    for node_dict in result["closed_nodes"]:
        assert node_dict["status"] == "closed"
        assert node_dict["closure_reason"] == "resolved"

    # decision_node: full row dict
    assert result["decision_node"] is not None
    assert result["decision_node"]["id"] == "d1"
    assert result["decision_node"]["type"] == "decision"
    assert result["decision_node"]["parent_id"] == "root_a"
    assert result["decision_node"]["parent_kind"] == "root"

    # rationale_node: None (not requested)
    assert result["rationale_node"] is None

    # edges_created: list of full edge dicts
    assert len(result["edges_created"]) == 3
    assert all(e["type"] == "derived_from" for e in result["edges_created"])
    assert all(e["from_node"] == "d1" for e in result["edges_created"])
    assert sorted(e["to_node"] for e in result["edges_created"]) == ["h1", "h2", "h3"]

    # Verify state in DB
    for nid in ["h1", "h2", "h3"]:
        node = storage.get_node(session_id="ses_1", node_id=nid)
        assert node["status"] == "closed"
        assert node["closure_reason"] == "resolved"

    d1 = storage.get_node(session_id="ses_1", node_id="d1")
    assert d1["type"] == "decision"
    assert d1["parent_id"] == "root_a"
    assert d1["parent_kind"] == "root"

    edges = storage.list_edges(
        session_id="ses_1", from_node="d1", edge_type="derived_from",
    )
    assert len(edges) == 3
    assert sorted(e["to_node"] for e in edges) == ["h1", "h2", "h3"]


def test_resolve_branch_all_false_closes_all_rejected(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    result = storage.resolve_branch(
        session_id="ses_1",
        parent_id="root_a",
        parent_kind="root",
        results=[
            {"node_id": "h1", "closure_reason": "rejected"},
            {"node_id": "h2", "closure_reason": "rejected"},
            {"node_id": "h3", "closure_reason": "rejected"},
        ],
        decision_id="d1",
        decision_text="all candidates are dead-ends",
        rationale_id=None,
        rationale_text=None,
        derived_from_node_ids=["h1", "h2", "h3"],
        now="2026-05-21T11:00:00Z",
    )
    for nid in ["h1", "h2", "h3"]:
        node = storage.get_node(session_id="ses_1", node_id=nid)
        assert node["closure_reason"] == "rejected"
    assert len(result["edges_created"]) == 3


def test_resolve_branch_mixed_verdicts(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    result = storage.resolve_branch(
        session_id="ses_1",
        parent_id="root_a",
        parent_kind="root",
        results=[
            {"node_id": "h1", "closure_reason": "resolved"},
            {"node_id": "h2", "closure_reason": "rejected"},
            {"node_id": "h3", "closure_reason": "invalidated"},
        ],
        decision_id="d1",
        decision_text="h1 wins, h2 wrong, h3 invalidated by new constraint",
        rationale_id=None,
        rationale_text=None,
        derived_from_node_ids=None,
        now="2026-05-21T11:00:00Z",
    )
    h1 = storage.get_node(session_id="ses_1", node_id="h1")
    h2 = storage.get_node(session_id="ses_1", node_id="h2")
    h3 = storage.get_node(session_id="ses_1", node_id="h3")
    assert h1["closure_reason"] == "resolved"
    assert h2["closure_reason"] == "rejected"
    assert h3["closure_reason"] == "invalidated"
    assert result["edges_created"] == []


def test_resolve_branch_decision_only_no_results(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    result = storage.resolve_branch(
        session_id="ses_1",
        parent_id="root_a",
        parent_kind="root",
        results=[],
        decision_id="d1",
        decision_text="adding a post-hoc summary decision",
        rationale_id=None,
        rationale_text=None,
        derived_from_node_ids=None,
        now="2026-05-21T11:00:00Z",
    )
    assert result["closed_nodes"] == []
    assert result["decision_node"] is not None
    assert result["decision_node"]["id"] == "d1"
    d1 = storage.get_node(session_id="ses_1", node_id="d1")
    assert d1["type"] == "decision"


def test_resolve_branch_rejects_empty_results_without_decision(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    with pytest.raises(ValueError, match="requires either results or decision_text"):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="root_a",
            parent_kind="root",
            results=[],
            decision_id=None,
            decision_text=None,
            rationale_id=None,
            rationale_text=None,
            derived_from_node_ids=None,
            now="2026-05-21T11:00:00Z",
        )


def test_resolve_branch_rejects_rationale_without_decision(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    with pytest.raises(ValueError, match="rationale_text requires decision_text"):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="root_a",
            parent_kind="root",
            results=[{"node_id": "h1", "closure_reason": "resolved"}],
            decision_id=None,
            decision_text=None,
            rationale_id="r1",
            rationale_text="orphan rationale",
            derived_from_node_ids=None,
            now="2026-05-21T11:00:00Z",
        )


def test_resolve_branch_rejects_node_not_direct_child(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)
    # Add a grandchild under h1
    storage.insert_node(
        node_id="h1a", session_id="ses_1", node_type="evidence",
        text="grandchild", parent_id="h1", parent_kind="node",
        now="2026-05-21T10:30:00Z",
    )

    with pytest.raises(ValueError, match="not a direct child"):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="root_a",
            parent_kind="root",
            results=[{"node_id": "h1a", "closure_reason": "resolved"}],
            decision_id="d1",
            decision_text="x",
            rationale_id=None,
            rationale_text=None,
            derived_from_node_ids=None,
            now="2026-05-21T11:00:00Z",
        )


def test_resolve_branch_rolls_back_on_partial_failure(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    with pytest.raises(ValueError):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="root_a",
            parent_kind="root",
            results=[
                {"node_id": "h1", "closure_reason": "resolved"},
                {"node_id": "nope", "closure_reason": "resolved"},  # missing
            ],
            decision_id="d1",
            decision_text="x",
            rationale_id=None,
            rationale_text=None,
            derived_from_node_ids=None,
            now="2026-05-21T11:00:00Z",
        )
    # h1 must still be open (rollback)
    h1 = storage.get_node(session_id="ses_1", node_id="h1")
    assert h1["status"] == "open"
    # No decision should exist
    assert storage.get_node(session_id="ses_1", node_id="d1") is None


def test_resolve_branch_rejects_derived_from_without_decision(tmp_db_path: str) -> None:
    """derived_from_node_ids requires decision_text — symmetric with rationale guard."""
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    with pytest.raises(ValueError, match="derived_from_node_ids requires decision_text"):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="root_a",
            parent_kind="root",
            results=[{"node_id": "h1", "closure_reason": "resolved"}],
            decision_id=None,
            decision_text=None,
            rationale_id=None,
            rationale_text=None,
            derived_from_node_ids=["h1"],
            now="2026-05-21T11:00:00Z",
        )


def test_resolve_branch_rejects_unknown_derived_from_target(tmp_db_path: str) -> None:
    """derived_from_node_ids targets must exist in the session (nodes or roots)."""
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    with pytest.raises(ValueError, match="derived_from target"):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="root_a",
            parent_kind="root",
            results=[{"node_id": "h1", "closure_reason": "resolved"}],
            decision_id="d1",
            decision_text="adopt h1",
            rationale_id=None,
            rationale_text=None,
            derived_from_node_ids=["h1", "ghost_node"],  # ghost_node does not exist
            now="2026-05-21T11:00:00Z",
        )
    # Transaction must have rolled back: h1 still open, d1 not inserted
    h1 = storage.get_node(session_id="ses_1", node_id="h1")
    assert h1["status"] == "open"
    assert storage.get_node(session_id="ses_1", node_id="d1") is None


def test_resolve_branch_derived_from_allows_root_as_target(tmp_db_path: str) -> None:
    """A root_id is a valid derived_from target (nodes OR roots lookup)."""
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    result = storage.resolve_branch(
        session_id="ses_1",
        parent_id="root_a",
        parent_kind="root",
        results=[{"node_id": "h1", "closure_reason": "resolved"}],
        decision_id="d1",
        decision_text="derived from root",
        rationale_id=None,
        rationale_text=None,
        derived_from_node_ids=["root_a"],  # root is a valid target
        now="2026-05-21T11:00:00Z",
    )
    assert len(result["edges_created"]) == 1
    assert result["edges_created"][0]["to_node"] == "root_a"


def test_resolve_branch_rejects_unknown_parent_in_decision_only(tmp_db_path: str) -> None:
    """Decision-only path (results=[]) must validate parent existence —
    Multi-agent review (Claude + Codex) convergent must-fix.
    """
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)  # gives root_a + h1/h2/h3

    with pytest.raises(ValueError, match="parent root 'totally_bogus' not found"):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="totally_bogus",
            parent_kind="root",
            results=[],
            decision_id="d1",
            decision_text="orphan attempt",
            rationale_id=None,
            rationale_text=None,
            derived_from_node_ids=None,
            now="2026-05-21T11:00:00Z",
        )
    # Decision must not have been inserted
    assert storage.get_node(session_id="ses_1", node_id="d1") is None


def test_resolve_branch_rejects_unknown_parent_with_results(tmp_db_path: str) -> None:
    """Same parent check applies even when results are provided — earlier path
    also relied on the loop to catch this indirectly via direct-child check,
    but the unified check is clearer.
    """
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    with pytest.raises(ValueError, match="parent root 'totally_bogus' not found"):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="totally_bogus",
            parent_kind="root",
            results=[{"node_id": "h1", "closure_reason": "resolved"}],
            decision_id="d1",
            decision_text="x",
            rationale_id=None,
            rationale_text=None,
            derived_from_node_ids=None,
            now="2026-05-21T11:00:00Z",
        )
    # h1 must still be open (rollback)
    h1 = storage.get_node(session_id="ses_1", node_id="h1")
    assert h1["status"] == "open"


def test_resolve_branch_rejects_decision_text_without_decision_id(tmp_db_path: str) -> None:
    """decision_text requires decision_id (caller-paired). Codex re-review finding."""
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    with pytest.raises(ValueError, match="decision_id required when decision_text"):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="root_a",
            parent_kind="root",
            results=[{"node_id": "h1", "closure_reason": "resolved"}],
            decision_id=None,
            decision_text="dangling text",
            rationale_id=None,
            rationale_text=None,
            derived_from_node_ids=None,
            now="2026-05-21T11:00:00Z",
        )
    # h1 must still be open (rollback / no mutation)
    h1 = storage.get_node(session_id="ses_1", node_id="h1")
    assert h1["status"] == "open"


def test_resolve_branch_rejects_rationale_text_without_rationale_id(tmp_db_path: str) -> None:
    """rationale_text requires rationale_id (caller-paired). Codex re-review finding."""
    storage = Storage.open(tmp_db_path)
    _seed_hypothesis_branch(storage)

    with pytest.raises(ValueError, match="rationale_id required when rationale_text"):
        storage.resolve_branch(
            session_id="ses_1",
            parent_id="root_a",
            parent_kind="root",
            results=[{"node_id": "h1", "closure_reason": "resolved"}],
            decision_id="d1",
            decision_text="ok decision",
            rationale_id=None,
            rationale_text="dangling rationale",
            derived_from_node_ids=None,
            now="2026-05-21T11:00:00Z",
        )
    # h1 must still be open (rollback / no mutation)
    h1 = storage.get_node(session_id="ses_1", node_id="h1")
    assert h1["status"] == "open"
    # No decision should exist (pre-flight failure)
    assert storage.get_node(session_id="ses_1", node_id="d1") is None


# ---------------------------------------------------------------------------
# v0.3 schema tests
# ---------------------------------------------------------------------------


def test_v3_pool_items_table_exists(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    with storage.connect() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pool_items'"
        )
        assert cursor.fetchone() is not None


def test_v3_nodes_has_new_columns(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    with storage.connect() as conn:
        rows = conn.execute("PRAGMA table_info(nodes)").fetchall()
        names = {row[1] for row in rows}
    for col in ("paired_for", "achievement_conditions",
                "achievement_conditions_satisfied", "state",
                "archived_at", "closed_at", "deletable_at"):
        assert col in names, f"missing column {col}"


def test_v3_roots_has_scope_columns(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    with storage.connect() as conn:
        rows = conn.execute("PRAGMA table_info(roots)").fetchall()
        names = {row[1]: row for row in rows}
    for col in ("scope", "scope_root", "migrated_to_start_id"):
        assert col in names, f"missing column {col}"
    # session_id must be NULLABLE in v3 (column 3 of PRAGMA = notnull flag)
    notnull = names["session_id"][3]
    assert notnull == 0, "roots.session_id must be NULLABLE in v3"


def test_v3_node_types_include_start_and_end(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    with storage.connect() as conn:
        # Insert a session + a root + nodes with start/end types
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) VALUES "
            "('ses_1', '2026-05-22T00:00:00Z', '2026-05-22T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) VALUES "
            "('root_1', 'ses_1', 't', 'active', '2026-05-22T00:00:00Z')"
        )
        # Both must succeed:
        for t in ("start", "end"):
            conn.execute(
                "INSERT INTO nodes "
                "(id, session_id, type, text, status, parent_id, parent_kind, "
                "created_at, updated_at) VALUES "
                f"('n_{t}', 'ses_1', '{t}', 'x', 'open', 'root_1', 'root', "
                "'2026-05-22T00:00:00Z', '2026-05-22T00:00:00Z')"
            )


def test_storage_open_migrates_v2_to_v3_schema(tmp_db_path: str) -> None:
    """A pre-existing v0.2-shaped db should be upgradeable to v3 via Storage.open()."""
    # Seed a v0.2-shaped db manually (no new columns)
    conn = sqlite3.connect(tmp_db_path)
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, scope TEXT, label TEXT,
            started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            focus_node_id TEXT
        );
        CREATE TABLE roots (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active','archived','deferred')),
            spawned_at TEXT NOT NULL, last_focused_at TEXT
        );
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            type TEXT NOT NULL, text TEXT NOT NULL,
            status TEXT NOT NULL, closure_reason TEXT,
            parent_id TEXT NOT NULL, parent_kind TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            from_node TEXT NOT NULL, to_node TEXT NOT NULL,
            type TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL
        );
        PRAGMA user_version = 2;
    """)
    conn.commit()
    conn.close()

    # Opening with the v3 Storage class must not throw, and must add v3 columns.
    storage = Storage.open(tmp_db_path)
    with storage.connect() as conn:
        node_cols = {row[1] for row in conn.execute("PRAGMA table_info(nodes)")}
        root_cols = {row[1] for row in conn.execute("PRAGMA table_info(roots)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    for col in ("paired_for", "state", "archived_at"):
        assert col in node_cols, f"missing nodes.{col}"
    for col in ("scope", "scope_root", "migrated_to_start_id"):
        assert col in root_cols, f"missing roots.{col}"
    assert version == 3, f"user_version should be 3, got {version}"


def test_v3_scope_root_requires_non_null_scope(tmp_db_path: str) -> None:
    """On a freshly-created v3 db, inserting a scope_root row with NULL scope must fail."""
    storage = Storage.open(tmp_db_path)
    with storage.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO roots (id, session_id, scope, scope_root, "
                "topic, lifecycle, spawned_at) "
                "VALUES ('r_bad', NULL, NULL, 1, 't', 'active', '2026-05-22T00:00:00Z')"
            )


# ---------------------------------------------------------------------------
# v0.3 Task 2: scope_root resolution + pool_items CRUD
# ---------------------------------------------------------------------------


def test_get_or_create_scope_root_first_call_creates(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    row = storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T00:00:00Z")
    assert row["scope_root"] == 1
    assert row["scope"] == "dpd"
    assert row["session_id"] is None


def test_get_or_create_scope_root_second_call_returns_same_row(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    r1 = storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T00:00:00Z")
    r2 = storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T01:00:00Z")
    assert r1["id"] == r2["id"]


def test_get_or_create_scope_root_distinct_per_scope(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    a = storage.get_or_create_scope_root(scope="alpha", now="2026-05-22T00:00:00Z")
    b = storage.get_or_create_scope_root(scope="beta", now="2026-05-22T00:00:00Z")
    assert a["id"] != b["id"]


def test_insert_pool_item_and_list(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    root = storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T00:00:00Z")
    storage.insert_pool_item(
        pool_id="pool_aaaa1111",
        scope_root_id=root["id"],
        text="raw thought 1",
        origin_session_id=None,
        origin_turn=None,
        tags="tangent",
        now="2026-05-22T00:00:00Z",
    )
    items = storage.list_pool_items(scope_root_id=root["id"], active_only=True)
    assert len(items) == 1
    assert items[0]["text"] == "raw thought 1"
    assert items[0]["tags"] == "tangent"


def test_pool_elevate_marks_elevated_to(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    root = storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T00:00:00Z")
    # Need a session and a target node to elevate into.
    storage.insert_session(session_id="ses_1", scope="dpd", label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r_legacy", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node(node_id="n_target", session_id="ses_1",
                        node_type="question", text="q", parent_id="r_legacy",
                        parent_kind="root", now="2026-05-22T00:00:00Z")
    storage.insert_pool_item(pool_id="pool_b1", scope_root_id=root["id"],
                             text="t1", origin_session_id=None,
                             origin_turn=None, tags=None,
                             now="2026-05-22T00:00:00Z")
    storage.mark_pool_elevated(pool_id="pool_b1", elevated_to="n_target",
                               now="2026-05-22T01:00:00Z")
    actives = storage.list_pool_items(scope_root_id=root["id"], active_only=True)
    assert actives == []
    all_items = storage.list_pool_items(scope_root_id=root["id"], active_only=False)
    assert len(all_items) == 1
    assert all_items[0]["elevated_to"] == "n_target"
    assert all_items[0]["elevated_at"] == "2026-05-22T01:00:00Z"


def test_pool_drop_marks_dropped_at(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    root = storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T00:00:00Z")
    storage.insert_pool_item(pool_id="pool_c1", scope_root_id=root["id"],
                             text="x", origin_session_id=None,
                             origin_turn=None, tags=None,
                             now="2026-05-22T00:00:00Z")
    storage.drop_pool_item(pool_id="pool_c1", reason="noise",
                           now="2026-05-22T02:00:00Z")
    actives = storage.list_pool_items(scope_root_id=root["id"], active_only=True)
    assert actives == []


def test_insert_start_node_with_paired_for_unset(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(
        node_id="n_start", session_id="ses_1", node_type="start",
        text="Start of subgraph", parent_id="r1",
        paired_for=None, achievement_conditions=None,
        now="2026-05-22T00:00:00Z",
    )
    node = storage.get_node(session_id="ses_1", node_id="n_start")
    assert node["type"] == "start"
    assert node["state"] == "active"
    assert node["paired_for"] is None


def test_insert_end_node_requires_paired_for(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(
        node_id="n_start", session_id="ses_1", node_type="start",
        text="s", parent_id="r1",
        paired_for=None, achievement_conditions=None,
        now="2026-05-22T00:00:00Z",
    )
    import pytest
    with pytest.raises(ValueError, match="paired_for"):
        storage.insert_node_v3(
            node_id="n_end", session_id="ses_1", node_type="end",
            text="e", parent_id="n_start",
            paired_for=None,  # MISSING — must raise
            achievement_conditions="done when X",
            now="2026-05-22T00:00:00Z",
        )


def test_mark_reached_transitions_subgraph_to_closed(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    # Subgraph: r1 → start → middle → end
    storage.insert_node_v3(node_id="n_s", session_id="ses_1", node_type="start",
                           text="s", parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_m", session_id="ses_1", node_type="question",
                           text="m", parent_id="n_s",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_e", session_id="ses_1", node_type="end",
                           text="e", parent_id="n_m",
                           paired_for="n_s",
                           achievement_conditions="done when X",
                           now="2026-05-22T00:00:00Z")
    storage.mark_reached(session_id="ses_1", end_node_id="n_e",
                         now="2026-05-22T01:00:00Z")
    for nid in ("n_s", "n_m", "n_e"):
        node = storage.get_node(session_id="ses_1", node_id=nid)
        assert node["state"] == "closed", f"{nid} not closed"
    end = storage.get_node(session_id="ses_1", node_id="n_e")
    assert end["achievement_conditions_satisfied"] == 1
    assert end["archived_at"] is not None
    assert end["closed_at"] is not None


def test_mark_reached_rejects_unreachable_end(tmp_db_path: str) -> None:
    """End must be reachable from its paired Start via parent_id chain."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s", session_id="ses_1", node_type="start",
                           text="s", parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    # End is NOT under start (parented to root instead)
    storage.insert_node_v3(node_id="n_e", session_id="ses_1", node_type="end",
                           text="e", parent_id="r1",
                           paired_for="n_s",
                           achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    import pytest
    with pytest.raises(ValueError, match="not reachable"):
        storage.mark_reached(session_id="ses_1", end_node_id="n_e",
                             now="2026-05-22T01:00:00Z")


def test_dump_persist_transitions_closed_to_deletable(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s", session_id="ses_1", node_type="start",
                           text="s", parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_e", session_id="ses_1", node_type="end",
                           text="e", parent_id="n_s",
                           paired_for="n_s",
                           achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.mark_reached(session_id="ses_1", end_node_id="n_e",
                         now="2026-05-22T01:00:00Z")
    storage.dump_persist_subgraph(session_id="ses_1", start_node_id="n_s",
                                  destination="/tmp/dump.md",
                                  now="2026-05-22T02:00:00Z")
    for nid in ("n_s", "n_e"):
        node = storage.get_node(session_id="ses_1", node_id=nid)
        assert node["state"] == "deletable"


def test_delete_subgraph_removes_nodes_and_edges(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s", session_id="ses_1", node_type="start",
                           text="s", parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_e", session_id="ses_1", node_type="end",
                           text="e", parent_id="n_s",
                           paired_for="n_s",
                           achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.mark_reached(session_id="ses_1", end_node_id="n_e",
                         now="2026-05-22T01:00:00Z")
    storage.dump_persist_subgraph(session_id="ses_1", start_node_id="n_s",
                                  destination=None,
                                  now="2026-05-22T02:00:00Z")
    storage.delete_subgraph(session_id="ses_1", start_node_id="n_s",
                            now="2026-05-22T03:00:00Z")
    assert storage.get_node(session_id="ses_1", node_id="n_s") is None
    assert storage.get_node(session_id="ses_1", node_id="n_e") is None


def test_get_or_create_scope_root_top_level_uses_empty_string_sentinel(tmp_db_path: str) -> None:
    """scope=None should be normalized to empty-string sentinel internally."""
    storage = Storage.open(tmp_db_path)
    row = storage.get_or_create_scope_root(scope=None, now="2026-05-22T00:00:00Z")
    assert row["scope"] == ""
    assert row["scope_root"] == 1


def test_get_or_create_scope_root_top_level_idempotent(tmp_db_path: str) -> None:
    """Two calls with scope=None should return the same row."""
    storage = Storage.open(tmp_db_path)
    r1 = storage.get_or_create_scope_root(scope=None, now="2026-05-22T00:00:00Z")
    r2 = storage.get_or_create_scope_root(scope=None, now="2026-05-22T01:00:00Z")
    assert r1["id"] == r2["id"]


def test_insert_node_v3_atomic_classify_and_insert(tmp_db_path: str) -> None:
    """insert_node_v3 must classify and insert in a single transaction.

    Verify behaviorally by checking that the method signature no longer takes
    parent_kind (= classification happens internally)."""
    import inspect
    from dpd_mcp_server.storage import Storage
    sig = inspect.signature(Storage.insert_node_v3)
    assert "parent_kind" not in sig.parameters, \
        "insert_node_v3 must not take parent_kind (classify internally)"


def test_insert_node_v3_raises_when_parent_missing(tmp_db_path: str) -> None:
    """insert_node_v3 must raise ValueError when parent_id doesn't exist."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    import pytest
    with pytest.raises(ValueError, match="parent_id"):
        storage.insert_node_v3(
            node_id="n_orphan", session_id="ses_1", node_type="question",
            text="x", parent_id="nonexistent_parent",
            paired_for=None, achievement_conditions=None,
            now="2026-05-22T00:00:00Z",
        )


def test_delete_subgraph_tombstones_elevated_pool_items(tmp_db_path: str) -> None:
    """When a subgraph that consumed pool items is deleted, those pool items
    should be tombstoned (dropped_at set) — not silently reactivated."""
    storage = Storage.open(tmp_db_path)
    root = storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T00:00:00Z")
    storage.insert_session(session_id="ses_1", scope="dpd", label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s", session_id="ses_1",
                           node_type="start", text="s",
                           parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_e", session_id="ses_1",
                           node_type="end", text="e",
                           parent_id="n_s",
                           paired_for="n_s",
                           achievement_conditions="done",
                           now="2026-05-22T00:00:00Z")
    # Elevate a pool item into the subgraph
    storage.insert_pool_item(pool_id="pool_evidence", scope_root_id=root["id"],
                             text="evidence X", origin_session_id=None,
                             origin_turn=None, tags=None,
                             now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_evidence", session_id="ses_1",
                           node_type="evidence", text="evidence X",
                           parent_id="n_e",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.mark_pool_elevated(pool_id="pool_evidence", elevated_to="n_evidence",
                               now="2026-05-22T00:30:00Z")
    # Run the full lifecycle to delete
    storage.mark_reached(session_id="ses_1", end_node_id="n_e",
                         now="2026-05-22T01:00:00Z")
    storage.dump_persist_subgraph(session_id="ses_1", start_node_id="n_s",
                                  destination=None,
                                  now="2026-05-22T02:00:00Z")
    storage.delete_subgraph(session_id="ses_1", start_node_id="n_s",
                            now="2026-05-22T03:00:00Z")
    # Pool item should be tombstoned (dropped_at set), NOT active
    active_items = storage.list_pool_items(scope_root_id=root["id"], active_only=True)
    assert active_items == []
    all_items = storage.list_pool_items(scope_root_id=root["id"], active_only=False)
    assert len(all_items) == 1
    assert all_items[0]["dropped_at"] is not None  # tombstoned
    assert all_items[0]["elevated_at"] == "2026-05-22T00:30:00Z"  # audit preserved


# ---------------------------------------------------------------------------
# Fix #1: schema migration relaxes v2 constraints (RED)
# ---------------------------------------------------------------------------


def test_storage_open_migration_relaxes_v2_constraints(tmp_db_path: str) -> None:
    """A pre-existing v0.2 db must allow scope_root insert and start/end node
    insert after migration — verifying that NOT NULL and CHECK constraints are
    actually rebuilt, not just new columns added via ALTER TABLE."""
    import sqlite3 as _sqlite3
    # Seed a v0.2-shaped database with the STRICT v0.2 constraints intact.
    conn = _sqlite3.connect(tmp_db_path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, scope TEXT, label TEXT,
                              started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                              focus_node_id TEXT);
        CREATE TABLE roots (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                            topic TEXT NOT NULL,
                            lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active','archived','deferred')),
                            spawned_at TEXT NOT NULL, last_focused_at TEXT);
        CREATE TABLE nodes (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                            type TEXT NOT NULL CHECK (type IN (
                                'question','plan','hypothesis','goal','problem',
                                'answer','action','verification','decision','resolution',
                                'evidence','constraint','assumption','rationale','risk')),
                            text TEXT NOT NULL,
                            status TEXT NOT NULL CHECK (status IN ('open','closed')),
                            closure_reason TEXT,
                            parent_id TEXT NOT NULL, parent_kind TEXT NOT NULL,
                            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT NOT NULL,
                            from_node TEXT NOT NULL, to_node TEXT NOT NULL,
                            type TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL);
        PRAGMA user_version = 2;
    """)
    conn.commit()
    conn.close()

    # Open with v3 — must rebuild tables with relaxed constraints.
    storage = Storage.open(tmp_db_path)

    # Now we must be able to insert scope_root with session_id=NULL:
    storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T00:00:00Z")

    # AND we must be able to insert Start/End nodes (= new type vocab):
    storage.insert_session(session_id="ses_1", scope="dpd", label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s", session_id="ses_1", node_type="start",
                           text="s", parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    # If we got here without IntegrityError, the constraints were properly relaxed.
    node = storage.get_node(session_id="ses_1", node_id="n_s")
    assert node is not None
    assert node["type"] == "start"


# ---------------------------------------------------------------------------
# Fix #3: mark_reached syncs legacy status column (RED)
# ---------------------------------------------------------------------------


def test_mark_reached_syncs_legacy_status(tmp_db_path: str) -> None:
    """mark_reached must update both state and legacy status columns.

    If status remains 'open' after mark_reached, list_open_nodes (which
    filters by status='open') incorrectly continues showing closed subgraphs.
    """
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s", session_id="ses_1", node_type="start",
                           text="s", parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_e", session_id="ses_1", node_type="end",
                           text="e", parent_id="n_s",
                           paired_for="n_s", achievement_conditions="done",
                           now="2026-05-22T00:00:00Z")

    storage.mark_reached(session_id="ses_1", end_node_id="n_e",
                         now="2026-05-22T01:00:00Z")

    for nid in ("n_s", "n_e"):
        node = storage.get_node(session_id="ses_1", node_id=nid)
        assert node["state"] == "closed", f"{nid} state should be closed"
        assert node["status"] == "closed", \
            f"{nid} legacy status should be 'closed' (was left 'open' before fix)"


def test_force_delete_node_handles_paired_and_elevated_fks(tmp_db_path: str) -> None:
    """force_delete_node must null paired_for and tombstone pool_items.elevated_to
    to avoid FK violations."""
    storage = Storage.open(tmp_db_path)
    root = storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T00:00:00Z")
    storage.insert_session(session_id="ses_1", scope="dpd", label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s", session_id="ses_1",
                           node_type="start", text="s",
                           parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_e", session_id="ses_1",
                           node_type="end", text="e",
                           parent_id="n_s",
                           paired_for="n_s",
                           achievement_conditions="done",
                           now="2026-05-22T00:00:00Z")
    # Elevate pool item into End node
    storage.insert_pool_item(pool_id="pool_x", scope_root_id=root["id"],
                             text="x", origin_session_id=None,
                             origin_turn=None, tags=None,
                             now="2026-05-22T00:00:00Z")
    storage.mark_pool_elevated(pool_id="pool_x", elevated_to="n_e",
                               now="2026-05-22T00:30:00Z")
    # Force delete the End node — should null paired_for + tombstone pool, no FK error
    storage.force_delete_node(session_id="ses_1", node_id="n_e",
                              now="2026-05-22T01:00:00Z")
    # End node is gone
    assert storage.get_node(session_id="ses_1", node_id="n_e") is None
    # Pool item is tombstoned, not active
    actives = storage.list_pool_items(scope_root_id=root["id"], active_only=True)
    assert actives == []
    all_items = storage.list_pool_items(scope_root_id=root["id"], active_only=False)
    assert all_items[0]["dropped_at"] is not None
