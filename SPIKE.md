# PRGP spike — MCP roots feature verification

**Status**: throwaway PoC (branch `spike/mcp-roots`). Do NOT merge to `main`.
**Goal**: validate v0.2-draft §6 assumption — *"when Claude Code spawns this server via stdio, the client exposes its workspace roots, and the server can retrieve them via `roots/list`."*

If this works, scope auto-detection (the core of PRGP's MCP architecture) is feasible.
If it doesn't, we need plan B (env var / CLI arg / config file based scope).

## What's in this spike

| File | Purpose |
|---|---|
| `spike_roots.py` | Minimum stdio MCP server with one tool: `whoami` |
| `.venv/` | Python 3.11 venv with `mcp==1.27.1` installed (gitignored) |
| `SPIKE.md` | This file |

`whoami` calls `session.list_roots()` and returns:

```json
{
  "client_supports_roots": <bool>,
  "list_changed_supported": <bool>,
  "roots": [{"uri": "file://...", "name": "..."}],
  "error": null | "<reason>"
}
```

## Local smoke test (already verified)

The server starts on stdio and responds to `initialize`:

```bash
cd /Volumes/Workspace/scopes/mcp/scopes/problem-graph.protocol/repos/prgp-mcp-server
{
  echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{"roots":{"listChanged":true}},"clientInfo":{"name":"smoke","version":"0.0"}}}'
  echo '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  sleep 1
} | .venv/bin/python spike_roots.py
```

Expected: a JSON-RPC response containing `serverInfo.name = "prgp-spike-roots"`.
Stderr log: `PRGP spike server starting (stdio transport)`.

## Real verification (against Claude Code)

### 1. Register the server

From a fresh terminal at this directory:

```bash
claude mcp add prgp-spike-roots \
  -- /Volumes/Workspace/scopes/mcp/scopes/problem-graph.protocol/repos/prgp-mcp-server/.venv/bin/python \
     /Volumes/Workspace/scopes/mcp/scopes/problem-graph.protocol/repos/prgp-mcp-server/spike_roots.py
```

(Alternative: drop a `.mcp.json` at the workspace root — see [Claude Code MCP docs](https://docs.claude.com/en/docs/claude-code/mcp).)

### 2. Restart Claude Code so it picks up the new server

In Claude Code, run `/mcp` and confirm `prgp-spike-roots` is listed as connected.

### 3. Invoke the `whoami` tool

In a Claude Code chat at any working directory under `/Volumes/Workspace/scopes/mcp/`, ask:

> Use the `whoami` tool from `prgp-spike-roots` and show me the raw JSON it returns.

### 4. Interpret the result

| Result | Meaning | Action |
|---|---|---|
| `client_supports_roots: true` + non-empty `roots` array | ✅ Assumption confirmed. Scope auto-detect via roots is feasible. | Proceed to 第 1 step (minimum CRUD + scope detect). |
| `client_supports_roots: true` + empty `roots` | ⚠️ Client advertises capability but exposes nothing. Investigate when/how Claude Code populates roots. | Check Claude Code docs / configuration. |
| `client_supports_roots: false` | ❌ Claude Code doesn't expose roots. v0.2-draft §6 needs revision. | Plan B: env var (`PRGP_SCOPE`) or explicit `scope` arg on every tool call. |
| `error` field non-null | depends on error — likely SDK or transport issue | debug |

### 5. Bonus — multi-root behavior

If `client_supports_roots: true`, also test what happens when Claude Code is opened with multiple workspace folders. Does each appear as a separate root? Or only the primary? This affects how PRGP picks the right scope when the user has several scopes open.

## After verification

- **Record the observed result in `docs/prgp-v0.2-draft.md` §6.5 (or a new §6.6)** before discarding the spike.
- The spike branch can stay as history but should NOT merge to `main`.
- 第 1 step (minimum CRUD + sqlite + scope detect) starts on a fresh `feat/...` branch with TDD.
