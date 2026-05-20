"""MCP server wiring: register the 7 PRGP tools over stdio."""

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
log = logging.getLogger("prgp-server")

app = Server("prgp-mcp-server")

_storage: Storage | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _data_dir() -> Path:
    override = os.environ.get("PRGP_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "prgp-server" / "data"


async def _get_storage() -> Storage:
    """Resolve agent scope from MCP roots and open (lazily) the sqlite db."""
    global _storage
    if _storage is not None:
        return _storage

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

    db_path = _data_dir() / encoded / "graph.sqlite"
    log.info("opening sqlite at %s", db_path)
    _storage = Storage.open(str(db_path))
    return _storage


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="start_session",
            title="Start session",
            description="Begin a new PRGP session. Returns session_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {"type": ["string", "null"],
                              "description": "Sub-scope identifier (optional)."},
                    "label": {"type": ["string", "null"],
                              "description": "Human-readable label (optional)."},
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
                },
            },
        ),
        types.Tool(
            name="walk_subtree",
            title="Walk subtree",
            description="Return all descendants of a root.",
            inputSchema={
                "type": "object",
                "required": ["session_id", "root_id"],
                "properties": {
                    "session_id": {"type": "string"},
                    "root_id": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="list_active_roots",
            title="List active roots",
            description="List all roots with lifecycle=active in this session.",
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {"session_id": {"type": "string"}},
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    storage = await _get_storage()
    now = _now_iso()

    if name == "start_session":
        return tools.start_session(
            storage=storage, arguments=arguments, now=now, new_id=new_id
        )
    if name == "spawn_root":
        return tools.spawn_root(
            storage=storage, arguments=arguments, now=now, new_id=new_id
        )
    if name == "add_node":
        return tools.add_node(
            storage=storage, arguments=arguments, now=now, new_id=new_id
        )
    if name == "close_node":
        return tools.close_node(
            storage=storage, arguments=arguments, now=now
        )
    if name == "get_node":
        return tools.get_node(storage=storage, arguments=arguments)
    if name == "walk_subtree":
        return tools.walk_subtree(storage=storage, arguments=arguments)
    if name == "list_active_roots":
        return tools.list_active_roots(storage=storage, arguments=arguments)

    raise ValueError(f"unknown tool: {name}")


async def main() -> None:
    log.info("PRGP server starting (stdio transport)")
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def cli() -> None:
    """Entry point for the `prgp-mcp-server` console script."""
    anyio.run(main)
