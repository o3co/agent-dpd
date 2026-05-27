"""Tests for dpd_mcp_server.tools (business logic of each MCP tool)."""

from __future__ import annotations

import pytest

from dpd_mcp_server.storage import Storage
from dpd_mcp_server.tools import (
    resolve_branch,
    resolve_hypothesis_branch,
    add_edge,
    add_node,
    close_node,
    export_mermaid,
    export_yaml,
    get_node,
    get_session_state,
    list_active_roots,
    list_edges,
    list_open_nodes,
    list_sessions,
    set_focus,
    set_root_lifecycle,
    spawn_root,
    start_session,
    walk_subtree,
)


@pytest.fixture
def storage(tmp_db_path: str) -> Storage:
    return Storage.open(tmp_db_path)


def test_start_session_returns_new_id_and_persists(storage: Storage) -> None:
    result = start_session(
        storage=storage,
        arguments={"scope": "dev.dpd", "label": "exp"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda prefix: f"{prefix}_fixed",
    )

    assert result == {"session_id": "ses_fixed"}

    row = storage.get_session(session_id="ses_fixed")
    assert row["scope"] == "dev.dpd"
    assert row["label"] == "exp"


def test_start_session_scope_and_label_optional(storage: Storage) -> None:
    result = start_session(
        storage=storage,
        arguments={},
        now="2026-05-20T10:00:00Z",
        new_id=lambda prefix: f"{prefix}_x",
    )

    row = storage.get_session(session_id=result["session_id"])
    assert row["scope"] is None
    assert row["label"] is None


def test_list_sessions_filters_by_scope(storage: Storage) -> None:
    storage.insert_session(
        session_id="ses_a", scope="alpha", label="A",
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_session(
        session_id="ses_b", scope="beta", label=None,
        now="2026-05-20T10:01:00Z",
    )

    result = list_sessions(storage=storage, arguments={"scope": "alpha"})

    assert [s["id"] for s in result["sessions"]] == ["ses_a"]
    assert result["sessions"][0]["scope"] == "alpha"
    assert result["sessions"][0]["label"] == "A"


def test_get_session_state_returns_session_plus_active_roots(
    storage: Storage,
) -> None:
    start_session(
        storage=storage,
        arguments={"scope": "dev.dpd", "label": "exp"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "ses_1",
    )
    spawn_root(
        storage=storage,
        arguments={"session_id": "ses_1", "topic": "MCP arch"},
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "root_a",
    )
    spawn_root(
        storage=storage,
        arguments={"session_id": "ses_1", "topic": "Storage"},
        now="2026-05-20T10:02:00Z",
        new_id=lambda p: "root_b",
    )

    result = get_session_state(
        storage=storage, arguments={"session_id": "ses_1"}
    )

    assert result["session"]["id"] == "ses_1"
    assert result["session"]["scope"] == "dev.dpd"
    assert result["session"]["label"] == "exp"
    assert result["session"]["focus_node_id"] is None
    assert [r["id"] for r in result["active_roots"]] == ["root_a", "root_b"]
    assert result["focus_node"] is None


def test_get_session_state_resolves_focus_node_when_set(storage: Storage) -> None:
    start_session(
        storage=storage, arguments={}, now="2026-05-20T10:00:00Z",
        new_id=lambda p: "ses_1",
    )
    spawn_root(
        storage=storage,
        arguments={"session_id": "ses_1", "topic": "t"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "root_a",
    )
    add_node(
        storage=storage,
        arguments={
            "session_id": "ses_1", "parent_id": "root_a",
            "type": "question", "text": "Should X?",
        },
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "q1",
    )
    with storage.connect() as conn:
        conn.execute(
            "UPDATE sessions SET focus_node_id = ? WHERE id = ?",
            ("q1", "ses_1"),
        )

    result = get_session_state(
        storage=storage, arguments={"session_id": "ses_1"}
    )

    assert result["focus_node"] is not None
    assert result["focus_node"]["id"] == "q1"
    assert result["focus_node"]["text"] == "Should X?"


def test_get_session_state_raises_when_session_missing(storage: Storage) -> None:
    with pytest.raises(ValueError, match="not found"):
        get_session_state(
            storage=storage, arguments={"session_id": "ses_missing"}
        )


def test_get_session_state_requires_session_id(storage: Storage) -> None:
    with pytest.raises(ValueError, match="session_id"):
        get_session_state(storage=storage, arguments={})


def test_list_sessions_empty_scope_means_top_level(storage: Storage) -> None:
    storage.insert_session(
        session_id="ses_top", scope=None, label=None,
        now="2026-05-20T10:00:00Z",
    )
    storage.insert_session(
        session_id="ses_sub", scope="some.scope", label=None,
        now="2026-05-20T10:01:00Z",
    )

    # Both missing key and empty string normalize to None → top-level only.
    via_missing = list_sessions(storage=storage, arguments={})
    via_empty = list_sessions(storage=storage, arguments={"scope": ""})

    assert [s["id"] for s in via_missing["sessions"]] == ["ses_top"]
    assert [s["id"] for s in via_empty["sessions"]] == ["ses_top"]


def test_spawn_root_creates_active_root(storage: Storage) -> None:
    start_session(
        storage=storage,
        arguments={},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "ses_1",
    )

    result = spawn_root(
        storage=storage,
        arguments={
            "session_id": "ses_1",
            "topic": "MCP architecture",
            "reason": "needed to scope phase 1",
        },
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "root_a",
    )

    assert result["root"]["id"] == "root_a"
    assert result["root"]["topic"] == "MCP architecture"
    assert result["root"]["lifecycle"] == "active"
    active = storage.list_active_roots(session_id="ses_1")
    assert [r["id"] for r in active] == ["root_a"]


def test_spawn_root_requires_topic(storage: Storage) -> None:
    start_session(
        storage=storage,
        arguments={},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "ses_1",
    )
    with pytest.raises(ValueError):
        spawn_root(
            storage=storage,
            arguments={"session_id": "ses_1", "reason": "x"},
            now="2026-05-20T10:00:00Z",
            new_id=lambda p: "root_a",
        )


def _start_with_root(storage: Storage) -> str:
    start_session(
        storage=storage,
        arguments={},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "ses_1",
    )
    spawn_root(
        storage=storage,
        arguments={"session_id": "ses_1", "topic": "t", "reason": "r"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "root_a",
    )
    return "ses_1"


def test_add_node_under_root_records_parent_kind_root(storage: Storage) -> None:
    sid = _start_with_root(storage)

    result = add_node(
        storage=storage,
        arguments={
            "session_id": sid,
            "parent_id": "root_a",
            "type": "question",
            "text": "Should we use MCP?",
        },
        now="2026-05-20T10:05:00Z",
        new_id=lambda p: "q1",
    )

    assert result["node"]["id"] == "q1"
    assert result["node"]["type"] == "question"
    assert result["node"]["parent_id"] == "root_a"
    assert result["node"]["parent_kind"] == "root"
    assert result["node"]["status"] == "open"


def test_add_node_under_node_records_parent_kind_node(storage: Storage) -> None:
    sid = _start_with_root(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:05:00Z",
        new_id=lambda p: "q1",
    )

    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "q1",
                   "type": "answer", "text": "yes"},
        now="2026-05-20T10:06:00Z",
        new_id=lambda p: "a1",
    )

    row = storage.get_node(session_id=sid, node_id="a1")
    assert row["parent_kind"] == "node"
    assert row["parent_id"] == "q1"


def test_add_node_unknown_parent_raises(storage: Storage) -> None:
    sid = _start_with_root(storage)
    with pytest.raises(ValueError):
        add_node(
            storage=storage,
            arguments={"session_id": sid, "parent_id": "missing",
                       "type": "question", "text": "?"},
            now="2026-05-20T10:05:00Z",
            new_id=lambda p: "q1",
        )


def test_close_node_marks_closed_with_reason(storage: Storage) -> None:
    sid = _start_with_root(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "q1",
    )

    close_node(
        storage=storage,
        arguments={"session_id": sid, "node_id": "q1",
                   "closure_reason": "resolved"},
        now="2026-05-20T11:00:00Z",
    )

    row = storage.get_node(session_id=sid, node_id="q1")
    assert row["status"] == "closed"
    assert row["closure_reason"] == "resolved"


def test_close_node_rejects_invalid_reason(storage: Storage) -> None:
    sid = _start_with_root(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "q1",
    )

    with pytest.raises(ValueError):
        close_node(
            storage=storage,
            arguments={"session_id": sid, "node_id": "q1",
                       "closure_reason": "bogus"},
            now="2026-05-20T11:00:00Z",
        )


def test_close_node_raises_when_node_missing(storage: Storage) -> None:
    sid = _start_with_root(storage)
    with pytest.raises(ValueError):
        close_node(
            storage=storage,
            arguments={"session_id": sid, "node_id": "does_not_exist",
                       "closure_reason": "resolved"},
            now="2026-05-20T11:00:00Z",
        )


def test_get_node_returns_full_record(storage: Storage) -> None:
    sid = _start_with_root(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "q1",
    )

    result = get_node(
        storage=storage,
        arguments={"session_id": sid, "node_id": "q1"},
    )

    assert result["node"]["id"] == "q1"
    assert result["node"]["type"] == "question"
    assert result["node"]["status"] == "open"
    assert result["node"]["parent_kind"] == "root"


def test_get_node_missing_returns_none_payload(storage: Storage) -> None:
    sid = _start_with_root(storage)
    result = get_node(
        storage=storage,
        arguments={"session_id": sid, "node_id": "missing"},
    )
    assert result == {"node": None}


def test_walk_subtree_returns_flat_descendants(storage: Storage) -> None:
    sid = _start_with_root(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "q1",
    )
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "q1",
                   "type": "answer", "text": "yes"},
        now="2026-05-20T10:02:00Z",
        new_id=lambda p: "a1",
    )

    result = walk_subtree(
        storage=storage,
        arguments={"session_id": sid, "root_id": "root_a"},
    )

    assert [n["id"] for n in result["nodes"]] == ["q1", "a1"]


def test_list_active_roots_returns_summaries(storage: Storage) -> None:
    sid = _start_with_root(storage)

    result = list_active_roots(
        storage=storage,
        arguments={"session_id": sid},
    )

    assert len(result["roots"]) == 1
    assert result["roots"][0]["id"] == "root_a"
    assert result["roots"][0]["topic"] == "t"
    assert result["roots"][0]["lifecycle"] == "active"


def test_start_session_coerces_empty_scope_and_label_to_none(storage: Storage) -> None:
    result = start_session(
        storage=storage,
        arguments={"scope": "", "label": ""},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: f"{p}_e",
    )

    row = storage.get_session(session_id=result["session_id"])
    assert row["scope"] is None
    assert row["label"] is None


def test_start_session_default_mode_entry(storage: Storage) -> None:
    """Without mode arg, sessions.mode = 'entry'."""
    result = start_session(
        storage=storage,
        arguments={},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: f"{p}_m0",
    )

    row = storage.get_session(session_id=result["session_id"])
    assert row["mode"] == "entry"


def test_start_session_explicit_mode_idle(storage: Storage) -> None:
    """start_session(mode='idle') stores 'idle'."""
    result = start_session(
        storage=storage,
        arguments={"mode": "idle"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: f"{p}_m1",
    )

    row = storage.get_session(session_id=result["session_id"])
    assert row["mode"] == "idle"


def test_start_session_invalid_mode_raises(storage: Storage) -> None:
    """start_session(mode='bogus') raises ValueError."""
    with pytest.raises(ValueError, match="mode"):
        start_session(
            storage=storage,
            arguments={"mode": "bogus"},
            now="2026-05-20T10:00:00Z",
            new_id=lambda p: f"{p}_m2",
        )


def test_spawn_root_invalid_session_raises_value_error(storage: Storage) -> None:
    with pytest.raises(ValueError):
        spawn_root(
            storage=storage,
            arguments={"session_id": "ses_missing", "topic": "t"},
            now="2026-05-20T10:00:00Z",
            new_id=lambda p: "root_a",
        )


import sqlite3 as _sqlite3  # noqa: E402


def test_add_node_rejects_invalid_type_via_storage_constraint(storage: Storage) -> None:
    sid = _start_with_root(storage)
    # Tool layer doesn't validate type; storage layer does.
    # IntegrityError is wrapped into ValueError by add_node's try/except.
    with pytest.raises(ValueError):
        add_node(
            storage=storage,
            arguments={"session_id": sid, "parent_id": "root_a",
                       "type": "bogus", "text": "?"},
            now="2026-05-20T10:00:00Z",
            new_id=lambda p: "node_x",
        )


# ---------------------------------------------------------------------------
# Phase 2.5 tools
# ---------------------------------------------------------------------------


def test_set_focus_tool_updates_session(storage: Storage) -> None:
    sid = _start_with_root(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "q1",
    )

    result = set_focus(
        storage=storage,
        arguments={"session_id": sid, "node_id": "q1"},
        now="2026-05-20T11:00:00Z",
    )

    assert result == {"session_id": sid, "focus_node_id": "q1"}
    row = storage.get_session(session_id=sid)
    assert row["focus_node_id"] == "q1"


def test_set_focus_tool_clears_focus_when_node_id_omitted(storage: Storage) -> None:
    sid = _start_with_root(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "q1",
    )
    set_focus(
        storage=storage,
        arguments={"session_id": sid, "node_id": "q1"},
        now="2026-05-20T11:00:00Z",
    )

    result = set_focus(
        storage=storage,
        arguments={"session_id": sid},
        now="2026-05-20T12:00:00Z",
    )

    assert result["focus_node_id"] is None
    row = storage.get_session(session_id=sid)
    assert row["focus_node_id"] is None


def test_set_root_lifecycle_tool_archives_root(storage: Storage) -> None:
    sid = _start_with_root(storage)

    result = set_root_lifecycle(
        storage=storage,
        arguments={"session_id": sid, "root_id": "root_a",
                   "lifecycle": "archived"},
        now="2026-05-20T11:00:00Z",
    )

    assert result == {"root_id": "root_a", "lifecycle": "archived"}
    assert storage.list_active_roots(session_id=sid) == []


def test_set_root_lifecycle_tool_rejects_invalid_lifecycle(storage: Storage) -> None:
    sid = _start_with_root(storage)

    with pytest.raises(ValueError, match="lifecycle"):
        set_root_lifecycle(
            storage=storage,
            arguments={"session_id": sid, "root_id": "root_a",
                       "lifecycle": "bogus"},
            now="2026-05-20T11:00:00Z",
        )


def test_set_root_lifecycle_tool_raises_when_root_missing(storage: Storage) -> None:
    sid = _start_with_root(storage)

    with pytest.raises(ValueError, match="root"):
        set_root_lifecycle(
            storage=storage,
            arguments={"session_id": sid, "root_id": "root_ghost",
                       "lifecycle": "archived"},
            now="2026-05-20T11:00:00Z",
        )


def test_list_open_nodes_tool_full_session(storage: Storage) -> None:
    sid = _start_with_root(storage)
    for nid in ["q1", "q2"]:
        add_node(
            storage=storage,
            arguments={"session_id": sid, "parent_id": "root_a",
                       "type": "question", "text": "?"},
            now="2026-05-20T10:01:00Z",
            new_id=lambda p, _nid=nid: _nid,
        )
    close_node(
        storage=storage,
        arguments={"session_id": sid, "node_id": "q1",
                   "closure_reason": "resolved"},
        now="2026-05-20T10:30:00Z",
    )

    result = list_open_nodes(
        storage=storage, arguments={"session_id": sid}
    )

    assert [n["id"] for n in result["nodes"]] == ["q2"]


def test_list_open_nodes_tool_filtered_by_root(storage: Storage) -> None:
    sid = _start_with_root(storage)
    # Add a second root
    spawn_root(
        storage=storage,
        arguments={"session_id": sid, "topic": "B"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "root_b",
    )
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "qa",
    )
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_b",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:02:00Z",
        new_id=lambda p: "qb",
    )

    result = list_open_nodes(
        storage=storage,
        arguments={"session_id": sid, "root_id": "root_a"},
    )

    assert [n["id"] for n in result["nodes"]] == ["qa"]


def test_add_edge_and_list_edges_tools(storage: Storage) -> None:
    sid = _start_with_root(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "n1",
    )

    add_result = add_edge(
        storage=storage,
        arguments={
            "session_id": sid,
            "from_node": "n1", "to_node": "root_a",
            "type": "derived_from", "reason": "dep",
        },
        now="2026-05-20T11:00:00Z",
    )

    assert isinstance(add_result["edge_id"], int)

    list_result = list_edges(
        storage=storage, arguments={"session_id": sid}
    )

    assert len(list_result["edges"]) == 1
    assert list_result["edges"][0]["from_node"] == "n1"
    assert list_result["edges"][0]["to_node"] == "root_a"
    assert list_result["edges"][0]["type"] == "derived_from"


def test_list_edges_tool_filters_by_type(storage: Storage) -> None:
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-21T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-21T10:00:00Z",
    )
    storage.insert_node(
        node_id="n1", session_id="ses_1", node_type="hypothesis",
        text="x", parent_id="root_a", parent_kind="root",
        now="2026-05-21T10:01:00Z",
    )
    storage.insert_node(
        node_id="n2", session_id="ses_1", node_type="evidence",
        text="y", parent_id="root_a", parent_kind="root",
        now="2026-05-21T10:01:00Z",
    )
    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="n2",
        edge_type="supports", reason=None, now="2026-05-21T10:02:00Z",
    )
    storage.add_edge(
        session_id="ses_1", from_node="n1", to_node="n2",
        edge_type="contradicts", reason=None, now="2026-05-21T10:03:00Z",
    )

    result = list_edges(
        storage=storage,
        arguments={"session_id": "ses_1", "type": "supports"},
    )
    assert len(result["edges"]) == 1
    assert result["edges"][0]["type"] == "supports"


