# AGENTS.md — DPD repository

Development guidelines for AI coding assistants (Claude, Cursor, Copilot, …) and human contributors working on this repo. Read this file before making changes.

## Project overview

**DPD (Decompose-Propagate Decision)** is a graph-based protocol for structuring AI conversations as decision graphs. This repository contains the reference implementation:

- `core/server/` — Model Context Protocol server (Python, stdio, sqlite). Owns graph state and tool API.
- `core/skills/` — Skill family (`/dpd`, `/dpd-status`, `/dpd-fill`, …). Single source of truth for SKILL.md content.
- `packaging/<agent>/` — Per-agent packaging wrappers (currently `claude-code/`). Each wraps `core/` with the agent's manifest + symlinks; symlinks are dereferenced at install time.
- `install.sh` — Escape-hatch installer for Cursor (and CI/dev). Not used for Claude Code (plugin system is the install path there).
- `.claude-plugin/marketplace.json` — Registers this repo as a Claude Code marketplace.
- `docs/` — Specs, migration guides, ADRs.

The MCP server is stateless w.r.t. conversation; the skill is stateless w.r.t. graph data. Both move together.

## Setup

Requires Python 3.11+.

### For development on this repo

```bash
git clone https://github.com/o3co/agent-dpd.git
cd agent-dpd
python3.11 -m venv core/server/.venv
core/server/.venv/bin/pip install -e 'core/server[dev]'
```

### To use DPD locally (Claude Code plugin path)

For testing the plugin from this source tree (without going through marketplace install):

```bash
claude --plugin-dir packaging/claude-code
```

Or to install permanently:

```text
/plugin marketplace add o3co/agent-dpd
/plugin install dpd@agent-dpd
```

The plugin lays out `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` (read-only on update; e.g. `~/.claude/plugins/cache/agent-dpd/dpd/0.4.0/`) and `~/.claude/plugins/data/<plugin>-<marketplace>/.venv/` (persistent; e.g. `~/.claude/plugins/data/dpd-agent-dpd/.venv/`, venv lazy-bootstrapped by `packaging/claude-code/hooks/session-start.sh`).

Runtime DPD data lives at `~/.claude/dpd-server/data/<encoded-agent-scope>/graph.sqlite`. Override with `DPD_DATA_DIR` (tests use this).

### Tests

```bash
core/server/.venv/bin/python -m pytest core/server/tests/ -q
```

All tests must pass before commit. Includes a stdio end-to-end smoke that spawns the server binary, install.sh helper tests, and the SessionStart hook tests under `packaging/claude-code/hooks/tests/`.

## Cross-platform notes

The `packaging/<agent>/skills/*` entries are symlinks to `core/skills/*` (single source of truth). On Windows, creating symlinks requires either Developer Mode enabled or an elevated shell (`mklink /D`). Mac/Linux work without special permissions. End users of the plugin are NOT affected — Claude Code dereferences symlinks during plugin cache install, so the installed plugin contains real file copies, no live symlinks.

### Verify symlink dereference on install

The dereference behavior above is load-bearing for the whole `packaging/claude-code/skills/*` + `core` symlink approach (without it the installed plugin would have dangling relative-path symlinks). Verify manually before any release that touches `packaging/claude-code/` layout:

```bash
# After /plugin marketplace add o3co/agent-dpd && /plugin install dpd@agent-dpd
# in a fresh Claude Code session, confirm the installed plugin has real dirs, not symlinks:
INSTALLED="$HOME/.claude/plugins/cache/agent-dpd/dpd"
[ -d "$INSTALLED" ] || { echo "plugin not installed at $INSTALLED"; exit 1; }
INSTALLED="$INSTALLED/$(ls -1 "$INSTALLED" | sort -V | tail -1)"  # latest version dir
ls -la "$INSTALLED/skills" "$INSTALLED/core"  # both should be real dirs (d), NOT symlinks (l)
find "$INSTALLED" -type l                     # should print nothing
```

If `find -type l` lists anything under the installed plugin, the dereference assumption is broken and the install will fail on machines where the symlink targets don't exist.

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

The **full implementation-level spec** (SQL DDL, error codes, state machine tables, migration semantics) currently lives in the upstream workspace (see [docs/concept.md](docs/concept.md) — "Implementation spec" section). If you need it for non-trivial implementation work, ask the maintainers — graduation into this repo is planned but not done.

ADRs and migration guides go in `docs/`.

## Sub-scope detection (`.dpdrc`)

DPD distinguishes two levels of scope:

- **Agent scope** — the workspace root the user is operating in. Detected server-side from MCP `roots/list` by walking up to a `.dpdrc` marker file. Each agent scope gets its own sqlite DB.
- **Sub-scope** — a finer-grained partition within an agent scope (e.g., a sub-project, a workstream). Detected skill-side from `--scope=<name>` arg, falling back to walking up from cwd to a `.dpdrc` containing `scope=<name>`.

A `.dpdrc` is a single-line marker (`scope=<name>` or empty for agent-scope-only). Don't introduce new convention files for scope; reuse `.dpdrc`.

## Commits & PRs

- Conventional commit prefixes (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`).
- Breaking changes get `!` (e.g., `refactor!: rename mcp/ → core/server/`).
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
