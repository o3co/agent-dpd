"""MCP server wiring: register the 31 DPD tools over stdio."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import tools
from .ids import new_id
from .scope import AgentScopeResolutionError, resolve_agent_scope
from .storage import Storage
from .tool_aliases import LEGACY_ALIASES

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("dpd-server")

app = Server("dpd-mcp-server")

_storages: dict[str, Storage] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _data_dir() -> Path:
    override = os.environ.get("DPD_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "dpd-server" / "data"


async def _get_storage(arguments: dict) -> Storage:
    """Resolve agent scope and open (lazily) the sqlite db.

    If ``arguments`` contains a non-empty ``agent_scope`` key, use it
    directly as the encoded scope name, bypassing ``roots/list``.
    Otherwise resolve via the MCP roots capability.  Results are cached
    per encoded scope name.
    """
    explicit = arguments.get("agent_scope")
    if explicit:
        encoded = explicit
    else:
        session = app.request_context.session
        if (
            session.client_params is None
            or session.client_params.capabilities.roots is None
        ):
            raise AgentScopeResolutionError(
                "client did not advertise roots capability"
            )

        roots_result = await session.list_roots()
        roots = [str(r.uri) for r in roots_result.roots]
        encoded = resolve_agent_scope(roots)
        log.info("resolved agent scope: encoded=%s roots=%s", encoded, roots)

    if encoded not in _storages:
        db_path = _data_dir() / encoded / "graph.sqlite"
        log.info("opening sqlite at %s", db_path)
        _storages[encoded] = Storage.open(str(db_path))
    return _storages[encoded]


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    tools = [
        types.Tool(
            name="start_session",
            title="Start session",
            description="Begin a new DPD session. Returns session_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {"type": ["string", "null"],
                              "description": "Sub-scope identifier (optional)."},
                    "label": {"type": ["string", "null"],
                              "description": "Human-readable label (optional)."},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["entry", "ambient", "idle"],
                        "default": "entry",
                        "description": "Session mode (default 'entry'). Transitions via set_session_mode.",
                    },
                },
            },
        ),
        types.Tool(
            name="spawn_root",
            title="Spawn root",
            description="Create a new root topic under the current session.",
            inputSchema={
                "type": "object",
                "required": ["session_id", "topic"],
                "properties": {
                    "session_id": {"type": "string"},
                    "topic": {"type": "string"},
                    "reason": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="add_node",
            title="Add node",
            description=(
                "Add a child node under a root or node. "
                "v3 fields: type 'start'/'end' and optional paired_for / "
                "achievement_conditions are supported. For 'end' nodes, "
                "paired_for (the Start node id) is required. "
                "Backward-compatible: omitting v3 fields follows v2 behavior."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "parent_id", "type", "text"],
                "properties": {
                    "session_id": {"type": "string"},
                    "parent_id": {"type": "string"},
                    "type": {
                        "type": "string",
                        "description": (
                            "Node type. v2 types: question, answer, hypothesis, "
                            "evidence, decision, rationale. "
                            "v3 additions: start, end."
                        ),
                    },
                    "text": {"type": "string"},
                    "paired_for": {
                        "type": ["string", "null"],
                        "description": "For 'end' nodes: the id of the paired 'start' node.",
                    },
                    "achievement_conditions": {
                        "type": ["string", "null"],
                        "description": "Optional textual description of the conditions that mark this subgraph achieved.",
                    },
                    "provenance": {
                        "type": "string",
                        "enum": ["grounded", "inferred", "imported", "manual"],
                        "description": "Node origin: grounded (conversation), inferred (claude guess), imported (external doc), manual (/dpd-edit)",
                    },
                    "state": {
                        "type": "string",
                        "enum": ["active", "archived", "closed", "deletable", "gone"],
                        "description": "Node state (default 'active'). Used by /dpd-import to start archived.",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="close_node",
            title="Close node",
            description="Mark a node as closed with a closure reason.",
            inputSchema={
                "type": "object",
                "required": ["session_id", "node_id", "closure_reason"],
                "properties": {
                    "session_id": {"type": "string"},
                    "node_id": {"type": "string"},
                    "closure_reason": {
                        "type": "string",
                        "enum": ["resolved", "rejected", "invalidated"],
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="get_node",
            title="Get node",
            description="Fetch a node by id within a session.",
            inputSchema={
                "type": "object",
                "required": ["session_id", "node_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "node_id": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="walk_subtree",
            title="Walk subtree",
            description=(
                "Return all descendants of a root, depth-first by creation time. "
                "Returns empty list if the session or root does not exist."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "root_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "root_id": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="list_active_roots",
            title="List active roots",
            description=(
                "List all roots with lifecycle=active in this session. "
                "Returns empty list if the session does not exist or has no active roots."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="list_sessions",
            title="List sessions",
            description=(
                "List sessions for the given sub-scope, most-recently-updated first. "
                "Omit (or pass empty/null) ``scope`` to list top-level sessions only "
                "(rows with NULL scope). Used by the skill startup flow to offer "
                "resume vs new-session UX. "
                "Optionally filter by session mode via ``mode_filter``."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": ["string", "null"],
                        "description": "Sub-scope to filter by. Omit or pass null/empty for top-level sessions only.",
                    },
                    "mode_filter": {
                        "oneOf": [
                            {"type": "string", "enum": ["entry", "ambient", "idle"]},
                            {
                                "type": "array",
                                "items": {"type": "string", "enum": ["entry", "ambient", "idle"]},
                            },
                            {"type": "null"},
                        ],
                        "description": "Filter sessions by mode. Single string or list of strings (entry/ambient/idle). Omit or pass null to return all sessions regardless of mode.",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="get_session_state",
            title="Get session state",
            description=(
                "Return a session's row plus its active roots and resolved focus_node "
                "(null when focus_node_id is unset). Used by the skill startup flow "
                "to brief the AI on resume."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="set_focus",
            title="Set focus node",
            description=(
                "Set or clear sessions.focus_node_id. Pass node_id=null (or omit) "
                "to clear focus. Validates the id exists in the session — accepts "
                "both node ids and root ids as the focus target."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "node_id": {
                        "type": ["string", "null"],
                        "description": (
                            "Target node id or root id, or null to clear focus. "
                            "Both nodes and roots are valid focus targets."
                        ),
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="set_root_lifecycle",
            title="Set root lifecycle",
            description=(
                "Transition a root's lifecycle to one of 'active', 'archived', "
                "'deferred'. Used to retire roots whose discussion has wrapped up "
                "without removing their history."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "root_id", "lifecycle"],
                "properties": {
                    "session_id": {"type": "string"},
                    "root_id": {"type": "string"},
                    "lifecycle": {
                        "type": "string",
                        "enum": ["active", "archived", "deferred"],
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="list_open_nodes",
            title="List open nodes",
            description=(
                "Return open nodes in the session, optionally restricted to one root's "
                "subtree and/or filtered by the state column. Powers next_focus "
                "selection (deepest-within after recency-ranked root)."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "root_id": {
                        "type": ["string", "null"],
                        "description": "If given, restrict to this root's subtree.",
                    },
                    "state": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional filter on the state column "
                            "(e.g. 'active', 'closed', 'deletable'). "
                            "Omit to return all open nodes regardless of state."
                        ),
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="add_edge",
            title="Add edge",
            description=(
                "Insert an edge between two nodes. Edge type is restricted to "
                "the canonical vocabulary (derived_from, requires, blocks, "
                "supports, contradicts, contributes_to, supersedes, qualifies, "
                "invalidates). Self-loops (from_node == to_node) are rejected. "
                "Use delete_edge to remove a mis-typed or stale edge."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "from_node", "to_node", "type"],
                "properties": {
                    "session_id": {"type": "string"},
                    "from_node": {"type": "string"},
                    "to_node": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "derived_from", "requires", "blocks", "supports",
                            "contradicts", "contributes_to", "supersedes",
                            "qualifies", "invalidates",
                        ],
                    },
                    "reason": {"type": ["string", "null"]},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="delete_edge",
            title="Delete edge",
            description=(
                "Delete a single edge by id within the session. Use to clean "
                "up edges added in error (typo, mis-direction). Raises if "
                "edge_id is not found in the session."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "edge_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "edge_id": {"type": "integer"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="list_edges",
            title="List edges",
            description=(
                "List edges in the session, optionally filtered by from_node, "
                "to_node, and/or type. When multiple filters are given they "
                "are AND'd. Returns empty list if the session has no matching "
                "edges."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "from_node": {"type": ["string", "null"]},
                    "to_node": {"type": ["string", "null"]},
                    "type": {
                        "type": ["string", "null"],
                        "description": "Filter by edge type (e.g., 'derived_from', 'contradicts').",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="list_unblocked_open_nodes",
            title="List unblocked open nodes",
            description=(
                "Return open nodes that are NOT blocked by any open node via the "
                "given edge type (default 'blocks'; directional convention: "
                "edge.from blocks edge.to). Useful for next_focus selection when "
                "explicit dependency edges have been declared. Optionally "
                "restrict to one root's subtree."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "root_id": {
                        "type": ["string", "null"],
                        "description": "If given, restrict to this root's subtree.",
                    },
                    "blocker_edge_type": {
                        "type": ["string", "null"],
                        "description": "Edge type that counts as a blocker. Defaults to 'blocks'.",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="export_mermaid",
            title="Export Mermaid graph",
            description=(
                "Render the session (or one root's subtree) as a Mermaid "
                "`graph TD` text block. Closed nodes get a class assignment so "
                "they can be styled distinctly (by closure_reason). Non-tree "
                "edges appear as dotted, labeled arrows."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "root_id": {
                        "type": ["string", "null"],
                        "description": "If given, restrict export to this root's subtree.",
                    },
                    "max_label_chars": {
                        "type": ["integer", "null"],
                        "default": 60,
                        "description": (
                            "Maximum node-label length (inclusive of trailing "
                            "ellipsis). Pass null to disable truncation — use "
                            "for README/docs embeds where full labels matter. "
                            "Default 60 keeps large graphs visually balanced."
                        ),
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="export_yaml",
            title="Export YAML (JSON-compatible)",
            description=(
                "Render session + tree + edges as JSON-formatted YAML (JSON is "
                "a strict subset of YAML). Round-trippable via json.loads. "
                "Useful for spec review, archival, or diffing sessions."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "root_id": {
                        "type": ["string", "null"],
                        "description": "If given, restrict export to this root's subtree.",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="resolve_branch",
            title="Resolve branch (generic)",
            description=(
                "Atomically close N sibling nodes with per-node "
                "closure_reason, optionally inserting a decision, rationale, "
                "and derived_from edges. Generic counterpart to "
                "resolve_hypothesis_branch (which is locked to "
                "select-1-of-N). See spec Phase 2.7 §2 for full contract."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "parent_id", "parent_kind", "results"],
                "properties": {
                    "session_id": {"type": "string"},
                    "parent_id": {"type": "string"},
                    "parent_kind": {"type": "string", "enum": ["root", "node"]},
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["node_id", "closure_reason"],
                            "properties": {
                                "node_id": {"type": "string"},
                                "closure_reason": {
                                    "type": "string",
                                    "enum": ["resolved", "rejected", "invalidated"],
                                },
                            },
                        },
                    },
                    "decision_text": {"type": ["string", "null"]},
                    "rationale_text": {"type": ["string", "null"]},
                    "derived_from_node_ids": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="pool_add",
            title="Pool: add item",
            description="Append a raw thought to the scope's Pool (staging area before DPD assignment).",
            inputSchema={
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                    "scope": {
                        "type": ["string", "null"],
                        "description": "Sub-scope identifier for the Pool (optional; defaults to top-level).",
                    },
                    "tags": {"type": "string", "description": "Comma-separated free-form tags."},
                    "origin_session_id": {"type": "string"},
                    "origin_turn": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="pool_list",
            title="Pool: list items",
            description="List Pool items for the current scope.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": ["string", "null"],
                        "description": "Sub-scope identifier for the Pool (optional; defaults to top-level).",
                    },
                    "active_only": {
                        "type": "boolean",
                        "default": True,
                        "description": "Exclude rejected and dropped items (default). Mutually exclusive with rejected_only.",
                    },
                    "include_rejected": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include rejected items in results (still excludes dropped items).",
                    },
                    "rejected_only": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return only rejected items. Mutually exclusive with active_only.",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="pool_elevate",
            title="Pool: elevate item",
            description="Elevate a Pool item into the DPD graph as a child of the target End node.",
            inputSchema={
                "type": "object",
                "required": ["pool_id", "target_end_node_id", "type", "session_id"],
                "properties": {
                    "pool_id": {"type": "string"},
                    "target_end_node_id": {"type": "string"},
                    "type": {"type": "string"},
                    "session_id": {"type": "string"},
                    "scope": {
                        "type": ["string", "null"],
                        "description": "Sub-scope identifier for the Pool (optional; defaults to top-level).",
                    },
                    "text": {"type": "string", "description": "Optional override of Pool item text."},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="pool_drop",
            title="Pool: drop item",
            description="Mark a Pool item as dropped (= no longer active for elevation).",
            inputSchema={
                "type": "object",
                "required": ["pool_id"],
                "properties": {
                    "pool_id": {"type": "string"},
                    "scope": {
                        "type": ["string", "null"],
                        "description": "Sub-scope identifier for the Pool (optional; defaults to top-level).",
                    },
                    "reason": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="pool_reject",
            title="Reject pool item (soft suppress)",
            description=(
                "Mark a pool item as rejected. Orthogonal to pool_drop: rejection is "
                "soft suppression (signal for Claude to auto-suppress re-detection), "
                "drop is hard removal. Both can coexist on the same item."
            ),
            inputSchema={
                "type": "object",
                "required": ["pool_id"],
                "properties": {
                    "pool_id": {"type": "string", "description": "ID of the pool item"},
                    "reason": {
                        "type": ["string", "null"],
                        "description": "Optional reason (e.g., user's reject statement)",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="mark_reached",
            title="Mark End node reached",
            description="Signal End node achievement → transitions subgraph to closed state.",
            inputSchema={
                "type": "object",
                "required": ["session_id", "end_node_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "end_node_id": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="dump_persist",
            title="Dump persist subgraph",
            description="Transition closed subgraph to deletable; record optional dump destination path.",
            inputSchema={
                "type": "object",
                "required": ["session_id", "start_node_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "start_node_id": {"type": "string"},
                    "destination": {
                        "type": ["string", "null"],
                        "description": "Optional file path where the subgraph was (or will be) dumped.",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="delete",
            title="Delete subgraph",
            description="Physically delete a subgraph (requires state=deletable).",
            inputSchema={
                "type": "object",
                "required": ["session_id", "start_node_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "start_node_id": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="force_delete",
            title="Force delete node",
            description="Single-node force delete bypassing state precondition (emergency / cleanup only).",
            inputSchema={
                "type": "object",
                "required": ["session_id", "node_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "node_id": {"type": "string"},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="resolve_hypothesis_branch",
            title="Accept hypothesis (atomic decision)",
            description=(
                "Atomic closure accelerator: closes the chosen hypothesis as resolved, "
                "rejects open sibling hypotheses (same parent), inserts a 'decision' "
                "node under the same parent, optionally inserts a 'rationale' under "
                "the decision — all in a single transaction. Replaces 5+ separate "
                "close_node/add_node calls in the typical N-options closure flow."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "hyp_id", "decision_text"],
                "properties": {
                    "session_id": {"type": "string"},
                    "hyp_id": {"type": "string"},
                    "decision_text": {"type": "string"},
                    "rationale_text": {
                        "type": ["string", "null"],
                        "description": "Optional. If given, a rationale node is inserted under the decision.",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="set_session_mode",
            title="Set session mode",
            description=(
                "Transition session mode per the v0.3.1 lifecycle: "
                "entry → ambient → idle → entry. "
                "Validates the transition against the allowed table (§9.1.1). "
                "Raises if the transition is disallowed (e.g. ambient → entry, "
                "idle → ambient). Self-transitions are idempotent."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "mode"],
                "properties": {
                    "session_id": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["entry", "ambient", "idle"],
                        "description": "Target mode. Must be entry, ambient, or idle.",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="bulk_import_subgraph",
            title="Bulk import subgraph",
            description=(
                "Atomically import a multi-node + edge subgraph under an existing root. "
                "Used by /dpd-import to construct a hypothetical archived subgraph from "
                "external prose/spec/graph. All FK refs validated pre-flight; full rollback "
                "on any failure."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "root_id", "nodes"],
                "properties": {
                    "session_id": {"type": "string"},
                    "root_id": {"type": "string"},
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "type", "text", "parent_id", "parent_kind"],
                            "properties": {
                                "id": {"type": "string"},
                                "type": {"type": "string"},
                                "text": {"type": "string"},
                                "parent_id": {"type": ["string", "null"]},
                                "parent_kind": {"type": "string", "enum": ["node", "root"]},
                                "paired_for": {"type": ["string", "null"]},
                                "achievement_conditions": {"type": ["string", "null"]},
                            },
                        },
                    },
                    "edges": {
                        "type": "array",
                        "default": [],
                        "items": {
                            "type": "object",
                            "required": ["from", "to", "type"],
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "type": {"type": "string"},
                                "reason": {"type": ["string", "null"]},
                            },
                        },
                    },
                    "provenance": {
                        "type": "string",
                        "enum": ["grounded", "inferred", "imported", "manual"],
                        "default": "imported",
                    },
                    "state": {
                        "type": "string",
                        "enum": ["active", "archived", "closed", "deletable", "gone"],
                        "default": "archived",
                    },
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
            },
        ),
        types.Tool(
            name="find_similar",
            title="Find similar subgraphs",
            description=(
                "Retrieve closed/archived subgraphs whose FTS5 index matches "
                "the query. Default state filter: closed+archived. "
                "include_open=True also runs a dynamic LIKE scan over active "
                "subgraphs. v0.3.2."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Literal phrase to match (lowercased, ≥3 chars)."},
                    "scope": {"type": ["string", "null"],
                              "description": "Sub-scope filter (None = all sub-scopes in agent_scope)."},
                    "top_k": {"type": "integer", "default": 5,
                              "description": "Max number of subgraph summaries to return."},
                    "include_open": {"type": "boolean", "default": False,
                                     "description": "If True, also run dynamic LIKE scan over active subgraphs."},
                    "agent_scope": {
                        "type": ["string", "null"],
                        "description": "Optional override for the agent scope encoded directory name. Bypasses MCP roots/list.",
                    },
                },
                "required": ["query"],
            },
        ),
    ]
    by_name = {t.name: t for t in tools}
    for old, new in LEGACY_ALIASES.items():
        new_tool = by_name.get(new)
        if new_tool is None:
            continue  # safeguard: alias target doesn't exist
        tools.append(types.Tool(
            name=old,
            title=new_tool.title,
            description=f"[DEPRECATED: use '{new}' instead] {new_tool.description}",
            inputSchema=new_tool.inputSchema,
        ))
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name in LEGACY_ALIASES:
        new_name = LEGACY_ALIASES[name]
        log.warning(
            "Tool %r is deprecated, use %r instead", name, new_name
        )
        name = new_name
    storage = await _get_storage(arguments)
    now = _now_iso()
    # Strip the routing-only control argument before passing to tool functions.
    tool_args = {k: v for k, v in arguments.items() if k != "agent_scope"}

    if name == "start_session":
        return tools.start_session(
            storage=storage, arguments=tool_args, now=now, new_id=new_id
        )
    if name == "spawn_root":
        return tools.spawn_root(
            storage=storage, arguments=tool_args, now=now, new_id=new_id
        )
    if name == "add_node":
        return tools.add_node(
            storage=storage, arguments=tool_args, now=now, new_id=new_id
        )
    if name == "close_node":
        return tools.close_node(
            storage=storage, arguments=tool_args, now=now
        )
    if name == "get_node":
        return tools.get_node(storage=storage, arguments=tool_args)
    if name == "walk_subtree":
        return tools.walk_subtree(storage=storage, arguments=tool_args)
    if name == "list_active_roots":
        return tools.list_active_roots(storage=storage, arguments=tool_args)
    if name == "list_sessions":
        return tools.list_sessions(storage=storage, arguments=tool_args)
    if name == "get_session_state":
        return tools.get_session_state(storage=storage, arguments=tool_args)
    if name == "set_focus":
        return tools.set_focus(storage=storage, arguments=tool_args, now=now)
    if name == "set_root_lifecycle":
        return tools.set_root_lifecycle(
            storage=storage, arguments=tool_args, now=now
        )
    if name == "list_open_nodes":
        return tools.list_open_nodes(storage=storage, arguments=tool_args)
    if name == "add_edge":
        return tools.add_edge(storage=storage, arguments=tool_args, now=now)
    if name == "delete_edge":
        return tools.delete_edge(storage=storage, arguments=tool_args, now=now)
    if name == "list_edges":
        return tools.list_edges(storage=storage, arguments=tool_args)
    if name == "list_unblocked_open_nodes":
        return tools.list_unblocked_open_nodes(
            storage=storage, arguments=tool_args
        )
    if name == "export_mermaid":
        return tools.export_mermaid(storage=storage, arguments=tool_args)
    if name == "export_yaml":
        return tools.export_yaml(storage=storage, arguments=tool_args)
    if name == "resolve_hypothesis_branch":
        return tools.resolve_hypothesis_branch(
            storage=storage, arguments=tool_args, now=now, new_id=new_id
        )
    if name == "resolve_branch":
        return tools.resolve_branch(
            storage=storage, arguments=tool_args, now=now, new_id=new_id
        )
    if name == "mark_reached":
        return tools.mark_reached(storage, arguments=tool_args, now=now)
    if name == "dump_persist":
        return tools.dump_persist(storage, arguments=tool_args, now=now)
    if name == "delete":
        return tools.delete(storage, arguments=tool_args, now=now)
    if name == "force_delete":
        return tools.force_delete(storage, arguments=tool_args, now=now)
    if name == "pool_add":
        scope = tool_args.get("scope") or None
        return tools.pool_add(
            storage, scope=scope, arguments=tool_args, now=now
        )
    if name == "pool_list":
        scope = tool_args.get("scope") or None
        return tools.pool_list(
            storage, scope=scope, arguments=tool_args, now=now
        )
    if name == "pool_elevate":
        scope = tool_args.get("scope") or None
        return tools.pool_elevate(
            storage, scope=scope, arguments=tool_args, now=now
        )
    if name == "pool_drop":
        scope = tool_args.get("scope") or None
        return tools.pool_drop(
            storage, scope=scope, arguments=tool_args, now=now
        )
    if name == "pool_reject":
        return tools.pool_reject(storage, arguments=tool_args, now=now)
    if name == "set_session_mode":
        return tools.set_session_mode(
            storage=storage, arguments=tool_args, now=now
        )
    if name == "bulk_import_subgraph":
        return tools.bulk_import_subgraph(
            storage=storage, arguments=tool_args, now=now
        )
    if name == "find_similar":
        return tools.find_similar(storage=storage, arguments=tool_args)

    raise ValueError(f"unknown tool: {name}")


async def main() -> None:
    log.info("DPD server starting (stdio transport)")
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def cli() -> None:
    """Entry point for the `dpd-mcp-server` console script."""
    anyio.run(main)
