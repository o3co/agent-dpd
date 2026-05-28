# Changelog

All notable changes to DPD will be documented in this file.

Versions follow [SemVer](https://semver.org/). The `0.x` series allows breaking
changes on every MINOR bump until `1.0` (see [AGENTS.md](AGENTS.md#versioning)).

## [Unreleased]

## [0.5.0] — 2026-05-28

Issue-driven release: closes nine GitHub issues surfaced from v0.4.0 dogfood
use (#3, #5, #10, #11, #12, #14, #31, #32, #33, #41). Adds a schema migration
(v5 → v6), three new MCP tools, and a batch of skill-methodology refinements.

### BREAKING CHANGES

- **`add_edge` edge-type validation:** `add_edge` (and the `bulk_import_subgraph`
  edge path) now reject any `type` outside the canonical vocabulary
  (`derived_from`, `requires`, `blocks`, `supports`, `contradicts`,
  `contributes_to`, `supersedes`, `qualifies`, `invalidates`) and reject
  self-loops (`from_node == to_node`). Previously the type column was free-form.
  Existing edges are untouched (validation runs only on insert), but callers
  passing non-canonical types will now get a `ValueError`. (#10)

### Schema migration (v5 → v6)

- Adds `nodes.severity` (TEXT, nullable). Migration is additive and runs
  automatically on `Storage.open()`; existing rows get `severity = NULL`.
  Downgrade is not supported (forward-only, per the `0.x` policy). (#32)

### Added

- `delete_edge(session_id, edge_id)` — remove a mis-typed or stale edge without
  dropping to sqlite SQL. (#10)
- `purge_session(session_id)` — delete a finished session's row + roots + edge
  back-refs after its subgraphs were `delete`-d (precondition: `idle`/null mode
  and no nodes remain). `force_purge_session(session_id)` cascades the same
  cleanup, bypassing preconditions, for emergency use. Pool items survive
  (`origin_session_id` nulled). (#12)
- `add_node` optional `severity` field (conventional values `logical` /
  `surface` / `cosmetic`, free-form) for §4.5 grouping. (#32)
- `export_mermaid` `max_label_chars` parameter — pass `null` to disable label
  truncation for README/docs embeds; default stays 60. (#14)
- Skill methodology: §4.5.1 severity-aware grouping, §4.5.2 sibling-granularity
  check (skill-only, transient), §5.1.3 canonical subgraph layout, dpd-import
  eager edge-pinning step, and a "hard rules vs permissive defaults" convention. (#33, #41, #43)

### Changed

- `find_similar` `matched_snippet` now comes from the FTS column that actually
  matched the query (column `-1`), instead of always `anchor_text`. (#3)
- §3.2.1 End narrowing now requires a **concrete partitioned split proposal** at
  ≥6 `achievement_conditions`, not a dismissible "too wide" flag. (#41)
- Narrative docs (`concept.md`, skill READMEs, SKILL.md) clarify that `/fcot` is
  **stakes-based opt-in** — automatic only on high-stakes inferred nodes — rather
  than a mandatory pipeline step. (#5)

### Fixed

- `mark_reached`: the "not reachable" error now names the canonical layout (End
  must be a `parent_id` descendant of Start) and the recovery path; SKILL.md
  §5.1.3 documents the requirement. Removes the routine `force_delete` workaround
  that made #11's "emergency only" framing inaccurate. (#31, #11)

## [0.4.0] — 2026-05-25

### BREAKING CHANGES

- **Repo structure:** `mcp/` renamed to `core/server/`, `skill/` renamed to `core/skills/`. The main `/dpd` skill now lives at `core/skills/dpd/SKILL.md` (was `skill/SKILL.md`). PyPI package name `dpd-mcp-server` is unchanged.
- **Install model:** Plugin-first via Claude Code's plugin system. `install.sh` is no longer the primary install path; it is now an escape hatch for Cursor and CI use cases. Users on Claude Code should install via `/plugin marketplace add o3co/agent-dpd` + `/plugin install dpd@agent-dpd`.
- **Install locations changed:** Plugin cache at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` (e.g. `~/.claude/plugins/cache/agent-dpd/dpd/0.4.0/`); persistent venv at `~/.claude/plugins/data/<plugin>-<marketplace>/.venv/` (e.g. `~/.claude/plugins/data/dpd-agent-dpd/.venv/`). The old symlink layout under `~/.claude/skills/` is no longer used by the plugin install path (still used by the Cursor `install.sh` path).

### Upgrading from 0.3.x

If you installed DPD 0.3.x on Claude Code via `install.sh`, the old skill symlinks and MCP registration are not cleaned automatically by the plugin install. Run these once before `/plugin install dpd@agent-dpd` (harmless no-op if the old install wasn't actually present):

```bash
# 1. Remove old skill symlinks left by install.sh.
#    If Claude Code still auto-discovers ~/.claude/skills/, leaving them in
#    place would register a second copy of /dpd, /dpd-status, … alongside
#    the plugin's copy. If that path is no longer discovered, this is a no-op.
rm -f ~/.claude/skills/dpd ~/.claude/skills/dpd-*

# 2. Remove the old MCP registration. install.sh used `claude mcp add` without
#    --scope, which defaults to project-local — so this command only removes
#    the registration from the *current* directory. If 0.3.x was originally
#    installed from a different directory, run this from there, or first
#    inspect with `claude mcp list` to find where the registration lives.
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
