"""MCP server wiring: register the 18 DPD tools over stdio."""

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
            description="Add a child node under a root or node.",
            inputSchema={
                "type": "object",
                "required": ["session_id", "parent_id", "type", "text"],
                "properties": {
                    "session_id": {"type": "string"},
                    "parent_id": {"type": "string"},
                    "type": {"type": "string"},
                    "text": {"type": "string"},
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
                "resume vs new-session UX."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": ["string", "null"],
                        "description": "Sub-scope to filter by. Omit or pass null/empty for top-level sessions only.",
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
                "to clear focus. Validates the node exists in the session."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "node_id": {
                        "type": ["string", "null"],
                        "description": "Target node id, or null to clear focus.",
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
                "subtree. Powers next_focus selection (deepest-within after recency-"
                "ranked root)."
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
                "Insert an edge between two nodes (e.g., requires/blocks/derived_from). "
                "Edge type vocabulary is free-form for now (no DB-level CHECK)."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id", "from_node", "to_node", "type"],
                "properties": {
                    "session_id": {"type": "string"},
                    "from_node": {"type": "string"},
                    "to_node": {"type": "string"},
                    "type": {"type": "string"},
                    "reason": {"type": ["string", "null"]},
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

    raise ValueError(f"unknown tool: {name}")


async def main() -> None:
    log.info("DPD server starting (stdio transport)")
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def cli() -> None:
    """Entry point for the `dpd-mcp-server` console script."""
    anyio.run(main)
