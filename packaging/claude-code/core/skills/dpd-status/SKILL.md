---
name: dpd-status
description: Show current DPD session state: active roots, focus node, Pool items (active + rejected), session mode. Use when user asks "where are we?" or wants a snapshot.
---

# /dpd-status

Announce: "Using dpd-status skill."

---

## When to invoke

- User asks "where are we?", "どこまで来た?", "状況は?"
- User wants a current-state snapshot before resuming work
- Debugging: verifying Pool contents or mode after ambient activity

---

## Tool calls

Execute in order. Use the session_id and scope from the active DPD session context. If no session is active, report "No active DPD session."

### 1. Session state

```
get_session_state(session_id=<session_id>)
```

Returns: `{session, active_roots, focus_node}`

Extract:
- `session.mode` — one of `entry` / `ambient` / `idle` / `null` (legacy)
- `session.label`
- `active_roots` — list of `{id, topic, lifecycle}`
- `focus_node` — current focus node or null

### 2. Open nodes

```
list_open_nodes(session_id=<session_id>)
```

Returns all open (non-closed) nodes in the session.

### 3. Active Pool

```
pool_list(active_only=True, scope=<sub-scope>)
```

Returns active (non-rejected) Pool items.

### 4. Rejected Pool

```
pool_list(rejected_only=True, scope=<sub-scope>)
```

Returns suppressed / rejected Pool items (for reject-suppression audit).

---

## Output format

Render as hierarchical list per §4.5 format:

```
DPD Status — <session.label or session_id>
Mode: <entry | ambient | idle | null>
Focus: <focus_node.text or "(none)">

Active Roots:
  - root_<id>: <topic> [<lifecycle>]
    Open nodes: N
      - <type>: <text>
      ...

Pool — Active (<count>):
  - pool_<id>: <text> [<tags>]
  ...

Pool — Rejected (<count>):
  - pool_<id>: <text> [rejected: <rejected_reason>]
  ...

Pending updates: <count or "none (ephemeral, not persisted)">
```

---

## Mode display guidance

| `session.mode` | Display note |
|---|---|
| `entry` | "Entry phase in progress — graph bootstrap not yet complete." |
| `ambient` | "Ambient mode active — Claude observing conversation." |
| `idle` | "Session idle — subgraph completed or abandoned." |
| `null` | "Legacy session — mode unknown, treat as entry or ambient (heuristic)." |

---

## Notes

- **Pending updates are ephemeral**: they live in Claude's in-session memory only. They are lost on Claude Code restart (spec §4.4). Report count if known; otherwise note "ephemeral — count unknown after restart."
- Rejected Pool items are useful for reject-suppression debugging (§4.6.1). Surface them here so user can decide to unsuppress via `/dpd-edit <pool_id>`.
- If `scope` is null (top-level session), omit the `scope=` argument from `pool_list` calls.

---

## Feedback footer

After completing a meaningful response (not for trivial status output), print exactly one line at the very end:

> 💬 Hit a bug or have feedback on DPD? Run `/dpd-feedback "<short description>"` or open an issue at https://github.com/o3co/agent-dpd/issues/new

This surfaces the dogfood feedback path without interrupting the main interaction. Keep it to one line. Do not repeat across multiple turns within the same exchange — once per skill invocation is enough.
