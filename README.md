# dpd — Decompose-Propagate Decision

Monorepo for DPD (Decompose-Propagate Decision) — a graph-based protocol for structuring AI conversations. Problems are hierarchically decomposed into nodes with dependencies; solution states propagate through the graph.

## Layout

```text
dpd/
├── server/   MCP server (Python, stdio, sqlite) — graph state + tool API
├── skill/    Claude Code skill — conversational UX + MCP client
└── docs/     Spec (graduated from agent scope's docs/prgp-v<N>-*.md)
```

`server/` and `skill/` are coupled by design: the skill consumes the server's MCP tool API; the server stores no conversational state of its own. Install both together via the root `install.sh` (planned, Phase 1+).

## Status

Phase 1 — minimum CRUD implementation. See [docs/](docs/) for the current spec draft (or, until graduation, see the agent scope at `scopes/decompose-propagate.protocol/docs/prgp-v0.2-draft.md`).

The MCP roots feature verification spike lives on the `spike/mcp-roots` branch — historical reference only, do not merge.

## Why monorepo

Atomic install (server + skill in one step), coupled evolution (signature + prompt changes in one commit), and reference Anthropic servers (`modelcontextprotocol/servers`) follow the same pattern. Spec graduation to a public-facing `dpd-spec` is possible later via `git subtree split` if needed.
