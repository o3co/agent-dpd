---
name: dpd
description: Bootstrap a DPD (Decompose-Propagate Decision) graph-mode session — structures multi-step decisions, design analyses, or any thread that benefits from explicit decompose/propagate tracking. Invoke when the user runs /dpd, or proactively suggest when a conversation has accumulated several implicit hypotheses/decisions that would benefit from explicit graph tracking. Requires the dpd-mcp-server MCP server to be registered with Claude Code.
---

# DPD: Decompose-Propagate Decision

Announce: "Using dpd skill to bootstrap a DPD session."

DPD is a graph-based protocol for structuring decision dialogues. Graph state lives in an MCP server (sqlite per agent scope); this skill manages startup UX (resume vs new) and the per-turn graph-mode interaction loop.

## Prerequisites

The `dpd-mcp-server` MCP server must be registered with Claude Code. If its tools (e.g., `mcp__dpd-mcp-server__list_sessions`) are not available, stop and tell the user to register it:

```
claude mcp add dpd-mcp-server -- python -m dpd_mcp_server
# or add to .mcp.json manually
```

## Startup sequence (spec §8.3)

### 1. Detect sub-scope from cwd

Walk up from cwd to find the nearest `scope.yaml`. The directory containing it is the sub-scope; its `name:` value is the sub-scope identifier. If no `scope.yaml` ancestor exists, sub-scope = null (top-level).

```bash
dir="$(pwd)"
while [ "$dir" != "/" ]; do
  [ -f "$dir/scope.yaml" ] && { basename "$dir"; break; }
  dir="$(dirname "$dir")"
done
```

User override: if the user passes `/dpd --scope=<name>`, use that verbatim instead of cwd walk-up.

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
2. Issue MCP tool calls in serial.
3. Render a concise summary of the diff (not raw JSON).

Typical operations:

- New topic from scratch → `spawn_root(topic=...)`
- Child under existing parent → `add_node(parent_id, type, text)`
- Mark resolved → `close_node(node_id, closure_reason)`
- Inspect → `get_node`, `walk_subtree`, `list_active_roots`

## Node type vocabulary (spec §2.2)

The server enforces these via CHECK constraint. Pick the type that best fits the rhetorical role:

| Side | Examples |
|---|---|
| **Problem (open-flavor)** | `question`, `plan`, `hypothesis`, `goal`, `problem` |
| **Solution (close-flavor)** | `answer`, `action`, `verification`, `decision`, `resolution` |
| **Support** | `evidence`, `constraint`, `assumption`, `rationale`, `risk` |

`closure_reason` is one of `resolved` / `rejected` / `invalidated`.

## Tool reference (`dpd-mcp-server`)

| Tool | Purpose |
|---|---|
| `start_session(scope?, label?)` | Begin new session, returns `session_id` |
| `list_sessions(scope?)` | List sessions for sub-scope (most recent first) |
| `get_session_state(session_id)` | Session + active_roots + focus_node |
| `spawn_root(session_id, topic, reason?)` | Create new root topic |
| `add_node(session_id, parent_id, type, text)` | Add child node under root or node |
| `close_node(session_id, node_id, closure_reason)` | Mark resolved/rejected/invalidated |
| `get_node(session_id, node_id)` | Fetch single node |
| `walk_subtree(session_id, root_id)` | All descendants of root (pre-order) |
| `list_active_roots(session_id)` | Roots with lifecycle=active |

## Tone

Graph mode is a structural overlay on the conversation. Keep responses tight: after each tool call, give the user a one-line `<verb> <node-id>: <short text>` rather than narrating. The structure is the value, not the prose.
