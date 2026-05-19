"""PRGP spike — verify MCP roots feature on stdio.

Goal: confirm that when Claude Code (or any MCP host) spawns this server
via stdio, the client exposes its workspace roots, and the server can
retrieve them via the `roots/list` request.

This validates v0.2-draft §6 (scope auto-detection via MCP roots).

stdout is reserved for JSON-RPC. All logs go to stderr.
"""

import json
import logging
import sys

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("prgp-spike")

app = Server("prgp-spike-roots")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="whoami",
            title="PRGP spike: show roots",
            description=(
                "Returns the workspace roots the MCP client exposed to this server, "
                "plus the client's `roots` capability flags. Used to verify scope "
                "auto-detection feasibility for PRGP."
            ),
            inputSchema={"type": "object", "properties": {}},
        )
    ]


async def fetch_roots() -> dict:
    info: dict = {
        "client_supports_roots": False,
        "list_changed_supported": False,
        "roots": [],
        "error": None,
    }

    session = app.request_context.session
    client_params = session.client_params
    if client_params is None:
        info["error"] = "client_params not available (server not initialized?)"
        return info

    caps = client_params.capabilities
    if caps.roots is None:
        info["error"] = "client did not declare `roots` capability"
        return info

    info["client_supports_roots"] = True
    info["list_changed_supported"] = bool(caps.roots.listChanged)

    try:
        result = await session.list_roots()
        info["roots"] = [{"uri": str(r.uri), "name": r.name} for r in result.roots]
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"

    return info


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> dict:
    if name != "whoami":
        raise ValueError(f"Unknown tool: {name}")
    info = await fetch_roots()
    log.info("whoami invoked; result=%s", json.dumps(info))
    return info


async def main() -> None:
    log.info("PRGP spike server starting (stdio transport)")
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
