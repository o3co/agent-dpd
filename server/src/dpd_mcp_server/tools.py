"""Business logic for each MCP tool exposed by the DPD server.

These functions are pure-ish: they take a `Storage`, a `now` timestamp,
and a `new_id` factory, plus the raw `arguments` dict from MCP. The
server module wires them into MCP `@app.call_tool` handlers.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from .storage import Storage


def start_session(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
    new_id: Callable[[str], str],
) -> dict[str, Any]:
    session_id = new_id("ses")
    storage.insert_session(
        session_id=session_id,
        scope=arguments.get("scope") or None,
        label=arguments.get("label") or None,
        now=now,
    )
    return {"session_id": session_id}


def spawn_root(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
    new_id: Callable[[str], str],
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    topic = _required(arguments, "topic")
    # `reason` is recorded in audit log in a later phase; ignored here.
    root_id = new_id("root")
    try:
        storage.insert_root(
            root_id=root_id, session_id=session_id, topic=topic, now=now
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"cannot spawn root in session {session_id!r}: {exc}"
        ) from exc
    return {"root_id": root_id}


def add_node(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
    new_id: Callable[[str], str],
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    parent_id = _required(arguments, "parent_id")
    node_type = _required(arguments, "type")
    text = _required(arguments, "text")

    node_id = new_id("node")
    try:
        storage.insert_node_under_parent(
            node_id=node_id,
            session_id=session_id,
            node_type=node_type,
            text=text,
            parent_id=parent_id,
            now=now,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"cannot add node in session {session_id!r}: {exc}"
        ) from exc
    return {"node_id": node_id}


_VALID_CLOSURE_REASONS = {"resolved", "rejected", "invalidated"}


def close_node(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    node_id = _required(arguments, "node_id")
    reason = _required(arguments, "closure_reason")
    if reason not in _VALID_CLOSURE_REASONS:
        raise ValueError(
            f"closure_reason must be one of "
            f"{sorted(_VALID_CLOSURE_REASONS)}, got {reason!r}"
        )
    closed = storage.close_node(
        session_id=session_id,
        node_id=node_id,
        closure_reason=reason,
        now=now,
    )
    if not closed:
        raise ValueError(
            f"node {node_id!r} not found in session {session_id!r}"
        )
    return {"node_id": node_id, "status": "closed", "closure_reason": reason}


def get_node(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    node_id = _required(arguments, "node_id")
    row = storage.get_node(session_id=session_id, node_id=node_id)
    return {"node": _row_to_dict(row) if row is not None else None}


def walk_subtree(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    root_id = _required(arguments, "root_id")
    rows = storage.walk_subtree(session_id=session_id, root_id=root_id)
    return {"nodes": [_row_to_dict(r) for r in rows]}


def list_active_roots(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    rows = storage.list_active_roots(session_id=session_id)
    return {"roots": [_row_to_dict(r) for r in rows]}


def list_sessions(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scope = arguments.get("scope") or None
    rows = storage.list_sessions(scope=scope)
    return {"sessions": [_row_to_dict(r) for r in rows]}


def get_session_state(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Return session row + active roots + focus_node (resolved).

    Composes the three storage primitives needed by the skill startup
    brief (§8.3 step 7). focus_node is null when focus_node_id is unset.
    """
    session_id = _required(arguments, "session_id")
    session_row = storage.get_session(session_id=session_id)
    if session_row is None:
        raise ValueError(f"session {session_id!r} not found")
    roots = storage.list_active_roots(session_id=session_id)
    focus_node = None
    focus_id = session_row["focus_node_id"]
    if focus_id is not None:
        node_row = storage.get_node(session_id=session_id, node_id=focus_id)
        focus_node = _row_to_dict(node_row) if node_row is not None else None
    return {
        "session": _row_to_dict(session_row),
        "active_roots": [_row_to_dict(r) for r in roots],
        "focus_node": focus_node,
    }


def _required(arguments: dict[str, Any], key: str) -> Any:
    value = arguments.get(key)
    if value is None or value == "":
        raise ValueError(f"missing required argument: {key}")
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
