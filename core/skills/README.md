# dpd / skill — Claude Code skill family

[日本語](README.ja.md)

The conversational UX layer for DPD. The skill family interprets user intent ("/dpd", "this kept coming up so I want to log it"), proposes graph updates, and proxies confirmed updates into MCP tool calls. All graph state lives in the [MCP server](../mcp/) — the skill is stateless.

## Role

If the [`mcp/`](../mcp/) server is the protocol's "kernel" (state + tools), the skill is its "shell" (prompts + UX). The split is deliberate:

- **The skill decides *what to propose*** based on conversation context (decision verbs, hypothesis clusters, topic shifts). Detection logic, narrowing rules, and pause heuristics live here.
- **The MCP server decides *what is valid*** (parent-kind consistency, lifecycle monotonicity, atomic branch resolution). Schema and invariants live there.
- **Neither side stores conversation history**. Conversation lives in Claude Code's session transcript; graph state lives in SQLite.

## Family

The family is one entry skill plus a handful of explicit sub-skills the user can invoke directly.

### Entry skill: `/dpd`

[`SKILL.md`](SKILL.md) is the main `/dpd` skill. It governs the **full operating lifecycle**:

- **Bottom-up trigger** — user fires `/dpd` when they sense the conversation needs organizing.
- **Claude-suggested trigger** — Claude may volunteer a soft suggestion when it detects tangle (multiple open threads, decision verbs without anchor, contradictions surfacing).
- **Startup sequence** — sub-scope detection (`.dpdrc` walk-up or `--scope=<name>` arg), session list, resume-vs-new prompt.
- **Entry phase** — conversation summarization, goal narrowing, initial graph construction with grounded/inferred stratification.
- **Ambient mode** — steady-state observation: detect signals, evaluate attachment, accumulate pending updates, propose at natural pauses with custodial tone.
- **End achievement** — `mark_reached` proposal when achievement_conditions are satisfied + Pool disposition.

Read [`SKILL.md`](SKILL.md) in full before extending the behavior. It encodes design decisions that go beyond "what the code does" — particularly the End modification gate, drift detection, and Pool reject identity rules.

### Sub-skills

Each sub-skill is a directory with its own `SKILL.md`. They are independently invokable (e.g., `/dpd-status`) and the main `/dpd` skill may delegate to them implicitly.

| Sub-skill | Purpose |
| --- | --- |
| [`dpd-status`](dpd-status/) | Snapshot of current session: active roots, focus node, Pool items (active + rejected), session mode. Answers "where are we?" |
| [`dpd-dump`](dpd-dump/) | Dump the full DPD graph as JSON-formatted YAML (`export_yaml`, json.loads round-trippable). For audit, snapshots, diffing, or pasting into docs. |
| [`dpd-summary-md`](dpd-summary-md/) | Extract decided / closed items and render as markdown summary. For session wrap-up or producing spec material from a settled subgraph. |
| [`dpd-edit`](dpd-edit/) | Manual edit of a node or Pool item — wraps `close_node`, `add_node(provenance='manual')`, `pool_reject`, Pool unsuppress. Used when the user wants direct control beyond ambient mode. |
| [`dpd-fill`](dpd-fill/) | Generate inferred nodes for the current graph: missing decompositions, unstated assumptions, gap candidates. Each inferred node requires user opt-in. Often paired with `/fcot` for falsification. |
| [`dpd-import`](dpd-import/) | Import an external prose/spec/graph document as a hypothetical archived DPD subgraph. Used in the `dpd-import → dpd-fill → /fcot` pipeline for systematic gap analysis (e.g., self-validating a spec). |
| [`dpd-verify-edge`](dpd-verify-edge/) | Externally verify a `layer='necessary'` edge (proof-tree discipline, #42) via a context-stripped prompt, so an independent verifier judges the implication without rubber-stamping. Records the verdict; `refuted` proposes (never auto-applies) a downgrade. |

The `dpd-import → dpd-fill → /fcot` pipeline is **one documented** self-validation flow — the same one the v0.3.1 spec itself was checked against. `/fcot` runs automatically on high-stakes inferred nodes and is opt-in elsewhere, so the pipeline's verification cost scales with the rigor you need. See the top-level [`README.md`](../README.md#built-agent-driven-with-dpd) for the dogfood narrative.

## Installation

The skill family is deployed to `~/.claude/skills/dpd/` (and per-sub-skill subdirectories like `~/.claude/skills/dpd-status/`) for Claude Code to discover. The repo-root `install.sh` does this automatically by symlinking — see the [top-level README](../README.md#install) for the one-liner, or [AGENTS.md](../AGENTS.md#setup) for the manual symlink commands. Set `DPD_NO_SKILL_LINK=1` if you want install.sh to skip this step.

After install, restart Claude Code; the `/dpd`, `/dpd-status`, `/dpd-dump`, etc. invocations should become available.

## Relationship to the MCP server

The skill's `SKILL.md` references the MCP tools by their qualified names (`mcp__dpd-mcp-server__list_sessions`, etc.). If the server is not registered (see [`../mcp/README.md`](../mcp/README.md)), the skill detects the absence on startup and instructs the user to install it before proceeding.

The skill never persists data outside the MCP server. Anything you see the skill render — graph diagrams, session lists, pool snapshots — is freshly read from the server on each turn. This is why graph state survives Claude Code restarts: the skill carries no memory of its own.

## Versioning

The skill version tracks the MCP server major.minor (currently `0.3.x`). Skill updates that require server changes are coordinated as one PR per the [AGENTS.md](../AGENTS.md) workflow.
