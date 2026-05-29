"""Tests for the note layer (#55 slice): notes table + add_note/list_notes.

The note layer stores anchored long-form narrative (the residue that cannot
be structured into the graph). Notes anchor to a node OR a root (subgraph),
are single-active per (anchor, kind), and grow by append-only supersession.
"""

from __future__ import annotations

import pytest

from dpd_mcp_server import tools
from dpd_mcp_server.storage import Storage

T0 = "2026-05-29T00:00:00Z"
T1 = "2026-05-29T01:00:00Z"
T2 = "2026-05-29T02:00:00Z"


def _bootstrap(storage: Storage) -> None:
    """A session with one root and one node under it."""
    storage.insert_session(session_id="ses_1", scope=None, label=None, now=T0)
    storage.insert_root(root_id="root_1", session_id="ses_1", topic="t", now=T0)
    storage.insert_node_under_parent(
        node_id="node_1",
        session_id="ses_1",
        node_type="question",
        text="q",
        parent_id="root_1",
        now=T0,
    )


def test_add_note_round_trip(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)

    result = storage.add_note(
        session_id="ses_1",
        anchor_kind="node",
        anchor_id="node_1",
        kind="narrative",
        text="long-form residue",
        note_id="note_n1",
        now=T1,
    )

    assert result["note_id"] == "note_n1"
    assert result["superseded_note_id"] is None

    notes = storage.list_notes(session_id="ses_1")
    assert len(notes) == 1
    assert notes[0]["id"] == "note_n1"
    assert notes[0]["anchor_kind"] == "node"
    assert notes[0]["anchor_id"] == "node_1"
    assert notes[0]["kind"] == "narrative"
    assert notes[0]["text"] == "long-form residue"
    assert notes[0]["state"] == "active"
    assert notes[0]["created_at"] == T1


def test_add_note_to_root_anchor(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)

    storage.add_note(
        session_id="ses_1", anchor_kind="root", anchor_id="root_1",
        kind="caveat", text="subgraph-wide caveat", note_id="note_r1", now=T1,
    )

    notes = storage.list_notes(
        session_id="ses_1", anchor_kind="root", anchor_id="root_1",
    )
    assert len(notes) == 1
    assert notes[0]["anchor_kind"] == "root"


