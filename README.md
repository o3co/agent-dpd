# DPD — Decompose-Propagate Decision - A method for organizing thoughts with AI

[日本語](README.ja.md)

Ever had a sudden insight, or pivoted what you're aiming for mid-AI-conversation, only to find the AI lost the thread?

DPD turns the dialogue into a graph and organizes the thinking process toward your goal. From there, the AI itself helps you surface goal changes, missing considerations, and contradictions with what was said earlier.

Implementation: a Claude Code skill + MCP server.

> **Status**: `0.x` — pre-1.0, public surface may still change. See [docs/concept.md](docs/concept.md#status-and-versioning) for details.

---

## Install

Requires Python 3.11+ and [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

```bash
curl -fsSL https://raw.githubusercontent.com/o3co/agent-dpd/main/install.sh | bash
```

That single command clones the repo (to `~/agent-dpd` by default), creates a venv, installs the package, registers `dpd-mcp-server` with Claude Code, and symlinks the `/dpd` skill (plus sub-skills like `/dpd-status`, `/dpd-dump`, …) into `~/.claude/skills/`. Restart Claude Code afterwards so the new MCP server and skills become discoverable.

If you'd rather review the script first:

```bash
git clone https://github.com/o3co/agent-dpd.git
cd agent-dpd
./install.sh
```

### Environment overrides

| Variable | Default | Purpose |
| --- | --- | --- |
| `DPD_INSTALL_DIR` | `$HOME/agent-dpd` | Where to clone the repo |
| `DPD_PYTHON` | `python3.11` | Python interpreter to use |
| `DPD_NO_REGISTER` | unset | Set to skip Claude Code MCP registration |
| `DPD_NO_SKILL_LINK` | unset | Set to skip symlinking skills into `~/.claude/skills/` |

Manual steps (no install.sh) are in [AGENTS.md](AGENTS.md#setup).

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
| `/dpd-dump` | Full graph as tree or Mermaid diagram |
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

Walked-through use cases — actual graphs built with the real MCP tools, transcripts and Mermaid included — live in [`docs/examples.md`](docs/examples.md):

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
- **[skill/README.md](skill/README.md)** — Skill family overview (main `/dpd` + sub-skills)
- **[AGENTS.md](AGENTS.md)** — Contributor guidelines (TDD, review workflow, conventions)

---

## License

Apache 2.0 — see [LICENSE](LICENSE). Copyright © 2026 [1o1 Co. Ltd.](https://1o1.co.jp/)

Contributions welcome; read [AGENTS.md](AGENTS.md) before opening a PR.