def test_resolve_hypothesis_branch_tool_with_rationale(storage: Storage) -> None:
    sid = _start_with_root(storage)
    # Seed 3 hypothesis siblings under root_a
    for nid in ["h1", "h2", "h3"]:
        add_node(
            storage=storage,
            arguments={"session_id": sid, "parent_id": "root_a",
                       "type": "hypothesis", "text": f"opt {nid}"},
            now="2026-05-20T10:01:00Z",
            new_id=lambda p, _nid=nid: _nid,
        )

    ids = iter(["d1", "r1"])
    result = resolve_hypothesis_branch(
        storage=storage,
        arguments={
            "session_id": sid, "hyp_id": "h2",
            "decision_text": "Adopt h2",
            "rationale_text": "Best fit for the constraints",
        },
        now="2026-05-20T11:00:00Z",
        new_id=lambda p: next(ids),
    )

    assert result["hyp_id"] == "h2"
    assert result["decision_id"] == "d1"
    assert result["rationale_id"] == "r1"
    assert set(result["closed_siblings"]) == {"h1", "h3"}

    # State checks
    h2 = storage.get_node(session_id=sid, node_id="h2")
    assert h2["closure_reason"] == "resolved"
    h1 = storage.get_node(session_id=sid, node_id="h1")
    assert h1["closure_reason"] == "rejected"
    d1 = storage.get_node(session_id=sid, node_id="d1")
    assert d1["type"] == "decision"
    r1 = storage.get_node(session_id=sid, node_id="r1")
    assert r1["type"] == "rationale"
    assert r1["parent_id"] == "d1"


