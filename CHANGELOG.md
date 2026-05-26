# Changelog

All notable changes to DPD will be documented in this file.

Versions follow [SemVer](https://semver.org/). The `0.x` series allows breaking
changes on every MINOR bump until `1.0` (see [AGENTS.md](AGENTS.md#versioning)).

## [Unreleased]

## [0.4.0] — 2026-05-25

### BREAKING CHANGES

- **Repo structure:** `mcp/` renamed to `core/server/`, `skill/` renamed to `core/skills/`. The main `/dpd` skill now lives at `core/skills/dpd/SKILL.md` (was `skill/SKILL.md`). PyPI package name `dpd-mcp-server` is unchanged.
- **Install model:** Plugin-first via Claude Code's plugin system. `install.sh` is no longer the primary install path; it is now an escape hatch for Cursor and CI use cases. Users on Claude Code should install via `/plugin marketplace add o3co/agent-dpd` + `/plugin install dpd@agent-dpd`.
- **Install locations changed:** Plugin cache at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` (e.g. `~/.claude/plugins/cache/agent-dpd/dpd/0.4.0/`); persistent venv at `~/.claude/plugins/data/<plugin>-<marketplace>/.venv/` (e.g. `~/.claude/plugins/data/dpd-agent-dpd/.venv/`). The old symlink layout under `~/.claude/skills/` is no longer used by the plugin install path (still used by the Cursor `install.sh` path).

### Upgrading from 0.3.x

If you installed DPD 0.3.x on Claude Code via `install.sh`, the old skill symlinks and MCP registration are not cleaned automatically by the plugin install. Run these once before `/plugin install dpd@agent-dpd`:

```bash
# 1. Remove old skill symlinks (otherwise both old and new copies of /dpd, /dpd-status, … will be registered and one will shadow the other)
rm -f ~/.claude/skills/dpd ~/.claude/skills/dpd-*

# 2. Remove the old global MCP registration (the plugin ships its own .mcp.json; leaving the old one around can shadow the plugin's server)
claude mcp remove dpd-mcp-server 2>/dev/null || true
```

Your graph data is **preserved**: DPD stores graphs at `~/.claude/dpd-server/data/<encoded-agent-scope>/graph.sqlite`, and that path is unchanged across 0.3 → 0.4. All existing sessions, roots, Pool items, and archived subgraphs carry over.

After the cleanup, install per the README and start a **fresh** Claude Code session (new terminal `claude` or close+reopen the IDE) so the SessionStart hook fires and bootstraps the venv.

If you were on Cursor via `install.sh`, no action is required — `install.sh` remains the Cursor install path.

### Added

- `packaging/claude-code/` — Claude Code plugin manifest, skill symlinks, `.mcp.json`, and `hooks/session-start.sh` for venv lazy bootstrap.
- `.claude-plugin/marketplace.json` — registers this repo as a Claude Code marketplace.
- `/dpd-feedback` skill — submit dogfood feedback as GitHub issues with system metadata only (privacy hard constraint: no session content / scope / label / Pool / rejected hypotheses sent).
- Feedback link appended to each skill's output (lightweight discovery surface for the `/dpd-feedback` skill).

### Changed

- All slash command names unchanged (`/dpd`, `/dpd-status`, `/dpd-dump`, `/dpd-edit`, `/dpd-fill`, `/dpd-find-similar`, `/dpd-import`, `/dpd-summary-md`, plus new `/dpd-feedback`).
- README install instructions: plugin-first, with `install.sh` documented as escape hatch.

### Source

This release was tracked end-to-end in a DPD self-dogfood session (`ses_bb28a64c1713` in scope `decompose-propagate.protocol`). Architectural decisions are recorded in [`docs/ideas/dpd-plugin-refactor-2026-05-25.md`](docs/ideas/dpd-plugin-refactor-2026-05-25.md) (in the scope repo, not this one).

## [0.3.2] — 2026-05-22

See git log for prior releases (CHANGELOG was introduced in 0.4.0).

[Unreleased]: https://github.com/o3co/agent-dpd/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/o3co/agent-dpd/releases/tag/v0.4.0
[0.3.2]: https://github.com/o3co/agent-dpd/releases/tag/v0.3.2
