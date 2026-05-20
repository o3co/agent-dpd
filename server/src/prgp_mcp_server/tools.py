"""Business logic for each MCP tool exposed by the PRGP server.

These functions are pure-ish: they take a `Storage`, a `now` timestamp,
and a `new_id` factory, plus the raw `arguments` dict from MCP. The
server module wires them into MCP `@app.call_tool` handlers.
"""

from __future__ import annotations

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
        scope=arguments.get("scope"),
        label=arguments.get("label"),
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
    storage.insert_root(
        root_id=root_id, session_id=session_id, topic=topic, now=now
    )
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

    parent_kind = _classify_parent(
        storage=storage, session_id=session_id, parent_id=parent_id
    )

    node_id = new_id("node")
    storage.insert_node(
        node_id=node_id,
        session_id=session_id,
        node_type=node_type,
        text=text,
        parent_id=parent_id,
        parent_kind=parent_kind,
        now=now,
    )
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


def _required(arguments: dict[str, Any], key: str) -> Any:
    value = arguments.get(key)
    if value is None or value == "":
        raise ValueError(f"missing required argument: {key}")
    return value


def _classify_parent(
    *, storage: Storage, session_id: str, parent_id: str
) -> str:
    with storage.connect() as conn:
        if conn.execute(
            "SELECT 1 FROM roots WHERE session_id = ? AND id = ?",
            (session_id, parent_id),
        ).fetchone():
            return "root"
        if conn.execute(
            "SELECT 1 FROM nodes WHERE session_id = ? AND id = ?",
            (session_id, parent_id),
        ).fetchone():
            return "node"
    raise ValueError(
        f"parent_id {parent_id!r} not found in session {session_id!r}"
    )


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