def _seed_small_decision_tree(storage: Storage) -> str:
    """Build a session with root → hypothesis → resolve_hypothesis_branch shape.
    Returns the session_id."""
    start_session(
        storage=storage, arguments={"label": "L"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "ses_1",
    )
    spawn_root(
        storage=storage,
        arguments={"session_id": "ses_1", "topic": "Pick option"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "root_a",
    )
    for nid in ["h1", "h2"]:
        add_node(
            storage=storage,
            arguments={"session_id": "ses_1", "parent_id": "root_a",
                       "type": "hypothesis", "text": f"option {nid}"},
            now="2026-05-20T10:01:00Z",
            new_id=lambda p, _nid=nid: _nid,
        )
    ids = iter(["d1", "r1"])
    resolve_hypothesis_branch(
        storage=storage,
        arguments={
            "session_id": "ses_1", "hyp_id": "h1",
            "decision_text": "Adopt h1",
            "rationale_text": "best fit",
        },
        now="2026-05-20T11:00:00Z",
        new_id=lambda p: next(ids),
    )
    return "ses_1"


# ---------------------------------------------------------------------------
# export_mermaid
# ---------------------------------------------------------------------------


def test_export_mermaid_empty_session(storage: Storage) -> None:
    start_session(
        storage=storage, arguments={}, now="2026-05-20T10:00:00Z",
        new_id=lambda p: "ses_1",
    )

    result = export_mermaid(
        storage=storage, arguments={"session_id": "ses_1"}
    )

    assert "graph TD" in result["mermaid"]


def test_export_mermaid_basic_shape(storage: Storage) -> None:
    sid = _seed_small_decision_tree(storage)

    result = export_mermaid(
        storage=storage, arguments={"session_id": sid}
    )
    out = result["mermaid"]

    assert "graph TD" in out
    # Root + 2 hypotheses + decision + rationale (5 nodes total)
    for nid in ["root_a", "h1", "h2", "d1", "r1"]:
        assert nid in out
    # Tree edges (parent → child)
    assert "root_a --> h1" in out
    assert "root_a --> h2" in out
    assert "root_a --> d1" in out
    assert "d1 --> r1" in out


def test_export_mermaid_includes_derived_from_edge(storage: Storage) -> None:
    """resolve_hypothesis_branch auto-created a derived_from edge; export
    must include it in dotted-edge form."""
    sid = _seed_small_decision_tree(storage)

    out = export_mermaid(
        storage=storage, arguments={"session_id": sid}
    )["mermaid"]

    # derived_from edge from d1 → h1 rendered with dotted arrow + label
    assert "d1 -.derived_from.-> h1" in out


def test_export_mermaid_marks_closed_with_class(storage: Storage) -> None:
    sid = _seed_small_decision_tree(storage)

    out = export_mermaid(
        storage=storage, arguments={"session_id": sid}
    )["mermaid"]

    # Closed nodes get a class assignment for styling
    assert "class h1 closed_resolved" in out
    assert "class h2 closed_rejected" in out
    assert "class d1 closed_resolved" in out
    # classDef lines provide the actual styles
    assert "classDef closed_resolved" in out
    assert "classDef closed_rejected" in out


def test_export_mermaid_filters_to_one_root(storage: Storage) -> None:
    sid = _seed_small_decision_tree(storage)
    # Spawn a second root with one node — should NOT appear when root_id specified.
    spawn_root(
        storage=storage,
        arguments={"session_id": sid, "topic": "Other"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "root_b",
    )
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_b",
                   "type": "question", "text": "?"},
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "qb",
    )

    out = export_mermaid(
        storage=storage,
        arguments={"session_id": sid, "root_id": "root_a"},
    )["mermaid"]

    assert "root_a" in out
    assert "h1" in out
    assert "root_b" not in out
    assert "qb" not in out


_LONG_TEXT = (
    "this is a deliberately long node text that will exceed the default 60 "
    "character truncation budget so we can verify behavior"
)


def test_export_mermaid_default_truncates_long_labels(storage: Storage) -> None:
    """Default behavior: labels longer than 60 chars get a trailing ellipsis."""
    sid = _seed_small_decision_tree(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": _LONG_TEXT},
        now="2026-05-27T10:05:00Z",
        new_id=lambda p: "qlong",
    )

    out = export_mermaid(
        storage=storage, arguments={"session_id": sid}
    )["mermaid"]
    qlines = [l for l in out.split("\n") if "qlong" in l and "question" in l]
    assert qlines
    assert "…" in qlines[0]


def test_export_mermaid_max_label_chars_none_disables_truncation(
    storage: Storage,
) -> None:
    """Issue #14: max_label_chars=None must yield full text, no ellipsis."""
    sid = _seed_small_decision_tree(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": _LONG_TEXT},
        now="2026-05-27T10:05:00Z",
        new_id=lambda p: "qfull",
    )

    out = export_mermaid(
        storage=storage,
        arguments={"session_id": sid, "max_label_chars": None},
    )["mermaid"]
    qlines = [l for l in out.split("\n") if "qfull" in l and "question" in l]
    assert qlines
    assert "…" not in qlines[0]
    assert _LONG_TEXT in qlines[0]


def test_export_mermaid_max_label_chars_custom_width(storage: Storage) -> None:
    """Issue #14: max_label_chars=N truncates at N (inclusive of ellipsis)."""
    sid = _seed_small_decision_tree(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": _LONG_TEXT},
        now="2026-05-27T10:05:00Z",
        new_id=lambda p: "qsmall",
    )

    out = export_mermaid(
        storage=storage,
        arguments={"session_id": sid, "max_label_chars": 20},
    )["mermaid"]
    qlines = [l for l in out.split("\n") if "qsmall" in l and "question" in l]
    assert qlines
    inner = qlines[0].split('"', 1)[1].rsplit('"', 1)[0]
    # inner = "question: <truncated text>"; the truncated text portion
    # should be 20 chars including ellipsis.
    assert "…" in inner
    truncated_payload = inner.split(": ", 1)[1]
    assert len(truncated_payload) == 20


def test_export_mermaid_sanitizes_special_chars(storage: Storage) -> None:
    """Pipes and quotes in text must be sanitized so Mermaid parses."""
    sid = _seed_small_decision_tree(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "question", "text": 'has "quotes" and | pipe'},
        now="2026-05-20T10:05:00Z",
        new_id=lambda p: "qx",
    )

    out = export_mermaid(
        storage=storage, arguments={"session_id": sid}
    )["mermaid"]

    # Find the qx line — must not contain raw " or | inside the label
    qx_lines = [l for l in out.split("\n") if "qx" in l and "question" in l]
    assert qx_lines, "qx label line missing"
    label = qx_lines[0]
    # The double-quote at start/end of label is OK (Mermaid syntax),
    # but no inner unescaped " should remain.
    inner = label.split('"', 1)[1].rsplit('"', 1)[0]
    assert '"' not in inner
    assert "|" not in inner


# ---------------------------------------------------------------------------
# export_yaml
# ---------------------------------------------------------------------------


def test_export_yaml_basic_shape(storage: Storage) -> None:
    sid = _seed_small_decision_tree(storage)

    result = export_yaml(
        storage=storage, arguments={"session_id": sid}
    )
    out = result["yaml"]

    # YAML output should be parseable JSON-as-YAML and contain key markers
    assert "ses_1" in out
    assert "root_a" in out
    assert "h1" in out
    assert "Adopt h1" in out
    assert "derived_from" in out


def test_export_yaml_includes_edges(storage: Storage) -> None:
    sid = _seed_small_decision_tree(storage)

    out = export_yaml(
        storage=storage, arguments={"session_id": sid}
    )["yaml"]

    assert "edges" in out
    # The auto-created derived_from edge is present
    assert "derived_from" in out


def test_export_yaml_round_trip_through_json(storage: Storage) -> None:
    """YAML output is JSON-compatible (json is a YAML subset), so it must
    parse with the stdlib json module."""
    import json as _json
    sid = _seed_small_decision_tree(storage)

    out = export_yaml(
        storage=storage, arguments={"session_id": sid}
    )["yaml"]

    parsed = _json.loads(out)
    assert "session" in parsed
    assert "roots" in parsed
    assert "edges" in parsed
    assert parsed["session"]["id"] == sid
    assert any(r["id"] == "root_a" for r in parsed["roots"])


def test_export_yaml_filters_to_one_root(storage: Storage) -> None:
    sid = _seed_small_decision_tree(storage)
    spawn_root(
        storage=storage,
        arguments={"session_id": sid, "topic": "Other"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda p: "root_b",
    )

    out = export_yaml(
        storage=storage,
        arguments={"session_id": sid, "root_id": "root_a"},
    )["yaml"]

    assert "root_a" in out
    assert "root_b" not in out


def test_resolve_hypothesis_branch_tool_without_rationale(storage: Storage) -> None:
    sid = _start_with_root(storage)
    add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": "root_a",
                   "type": "hypothesis", "text": "h"},
        now="2026-05-20T10:01:00Z",
        new_id=lambda p: "h1",
    )

    ids = iter(["d1"])
    result = resolve_hypothesis_branch(
        storage=storage,
        arguments={"session_id": sid, "hyp_id": "h1",
                   "decision_text": "Adopt h1"},
        now="2026-05-20T11:00:00Z",
        new_id=lambda p: next(ids),
    )

    assert result["rationale_id"] is None
    assert storage.get_node(session_id=sid, node_id="d1") is not None


