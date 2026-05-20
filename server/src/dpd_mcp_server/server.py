"""MCP server wiring: register the 9 DPD tools over stdio."""

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
    return [
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
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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

    raise ValueError(f"unknown tool: {name}")


async def main() -> None:
    log.info("DPD server starting (stdio transport)")
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def cli() -> None:
    """Entry point for the `dpd-mcp-server` console script."""
    anyio.run(main)
