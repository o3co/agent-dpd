# DPD — Decompose-Propagate Decision - A method for organizing thoughts with AI

[日本語](README.ja.md)

Ever had a sudden insight, or pivoted what you're aiming for mid-AI-conversation, only to find the AI lost the thread?

DPD turns the dialogue into a graph and organizes the thinking process toward your goal. From there, the AI itself helps you surface goal changes, missing considerations, and contradictions with what was said earlier.

Implementation: a Claude Code skill + MCP server.

> **Status**: `0.x` — pre-1.0, public surface may still change. See [docs/concept.md](docs/concept.md#status-and-versioning) for details.

---

## Install

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and **Python 3.11+** on `PATH` (used by the SessionStart hook to bootstrap the bundled MCP server's venv). Other clients listed below.

### Claude Code (recommended)

```text
/plugin marketplace add https://github.com/o3co/agent-market.git
/plugin install dpd@agent-market
```

> **Note:** the `.git` suffix is required. The shorter `o3co/agent-market` form is documented as valid but some installs reject it with "Invalid marketplace source format" — the explicit URL form works universally. `dpd` is distributed through the shared [`agent-market`](https://github.com/o3co/agent-market) marketplace (alongside `fcot`).

**After install, start a fresh Claude Code session** (open a new terminal `claude` or close+reopen the IDE). `/reload-plugins` or window reload alone may not fire the SessionStart hook on first install, so the venv won't be bootstrapped until a genuinely new session starts. The first new session takes ~10–30s while the hook runs `pip install -e` on the bundled server source.

> **Upgrading from 0.3.x?** If you previously installed via `install.sh`, run `rm -f ~/.claude/skills/dpd ~/.claude/skills/dpd-*` and `claude mcp remove dpd-mcp-server` first so the old symlinks and MCP registration don't shadow the plugin. Your graph data at `~/.claude/dpd-server/data/` is preserved. See [CHANGELOG: Upgrading from 0.3.x](CHANGELOG.md#upgrading-from-03x).

That adds the `agent-market` marketplace and installs the `dpd` plugin from it. The plugin bundles:

- `/dpd`, `/dpd-status`, `/dpd-dump`, `/dpd-edit`, `/dpd-fill`, `/dpd-find-similar`, `/dpd-import`, `/dpd-summary-md`, `/dpd-verify-edge`, `/dpd-feedback` slash commands
- The MCP server (`dpd-mcp-server`), with venv lazy-bootstrapped on first session
- A SessionStart hook that keeps the venv in sync with the plugin's bundled Python package

Plugin body lives at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` (concretely: `~/.claude/plugins/cache/agent-market/dpd/0.7.0/`); persistent venv at `~/.claude/plugins/data/<plugin>-<marketplace>/.venv/` (concretely: `~/.claude/plugins/data/dpd-agent-market/.venv/`). The venv path is what Claude Code passes as `${CLAUDE_PLUGIN_DATA}` to the SessionStart hook.

To update: `/plugin update dpd` (or rely on Claude Code's auto-update) — this updates the plugin's bundled source, and the SessionStart hook will rebuild the venv on the next session via its hash check over the bundled `pyproject.toml` + `.claude-plugin/plugin.json`. Do **not** `pip install -U dpd-mcp-server` inside the plugin's venv directly: the hook will not notice the manual upgrade (it only rebuilds on bundled-source changes), and the venv will silently desync from the plugin source. If you need a clean venv, delete `~/.claude/plugins/data/dpd-agent-market/.venv/` and restart Claude Code — the hook will rebuild it.

### Cursor

```bash
curl -fsSL https://raw.githubusercontent.com/o3co/agent-dpd/main/install.sh | bash
```

That clones the repo, creates a venv at `core/server/.venv`, symlinks `core/skills/*` into `~/.cursor/skills/`, and patches `~/.cursor/mcp.json` to register `dpd-mcp-server`. Restart Cursor.

Env overrides for the Cursor installer:

| Var | Default | Purpose |
| --- | --- | --- |
| `DPD_INSTALL_DIR` | `$HOME/agent-dpd` | Clone target |
| `DPD_PYTHON` | `python3.11` | Python interpreter |
| `DPD_CURSOR_HOME` | `$HOME/.cursor` | Cursor config dir |
| `DPD_NO_CURSOR_SKILL_LINK` | unset | Skip skill symlinking |
| `DPD_NO_CURSOR_MCP_PATCH` | unset | Skip `mcp.json` patching |

### Cline

Cline auto-discovers Anthropic-format skills. Clone the repo and point Cline at `core/skills/` per Cline docs. MCP via Cline's MCP marketplace.

### Codex CLI / Gemini CLI / Claude Desktop / ChatGPT

Not in 0.4. See [tracking issue #16](https://github.com/o3co/agent-dpd/issues/16) for roadmap.

### Manual (any agent)

```bash
git clone https://github.com/o3co/agent-dpd.git
cd agent-dpd
python3.11 -m venv core/server/.venv
core/server/.venv/bin/pip install -e 'core/server[dev]'
# Then register dpd-mcp-server with your client per its docs.
# Skill family lives under core/skills/.
```

Manual setup details are in [AGENTS.md](AGENTS.md#setup).

---

## What using DPD looks like

A typical session — abbreviated:

```text
You: /dpd
DPD: No existing sessions for this workspace. Start a new one?
     I see we've been discussing "pick an auth strategy".
     Proposed anchor:
       Start: pick auth strategy
       End:   auth strategy decided + rationale documented
     Apply?
You: yes
DPD: [creates root + Start + End anchor]
     Now in ambient mode. Continue normally.

[you discuss JWT, session cookies, OAuth for a while]

DPD: I'd like to record what we just covered:
       - hypothesis: JWT          → close as rejected (token revocation hard)
       - hypothesis: session cookies
       - hypothesis: OAuth-only   → close as rejected (3rd-party dep)
     Apply?
You: yes
DPD: [adds nodes, closes rejected ones]

[more discussion lands the decision]

DPD: Looks like we settled on session cookies. Recording:
       - decision: go with session cookies
       - rationale: simplest correct given same-origin constraint
       - derived_from: ↑ same-origin constraint (evidence node)
     End condition satisfied — mark this subgraph as reached?
You: yes
DPD: [mark_reached] Session is idle. Subgraph closed.
```

You type prose. DPD does the bookkeeping (proposing, recording, narrowing) and asks for confirmation at natural pauses. Nothing changes in the graph without your explicit OK.

### What was happening here

Mapped to DPD's terms, the walkthrough was three phases:

1. **Entry phase** — right after `/dpd` fires, the goal (Start) and achievement conditions (End) get pinned down as anchors. Everything that follows can now be measured for drift against this anchor.
2. **Ambient mode** — normal conversation continues; DPD observes and, at natural pauses, says "here's what I'd record". Hypothesis adds, closures, and decisions all require your explicit OK.
3. **mark_reached** — when the End condition is satisfied, the subgraph is closed and frozen in a "what was decided, and why" state you can revisit later.

The basic per-session rhythm: **set the anchor → observe the conversation → propose tidy-up at natural pauses → freeze when done**.

---

## What you can do

### Commands

| Command | Purpose |
| --- | --- |
| `/dpd` | Start tracking decisions, or resume an existing session |
| `/dpd-status` | "Where are we?" snapshot — active roots, focus, Pool items, mode |
| `/dpd-dump` | Full graph as JSON (YAML 1.2-compatible) |
| `/dpd-summary-md` | Extract decided/closed items into a markdown summary |
| `/dpd-edit <id>` | Manual edit when you want direct control |
| `/dpd-import <file>` | Import a prose/spec/graph doc as an archived subgraph |
| `/dpd-fill` | Generate inferred gap candidates against the current graph |

#### Run / resume with a scope

```text
# Pass any name to set the sub-scope explicitly.
# Without --scope: runs as top-level (uses `.dpdrc` walk-up if found, else no scope).
# With a `.dpdrc` in place: any /dpd invoked under that directory auto-attaches to its scope.
/dpd --scope=<scope-name>
```

### Detailed examples

Walked-through use cases — actual graphs built with the real MCP tools, transcripts and graph illustrations included — live in [`docs/examples.md`](docs/examples.md):

1. **Decide a monetization model** — multiple hypotheses → evidence → decision with the rejected alternatives preserved.
2. **Narrow a vague service idea into a minimum spec** — End-narrowing pressure forces "I want to build an app" down to a concrete first-version spec with explicit non-goals.
3. **Validate a spec for consistency and completeness** — the `/dpd-import → /dpd-fill → /fcot` pipeline; the same one used on DPD's own spec before release.
4. **Multi-agent dev workflow across sessions** — using the session as a handoff surface when implementation takes more context than a single agent has.

### Good fits

- Multi-day work or multi-session projects where "what did we decide" matters later
- Conversations with several viable branches you'll want to revisit
- Architectural / scope / policy decisions where you'd want a paper trail
- Spec / design-document review (via the self-validation pipeline above)

### Overkill for

- Short single-threaded conversations
- Mechanical, fully-specified tasks
- Throw-away exploration with no resume

---

## Optional: scope marker

If you want one DPD database shared across sibling project directories (e.g., a monorepo), drop a `.dpdrc` at the workspace root:

```ini
# .dpdrc — DPD scope marker
scope=my-workspace
```

The server walks up from your editor's cwd to find this marker and uses its location as the agent-scope identifier. Details and sub-scope behavior: [AGENTS.md](AGENTS.md#sub-scope-detection-dpdrc).

---

## About DPD's methodology

Why represent decisions as a *graph*, what failure modes the End modification gate / Pool / rejected-hypothesis-retention mechanisms prevent, and how DPD itself was developed via DPD's own self-validation pipeline — the rationale behind these design choices lives in [docs/concept.md](docs/concept.md).

- **[docs/concept.md](docs/concept.md)** — What DPD is, why it exists, how the graph works, lifecycle states, the agent-driven dogfood story

## Other docs

- **[core/server/README.md](core/server/README.md)** — MCP server architecture + 30-tool reference
- **[core/skills/README.md](core/skills/README.md)** — Skill family overview (main `/dpd` + sub-skills)
- **[AGENTS.md](AGENTS.md)** — Contributor guidelines (TDD, review workflow, conventions)

---

## License

Apache 2.0 — see [LICENSE](LICENSE). Copyright © 2026 [1o1 Co. Ltd.](https://1o1.co.jp/)

Contributions welcome; read [AGENTS.md](AGENTS.md) before opening a PR.
