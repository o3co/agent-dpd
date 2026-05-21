---
name: dpd
description: Bootstrap a DPD (Decompose-Propagate Decision) graph-mode session — structures multi-step decisions, design analyses, or any thread that benefits from explicit decompose/propagate tracking. Invoke when the user runs /dpd, or proactively suggest when a conversation has accumulated several implicit hypotheses/decisions that would benefit from explicit graph tracking. Requires the dpd-mcp-server MCP server to be registered with Claude Code.
---

# DPD: Decompose-Propagate Decision

Announce: "Using dpd skill to bootstrap a DPD session."

DPD is a graph-based protocol for structuring decision dialogues. Graph state lives in an MCP server (sqlite per agent scope); this skill manages startup UX (resume vs new) and the per-turn graph-mode interaction loop.

## Prerequisites

The `dpd-mcp-server` package must be installed in the Python env Claude Code uses, AND registered as an MCP server. If its tools (e.g., `mcp__dpd-mcp-server__list_sessions`) are not available, stop and instruct the user to set it up:

**1. Install the package** (from this monorepo's `server/` dir):

```bash
pip install -e ./server
```

This exposes the `dpd-mcp-server` console script declared in `server/pyproject.toml`.

**2. Register with Claude Code**:

```bash
claude mcp add dpd-mcp-server -- dpd-mcp-server
# alternative: python -m dpd_mcp_server (same effect)
# or edit .mcp.json directly
```

**3. Restart Claude Code** so the tools become discoverable. If `mcp__dpd-mcp-server__list_sessions` etc. still don't appear, re-verify both steps before continuing.

## Startup sequence (spec §8.3)

### 1. Detect sub-scope

Resolve in this priority (first match wins):

1. **Explicit `--scope=<name>` argument**: when the user invokes `/dpd --scope=<name>`, use that verbatim. This is the most reliable signal — use it whenever provided.
2. **cwd walk-up to `scope.yaml`**: from cwd, walk parents looking for `scope.yaml`. Read its declared `name:` field (NOT the directory basename, which may differ).
3. **Fallback**: sub-scope = null (top-level session).

The override priority matters: when claude is launched from a workspace **above** the sub-scope (e.g., `/Volumes/Workspace/scopes/mcp/` while wanting to work in `decompose-propagate.protocol`), walk-up alone returns null and silently routes the work to top-level. Always check for an explicit `--scope=` first.

```bash
# walk-up implementation (only when explicit override absent)
dir="$(pwd)"
while [ "$dir" != "/" ]; do
  if [ -f "$dir/scope.yaml" ]; then
    name=$(grep -E '^name:[[:space:]]*' "$dir/scope.yaml" | head -1 \
             | sed 's/^name:[[:space:]]*//; s/[[:space:]]*$//')
    echo "${name:-$(basename "$dir")}"
    break
  fi
  dir="$(dirname "$dir")"
done
```

### 2. List existing sessions

Call `list_sessions` with the detected scope (omit `scope` argument entirely for top-level). The server auto-resolves the agent scope from MCP roots — do NOT pass `agent_scope`.

### 3. Resume vs new (NEVER auto-resume)

- **0 existing sessions** → call `start_session(scope=<sub-scope>, label=<optional>)`.
- **1+ existing sessions** → ask the user via `AskUserQuestion`:
  - (a) Resume most recent (show its label + started_at)
  - (b) Resume a specific session (show full list)
  - (c) Start a new session

Auto-resume is forbidden: it pollutes a stale session with unrelated work.

### 4. On resume — brief the AI with state

Call `get_session_state(session_id=<chosen>)`. Returns `{session, active_roots, focus_node}`. Summarize internally:

```
sub_scope   = <session.scope or "(top-level)">
session_id  = <session.id>
label       = <session.label or "(unlabeled)">
focus       = <focus_node.text or "(none set)">
active_roots: [<{id, topic, lifecycle}, ...>]
```

### 5. Graph-mode loop

Each user turn:

1. Identify graph operations implied by the user's message.
2. Issue MCP tool calls (parallel when independent, serial when ordered).
3. Render a concise summary of the diff (not raw JSON).

Common idioms:

- **New topic** → `spawn_root(topic=...)` (returns the full root row, no extra fetch needed)
- **Child under parent** → `add_node(parent_id, type, text)` (returns the full node row)
- **Pick from N hypothesis options (atomic)** → `resolve_hypothesis_branch(hyp_id, decision_text, rationale_text?)`. This is the **preferred closure path** for select-from-N decisions: it closes the chosen hypothesis as resolved, siblings as rejected, inserts decision + rationale, **and auto-inserts a `derived_from` edge from the decision to the accepted hypothesis** — all in a single transaction. Replaces the 5+ separate calls of the old close-each-individually pattern.
- **Mark a single node resolved** → `close_node(node_id, closure_reason)` for cases that aren't a hypothesis selection (e.g., closing an answer / verification / evidence).
- **Set focus for resume context** → `set_focus(node_id)` after a meaningful turn so the next session resumes pointed at the right place. Pass `node_id=null` to clear focus.
- **Retire a finished root** → `set_root_lifecycle(root_id, "archived")` once all the discussion under it is closed and you don't want it cluttering `list_active_roots`.
- **Pick next thing to work on (next_focus)** → `list_open_nodes(root_id=<recency-ranked root>)`, then pick the deepest open node.
- **Express cross-node relations** → `add_edge(from_node, to_node, type, reason?)` for `requires` / `blocks` / `derived_from` style links that don't fit the parent-child tree.
- **Inspect** → `get_node` / `walk_subtree` / `list_active_roots` / `list_edges`.

## Node type vocabulary (spec §2.2)

The server enforces these via CHECK constraint. Pick the type that best fits the rhetorical role:

| Side | Examples |
|---|---|
| **Problem (open-flavor)** | `question`, `plan`, `hypothesis`, `goal`, `problem` |
| **Solution (close-flavor)** | `answer`, `action`, `verification`, `decision`, `resolution` |
| **Support** | `evidence`, `constraint`, `assumption`, `rationale`, `risk` |

`closure_reason` is one of `resolved` / `rejected` / `invalidated`. Per-type intent:

| Type group | `resolved` | `rejected` | `invalidated` |
|---|---|---|---|
| `hypothesis` | adopted as decision | ruled out (sibling of accepted) | later found incoherent |
| `decision` / `answer` / `resolution` | final | (rarely applicable) | revoked / superseded |
| `question` / `plan` / `goal` / `problem` | the open thread is closed (answered / done) | abandoned without answer | the question itself was malformed |
| `evidence` / `rationale` / `constraint` / `assumption` | articulated and stands | (rarely applicable) | later found incorrect |
| `verification` / `action` | done | abandoned | later invalidated by new info |
| `risk` | mitigated / accepted | rejected (no longer a risk) | re-evaluated as different risk |

The `resolve_hypothesis_branch` tool encodes the most common closure pattern: target = `resolved`, siblings = `rejected`. Use `close_node` for everything else.

## Tool reference (`dpd-mcp-server`)

| Tool | Purpose |
|---|---|
| `start_session(scope?, label?)` | Begin new session, returns `session_id` |
| `list_sessions(scope?)` | List sessions for sub-scope (most recent first) |
| `get_session_state(session_id)` | Session + active_roots + focus_node |
| `spawn_root(session_id, topic, reason?)` | Create new root topic → `{root: {...}}` (full row) |
| `add_node(session_id, parent_id, type, text)` | Add child node under root or node → `{node: {...}}` (full row) |
| `close_node(session_id, node_id, closure_reason)` | Mark resolved/rejected/invalidated |
| `resolve_hypothesis_branch(session_id, hyp_id, decision_text, rationale_text?)` | **Atomic**: close target resolved + open siblings rejected + insert decision + auto-insert `derived_from` edge (decision → accepted hypothesis) + insert rationale (if any) |
| `set_focus(session_id, node_id?)` | Set/clear `focus_node_id`. Pass `node_id=null` to clear |
| `set_root_lifecycle(session_id, root_id, lifecycle)` | Transition `active` ↔ `archived` ↔ `deferred` |
| `list_open_nodes(session_id, root_id?)` | Open nodes in session (or within one root's subtree) |
| `add_edge(session_id, from_node, to_node, type, reason?)` | Insert a free-form-typed edge between nodes |
| `list_edges(session_id, from_node?, to_node?, type?)` | List edges (optional from/to/type filters, AND'd) |
| `list_unblocked_open_nodes(session_id, root_id?, blocker_edge_type?)` | Open nodes that no open node is blocking via the given edge type (default 'blocks') |
| `export_mermaid(session_id, root_id?)` | Render as Mermaid `graph TD` text (paste in markdown) |
| `export_yaml(session_id, root_id?)` | JSON-formatted YAML dump (json.loads round-trippable) |
| `get_node(session_id, node_id)` | Fetch single node |
| `walk_subtree(session_id, root_id)` | All descendants of root (pre-order) |
| `list_active_roots(session_id)` | Roots with lifecycle=active |

## Edge type vocabulary

Documented edge types and their direction conventions:

| Type | Direction (from → to) | Use |
| --- | --- | --- |
| `derived_from` | derived → source | Decision/evidence is derived from earlier node (e.g., `decision → hypothesis`, `new_finding → origin_decision`) |
| `supports` | supporter → supported | Evidence supports a decision/hypothesis |
| `contradicts` | contradictor → contradicted | Observation contradicts a decision/hypothesis |
| `qualifies` | qualifier → qualified | Finding limits or scopes a target decision without overturning it |
| `invalidates` | invalidator → invalidated | Finding shows a target decision's premise no longer holds |
| `blocks` | blocker → blocked | Dependency: blocker must close before blocked can be addressed |

**Direction rule**: from-side is the "active" side (supporting, contradicting, qualifying, deriving); to-side is the target. This matches `storage.py:457-463` for `derived_from` (`from=decision, to=hypothesis`).

## Cross-TBD post-hoc evidence (canonical form)

When working under one root reveals a finding that strengthens, qualifies, or undermines a decision already made in a different root, record the finding using this canonical form.

**Step 1 — Decide node-or-edge-only**

Ask: "Could this finding later be refined, extended, or objected to?"

- **YES** → make a node (Step 2 onward)
- **NO** → 1 edge only: `add_edge(from=origin_decision, to=target_decision, type=qualifies|invalidates|supports|contradicts)`. Done.

**Step 2 — Add the evidence node under the target root**

```text
add_node(
  session_id,
  parent_id = <target_root_id>,           # target root (physical proximity)
  type      = "evidence",                 # or "rationale" when appropriate
  text      = "<finding> (Discovered in <origin_root> during <origin_node>)"
)
→ new_node_id
```

**Step 3 — Valence edge to the target decision**

```text
add_edge(
  session_id,
  from = new_node_id,
  to   = <target_decision_id>,
  type = qualifies | invalidates | supports | contradicts,
  reason = "<short label>"
)
```

**Step 4 — Provenance edge from new node to the origin decision**

```text
add_edge(
  session_id,
  from = new_node_id,                     # the derived finding
  to   = <origin_decision_id>,            # the source it was derived from
  type = "derived_from",
  reason = "post-hoc finding from <origin_root>"
)
```

To later trace where a finding came from:

```text
list_edges(session_id, from_node=<new_node_id>, type="derived_from")
```

## Searching dissenting evidence

To find all contradicting findings in the session:

```text
list_edges(session_id, type="contradicts")
```

Combine with `to_node=<decision_id>` to find dissent against a specific decision.

## Tone

Graph mode is a structural overlay on the conversation. Keep responses tight: after each tool call, give the user a one-line `<verb> <node-id>: <short text>` rather than narrating. The structure is the value, not the prose.