# ---------------------------------------------------------------------------
# resolve_branch tool tests
# ---------------------------------------------------------------------------


def test_resolve_branch_tool_all_true(storage: Storage) -> None:
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-21T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-21T10:00:00Z",
    )
    for nid in ["h1", "h2"]:
        storage.insert_node(
            node_id=nid, session_id="ses_1", node_type="hypothesis",
            text=f"opt {nid}", parent_id="root_a", parent_kind="root",
            now="2026-05-21T10:01:00Z",
        )

    counter = {"n": 0}
    def fake_new_id(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}_{counter['n']}"

    result = resolve_branch(
        storage=storage,
        arguments={
            "session_id": "ses_1",
            "parent_id": "root_a",
            "parent_kind": "root",
            "results": [
                {"node_id": "h1", "closure_reason": "resolved"},
                {"node_id": "h2", "closure_reason": "resolved"},
            ],
            "decision_text": "both confirmed",
            "derived_from_node_ids": ["h1", "h2"],
        },
        now="2026-05-21T11:00:00Z",
        new_id=fake_new_id,
    )

    assert sorted(n["id"] for n in result["closed_nodes"]) == ["h1", "h2"]
    assert result["decision_node"] is not None
    assert result["decision_node"]["id"] is not None
    assert result["rationale_node"] is None
    assert len(result["edges_created"]) == 2


def test_resolve_branch_tool_validates_closure_reason(storage: Storage) -> None:
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, now="2026-05-21T10:00:00Z"
    )
    storage.insert_root(
        root_id="root_a", session_id="ses_1", topic="t",
        now="2026-05-21T10:00:00Z",
    )
    storage.insert_node(
        node_id="h1", session_id="ses_1", node_type="hypothesis",
        text="x", parent_id="root_a", parent_kind="root",
        now="2026-05-21T10:01:00Z",
    )

    with pytest.raises(ValueError, match="closure_reason"):
        resolve_branch(
            storage=storage,
            arguments={
                "session_id": "ses_1",
                "parent_id": "root_a",
                "parent_kind": "root",
                "results": [
                    {"node_id": "h1", "closure_reason": "bogus"},
                ],
                "decision_text": "x",
            },
            now="2026-05-21T11:00:00Z",
            new_id=lambda p: f"{p}_x",
        )


def test_pool_add_creates_scope_root_if_missing(tmp_db_path):
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    result = tools.pool_add(
        storage, scope="dpd",
        arguments={"text": "raw thought 1", "tags": "tangent"},
        now="2026-05-22T00:00:00Z",
    )
    assert "pool_item" in result
    assert result["pool_item"]["text"] == "raw thought 1"
    # scope_root was auto-created
    items = tools.pool_list(storage, scope="dpd", arguments={}, now="...")
    assert len(items["items"]) == 1


def test_pool_add_populates_text_hash(tmp_db_path):
    """pool_add stores canonical text_hash per spec §4.6.1.

    Hash = SHA-256(lower(strip(text)))[:16]. Tests:
    - hash is populated (non-NULL, 16 hex chars)
    - same canonical form → same hash (case + whitespace insensitive)
    - distinct text → distinct hash
    """
    import hashlib
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)

    r1 = tools.pool_add(
        storage, scope="dpd",
        arguments={"text": "  Observation X  "},
        now="2026-05-22T00:00:00Z",
    )
    r2 = tools.pool_add(
        storage, scope="dpd",
        arguments={"text": "observation x"},  # same canonical form
        now="2026-05-22T00:00:01Z",
    )
    r3 = tools.pool_add(
        storage, scope="dpd",
        arguments={"text": "Different observation"},
        now="2026-05-22T00:00:02Z",
    )

    h1 = r1["pool_item"]["text_hash"]
    h2 = r2["pool_item"]["text_hash"]
    h3 = r3["pool_item"]["text_hash"]

    # populated, correct length
    assert h1 is not None
    assert len(h1) == 16
    assert all(c in "0123456789abcdef" for c in h1)

    # canonical equivalence (case + whitespace)
    assert h1 == h2, f"expected same hash for canonical-equivalent text, got {h1!r} vs {h2!r}"

    # distinct text → distinct hash
    assert h1 != h3

    # matches spec formula
    expected = hashlib.sha256(b"observation x").hexdigest()[:16]
    assert h1 == expected


def test_pool_elevate_links_to_node(tmp_db_path):
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    # Set up scope_root + session + a target end node to elevate into.
    storage.get_or_create_scope_root(scope="dpd", now="2026-05-22T00:00:00Z")
    storage.insert_session(session_id="ses_1", scope="dpd", label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r_legacy", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s", session_id="ses_1", node_type="start",
                           text="s", parent_id="r_legacy",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_e", session_id="ses_1", node_type="end",
                           text="e", parent_id="n_s",
                           paired_for="n_s", achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")

    pool_added = tools.pool_add(
        storage, scope="dpd",
        arguments={"text": "evidence X"},
        now="2026-05-22T00:00:00Z",
    )
    pid = pool_added["pool_item"]["id"]
    result = tools.pool_elevate(
        storage, scope="dpd",
        arguments={
            "pool_id": pid,
            "target_end_node_id": "n_e",
            "type": "evidence",
            "session_id": "ses_1",
        },
        now="2026-05-22T01:00:00Z",
    )
    assert "elevated_node" in result
    # pool item is no longer in active list
    actives = tools.pool_list(storage, scope="dpd",
                              arguments={"active_only": True},
                              now="...")
    assert actives["items"] == []


def test_pool_drop_marks_active_false(tmp_db_path):
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    added = tools.pool_add(storage, scope="dpd",
                           arguments={"text": "noise"},
                           now="2026-05-22T00:00:00Z")
    tools.pool_drop(storage, scope="dpd",
                    arguments={"pool_id": added["pool_item"]["id"],
                               "reason": "irrelevant"},
                    now="2026-05-22T01:00:00Z")
    actives = tools.pool_list(storage, scope="dpd",
                              arguments={"active_only": True},
                              now="...")
    assert actives["items"] == []


# ---------------------------------------------------------------------------
# Task 2 (v0.3.1): pool_list include_rejected / rejected_only filters
# ---------------------------------------------------------------------------


def _make_rejected_pool_item(storage: Storage, scope: str, text: str) -> str:
    """Helper: add a pool item and mark it rejected via raw SQL (pool_reject
    tool does not exist yet — Task 3).  Returns the pool item id."""
    import sqlite3 as _sqlite3
    from dpd_mcp_server import tools as _tools
    added = _tools.pool_add(storage, scope=scope,
                            arguments={"text": text},
                            now="2026-05-22T00:00:00Z")
    pid = added["pool_item"]["id"]
    with _sqlite3.connect(storage._db_path) as conn:
        conn.execute(
            "UPDATE pool_items SET rejected_at = '2026-05-22T01:00:00Z' WHERE id = ?",
            (pid,),
        )
    return pid


def test_pool_list_default_excludes_rejected(tmp_db_path):
    """Default (active_only=True) must exclude rejected items."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    # Add one active item.
    tools.pool_add(storage, scope="dpd",
                   arguments={"text": "active item"},
                   now="2026-05-22T00:00:00Z")
    # Add one rejected item.
    _make_rejected_pool_item(storage, scope="dpd", text="rejected item")

    result = tools.pool_list(storage, scope="dpd", arguments={}, now="...")
    texts = [i["text"] for i in result["items"]]
    assert "active item" in texts
    assert "rejected item" not in texts


def test_pool_list_include_rejected_returns_all(tmp_db_path):
    """include_rejected=True returns both active and rejected (excludes dropped)."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    tools.pool_add(storage, scope="dpd",
                   arguments={"text": "active item"},
                   now="2026-05-22T00:00:00Z")
    _make_rejected_pool_item(storage, scope="dpd", text="rejected item")
    # Also add a dropped item — should stay excluded.
    dropped = tools.pool_add(storage, scope="dpd",
                             arguments={"text": "dropped item"},
                             now="2026-05-22T00:00:00Z")
    tools.pool_drop(storage, scope="dpd",
                    arguments={"pool_id": dropped["pool_item"]["id"],
                               "reason": "noise"},
                    now="2026-05-22T02:00:00Z")

    result = tools.pool_list(storage, scope="dpd",
                             arguments={"include_rejected": True},
                             now="...")
    texts = [i["text"] for i in result["items"]]
    assert "active item" in texts
    assert "rejected item" in texts
    assert "dropped item" not in texts


def test_pool_list_rejected_only_returns_only_rejected(tmp_db_path):
    """rejected_only=True returns ONLY rejected items."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    tools.pool_add(storage, scope="dpd",
                   arguments={"text": "active item"},
                   now="2026-05-22T00:00:00Z")
    _make_rejected_pool_item(storage, scope="dpd", text="rejected item")

    result = tools.pool_list(storage, scope="dpd",
                             arguments={"rejected_only": True},
                             now="...")
    texts = [i["text"] for i in result["items"]]
    assert texts == ["rejected item"]


def test_pool_list_active_only_and_rejected_only_raises(tmp_db_path):
    """active_only=True + rejected_only=True must raise ValueError."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    import pytest as _pytest
    with _pytest.raises(ValueError, match="mutually exclusive"):
        tools.pool_list(storage, scope="dpd",
                        arguments={"active_only": True, "rejected_only": True},
                        now="...")


