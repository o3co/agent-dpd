# prgp / server — MCP server

PRGP MCP server. Stdio transport, Python 3.11+, sqlite storage (WAL mode).

Phase 1 scope: agent-scope resolution via MCP `roots/list`, sqlite schema (sessions / roots / nodes / edges / audit), minimum CRUD tools (`start_session`, `spawn_root`, `add_node`, `close_node`, `get_node`, `walk_subtree`, `list_active_roots`).

Implementation begins in this directory under TDD discipline. `pyproject.toml`, `src/prgp_mcp_server/`, and `tests/` are added by the first RED test in Phase 1.

See [../docs/](../docs/) (or the agent scope's `docs/prgp-v0.2-draft.md` until graduation) for the protocol spec.
