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
        arguments={"scope": "decompose-propagate.protocol", "label": "exp"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda prefix: f"{prefix}_fixed",
    )

    assert result == {"session_id": "ses_fixed"}

    row = storage.get_session(session_id="ses_fixed")
    assert row["scope"] == "decompose-propagate.protocol"
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
        arguments={"scope": "decompose-propagate.protocol", "label": "exp"},
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
    assert result["session"]["scope"] == "decompose-propagate.protocol"
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
