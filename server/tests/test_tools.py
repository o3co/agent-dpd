"""Tests for prgp_mcp_server.tools (business logic of each MCP tool)."""

from __future__ import annotations

import pytest

from prgp_mcp_server.storage import Storage
from prgp_mcp_server.tools import (
    add_node,
    close_node,
    get_node,
    list_active_roots,
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
        arguments={"scope": "problem-graph.protocol", "label": "exp"},
        now="2026-05-20T10:00:00Z",
        new_id=lambda prefix: f"{prefix}_fixed",
    )

    assert result == {"session_id": "ses_fixed"}

    row = storage.get_session(session_id="ses_fixed")
    assert row["scope"] == "problem-graph.protocol"
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

    assert result == {"root_id": "root_a"}
    active = storage.list_active_roots(session_id="ses_1")
    assert [r["id"] for r in active] == ["root_a"]
    assert active[0]["topic"] == "MCP architecture"


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

    assert result == {"node_id": "q1"}
    row = storage.get_node(session_id=sid, node_id="q1")
    assert row["type"] == "question"
    assert row["parent_id"] == "root_a"
    assert row["parent_kind"] == "root"


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
