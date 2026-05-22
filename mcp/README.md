# dpd / mcp — MCP server

[日本語](README.ja.md)

DPD's Model Context Protocol server. Owns all graph state; speaks no English to the user.

This server is one half of the DPD reference implementation — the persistence-and-tools half. The other half is [`../skill/`](../skill/), which drives the conversational UX and calls the tools defined here. For the protocol concept and overall use, see [`../README.md`](../README.md).

## Role

The MCP server is what makes DPD a **protocol** rather than just a prompt:

- **State lives here, not in the skill.** Sessions, roots, nodes, edges, and Pool items are persisted to SQLite (WAL mode) so they survive Claude Code restarts, context resets, and parallel sessions.
- **One DB per agent scope.** The server walks up from the MCP `roots/list` advertised by the client to a `.dpdrc` marker, and uses that path as the agent-scope identifier. Two unrelated workspaces never share state by accident.
- **Stdio transport.** No HTTP server, no port to expose. The MCP server is spawned as a subprocess of Claude Code over stdin/stdout, and dies when Claude Code dies. This matches the security model of MCP itself.
- **Tools, not prompts.** All graph operations are exposed as MCP tools with typed arguments. The skill builds prompts; the server validates inputs, runs the SQL, and returns structured results. The boundary keeps the prompt layer swappable.

## Architecture

```text
┌─────────────────────────────┐
│ Claude Code (host)          │
│  ├─ skill (prompts, UX)     │
│  └─ MCP client              │
└─────────────┬───────────────┘
              │ stdio (JSON-RPC)
┌─────────────▼───────────────┐
│ dpd-mcp-server (this dir)   │
│  ├─ tool dispatch           │
│  ├─ scope resolution        │
│  ├─ sqlite storage (WAL)    │
│  └─ schema migration        │
└─────────────────────────────┘

Storage path: ~/.claude/dpd-server/data/<encoded-agent-scope>/graph.sqlite
              (override with DPD_DATA_DIR)
```

Schema migrations run automatically when an older DB is opened: the current schema version is `v4`, and the server applies `migrate_v2_to_v3.py` and `migrate_v3_to_v4.py` in sequence as needed. No manual step required for normal use.

## Install

From the repository root:

```bash
make install        # creates mcp/.venv, installs editable + dev deps
make register       # registers dpd-mcp-server with Claude Code
```

Or manually:

```bash
python3.11 -m venv mcp/.venv
mcp/.venv/bin/pip install -e 'mcp[dev]'
claude mcp add dpd-mcp-server -- "$(pwd)/mcp/.venv/bin/dpd-mcp-server"
```

Restart Claude Code so the `mcp__dpd-mcp-server__*` tools are discoverable.

## Tools

30 MCP tools, organized by concern:

### Session lifecycle

| Tool | Purpose |
| --- | --- |
| `start_session` | Create a new session (entry mode). |
| `list_sessions` | List sessions for the agent scope, filterable by sub-scope and mode. |
| `get_session_state` | Snapshot: session metadata + active roots + focus node. |
| `set_session_mode` | Transition `entry → ambient → idle`. |

### Root management

| Tool | Purpose |
| --- | --- |
| `spawn_root` | Create a new root subgraph in a session. |
| `list_active_roots` | List roots with `lifecycle=active`. |
| `set_root_lifecycle` | Move a root through `active → archived → closed` (monotonic). |
| `set_focus` | Set the session's focus node (for resume context). |

### Node CRUD

| Tool | Purpose |
| --- | --- |
| `add_node` | Add a node (type ∈ start / end / question / hypothesis / decision / rationale / evidence / …). Supports `provenance` (grounded / inferred / imported / manual). |
| `get_node` | Fetch a node by id. |
| `close_node` | Mark a node closed with `closure_reason`. |
| `list_open_nodes` | List open nodes under a root. |
| `list_unblocked_open_nodes` | Same, but filters out nodes with incoming `blocks` edges. |
| `walk_subtree` | Walk a subtree from a parent; filters by `parent_kind` for safety. |

### Edge management

| Tool | Purpose |
| --- | --- |
| `add_edge` | Add an edge (`derived_from` / `contributes_to` / `blocks` / …). |
| `list_edges` | List edges, filterable by endpoints and type. |

### Decision flow

| Tool | Purpose |
| --- | --- |
| `resolve_branch` | Generic branch resolution: close N sibling nodes + create a decision + optional rationale + `derived_from` edges atomically. |
| `resolve_hypothesis_branch` | Specialization: accept one hypothesis, close its siblings as rejected, attach decision + rationale. |
| `mark_reached` | Mark an End node as reached after evaluating `achievement_conditions`. |

### Pool

| Tool | Purpose |
| --- | --- |
| `pool_add` | Park an observation in the Pool. Auto-computes `text_hash` for dedup. |
| `pool_list` | List Pool items, optionally including rejected items. |
| `pool_elevate` | Promote a Pool item to a graph node with explicit edges. |
| `pool_drop` | Remove without recording rejection. |
| `pool_reject` | Reject with a reason — suppresses re-proposal of the same canonical text. |

### Bulk & export

| Tool | Purpose |
| --- | --- |
| `bulk_import_subgraph` | Insert a whole subgraph (nodes + edges) in one transaction, with parent_kind consistency validation. |
| `export_mermaid` | Render a subgraph (or active roots) as Mermaid. |
| `export_yaml` | Render a subgraph as JSON-compatible YAML. |
| `dump_persist` | Dump session state to a stable on-disk format. |

### Advanced lifecycle

| Tool | Purpose |
| --- | --- |
| `delete` | Soft-delete (only allowed once a node is in `state=closed` for the documented grace period). |
| `force_delete` | Hard-delete, bypassing the grace period (logged as `audit.kind=force_delete`). |

For exact argument shapes, see [`src/dpd_mcp_server/server.py`](src/dpd_mcp_server/server.py) — each `types.Tool(...)` definition carries a JSON Schema for its inputs.

## Storage layout

```text
~/.claude/dpd-server/data/
└── <encoded-agent-scope>/        # e.g., "-Volumes-Workspace-scopes-mcp"
    └── graph.sqlite               # one DB per agent scope (WAL mode)
```

The agent-scope encoding replaces `/` with `-` in the absolute path of the scope root, so each scope gets a deterministic directory name without collisions.

Override the root with `DPD_DATA_DIR` (used by tests to avoid touching real data).

## Tests

```bash
make test    # or: mcp/.venv/bin/python -m pytest mcp/tests/ -q
```

The suite (255 tests as of v0.3.1) includes a stdio end-to-end smoke that spawns the actual server binary and walks a full tool chain. Schema migration tests inject constraint violations to verify rollback atomicity.

## Migrations

Migrations run automatically inside `Storage.open()`: when the DB's `PRAGMA user_version` is below the current schema version, the appropriate `migrate_v<N>_to_v<N+1>.py` is applied transactionally before the connection is returned. No manual step is required.

To migrate a DB offline (e.g., before bundling a copy for archive):

```bash
mcp/.venv/bin/python -m dpd_mcp_server.migrate_v3_to_v4 path/to/graph.sqlite
```
