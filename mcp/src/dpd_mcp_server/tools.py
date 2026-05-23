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


_VALID_SESSION_MODES = {"entry", "ambient", "idle"}


def start_session(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
    new_id: Callable[[str], str],
) -> dict[str, Any]:
    mode = arguments.get("mode") or "entry"
    if mode not in _VALID_SESSION_MODES:
        raise ValueError(
            f"invalid mode {mode!r}; must be one of {sorted(_VALID_SESSION_MODES)}"
        )
    session_id = new_id("ses")
    storage.insert_session(
        session_id=session_id,
        scope=arguments.get("scope") or None,
        label=arguments.get("label") or None,
        mode=mode,
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
    row = storage.get_root(session_id=session_id, root_id=root_id)
    return {"root": _row_to_dict(row)}


_V3_NODE_TYPES = {"start", "end"}


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
    paired_for = arguments.get("paired_for") or None
    achievement_conditions = arguments.get("achievement_conditions") or None
    provenance = arguments.get("provenance") or "grounded"
    state = arguments.get("state") or "active"

    nid = new_id("node")

    is_v3 = node_type in _V3_NODE_TYPES or paired_for is not None or achievement_conditions is not None
    if is_v3:
        try:
            storage.insert_node_v3(
                node_id=nid,
                session_id=session_id,
                node_type=node_type,
                text=text,
                parent_id=parent_id,
                paired_for=paired_for,
                achievement_conditions=achievement_conditions,
                now=now,
                provenance=provenance,
                state=state,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"cannot add node in session {session_id!r}: {exc}"
            ) from exc
    else:
        try:
            storage.insert_node_under_parent(
                node_id=nid,
                session_id=session_id,
                node_type=node_type,
                text=text,
                parent_id=parent_id,
                now=now,
                provenance=provenance,
                state=state,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"cannot add node in session {session_id!r}: {exc}"
            ) from exc

    row = storage.get_node(session_id=session_id, node_id=nid)
    return {"node": _row_to_dict(row)}


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
    mode_filter = arguments.get("mode_filter") or None
    rows = storage.list_sessions(scope=scope, mode_filter=mode_filter)
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
        # Dangling-id case: focus_node_id is set but the node row is gone.
        # No tool deletes nodes today (revert/cascade-delete not yet implemented),
        # so this branch is currently unreachable. When such a tool lands, decide
        # whether to: (a) clear focus_node_id eagerly on node removal, or (b) keep
        # surfacing focus_node_id via session.focus_node_id while returning null
        # focus_node here so callers can detect the drift.
        focus_node = _row_to_dict(node_row) if node_row is not None else None
    return {
        "session": _row_to_dict(session_row),
        "active_roots": [_row_to_dict(r) for r in roots],
        "focus_node": focus_node,
    }


_VALID_ROOT_LIFECYCLES = {"active", "archived", "deferred"}


def set_focus(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    node_id = arguments.get("node_id") or None
    storage.set_focus(session_id=session_id, node_id=node_id, now=now)
    return {"session_id": session_id, "focus_node_id": node_id}


def set_root_lifecycle(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    root_id = _required(arguments, "root_id")
    lifecycle = _required(arguments, "lifecycle")
    if lifecycle not in _VALID_ROOT_LIFECYCLES:
        raise ValueError(
            f"lifecycle must be one of {sorted(_VALID_ROOT_LIFECYCLES)}, "
            f"got {lifecycle!r}"
        )
    updated = storage.set_root_lifecycle(
        session_id=session_id, root_id=root_id,
        lifecycle=lifecycle, now=now,
    )
    if not updated:
        raise ValueError(
            f"root {root_id!r} not found in session {session_id!r}"
        )
    return {"root_id": root_id, "lifecycle": lifecycle}


def list_open_nodes(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    root_id = arguments.get("root_id") or None
    state = arguments.get("state") or None
    rows = storage.list_open_nodes(session_id=session_id, root_id=root_id, state=state)
    return {"nodes": [_row_to_dict(r) for r in rows]}


def add_edge(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    from_node = _required(arguments, "from_node")
    to_node = _required(arguments, "to_node")
    edge_type = _required(arguments, "type")
    reason = arguments.get("reason") or None
    edge_id = storage.add_edge(
        session_id=session_id,
        from_node=from_node, to_node=to_node,
        edge_type=edge_type, reason=reason, now=now,
    )
    return {"edge_id": edge_id}


def list_edges(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    from_node = arguments.get("from_node") or None
    to_node = arguments.get("to_node") or None
    edge_type = arguments.get("type") or None
    rows = storage.list_edges(
        session_id=session_id, from_node=from_node, to_node=to_node,
        edge_type=edge_type,
    )
    return {"edges": [_row_to_dict(r) for r in rows]}


def list_unblocked_open_nodes(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    session_id = _required(arguments, "session_id")
    root_id = arguments.get("root_id") or None
    blocker_edge_type = arguments.get("blocker_edge_type") or "blocks"
    rows = storage.list_unblocked_open_nodes(
        session_id=session_id, root_id=root_id,
        blocker_edge_type=blocker_edge_type,
    )
    return {"nodes": [_row_to_dict(r) for r in rows]}


def resolve_hypothesis_branch(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
    new_id: Callable[[str], str],
) -> dict[str, Any]:
    """Atomically close a hypothesis branch and record the decision.

    Closes the chosen hypothesis as 'resolved', sibling hypotheses (same
    parent, open) as 'rejected', then inserts decision (+ optional rationale)
    nodes — all in a single transaction. Replaces 5+ separate close_node /
    add_node calls in the typical "pick from N options" closure flow.
    """
    session_id = _required(arguments, "session_id")
    hyp_id = _required(arguments, "hyp_id")
    decision_text = _required(arguments, "decision_text")
    rationale_text = arguments.get("rationale_text") or None

    decision_id = new_id("node")
    rationale_id = new_id("node") if rationale_text is not None else None

    try:
        return storage.resolve_hypothesis_branch(
            session_id=session_id, hyp_id=hyp_id,
            decision_id=decision_id, decision_text=decision_text,
            rationale_id=rationale_id, rationale_text=rationale_text,
            now=now,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"resolve_hypothesis_branch failed in session {session_id!r}: {exc}"
        ) from exc


def resolve_branch(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
    new_id: Callable[[str], str],
) -> dict[str, Any]:
    """Atomically close N sibling nodes with per-node closure_reason and
    optionally insert decision + rationale + derived_from edges.

    See spec docs/dpd-phase-2.7-draft.md §2 for full contract.
    """
    session_id = _required(arguments, "session_id")
    parent_id = _required(arguments, "parent_id")
    parent_kind = _required(arguments, "parent_kind")
    results = arguments.get("results") or []
    decision_text = arguments.get("decision_text") or None
    rationale_text = arguments.get("rationale_text") or None
    derived_from_node_ids = arguments.get("derived_from_node_ids") or None

    for item in results:
        reason = item.get("closure_reason")
        if reason not in _VALID_CLOSURE_REASONS:
            raise ValueError(
                f"closure_reason must be one of "
                f"{sorted(_VALID_CLOSURE_REASONS)}, got {reason!r}"
            )

    decision_id = new_id("node") if decision_text is not None else None
    rationale_id = new_id("node") if rationale_text is not None else None

    try:
        return storage.resolve_branch(
            session_id=session_id,
            parent_id=parent_id,
            parent_kind=parent_kind,
            results=results,
            decision_id=decision_id,
            decision_text=decision_text,
            rationale_id=rationale_id,
            rationale_text=rationale_text,
            derived_from_node_ids=derived_from_node_ids,
            now=now,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"resolve_branch failed in session {session_id!r}: {exc}"
        ) from exc


_MERMAID_MAX_LABEL = 60


def _sanitize_for_mermaid(text: str) -> str:
    """Strip characters that would confuse Mermaid's label/edge syntax."""
    sanitized = (
        text.replace("\\", "/")
        .replace('"', "'")
        .replace("|", "/")
        .replace("\n", " ")
        .replace("\r", " ")
    )
    if len(sanitized) > _MERMAID_MAX_LABEL:
        sanitized = sanitized[: _MERMAID_MAX_LABEL - 1] + "…"
    return sanitized


def export_mermaid(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Render the session (or one root's subtree) as a Mermaid ``graph TD``.

    Output includes:
        - Parent → child arrows for the tree
        - Dotted, labeled arrows for non-tree edges (e.g., derived_from,
          blocks) between rendered nodes
        - classDef styling for closed nodes by closure_reason
    """
    session_id = _required(arguments, "session_id")
    root_id_filter = arguments.get("root_id") or None

    if root_id_filter is not None:
        root_row = storage.get_root(
            session_id=session_id, root_id=root_id_filter,
        )
        if root_row is None:
            raise ValueError(
                f"root {root_id_filter!r} not found in session {session_id!r}"
            )
        roots = [root_row]
    else:
        roots = storage.list_active_roots(session_id=session_id)

    lines: list[str] = ["graph TD"]
    rendered_ids: set[str] = set()
    class_assignments: list[str] = []

    for root in roots:
        root_id = root["id"]
        rendered_ids.add(root_id)
        topic = _sanitize_for_mermaid(root["topic"])
        lines.append(f'    {root_id}["root: {topic}"]')

        subtree = storage.walk_subtree(session_id=session_id, root_id=root_id)
        for node in subtree:
            nid = node["id"]
            rendered_ids.add(nid)
            ntype = node["type"]
            text = _sanitize_for_mermaid(node["text"])
            lines.append(f'    {nid}["{ntype}: {text}"]')
            lines.append(f"    {node['parent_id']} --> {nid}")
            if node["status"] == "closed":
                reason = node["closure_reason"] or "resolved"
                class_assignments.append(f"    class {nid} closed_{reason}")

    # Non-tree edges (only between rendered nodes)
    all_edges = storage.list_edges(session_id=session_id)
    for edge in all_edges:
        if edge["from_node"] in rendered_ids and edge["to_node"] in rendered_ids:
            etype = _sanitize_for_mermaid(edge["type"])
            lines.append(
                f"    {edge['from_node']} -.{etype}.-> {edge['to_node']}"
            )

    lines.extend(class_assignments)
    lines.extend([
        "    classDef closed_resolved fill:#a3d977,stroke:#5a8c30",
        "    classDef closed_rejected fill:#f5a3a3,stroke:#8c3030",
        "    classDef closed_invalidated fill:#cccccc,stroke:#666666",
    ])

    return {"mermaid": "\n".join(lines)}


def _node_to_yaml_dict(node: Any, children_by_parent: dict[str, list[Any]]) -> dict[str, Any]:
    """Recursively shape a node into the nested children form for YAML export."""
    out: dict[str, Any] = {
        "id": node["id"],
        "type": node["type"],
        "text": node["text"],
        "status": node["status"],
    }
    if node["closure_reason"] is not None:
        out["closure_reason"] = node["closure_reason"]
    child_rows = children_by_parent.get(node["id"], [])
    if child_rows:
        out["children"] = [
            _node_to_yaml_dict(c, children_by_parent) for c in child_rows
        ]
    return out


def export_yaml(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Render session + tree + edges as JSON-compatible YAML.

    Output is produced via ``json.dumps`` and is parseable by most YAML
    readers (JSON is a strict subset of YAML 1.2; some YAML 1.1 parsers
    handle a few edge cases differently, but the data we emit avoids them).
    Caller can ``json.loads`` the output back into a dict for round-trip.
    """
    import json as _json

    session_id = _required(arguments, "session_id")
    root_id_filter = arguments.get("root_id") or None

    session_row = storage.get_session(session_id=session_id)
    if session_row is None:
        raise ValueError(f"session {session_id!r} not found")

    if root_id_filter is not None:
        root_row = storage.get_root(
            session_id=session_id, root_id=root_id_filter,
        )
        if root_row is None:
            raise ValueError(
                f"root {root_id_filter!r} not found in session {session_id!r}"
            )
        roots = [root_row]
    else:
        roots = storage.list_active_roots(session_id=session_id)

    # Build children index keyed by parent_id (works for both root and node parents).
    rendered_root_ids: set[str] = set()
    children_by_parent: dict[str, list[Any]] = {}
    rendered_node_ids: set[str] = set()
    for root in roots:
        rendered_root_ids.add(root["id"])
        subtree = storage.walk_subtree(session_id=session_id, root_id=root["id"])
        for n in subtree:
            rendered_node_ids.add(n["id"])
            children_by_parent.setdefault(n["parent_id"], []).append(n)

    root_dicts: list[dict[str, Any]] = []
    for root in roots:
        root_dicts.append({
            "id": root["id"],
            "topic": root["topic"],
            "lifecycle": root["lifecycle"],
            "children": [
                _node_to_yaml_dict(c, children_by_parent)
                for c in children_by_parent.get(root["id"], [])
            ],
        })

    # Edges among rendered endpoints only (roots or nodes in scope).
    visible = rendered_root_ids | rendered_node_ids
    edge_dicts: list[dict[str, Any]] = []
    for edge in storage.list_edges(session_id=session_id):
        if edge["from_node"] in visible and edge["to_node"] in visible:
            edge_dicts.append({
                "id": edge["id"],
                "from": edge["from_node"],
                "to": edge["to_node"],
                "type": edge["type"],
                "reason": edge["reason"],
            })

    payload = {
        "session": {
            "id": session_row["id"],
            "scope": session_row["scope"],
            "label": session_row["label"],
            "started_at": session_row["started_at"],
            "updated_at": session_row["updated_at"],
            "focus_node_id": session_row["focus_node_id"],
        },
        "roots": root_dicts,
        "edges": edge_dicts,
    }
    return {"yaml": _json.dumps(payload, indent=2, ensure_ascii=False)}


def _compute_pool_text_hash(text: str) -> str:
    """SHA-256 of ``lower(strip(text))`` truncated to 16 hex chars.

    Canonical text hash for Pool item identity (spec v0.3.1 §4.6.1).
    Used to suppress re-detection of the same observation after a user
    rejects a proposed update.
    """
    import hashlib
    canonical = text.strip().lower().encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def pool_add(
    storage: Storage,
    *,
    scope: str | None,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Append a raw thought to the scope's Pool.

    Auto-creates scope_root if missing for this scope. Populates
    ``text_hash`` per spec §4.6.1 so reject-suppression matching can compare
    against the canonical-form hash without re-computing it.
    """
    from .ids import pool_id as _pool_id
    text = arguments["text"]
    tags = arguments.get("tags")
    origin_session_id = arguments.get("origin_session_id")
    origin_turn = arguments.get("origin_turn")
    root = storage.get_or_create_scope_root(scope=scope, now=now)
    new_id_ = _pool_id()
    text_hash = _compute_pool_text_hash(text)
    storage.insert_pool_item(
        pool_id=new_id_, scope_root_id=root["id"],
        text=text, origin_session_id=origin_session_id,
        origin_turn=origin_turn, tags=tags,
        text_hash=text_hash, now=now,
    )
    items = storage.list_pool_items(scope_root_id=root["id"], active_only=False)
    item = next((dict(r) for r in items if r["id"] == new_id_), None)
    return {"pool_item": item}


def pool_list(
    storage: Storage,
    *,
    scope: str | None,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """List Pool items for this scope. Auto-creates scope_root if missing
    (so a fresh dogfood session can call pool_list before pool_add)."""
    include_rejected = arguments.get("include_rejected", False)
    rejected_only = arguments.get("rejected_only", False)
    # active_only defaults True unless overridden by include_rejected or rejected_only.
    active_only_default = not (include_rejected or rejected_only)
    active_only = arguments.get("active_only", active_only_default)
    root = storage.get_or_create_scope_root(scope=scope, now=now)
    items = storage.list_pool_items(
        scope_root_id=root["id"],
        active_only=active_only,
        include_rejected=include_rejected,
        rejected_only=rejected_only,
    )
    return {"items": [dict(r) for r in items]}


def pool_elevate(
    storage: Storage,
    *,
    scope: str | None,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Move a Pool item into the DPD graph as a node under target_end_node_id's subgraph.

    Requires target_end_node_id (per spec §6.1 S5 fix — End-less elevation is
    not allowed). Creates a child node under the End and marks the Pool item elevated.
    """
    from .ids import node_id as _node_id
    pool_id_ = arguments["pool_id"]
    target_end_node_id = arguments["target_end_node_id"]
    node_type = arguments["type"]
    session_id = arguments["session_id"]
    text_override = arguments.get("text")

    end_node = storage.get_node(session_id=session_id, node_id=target_end_node_id)
    if end_node is None or end_node["type"] != "end":
        raise ValueError(
            f"target_end_node_id {target_end_node_id!r} is not an end node "
            f"in session {session_id!r}"
        )

    # Validate End node is still active — cannot elevate into a closed End.
    if end_node["state"] != "active":
        raise ValueError(
            f"target_end_node_id {target_end_node_id!r} is in state "
            f"{end_node['state']!r} — only active End nodes accept elevations"
        )

    # Validate session scope matches pool scope to prevent cross-scope contamination.
    session_row = storage.get_session(session_id=session_id)
    if session_row is None:
        raise ValueError(f"session_id {session_id!r} not found")
    session_scope: str | None = session_row["scope"] if session_row["scope"] else None
    arg_scope: str | None = scope if scope else None
    if session_scope != arg_scope:
        raise ValueError(
            f"session scope {session_scope!r} does not match pool scope {arg_scope!r}"
        )

    # Resolve text: use override or fall back to Pool item text.
    pool_root = storage.get_or_create_scope_root(scope=scope, now=now)
    items = storage.list_pool_items(
        scope_root_id=pool_root["id"], active_only=False
    )
    pool_item = next((dict(r) for r in items if r["id"] == pool_id_), None)
    if pool_item is None:
        raise ValueError(f"pool_id {pool_id_!r} not found in scope {scope!r}")

    # Validate pool item is still active — cannot re-elevate or elevate dropped items.
    if pool_item["elevated_to"] is not None:
        raise ValueError(
            f"pool_id {pool_id_!r} is already elevated "
            f"(to {pool_item['elevated_to']!r})"
        )
    if pool_item["dropped_at"] is not None:
        raise ValueError(
            f"pool_id {pool_id_!r} is dropped — cannot elevate"
        )

    final_text = text_override or pool_item["text"]

    new_id_ = _node_id()
    storage.insert_node_v3(
        node_id=new_id_, session_id=session_id,
        node_type=node_type, text=final_text,
        parent_id=target_end_node_id,
        paired_for=None, achievement_conditions=None,
        now=now,
    )
    storage.mark_pool_elevated(pool_id=pool_id_, elevated_to=new_id_, now=now)
    elevated = storage.get_node(session_id=session_id, node_id=new_id_)
    return {"elevated_node": dict(elevated), "pool_id": pool_id_}


def pool_drop(
    storage: Storage,
    *,
    scope: str | None,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Mark a Pool item as dropped (= no longer active for elevation)."""
    pool_id_ = arguments["pool_id"]
    reason = arguments.get("reason")
    storage.drop_pool_item(pool_id=pool_id_, reason=reason, now=now)
    return {"pool_id": pool_id_, "dropped_at": now}


def pool_reject(
    storage: Storage,
    *,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Soft-suppress a Pool item by marking it rejected.

    Orthogonal to pool_drop: rejection is a soft suppression signal (signals
    Claude to auto-suppress re-detection), drop is hard removal.  Both can
    coexist on the same item (reject first, then drop later).
    """
    pool_id = arguments["pool_id"]
    reason = arguments.get("reason")
    updated = storage.reject_pool_item(pool_id=pool_id, reason=reason, now=now)
    return {"pool_item": updated}


def mark_reached(
    storage: Storage,
    *,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Signal End node achievement; transitions subgraph to closed state."""
    session_id = arguments["session_id"]
    end_node_id = arguments["end_node_id"]
    storage.mark_reached(session_id=session_id, end_node_id=end_node_id, now=now)
    end = storage.get_node(session_id=session_id, node_id=end_node_id)
    return {"end_node": dict(end)}


def dump_persist(
    storage: Storage,
    *,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Transition closed subgraph to deletable; record optional dump destination."""
    session_id = arguments["session_id"]
    start_node_id = arguments["start_node_id"]
    destination = arguments.get("destination")
    storage.dump_persist_subgraph(
        session_id=session_id, start_node_id=start_node_id,
        destination=destination, now=now,
    )
    return {"start_node_id": start_node_id, "destination": destination}


def delete(
    storage: Storage,
    *,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Physically delete a subgraph (requires state=deletable)."""
    session_id = arguments["session_id"]
    start_node_id = arguments["start_node_id"]
    start = storage.get_node(session_id=session_id, node_id=start_node_id)
    if start is None or start["state"] != "deletable":
        raise ValueError(
            f"subgraph start {start_node_id!r} is not in deletable state"
        )
    storage.delete_subgraph(
        session_id=session_id, start_node_id=start_node_id, now=now,
    )
    return {"start_node_id": start_node_id, "deleted_at": now}


def force_delete(
    storage: Storage,
    *,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Single-node force delete bypassing state precondition (emergency/cleanup only)."""
    session_id = arguments["session_id"]
    node_id_ = arguments["node_id"]
    storage.force_delete_node(
        session_id=session_id, node_id=node_id_, now=now,
    )
    return {"node_id": node_id_, "force_deleted_at": now}


def bulk_import_subgraph(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Atomically import a multi-node + edge subgraph under an existing root.

    Validates all FK refs pre-flight (root_id, parent_id, paired_for, edge
    endpoints), performs topological sort + cycle detection, then inserts
    everything in a single transaction. Full rollback on any failure.
    """
    session_id = _required(arguments, "session_id")
    root_id = _required(arguments, "root_id")
    nodes = arguments.get("nodes") or []
    edges = arguments.get("edges") or []
    provenance = arguments.get("provenance") or "imported"
    state = arguments.get("state") or "archived"

    return storage.bulk_import_subgraph(
        session_id=session_id,
        root_id=root_id,
        nodes=nodes,
        edges=edges,
        provenance=provenance,
        state=state,
        now=now,
    )


def set_session_mode(
    *,
    storage: Storage,
    arguments: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Transition session mode per the v0.3.1 lifecycle table (§9.1.1).

    Validates the transition against the allowed table and raises ValueError
    for disallowed moves (e.g. ambient → entry, idle → ambient).
    Self-transitions are idempotent.
    """
    session_id = _required(arguments, "session_id")
    mode = _required(arguments, "mode")
    updated_session = storage.set_session_mode(
        session_id=session_id, mode=mode, now=now
    )
    return {"session": updated_session}


def find_similar(
    *,
    storage: Storage,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """List subgraphs whose FTS document matches the query."""
    query = _required(arguments, "query")
    scope = arguments.get("scope") or None
    top_k = int(arguments.get("top_k") or 5)
    include_open = bool(arguments.get("include_open") or False)
    results = storage.find_similar(
        query=query, scope=scope, top_k=top_k, include_open=include_open
    )
    return {"results": results}


def _required(arguments: dict[str, Any], key: str) -> Any:
    value = arguments.get(key)
    if value is None or value == "":
        raise ValueError(f"missing required argument: {key}")
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