def test_pool_list_active_only_and_include_rejected_raises(tmp_db_path):
    """active_only=True + include_rejected=True must raise ValueError."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    import pytest as _pytest
    with _pytest.raises(ValueError, match="mutually exclusive"):
        tools.pool_list(storage, scope="dpd",
                        arguments={"active_only": True, "include_rejected": True},
                        now="...")


def test_pool_list_include_rejected_excludes_elevated(tmp_db_path):
    """include_rejected=True must NOT surface items with elevated_to set."""
    import sqlite3 as _sqlite3
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    # Add an active item, then simulate elevation via raw SQL.
    added = tools.pool_add(storage, scope="dpd",
                           arguments={"text": "elevated item"},
                           now="2026-05-22T00:00:00Z")
    pid = added["pool_item"]["id"]
    with _sqlite3.connect(storage._db_path) as conn:
        conn.execute(
            "UPDATE pool_items SET elevated_to = 'fake_node_id' WHERE id = ?",
            (pid,),
        )
    # Also add a plain active item and a rejected item.
    tools.pool_add(storage, scope="dpd",
                   arguments={"text": "active item"},
                   now="2026-05-22T00:00:00Z")
    _make_rejected_pool_item(storage, scope="dpd", text="rejected item")

    result = tools.pool_list(storage, scope="dpd",
                             arguments={"include_rejected": True},
                             now="...")
    texts = [i["text"] for i in result["items"]]
    assert "elevated item" not in texts
    assert "active item" in texts
    assert "rejected item" in texts


def test_pool_list_rejected_only_excludes_elevated(tmp_db_path):
    """rejected_only=True must NOT surface items with elevated_to set."""
    import sqlite3 as _sqlite3
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    # Add a rejected item, then simulate elevation via raw SQL.
    pid = _make_rejected_pool_item(storage, scope="dpd", text="elevated+rejected item")
    with _sqlite3.connect(storage._db_path) as conn:
        conn.execute(
            "UPDATE pool_items SET elevated_to = 'fake_node_id' WHERE id = ?",
            (pid,),
        )
    # Add a normal rejected item that should appear.
    _make_rejected_pool_item(storage, scope="dpd", text="plain rejected item")

    result = tools.pool_list(storage, scope="dpd",
                             arguments={"rejected_only": True},
                             now="...")
    texts = [i["text"] for i in result["items"]]
    assert "elevated+rejected item" not in texts
    assert "plain rejected item" in texts


# ---------------------------------------------------------------------------
# Task 4: add_node provenance + state args
# ---------------------------------------------------------------------------


def test_add_node_default_provenance_grounded(tmp_db_path):
    """When provenance arg is omitted, node has provenance='grounded'."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    result = add_node(
        storage=storage,
        arguments={"session_id": "ses_1", "parent_id": "r1",
                   "type": "question", "text": "Q?"},
        now="2026-05-22T00:00:00Z",
        new_id=lambda p: "n1",
    )
    assert result["node"]["provenance"] == "grounded"


def test_add_node_inferred_provenance(tmp_db_path):
    """add_node(provenance='inferred') creates a node with provenance='inferred'."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    result = add_node(
        storage=storage,
        arguments={"session_id": "ses_1", "parent_id": "r1",
                   "type": "hypothesis", "text": "H",
                   "provenance": "inferred"},
        now="2026-05-22T00:00:00Z",
        new_id=lambda p: "n1",
    )
    assert result["node"]["provenance"] == "inferred"


def test_add_node_imported_with_archived_state(tmp_db_path):
    """add_node(provenance='imported', state='archived') creates the right combo."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    result = add_node(
        storage=storage,
        arguments={"session_id": "ses_1", "parent_id": "r1",
                   "type": "evidence", "text": "E",
                   "provenance": "imported", "state": "archived"},
        now="2026-05-22T00:00:00Z",
        new_id=lambda p: "n1",
    )
    assert result["node"]["provenance"] == "imported"
    assert result["node"]["state"] == "archived"


def test_add_node_invalid_provenance_raises(tmp_db_path):
    """add_node(provenance='bogus') raises ValueError (DB CHECK enforces)."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    with pytest.raises(ValueError):
        add_node(
            storage=storage,
            arguments={"session_id": "ses_1", "parent_id": "r1",
                       "type": "question", "text": "Q?",
                       "provenance": "bogus"},
            now="2026-05-22T00:00:00Z",
            new_id=lambda p: "n1",
        )


def test_add_node_manual_provenance(tmp_db_path):
    """add_node(provenance='manual') for user-edited nodes."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    result = add_node(
        storage=storage,
        arguments={"session_id": "ses_1", "parent_id": "r1",
                   "type": "decision", "text": "D",
                   "provenance": "manual"},
        now="2026-05-22T00:00:00Z",
        new_id=lambda p: "n1",
    )
    assert result["node"]["provenance"] == "manual"


def test_add_node_default_state_active(tmp_db_path):
    """When state arg is omitted, default = 'active'."""
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    result = add_node(
        storage=storage,
        arguments={"session_id": "ses_1", "parent_id": "r1",
                   "type": "question", "text": "Q?"},
        now="2026-05-22T00:00:00Z",
        new_id=lambda p: "n1",
    )
    assert result["node"]["state"] == "active"


# ---------------------------------------------------------------------------
# Task 6: add_node v3 extension, set_focus root_id, list_open_nodes state
# ---------------------------------------------------------------------------


def test_add_node_end_with_paired_for(tmp_db_path):
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    # Start node
    start = add_node(
        storage=storage,
        arguments={"session_id": "ses_1", "parent_id": "r1",
                   "type": "start", "text": "S"},
        now="2026-05-22T00:00:00Z",
        new_id=lambda p: "n_start",
    )
    assert start["node"]["type"] == "start"
    sid = start["node"]["id"]
    # End node with paired_for
    end = add_node(
        storage=storage,
        arguments={"session_id": "ses_1", "parent_id": sid,
                   "type": "end", "text": "E",
                   "paired_for": sid,
                   "achievement_conditions": "done when X"},
        now="2026-05-22T00:00:00Z",
        new_id=lambda p: "n_end",
    )
    assert end["node"]["type"] == "end"
    assert end["node"]["paired_for"] == sid


def test_set_focus_accepts_root_id(tmp_db_path):
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    result = set_focus(
        storage=storage,
        arguments={"session_id": "ses_1", "node_id": "r1"},
        now="2026-05-22T00:00:00Z",
    )
    assert result["focus_node_id"] == "r1"


def test_list_open_nodes_filters_by_state(tmp_db_path):
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_a", session_id="ses_1",
                           node_type="question", text="a",
                           parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_b", session_id="ses_1",
                           node_type="question", text="b",
                           parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    # Force n_b into closed state.
    with storage.connect() as conn:
        conn.execute("UPDATE nodes SET state = 'closed' WHERE id = 'n_b'")
    result = list_open_nodes(
        storage=storage,
        arguments={"session_id": "ses_1", "state": "active"},
    )
    ids = {n["id"] for n in result["nodes"]}
    assert ids == {"n_a"}


# ---------------------------------------------------------------------------
# State machine tools: mark_reached / dump_persist / delete / force_delete
# ---------------------------------------------------------------------------


def _seed_min_subgraph(storage: Storage) -> None:
    """Set up scope/session/root + Start/End for the state-machine tests."""
    storage.insert_session(session_id="ses_1", scope=None, label=None,
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
                           paired_for="n_s", achievement_conditions="done",
                           now="2026-05-22T00:00:00Z")


def test_mark_reached_tool(tmp_db_path: str) -> None:
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    _seed_min_subgraph(storage)
    result = tools.mark_reached(
        storage,
        arguments={"session_id": "ses_1", "end_node_id": "n_e"},
        now="2026-05-22T01:00:00Z",
    )
    assert result["end_node"]["state"] == "closed"


def test_dump_persist_tool(tmp_db_path: str) -> None:
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    _seed_min_subgraph(storage)
    tools.mark_reached(storage,
                       arguments={"session_id": "ses_1", "end_node_id": "n_e"},
                       now="2026-05-22T01:00:00Z")
    tools.dump_persist(storage,
                       arguments={"session_id": "ses_1",
                                  "start_node_id": "n_s",
                                  "destination": "/tmp/dump.md"},
                       now="2026-05-22T02:00:00Z")
    for nid in ("n_s", "n_e"):
        node = storage.get_node(session_id="ses_1", node_id=nid)
        assert node["state"] == "deletable"


def test_delete_tool_removes_subgraph(tmp_db_path: str) -> None:
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    _seed_min_subgraph(storage)
    tools.mark_reached(storage,
                       arguments={"session_id": "ses_1", "end_node_id": "n_e"},
                       now="2026-05-22T01:00:00Z")
    tools.dump_persist(storage,
                       arguments={"session_id": "ses_1",
                                  "start_node_id": "n_s",
                                  "destination": None},
                       now="2026-05-22T02:00:00Z")
    tools.delete(storage,
                 arguments={"session_id": "ses_1", "start_node_id": "n_s"},
                 now="2026-05-22T03:00:00Z")
    assert storage.get_node(session_id="ses_1", node_id="n_s") is None
    assert storage.get_node(session_id="ses_1", node_id="n_e") is None


