# AGENTS.md — DPD repository

[日本語](AGENTS.ja.md)

Development guidelines for AI coding assistants (Claude, Cursor, Copilot, …) and human contributors working on this repo. Read this file before making changes.

## Project overview

**DPD (Decompose-Propagate Decision)** is a graph-based protocol for structuring AI conversations as decision graphs. This repository contains the reference implementation:

- `mcp/` — Model Context Protocol server (Python, stdio, sqlite). Owns graph state and tool API.
- `skill/` — Claude Code skill. Provides the conversational UX layer that talks to the MCP server.
- `docs/` — Specs, migration guides, ADRs.

The MCP server is stateless w.r.t. conversation; the skill is stateless w.r.t. graph data. Both move together.

## Setup

Requires Python 3.11+.

```bash
# from repo root
python3.11 -m venv mcp/.venv
mcp/.venv/bin/pip install -e 'mcp[dev]'

# register with Claude Code (one-time)
claude mcp add dpd-mcp-server -- "$(pwd)/mcp/.venv/bin/dpd-mcp-server"
```

Restart Claude Code after registration so the `mcp__dpd-mcp-server__*` tools become discoverable.

Runtime data lives at `~/.claude/dpd-server/data/<encoded-agent-scope>/graph.sqlite`. Override with `DPD_DATA_DIR` env var (tests use this to avoid touching real data).

## Tests

```bash
mcp/.venv/bin/python -m pytest mcp/tests/ -q
```

All tests must pass before commit. The suite includes a stdio end-to-end smoke that spawns the actual server binary — if it fails, debug the failure rather than skipping it.

## Development workflow

### TDD discipline

Every feature and bug fix follows RED → GREEN → REFACTOR:

1. **RED** — write the failing test first. Verify it actually fails for the right reason.
2. **GREEN** — write the minimum code to pass.
3. **REFACTOR** — clean up while staying green.

Plans (in `.claude/superpowers/plans/` if present) break each task into these three steps explicitly.

### Code review

Run `/multi-agent-review` once per PR before declaring work done. Re-reviews after fixes are at the user's discretion — not automatic.

When dismissing review concerns, use the contract-based or verified-empirical template (rule + "covers when?" condition). Vague dismissals ("seems unlikely") default to must-fix.

When the same issue is raised by 2+ independent reviewers, default to must-fix even if you previously dismissed it.

## Code style

- **Python**: type-hinted, modern syntax (`X | None` over `Optional[X]`, `list[int]` over `List[int]`). `from __future__ import annotations` at the top of each module.
- **Comments**: default to none. Only write a comment when the *why* is non-obvious (a hidden constraint, a workaround for a specific issue, behavior that would surprise a reader). Never narrate *what* the code does.
- **Docstrings**: one short line for modules and public functions. No multi-paragraph essays.
- **Errors**: validate at boundaries (user input, MCP tool args). Trust internal call sites — don't add belt-and-suspenders checks.
- **No backward-compat hacks** until v1.0. If a change is needed, change the thing directly rather than keeping both old and new code paths.

## Spec & docs

The **user-facing readable spec** (concept + lifecycle + Mermaid diagrams) lives in [`README.md`](README.md) (or `docs/spec.md` if split out).

The **full implementation-level spec** (SQL DDL, error codes, state machine tables, migration semantics) currently lives in the upstream workspace at `scopes/decompose-propagate.protocol/docs/dpd-v<N>-draft.md`. If you need it for non-trivial implementation work, ask the maintainers — graduation into this repo is planned but not done.

ADRs and migration guides go in `docs/`.

## Sub-scope detection (`.dpdrc`)

DPD distinguishes two levels of scope:

- **Agent scope** — the workspace root the user is operating in. Detected server-side from MCP `roots/list` by walking up to a `.dpdrc` marker file. Each agent scope gets its own sqlite DB.
- **Sub-scope** — a finer-grained partition within an agent scope (e.g., a sub-project, a workstream). Detected skill-side from `--scope=<name>` arg, falling back to walking up from cwd to a `.dpdrc` containing `scope=<name>`.

A `.dpdrc` is a single-line marker (`scope=<name>` or empty for agent-scope-only). Don't introduce new convention files for scope; reuse `.dpdrc`.

## Commits & PRs

- Conventional commit prefixes (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`).
- Breaking changes get `!` (e.g., `refactor!: rename server/ → mcp/`).
- Commit message body explains the *why*, not the *what*.
- PRs require conversation resolution before merge (enforced by branch protection).
- Branch is auto-deleted after merge.
- Never force-push to `main`.

## License & copyright

- **License**: Apache 2.0. The `LICENSE` file is the verbatim official text from <https://www.apache.org/licenses/LICENSE-2.0.txt>. **Never AI-generate or paraphrase the LICENSE file** — even a single character drift breaks SPDX detection on GitHub and pkg.go.dev.
- **Copyright**: `1o1 Co. Ltd.` (株式会社 1o1, <https://1o1.co.jp/>). Not `o3co`, not `o3co Inc.`. The notice line lives at the top of `LICENSE` outside the verbatim license body.

## Versioning

Currently `0.x` — minor version bumps freely, breaking changes allowed without a major bump until `1.0`. New features land on `main` after review; no long-lived feature branches.

`1.0` will lock the public API surface (MCP tool names + args, `.dpdrc` schema, sqlite schema migration path). Don't promise stability before then.
