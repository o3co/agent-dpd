# DPD — Decompose-Propagate Decision

[日本語](README.ja.md)

A Claude Code skill + MCP server that turns long branching AI conversations into an explicit decision graph. Decisions get recorded with their evidence, rejected hypotheses don't get lost, and "what did we decide about X" stops being a transcript-search problem.

> **Status**: `0.x` — pre-1.0, public surface may still change. See [docs/concept.md](docs/concept.md#status-and-versioning) for details.

---

## Install

Requires Python 3.11+, [Claude Code](https://docs.anthropic.com/en/docs/claude-code), and `make`.

```bash
git clone https://github.com/o3co/agent-dpd.git
cd agent-dpd
make dev          # creates venv + installs + registers with Claude Code
```

Restart Claude Code so the `/dpd` skill becomes available. Manual install steps (no Make) are in [AGENTS.md](AGENTS.md#setup).

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

### Example: self-validating a spec

The `/dpd-import → /dpd-fill → /fcot` pipeline gives you systematic gap analysis on any design document:

```text
/dpd-import path/to/your-spec.md
    # imports the spec as an archived subgraph

/dpd-fill
    # generates inferred nodes — missing decompositions, unstated assumptions

/fcot
    # falsifies each inferred node against the spec text
    # → real gaps survive, plausible-but-already-covered ones get filtered out
```

This is how we validated DPD's own spec before release — see [docs/concept.md#built-agent-driven-with-dpd](docs/concept.md#built-agent-driven-with-dpd) for the story and what we found.

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

## Learn more

- **[docs/concept.md](docs/concept.md)** — What DPD is, why it exists, how the graph works, lifecycle states, the agent-driven dogfood story
- **[mcp/README.md](mcp/README.md)** — MCP server architecture + 30-tool reference
- **[skill/README.md](skill/README.md)** — Skill family overview (main `/dpd` + sub-skills)
- **[AGENTS.md](AGENTS.md)** — Contributor guidelines (TDD, review workflow, conventions)

---

## License

Apache 2.0 — see [LICENSE](LICENSE). Copyright © 2026 [1o1 Co. Ltd.](https://1o1.co.jp/)

Contributions welcome; read [AGENTS.md](AGENTS.md) before opening a PR.