def test_delete_rejects_non_deletable_state(tmp_db_path: str) -> None:
    """`delete` must require subgraph state='deletable' (pre-flight check)."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    _seed_min_subgraph(storage)
    with pytest.raises(ValueError, match="deletable"):
        tools.delete(storage,
                     arguments={"session_id": "ses_1", "start_node_id": "n_s"},
                     now="2026-05-22T01:00:00Z")


# ---------------------------------------------------------------------------
# Fix #2: pool_elevate validations (RED)
# ---------------------------------------------------------------------------


def _seed_pool_elevate_setup(storage: Storage, scope: str = "dpd") -> dict:
    """Return a dict with pool_id, session_id, n_s, n_e already set up."""
    from dpd_mcp_server import tools
    storage.get_or_create_scope_root(scope=scope, now="2026-05-22T00:00:00Z")
    storage.insert_session(session_id="ses_1", scope=scope, label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r1", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s", session_id="ses_1", node_type="start",
                           text="s", parent_id="r1",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_e", session_id="ses_1", node_type="end",
                           text="e", parent_id="n_s",
                           paired_for="n_s", achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    added = tools.pool_add(storage, scope=scope,
                           arguments={"text": "test thought"},
                           now="2026-05-22T00:00:00Z")
    return {"pool_id": added["pool_item"]["id"],
            "session_id": "ses_1", "n_s": "n_s", "n_e": "n_e"}


def test_pool_elevate_rejects_already_elevated(tmp_db_path: str) -> None:
    """pool_elevate must refuse if the pool item is already elevated."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    ctx = _seed_pool_elevate_setup(storage)

    # First elevation succeeds.
    tools.pool_elevate(storage, scope="dpd",
                       arguments={"pool_id": ctx["pool_id"],
                                  "target_end_node_id": ctx["n_e"],
                                  "type": "evidence",
                                  "session_id": ctx["session_id"]},
                       now="2026-05-22T01:00:00Z")

    # Second elevation must raise with "already elevated".
    with pytest.raises(ValueError, match="already elevated"):
        tools.pool_elevate(storage, scope="dpd",
                           arguments={"pool_id": ctx["pool_id"],
                                      "target_end_node_id": ctx["n_e"],
                                      "type": "evidence",
                                      "session_id": ctx["session_id"]},
                           now="2026-05-22T02:00:00Z")


def test_pool_elevate_rejects_dropped(tmp_db_path: str) -> None:
    """pool_elevate must refuse if the pool item was dropped."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    ctx = _seed_pool_elevate_setup(storage)

    tools.pool_drop(storage, scope="dpd",
                    arguments={"pool_id": ctx["pool_id"], "reason": "noise"},
                    now="2026-05-22T01:00:00Z")

    with pytest.raises(ValueError, match="is dropped"):
        tools.pool_elevate(storage, scope="dpd",
                           arguments={"pool_id": ctx["pool_id"],
                                      "target_end_node_id": ctx["n_e"],
                                      "type": "evidence",
                                      "session_id": ctx["session_id"]},
                           now="2026-05-22T02:00:00Z")


def test_pool_elevate_rejects_non_active_end(tmp_db_path: str) -> None:
    """pool_elevate must refuse if the target End node is not in state='active'."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    ctx = _seed_pool_elevate_setup(storage)

    # Reach the End to move it to state='closed'.
    tools.mark_reached(storage,
                       arguments={"session_id": ctx["session_id"],
                                  "end_node_id": ctx["n_e"]},
                       now="2026-05-22T01:00:00Z")

    # Elevation into a closed End must fail.
    with pytest.raises(ValueError, match="state.*closed|closed.*state"):
        tools.pool_elevate(storage, scope="dpd",
                           arguments={"pool_id": ctx["pool_id"],
                                      "target_end_node_id": ctx["n_e"],
                                      "type": "evidence",
                                      "session_id": ctx["session_id"]},
                           now="2026-05-22T02:00:00Z")


def test_pool_elevate_rejects_scope_mismatch(tmp_db_path: str) -> None:
    """pool_elevate must refuse if the session scope doesn't match pool scope."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    # Pool is in scope "alpha", session is in scope "beta".
    storage.get_or_create_scope_root(scope="alpha", now="2026-05-22T00:00:00Z")
    storage.get_or_create_scope_root(scope="beta", now="2026-05-22T00:00:00Z")
    storage.insert_session(session_id="ses_beta", scope="beta", label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r_beta", session_id="ses_beta", topic="t",
                        now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_s_b", session_id="ses_beta", node_type="start",
                           text="s", parent_id="r_beta",
                           paired_for=None, achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_node_v3(node_id="n_e_b", session_id="ses_beta", node_type="end",
                           text="e", parent_id="n_s_b",
                           paired_for="n_s_b", achievement_conditions=None,
                           now="2026-05-22T00:00:00Z")
    added = tools.pool_add(storage, scope="alpha",
                           arguments={"text": "alpha thought"},
                           now="2026-05-22T00:00:00Z")
    pid = added["pool_item"]["id"]

    # Elevating an "alpha" pool item into a "beta" session must fail.
    with pytest.raises(ValueError, match="scope.*does not match|does not match.*scope"):
        tools.pool_elevate(storage, scope="alpha",
                           arguments={"pool_id": pid,
                                      "target_end_node_id": "n_e_b",
                                      "type": "evidence",
                                      "session_id": "ses_beta"},
                           now="2026-05-22T02:00:00Z")


def test_force_delete_node_tool(tmp_db_path: str) -> None:
    """Single-node force delete bypasses state preconditions."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    _seed_min_subgraph(storage)
    # Force delete the End node first (paired_for FK constraint requires End → Start order on delete)
    tools.force_delete(storage,
                       arguments={"session_id": "ses_1", "node_id": "n_e"},
                       now="2026-05-22T01:00:00Z")
    assert storage.get_node(session_id="ses_1", node_id="n_e") is None
    # Start node still exists
    assert storage.get_node(session_id="ses_1", node_id="n_s") is not None


# ---------------------------------------------------------------------------
# Task 3 (v0.3.1): pool_reject tool
# ---------------------------------------------------------------------------


def test_pool_reject_sets_rejected_at_and_reason(tmp_db_path):
    """pool_reject(pool_id, reason='abc') sets rejected_at to now and rejected_reason to 'abc'."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    added = tools.pool_add(storage, scope="dpd",
                           arguments={"text": "to be rejected"},
                           now="2026-05-22T00:00:00Z")
    pid = added["pool_item"]["id"]

    result = tools.pool_reject(storage,
                               arguments={"pool_id": pid, "reason": "not relevant"},
                               now="2026-05-22T01:00:00Z")

    item = result["pool_item"]
    assert item["rejected_at"] == "2026-05-22T01:00:00Z"
    assert item["rejected_reason"] == "not relevant"


def test_pool_reject_without_reason(tmp_db_path):
    """pool_reject(pool_id) sets rejected_at but leaves rejected_reason NULL."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    added = tools.pool_add(storage, scope="dpd",
                           arguments={"text": "to be rejected silently"},
                           now="2026-05-22T00:00:00Z")
    pid = added["pool_item"]["id"]

    result = tools.pool_reject(storage,
                               arguments={"pool_id": pid},
                               now="2026-05-22T01:00:00Z")

    item = result["pool_item"]
    assert item["rejected_at"] == "2026-05-22T01:00:00Z"
    assert item["rejected_reason"] is None


def test_pool_reject_idempotent_reason_update(tmp_db_path):
    """Re-rejecting an already-rejected item updates rejected_reason but keeps original rejected_at."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    added = tools.pool_add(storage, scope="dpd",
                           arguments={"text": "reject twice"},
                           now="2026-05-22T00:00:00Z")
    pid = added["pool_item"]["id"]

    tools.pool_reject(storage,
                      arguments={"pool_id": pid, "reason": "first reason"},
                      now="2026-05-22T01:00:00Z")
    result = tools.pool_reject(storage,
                               arguments={"pool_id": pid, "reason": "updated reason"},
                               now="2026-05-22T02:00:00Z")

    item = result["pool_item"]
    # rejected_at must stay at the FIRST rejection timestamp.
    assert item["rejected_at"] == "2026-05-22T01:00:00Z"
    assert item["rejected_reason"] == "updated reason"


def test_pool_reject_on_dropped_raises(tmp_db_path):
    """pool_reject on a dropped item (dropped_at IS NOT NULL) raises ValueError."""
    from dpd_mcp_server import tools
    import pytest as _pytest
    storage = Storage.open(tmp_db_path)
    added = tools.pool_add(storage, scope="dpd",
                           arguments={"text": "dropped item"},
                           now="2026-05-22T00:00:00Z")
    pid = added["pool_item"]["id"]
    tools.pool_drop(storage, scope="dpd",
                    arguments={"pool_id": pid, "reason": "noise"},
                    now="2026-05-22T01:00:00Z")

    with _pytest.raises(ValueError, match="dropped"):
        tools.pool_reject(storage,
                          arguments={"pool_id": pid, "reason": "too late"},
                          now="2026-05-22T02:00:00Z")


def test_pool_reject_unknown_id_raises(tmp_db_path):
    """pool_reject with non-existent pool_id raises ValueError."""
    from dpd_mcp_server import tools
    import pytest as _pytest
    storage = Storage.open(tmp_db_path)

    with _pytest.raises(ValueError, match="not found"):
        tools.pool_reject(storage,
                          arguments={"pool_id": "nonexistent-id"},
                          now="2026-05-22T01:00:00Z")


def test_pool_reject_does_not_affect_dropped_at(tmp_db_path):
    """Rejecting an item does NOT modify its dropped_at (orthogonal lifecycle).

    Note: pool_reject raises if dropped_at IS NOT NULL (dropped items cannot
    be rejected). This test verifies the orthogonal direction: a newly rejected
    item has dropped_at = NULL and a subsequent call to pool_drop can still
    set it independently.
    """
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    added = tools.pool_add(storage, scope="dpd",
                           arguments={"text": "reject then drop"},
                           now="2026-05-22T00:00:00Z")
    pid = added["pool_item"]["id"]

    reject_result = tools.pool_reject(storage,
                                      arguments={"pool_id": pid, "reason": "rejected"},
                                      now="2026-05-22T01:00:00Z")
    # After rejection, dropped_at must still be NULL.
    assert reject_result["pool_item"]["dropped_at"] is None


# ---------------------------------------------------------------------------
# Task 6 (v0.3.1): set_session_mode — transition table validation
# ---------------------------------------------------------------------------


def _new_session(storage: Storage, mode: str | None = "entry") -> str:
    """Helper: insert a session with the given mode; returns session_id."""
    import sqlite3 as _sqlite3
    sid = f"ses_t_{mode or 'null'}"
    storage.insert_session(
        session_id=sid, scope=None, label=None,
        now="2026-05-22T00:00:00Z",
        mode=mode if mode is not None else "entry",
    )
    if mode is None:
        # Simulate legacy NULL mode by patching directly.
        with _sqlite3.connect(storage._db_path) as conn:
            conn.execute("UPDATE sessions SET mode = NULL WHERE id = ?", (sid,))
    return sid


def test_set_session_mode_entry_to_ambient_ok(tmp_db_path: str) -> None:
    """entry → ambient is allowed."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode="entry")
    result = tools.set_session_mode(
        storage=storage,
        arguments={"session_id": sid, "mode": "ambient"},
        now="2026-05-22T01:00:00Z",
    )
    assert result["session"]["mode"] == "ambient"
    assert storage.get_session(session_id=sid)["mode"] == "ambient"


