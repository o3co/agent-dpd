# dpd / skill — Claude Code skill

DPD skill — conversational UX for the graph protocol. Detects sub-scope from cwd, manages session resume UX, renders graph state inline, and proxies user intent into MCP tool calls.

Phase 2 scope (after `server/` Phase 1 is functional): startup sequence (cwd → sub-scope → list_sessions → user confirm → resume/start), base dialogue loop, render templates.

See [../docs/](../docs/) (or the agent scope's `docs/prgp-v0.2-draft.md` until graduation) for the protocol spec and skill ↔ MCP responsibility split.
