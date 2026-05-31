"""Node-type vocabulary extension + frozenset enforcement (#63).

Adds claim / requirement / open_question (17 -> 20) and moves node-type
enforcement from the DB CHECK to a code-defined Storage.NODE_TYPES frozenset
(mirroring Storage.EDGE_TYPES). These tests pin the vocabulary contract, the
app-code guard, the FTS body-ranking extension, and the drift guard for the
fixed-literal insert paths (resolve_branch / resolve_hypothesis_branch).
"""
from __future__ import annotations

import pytest

from dpd_mcp_server.storage import Storage
from dpd_mcp_server.tools import (
    add_node,
    bulk_import_subgraph,
    get_node,
    spawn_root,
    start_session,
)

_ORIGINAL_17 = {
    "question", "plan", "hypothesis", "goal", "problem",
    "answer", "action", "verification", "decision", "resolution",
    "evidence", "constraint", "assumption", "rationale", "risk",
    "start", "end",
}
_NEW_3 = {"claim", "requirement", "open_question"}


@pytest.fixture
def storage(tmp_db_path: str) -> Storage:
    return Storage.open(tmp_db_path)


def _start_with_root(storage: Storage) -> str:
    start_session(storage=storage, arguments={}, now="2026-06-01T10:00:00Z",
                  new_id=lambda p: "ses_1")
    spawn_root(storage=storage,
               arguments={"session_id": "ses_1", "topic": "t", "reason": "r"},
               now="2026-06-01T10:00:00Z", new_id=lambda p: "root_a")
    return "ses_1"


def test_node_types_is_the_17_plus_3():
    assert Storage.NODE_TYPES == _ORIGINAL_17 | _NEW_3
    assert len(Storage.NODE_TYPES) == 20
    assert _NEW_3 <= Storage.NODE_TYPES


def test_drift_guard_fixed_literals_stay_in_vocabulary():
    # resolve_branch / resolve_hypothesis_branch insert hardcoded 'decision'
    # and 'rationale' node types. After v9 drops the DB CHECK, the frozenset is
    # the sole enforcement, so those literals MUST remain members or those
    # paths would write rows the app-code guard now rejects.
    assert {"decision", "rationale"} <= Storage.NODE_TYPES


@pytest.mark.parametrize("new_type", sorted(_NEW_3))
def test_add_node_accepts_each_new_type(storage: Storage, new_type: str):
    sid = _start_with_root(storage)
    add_node(storage=storage,
             arguments={"session_id": sid, "parent_id": "root_a",
                        "type": new_type, "text": f"a {new_type}"},
             now="2026-06-01T10:05:00Z", new_id=lambda p: "n1")
    row = get_node(storage=storage, arguments={"session_id": sid, "node_id": "n1"})
    assert row["node"]["type"] == new_type


def test_add_node_rejects_unknown_type(storage: Storage):
    sid = _start_with_root(storage)
    with pytest.raises(ValueError):
        add_node(storage=storage,
                 arguments={"session_id": sid, "parent_id": "root_a",
                            "type": "not_a_real_type", "text": "x"},
                 now="2026-06-01T10:05:00Z", new_id=lambda p: "n1")


def test_bulk_import_accepts_mixed_new_and_old_types(storage: Storage):
    sid = _start_with_root(storage)
    nodes = [
        {"id": "n1", "type": "open_question", "text": "OQ", "parent_id": "root_a",
         "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
        {"id": "n2", "type": "claim", "text": "C", "parent_id": "n1",
         "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
        {"id": "n3", "type": "requirement", "text": "R", "parent_id": "n1",
         "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
        {"id": "n4", "type": "evidence", "text": "E", "parent_id": "n2",
         "parent_kind": "node", "paired_for": None, "achievement_conditions": None},
    ]
    result = bulk_import_subgraph(
        storage=storage,
        arguments={"session_id": sid, "root_id": "root_a", "nodes": nodes, "edges": []},
        now="2026-06-01T10:06:00Z",
    )
    assert {r["type"] for r in result["imported_nodes"]} == {
        "open_question", "claim", "requirement", "evidence"}


def test_bulk_import_rejects_unknown_type(storage: Storage):
    sid = _start_with_root(storage)
    nodes = [
        {"id": "n1", "type": "bogus", "text": "x", "parent_id": "root_a",
         "parent_kind": "root", "paired_for": None, "achievement_conditions": None},
    ]
    with pytest.raises(ValueError):
        bulk_import_subgraph(
            storage=storage,
            arguments={"session_id": sid, "root_id": "root_a", "nodes": nodes, "edges": []},
            now="2026-06-01T10:06:00Z",
        )


def test_body_types_gains_claim_and_requirement():
    # claim/requirement are spec-content assertions close to evidence/rationale,
    # so their text should feed body_text (not journey_text) for FTS ranking.
    # open_question is journey-flavored and stays out.
    assert {"claim", "requirement"} <= Storage._BODY_TYPES
    assert "open_question" not in Storage._BODY_TYPES