def test_set_session_mode_entry_to_idle_ok(tmp_db_path: str) -> None:
    """entry → idle is allowed (abort)."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode="entry")
    result = tools.set_session_mode(
        storage=storage,
        arguments={"session_id": sid, "mode": "idle"},
        now="2026-05-22T01:00:00Z",
    )
    assert result["session"]["mode"] == "idle"


def test_set_session_mode_ambient_to_idle_ok(tmp_db_path: str) -> None:
    """ambient → idle is allowed (normal completion)."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode="ambient")
    result = tools.set_session_mode(
        storage=storage,
        arguments={"session_id": sid, "mode": "idle"},
        now="2026-05-22T01:00:00Z",
    )
    assert result["session"]["mode"] == "idle"


def test_set_session_mode_idle_to_entry_ok(tmp_db_path: str) -> None:
    """idle → entry is allowed (resume into new subgraph)."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode="idle")
    result = tools.set_session_mode(
        storage=storage,
        arguments={"session_id": sid, "mode": "entry"},
        now="2026-05-22T01:00:00Z",
    )
    assert result["session"]["mode"] == "entry"


def test_set_session_mode_null_to_entry_ok(tmp_db_path: str) -> None:
    """null (legacy) → entry is allowed (legacy migration)."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode=None)
    result = tools.set_session_mode(
        storage=storage,
        arguments={"session_id": sid, "mode": "entry"},
        now="2026-05-22T01:00:00Z",
    )
    assert result["session"]["mode"] == "entry"


def test_set_session_mode_null_to_ambient_ok(tmp_db_path: str) -> None:
    """null (legacy) → ambient is allowed (heuristic auto-detect on resume)."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode=None)
    result = tools.set_session_mode(
        storage=storage,
        arguments={"session_id": sid, "mode": "ambient"},
        now="2026-05-22T01:00:00Z",
    )
    assert result["session"]["mode"] == "ambient"


def test_set_session_mode_self_transition_idempotent(tmp_db_path: str) -> None:
    """entry → entry (self-transition) is a no-op and must not raise."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode="entry")
    result = tools.set_session_mode(
        storage=storage,
        arguments={"session_id": sid, "mode": "entry"},
        now="2026-05-22T01:00:00Z",
    )
    assert result["session"]["mode"] == "entry"


def test_set_session_mode_ambient_to_entry_raises(tmp_db_path: str) -> None:
    """ambient → entry is disallowed (must go via idle first)."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode="ambient")
    with pytest.raises(ValueError, match="ambient"):
        tools.set_session_mode(
            storage=storage,
            arguments={"session_id": sid, "mode": "entry"},
            now="2026-05-22T01:00:00Z",
        )


def test_set_session_mode_idle_to_ambient_raises(tmp_db_path: str) -> None:
    """idle → ambient is disallowed (must go through entry first)."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode="idle")
    with pytest.raises(ValueError, match="idle"):
        tools.set_session_mode(
            storage=storage,
            arguments={"session_id": sid, "mode": "ambient"},
            now="2026-05-22T01:00:00Z",
        )


def test_set_session_mode_closed_value_raises(tmp_db_path: str) -> None:
    """'closed' is not a valid mode value; must reject regardless of current mode."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode="entry")
    with pytest.raises(ValueError, match="closed|invalid mode"):
        tools.set_session_mode(
            storage=storage,
            arguments={"session_id": sid, "mode": "closed"},
            now="2026-05-22T01:00:00Z",
        )


def test_set_session_mode_unknown_session_raises(tmp_db_path: str) -> None:
    """Passing a non-existent session_id must raise ValueError."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    with pytest.raises(ValueError, match="not found"):
        tools.set_session_mode(
            storage=storage,
            arguments={"session_id": "ses_ghost", "mode": "entry"},
            now="2026-05-22T01:00:00Z",
        )


def test_set_session_mode_invalid_mode_value_raises(tmp_db_path: str) -> None:
    """Passing an unrecognised mode string (e.g. 'bogus') must raise ValueError."""
    from dpd_mcp_server import tools
    storage = Storage.open(tmp_db_path)
    sid = _new_session(storage, mode="entry")
    with pytest.raises(ValueError, match="bogus|invalid mode"):
        tools.set_session_mode(
            storage=storage,
            arguments={"session_id": sid, "mode": "bogus"},
            now="2026-05-22T01:00:00Z",
        )


# ---------------------------------------------------------------------------
# Task 7: bulk_import_subgraph
# ---------------------------------------------------------------------------


def _setup_bulk_import(storage: Storage) -> tuple[str, str]:
    """Create a session + root, return (session_id, root_id)."""
    from dpd_mcp_server import tools as t
    t.start_session(
        storage=storage, arguments={}, now="2026-05-22T10:00:00Z",
        new_id=lambda p: "ses_bi",
    )
    t.spawn_root(
        storage=storage,
        arguments={"session_id": "ses_bi", "topic": "Import test"},
        now="2026-05-22T10:00:00Z",
        new_id=lambda p: "root_bi",
    )
    return "ses_bi", "root_bi"


def test_bulk_import_creates_all_nodes_atomically(tmp_db_path: str) -> None:
    """Import 5 nodes in a tree. All inserted with provenance='imported', state='archived'."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "n1", "type": "question", "text": "Q1", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
        {"id": "n2", "type": "hypothesis", "text": "H1", "parent_id": "n1", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
        {"id": "n3", "type": "hypothesis", "text": "H2", "parent_id": "n1", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
        {"id": "n4", "type": "evidence", "text": "E1", "parent_id": "n2", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
        {"id": "n5", "type": "evidence", "text": "E2", "parent_id": "n3", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
    ]
    result = t.bulk_import_subgraph(
        storage=storage,
        arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
        now="2026-05-22T10:01:00Z",
    )

    assert len(result["imported_nodes"]) == 5
    for row in result["imported_nodes"]:
        assert row["provenance"] == "imported"
        assert row["state"] == "archived"
        assert row["session_id"] == sid


def test_bulk_import_creates_edges(tmp_db_path: str) -> None:
    """Import 3 nodes + 2 edges. Edges visible via list_edges."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "n1", "type": "question", "text": "Q", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
        {"id": "n2", "type": "hypothesis", "text": "H1", "parent_id": "n1", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
        {"id": "n3", "type": "hypothesis", "text": "H2", "parent_id": "n1", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
    ]
    edges = [
        {"from": "n2", "to": "n3", "type": "contradicts", "reason": "mutually exclusive"},
        {"from": "n3", "to": "n1", "type": "supports", "reason": None},
    ]
    result = t.bulk_import_subgraph(
        storage=storage,
        arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": edges},
        now="2026-05-22T10:01:00Z",
    )

    assert len(result["imported_edges"]) == 2
    all_edges = storage.list_edges(session_id=sid)
    assert len(all_edges) == 2


def test_bulk_import_rolls_back_on_fk_failure(tmp_db_path: str) -> None:
    """If one node has invalid parent_id, transaction rolls back, no partial state."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "n1", "type": "question", "text": "Q", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
        {"id": "n2", "type": "hypothesis", "text": "H", "parent_id": "nonexistent_parent", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
    ]
    with pytest.raises(ValueError, match="parent_id|nonexistent_parent|not found"):
        t.bulk_import_subgraph(
            storage=storage,
            arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
            now="2026-05-22T10:01:00Z",
        )

    # No partial state: n1 was not inserted
    assert storage.get_node(session_id=sid, node_id="n1") is None


def test_bulk_import_rolls_back_on_duplicate_id(tmp_db_path: str) -> None:
    """If imported nodes contain duplicate ids, rollback."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "dup", "type": "question", "text": "Q1", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
        {"id": "dup", "type": "question", "text": "Q2", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
    ]
    with pytest.raises((ValueError, Exception)):
        t.bulk_import_subgraph(
            storage=storage,
            arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
            now="2026-05-22T10:01:00Z",
        )

    # No partial state
    assert storage.get_node(session_id=sid, node_id="dup") is None


def test_bulk_import_default_provenance_imported(tmp_db_path: str) -> None:
    """All imported nodes have provenance='imported' by default."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "n1", "type": "question", "text": "Q", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
    ]
    result = t.bulk_import_subgraph(
        storage=storage,
        arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
        now="2026-05-22T10:01:00Z",
    )
    assert result["imported_nodes"][0]["provenance"] == "imported"


def test_bulk_import_default_state_archived(tmp_db_path: str) -> None:
    """All imported nodes have state='archived' by default."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "n1", "type": "question", "text": "Q", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
    ]
    result = t.bulk_import_subgraph(
        storage=storage,
        arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
        now="2026-05-22T10:01:00Z",
    )
    assert result["imported_nodes"][0]["state"] == "archived"