def test_second_note_supersedes_first_on_same_axis(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    storage.add_note(
        session_id="ses_1", anchor_kind="node", anchor_id="node_1",
        kind="narrative", text="v1", note_id="note_v1", now=T1,
    )

    result = storage.add_note(
        session_id="ses_1", anchor_kind="node", anchor_id="node_1",
        kind="narrative", text="v2", note_id="note_v2", now=T2,
    )

    assert result["superseded_note_id"] == "note_v1"
    # Exactly one active note remains, and it's the new one.
    active = storage.list_notes(session_id="ses_1")
    assert [n["id"] for n in active] == ["note_v2"]
    assert active[0]["text"] == "v2"
    # The old note survives as archived history.
    old = storage.list_notes(session_id="ses_1", include_archived=True)
    archived = [n for n in old if n["id"] == "note_v1"]
    assert len(archived) == 1
    assert archived[0]["state"] == "archived"
    assert archived[0]["updated_at"] == T2


def test_different_kinds_coexist_on_same_anchor(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    storage.add_note(
        session_id="ses_1", anchor_kind="node", anchor_id="node_1",
        kind="narrative", text="n", note_id="note_a", now=T1,
    )
    storage.add_note(
        session_id="ses_1", anchor_kind="node", anchor_id="node_1",
        kind="caveat", text="c", note_id="note_b", now=T1,
    )
    active = storage.list_notes(session_id="ses_1")
    assert {n["id"] for n in active} == {"note_a", "note_b"}


def test_add_note_rejects_unknown_anchor(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    with pytest.raises(ValueError, match="not found"):
        storage.add_note(
            session_id="ses_1", anchor_kind="node", anchor_id="ghost",
            kind="narrative", text="x", note_id="note_x", now=T1,
        )


def test_add_note_rejects_bad_kind_and_anchor_kind(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    with pytest.raises(ValueError, match="kind"):
        storage.add_note(
            session_id="ses_1", anchor_kind="node", anchor_id="node_1",
            kind="freeform", text="x", note_id="note_x", now=T1,
        )
    with pytest.raises(ValueError, match="anchor_kind"):
        storage.add_note(
            session_id="ses_1", anchor_kind="edge", anchor_id="node_1",
            kind="narrative", text="x", note_id="note_x", now=T1,
        )


def test_add_note_allowed_on_archived_anchor(tmp_db_path: str) -> None:
    """C4: existence — not active state — is the bar (carry-forward / import)."""
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    storage.insert_node_under_parent(
        node_id="node_arch", session_id="ses_1", node_type="question",
        text="q2", parent_id="root_1", now=T0, state="archived",
    )
    storage.add_note(
        session_id="ses_1", anchor_kind="node", anchor_id="node_arch",
        kind="narrative", text="on archived", note_id="note_arch", now=T1,
    )
    assert len(storage.list_notes(
        session_id="ses_1", anchor_kind="node", anchor_id="node_arch")) == 1


# --- D7: notes are physically deleted alongside their anchors -------------

def test_force_delete_node_cascades_to_notes(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    storage.add_note(
        session_id="ses_1", anchor_kind="node", anchor_id="node_1",
        kind="narrative", text="doomed", note_id="note_d", now=T1,
    )

    storage.force_delete_node(session_id="ses_1", node_id="node_1", now=T2)

    assert storage.list_notes(session_id="ses_1", include_archived=True) == []


def test_delete_subgraph_cascades_to_member_notes(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    storage.insert_session(session_id="ses_1", scope=None, label=None, now=T0)
    storage.insert_root(root_id="root_1", session_id="ses_1", topic="t", now=T0)
    storage.insert_node_v3(
        node_id="start_1", session_id="ses_1", node_type="start", text="s",
        parent_id="root_1", paired_for=None, achievement_conditions=None,
        now=T0,
    )
    storage.insert_node_under_parent(
        node_id="child_1", session_id="ses_1", node_type="question", text="q",
        parent_id="start_1", now=T0,
    )
    storage.add_note(
        session_id="ses_1", anchor_kind="node", anchor_id="child_1",
        kind="narrative", text="doomed", note_id="note_c", now=T1,
    )
    # delete_subgraph requires the subgraph to be in 'deletable' state.
    with storage.connect() as conn:
        conn.execute(
            "UPDATE nodes SET state = 'deletable' WHERE session_id = 'ses_1'"
        )

    storage.delete_subgraph(
        session_id="ses_1", start_node_id="start_1", now=T2,
    )

    assert storage.list_notes(session_id="ses_1", include_archived=True) == []


def test_force_purge_session_removes_notes(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    storage.add_note(
        session_id="ses_1", anchor_kind="node", anchor_id="node_1",
        kind="narrative", text="node note", note_id="note_n", now=T1,
    )
    storage.add_note(
        session_id="ses_1", anchor_kind="root", anchor_id="root_1",
        kind="caveat", text="root note", note_id="note_r", now=T1,
    )

    # Must not raise (the session_id FK would fail if notes outlived the
    # session row), and must leave no notes behind.
    storage.force_purge_session(session_id="ses_1", now=T2)

    assert storage.list_notes(session_id="ses_1", include_archived=True) == []


def test_purge_session_removes_root_anchored_notes(tmp_db_path: str) -> None:
    """purge_session (no-nodes path) must still clear root-anchored notes,
    or the sessions FK on notes.session_id breaks the purge."""
    storage = Storage.open(tmp_db_path)
    # mode='idle' so purge_session's precondition is satisfied; no nodes exist
    # so the "no nodes remain" check also passes — the only thing left to clean
    # is the root-anchored note.
    storage.insert_session(
        session_id="ses_1", scope=None, label=None, mode="idle", now=T0,
    )
    storage.insert_root(root_id="root_1", session_id="ses_1", topic="t", now=T0)
    storage.add_note(
        session_id="ses_1", anchor_kind="root", anchor_id="root_1",
        kind="caveat", text="root note", note_id="note_r", now=T1,
    )

    storage.purge_session(session_id="ses_1", now=T2)

    assert storage.get_session(session_id="ses_1") is None


# --- D3: MCP tool surface (tools.add_note / tools.list_notes) -------------

def _fixed_id(prefix: str) -> str:
    return f"{prefix}_fixed"


def test_add_note_tool_round_trip(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)

    result = tools.add_note(
        storage=storage,
        arguments={
            "session_id": "ses_1",
            "anchor_kind": "node",
            "anchor_id": "node_1",
            "kind": "narrative",
            "text": "body",
        },
        now=T1,
        new_id=_fixed_id,
    )

    assert result["note_id"] == "note_fixed"
    assert result["superseded_note_id"] is None

    listed = tools.list_notes(
        storage=storage, arguments={"session_id": "ses_1"},
    )
    assert len(listed["notes"]) == 1
    assert listed["notes"][0]["text"] == "body"
    assert listed["notes"][0]["id"] == "note_fixed"


def test_list_notes_tool_rejects_partial_anchor(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    with pytest.raises(ValueError, match="anchor_kind"):
        tools.list_notes(
            storage=storage,
            arguments={"session_id": "ses_1", "anchor_kind": "node"},
        )
    with pytest.raises(ValueError, match="anchor_kind"):
        tools.list_notes(
            storage=storage,
            arguments={"session_id": "ses_1", "anchor_id": "node_1"},
        )


def test_add_note_tool_missing_required_arg(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    with pytest.raises(ValueError, match="missing required argument"):
        tools.add_note(
            storage=storage,
            arguments={"session_id": "ses_1", "anchor_kind": "node"},
            now=T1,
            new_id=_fixed_id,
        )


def test_add_note_rejects_empty_text(tmp_db_path: str) -> None:
    """Storage.add_note matches the tool layer: empty body is rejected."""
    storage = Storage.open(tmp_db_path)
    _bootstrap(storage)
    with pytest.raises(ValueError, match="text"):
        storage.add_note(
            session_id="ses_1", anchor_kind="node", anchor_id="node_1",
            kind="narrative", text="", note_id="note_x", now=T1,
        )
