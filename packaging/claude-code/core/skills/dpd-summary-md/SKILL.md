---
name: dpd-summary-md
description: Extract decided / closed items from the DPD graph and render as markdown summary. Use at session wrap-up or to produce spec material from a complete subgraph.
---

# /dpd-summary-md

Announce: "Using dpd-summary-md skill."

---

## When to invoke

- User wants a markdown export of decisions made during a DPD session
- Session wrap-up: converting graph state into a shareable doc
- Producing spec material from a completed subgraph
- User asks "決定事項をまとめて" / "summary of what we decided"

---

## Argument parsing

```
/dpd-summary-md [--root=<root_id>]
```

| Arg | Default | Description |
|---|---|---|
| `--root` | (all active roots) | Scope summary to a single root subtree |

---

## Tool calls

### 1. Enumerate roots

If no `--root` specified:

```
list_active_roots(session_id=<session_id>)
```

Returns all roots with `lifecycle=active`. Process each root in step 2.

### 2. Walk each root subtree

For each root (or the specified root):

```
walk_subtree(session_id=<session_id>, root_id=<root_id>)
```

Returns all descendants pre-order.

### 3. Filter

From the walk result, select nodes where **both** conditions hold:

- `state = 'closed'`
- `type` in `{'decision', 'resolution', 'answer'}`

These are the "decided" items. Also collect their immediate rationale children (`type='rationale'`, any state) for context.

---

## Output format

Render as markdown. Output to stdout — user can copy-paste.

```markdown
# DPD Summary — <session.label or session_id>
Generated: <date>

## <root.topic>

### <decision/resolution/answer node text>

- **Type**: <decision | resolution | answer>
- **Closed as**: <closure_reason>
- **Rationale**: <rationale node text, or "(none recorded)">
- **Node**: `<node_id>`

### <next decision>
...

---

## <next root.topic>
...
```

One heading per root, one subheading per closed decision/resolution/answer. Hierarchical order follows walk_subtree pre-order (parent decisions before children).

If no closed decision nodes exist: `No decided items found in this session.`

---

## Notes

- This skill is **read-only**: no graph mutations.
- `provenance='imported'` nodes (from `/dpd-import`) are included if they match the filter — useful for comparing imported decisions against session decisions.
- For a full graph dump (including open nodes), use `/dpd-dump` instead.
- Rationale nodes are surfaced inline for context but are not themselves headings.

---

## Feedback footer

After completing a meaningful response (not for trivial status output), print exactly one line at the very end:

> 💬 Hit a bug or have feedback on DPD? Run `/dpd-feedback "<short description>"` or open an issue at https://github.com/o3co/agent-dpd/issues/new

This surfaces the dogfood feedback path without interrupting the main interaction. Keep it to one line. Do not repeat across multiple turns within the same exchange — once per skill invocation is enough.