def test_bulk_import_custom_provenance_state(tmp_db_path: str) -> None:
    """provenance='inferred', state='active' overrides apply to all nodes."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "n1", "type": "question", "text": "Q", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
        {"id": "n2", "type": "hypothesis", "text": "H", "parent_id": "n1", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
    ]
    result = t.bulk_import_subgraph(
        storage=storage,
        arguments={
            "session_id": sid, "root_id": rid, "nodes": nodes, "edges": [],
            "provenance": "inferred", "state": "active",
        },
        now="2026-05-22T10:01:00Z",
    )
    for row in result["imported_nodes"]:
        assert row["provenance"] == "inferred"
        assert row["state"] == "active"


def test_bulk_import_paired_for_resolves(tmp_db_path: str) -> None:
    """End node paired_for refers to Start node in same import — resolves correctly."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "s1", "type": "start", "text": "Start", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
        {"id": "e1", "type": "end", "text": "End", "parent_id": "s1", "parent_kind": "node", "paired_for": "s1", "achievement_conditions": None},
    ]
    result = t.bulk_import_subgraph(
        storage=storage,
        arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
        now="2026-05-22T10:01:00Z",
    )
    assert len(result["imported_nodes"]) == 2
    end_node = next(r for r in result["imported_nodes"] if r["id"] == "e1")
    assert end_node["paired_for"] == "s1"


def test_bulk_import_edge_to_external_node(tmp_db_path: str) -> None:
    """Edge from imported node to pre-existing DB node works."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    # Pre-existing node
    t.add_node(
        storage=storage,
        arguments={"session_id": sid, "parent_id": rid, "type": "question", "text": "Existing"},
        now="2026-05-22T10:00:30Z",
        new_id=lambda p: "existing_node",
    )

    nodes = [
        {"id": "n1", "type": "hypothesis", "text": "H", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
    ]
    edges = [
        {"from": "n1", "to": "existing_node", "type": "supports", "reason": None},
    ]
    result = t.bulk_import_subgraph(
        storage=storage,
        arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": edges},
        now="2026-05-22T10:01:00Z",
    )
    assert len(result["imported_edges"]) == 1
    assert result["imported_edges"][0]["to_node"] == "existing_node"


def test_bulk_import_empty_edges_list_ok(tmp_db_path: str) -> None:
    """edges=[] is valid (nodes-only import)."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "n1", "type": "question", "text": "Q", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
    ]
    result = t.bulk_import_subgraph(
        storage=storage,
        arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
        now="2026-05-22T10:01:00Z",
    )
    assert result["imported_edges"] == []
    assert len(result["imported_nodes"]) == 1


def test_bulk_import_cycle_detection(tmp_db_path: str) -> None:
    """A→B→A cycle in parent_id chain raises ValueError."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    # n1 parent = n2, n2 parent = n1: cycle
    nodes = [
        {"id": "n1", "type": "question", "text": "Q", "parent_id": "n2", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
        {"id": "n2", "type": "hypothesis", "text": "H", "parent_id": "n1", "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
    ]
    with pytest.raises(ValueError, match="cycle|circular"):
        t.bulk_import_subgraph(
            storage=storage,
            arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
            now="2026-05-22T10:01:00Z",
        )


def test_bulk_import_unknown_root_raises(tmp_db_path: str) -> None:
    """Non-existent root_id raises ValueError before any insert."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "n1", "type": "question", "text": "Q", "parent_id": "ghost_root", "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
    ]
    with pytest.raises(ValueError, match="root_id|ghost_root|not found"):
        t.bulk_import_subgraph(
            storage=storage,
            arguments={"session_id": sid, "root_id": "ghost_root", "nodes": nodes, "edges": []},
            now="2026-05-22T10:01:00Z",
        )


def test_bulk_import_rejects_parent_kind_mismatch_root(tmp_db_path: str) -> None:
    """parent_id is root but parent_kind='node' → ValueError (silent corruption guard).

    Without this validation, walk_subtree (which filters children by both
    parent_id AND parent_kind) would not see this node, leaving it invisible
    from normal tree traversal.
    """
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        # parent_id=root but parent_kind='node' — mismatch
        {"id": "n1", "type": "question", "text": "Q", "parent_id": rid, "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
    ]
    with pytest.raises(ValueError, match="parent_kind"):
        t.bulk_import_subgraph(
            storage=storage,
            arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
            now="2026-05-22T10:01:00Z",
        )

    # Verify no nodes inserted (rollback).
    rows = storage.list_open_nodes(session_id=sid)
    assert len(rows) == 0


def test_bulk_import_rejects_parent_kind_mismatch_node(tmp_db_path: str) -> None:
    """parent_id is a node but parent_kind='root' → ValueError."""
    from dpd_mcp_server import tools as t
    storage = Storage.open(tmp_db_path)
    sid, rid = _setup_bulk_import(storage)

    nodes = [
        {"id": "n1", "type": "question", "text": "Q", "parent_id": rid, "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
        # n2 declares parent_id=n1 (a node) but parent_kind='root'
        {"id": "n2", "type": "hypothesis", "text": "H", "parent_id": "n1", "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
    ]
    with pytest.raises(ValueError, match="parent_kind"):
        t.bulk_import_subgraph(
            storage=storage,
            arguments={"session_id": sid, "root_id": rid, "nodes": nodes, "edges": []},
            now="2026-05-22T10:01:00Z",
        )

    # Verify no nodes inserted (rollback — n1 should NOT persist either).
    rows = storage.list_open_nodes(session_id=sid)
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# Task 8 (v0.3.1): list_sessions mode_filter arg
# ---------------------------------------------------------------------------


def _seed_mode_sessions(storage: Storage) -> None:
    """Insert sessions with entry, ambient, idle modes for mode_filter tests."""
    storage.insert_session(session_id="ses_entry", scope=None, label="e",
                           mode="entry", now="2026-05-22T10:00:00Z")
    storage.insert_session(session_id="ses_ambient", scope=None, label="a",
                           mode="ambient", now="2026-05-22T10:01:00Z")
    storage.insert_session(session_id="ses_idle", scope=None, label="i",
                           mode="idle", now="2026-05-22T10:02:00Z")


def test_list_sessions_no_filter_returns_all(tmp_db_path: str) -> None:
    """Without mode_filter, all sessions are returned (existing behavior)."""
    storage = Storage.open(tmp_db_path)
    _seed_mode_sessions(storage)

    result = list_sessions(storage=storage, arguments={})
    ids = {s["id"] for s in result["sessions"]}
    assert ids == {"ses_entry", "ses_ambient", "ses_idle"}


def test_list_sessions_single_mode_filter(tmp_db_path: str) -> None:
    """mode_filter='ambient' returns only ambient sessions."""
    storage = Storage.open(tmp_db_path)
    _seed_mode_sessions(storage)

    result = list_sessions(storage=storage, arguments={"mode_filter": "ambient"})
    ids = [s["id"] for s in result["sessions"]]
    assert ids == ["ses_ambient"]


def test_list_sessions_list_mode_filter(tmp_db_path: str) -> None:
    """mode_filter=['entry', 'ambient'] returns sessions with either mode."""
    storage = Storage.open(tmp_db_path)
    _seed_mode_sessions(storage)

    result = list_sessions(storage=storage, arguments={"mode_filter": ["entry", "ambient"]})
    ids = {s["id"] for s in result["sessions"]}
    assert ids == {"ses_entry", "ses_ambient"}


def test_list_sessions_invalid_mode_raises(tmp_db_path: str) -> None:
    """mode_filter='bogus' raises ValueError."""
    storage = Storage.open(tmp_db_path)

    with pytest.raises(ValueError, match="bogus|invalid"):
        list_sessions(storage=storage, arguments={"mode_filter": "bogus"})


def test_list_sessions_invalid_list_member_raises(tmp_db_path: str) -> None:
    """mode_filter=['entry', 'bogus'] raises ValueError."""
    storage = Storage.open(tmp_db_path)

    with pytest.raises(ValueError, match="bogus|invalid"):
        list_sessions(storage=storage, arguments={"mode_filter": ["entry", "bogus"]})


# ---------------------------------------------------------------------------
# Task 12 (v0.3.2): find_similar tool business-logic function
# ---------------------------------------------------------------------------


def test_find_similar_returns_list_of_dicts(tmp_db_path: str) -> None:
    from dpd_mcp_server.tools import find_similar
    storage = Storage.open(tmp_db_path)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('s', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('r', 's', 't', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('ns', 's', 'start', 'UNIQUE-SLUG-Q1', 'closed', 'r', "
            "'root', 'closed', ?, ?, ?)", (now, now, now),
        )
    storage._reindex_subgraph(start_node_id="ns")

    out = find_similar(
        storage=storage,
        arguments={"query": "unique-slug-q1", "top_k": 5},
    )
    assert "results" in out
    assert isinstance(out["results"], list)
    assert len(out["results"]) == 1
    assert out["results"][0]["start_node_id"] == "ns"


def test_find_similar_requires_query(tmp_db_path: str) -> None:
    from dpd_mcp_server.tools import find_similar
    storage = Storage.open(tmp_db_path)
    with pytest.raises(ValueError):
        find_similar(storage=storage, arguments={})
