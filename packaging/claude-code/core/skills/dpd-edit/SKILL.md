---
name: dpd-edit
description: Manual edit of a DPD node or pool item. Wraps close_node / add_node(provenance='manual') / pool_reject / pool unsuppress (= clear rejected_at). Use when user wants direct control beyond ambient mode.
---

# /dpd-edit <node_id|pool_id>

Announce: "Using dpd-edit skill."

---

## When to invoke

- User wants to close, add, or mutate a node directly without waiting for ambient detection
- User wants to reject, drop, or unsuppress a Pool item
- Correcting a misclassification that ambient mode introduced
- Any explicit manual graph surgery

---

## Argument parsing

```
/dpd-edit <id> [<operation>] [<additional args>]
```

`<id>` is either a `node_id` (e.g., `node_abc123`) or a `pool_id` (e.g., `pool_def456`). Determine target type from the prefix: `pool_` = pool item, anything else = node.

If `<operation>` is omitted, present available operations for the target and ask user to choose.

---

## Node operations

### Close a node

Use when user wants to mark a node resolved, rejected, or invalidated.

```
close_node(
  session_id=<session_id>,
  node_id=<node_id>,
  closure_reason=<"resolved" | "rejected" | "invalidated">
)
```

After: one-line summary — `closed <node_id>: <text> [<closure_reason>]`

### Add a child node (manual)

Use when user wants to add a node directly, not from conversation inference.

```
add_node(
  session_id=<session_id>,
  parent_id=<node_id>,
  type=<node_type>,
  text=<text>,
  provenance='manual'
)
```

`provenance='manual'` marks this as a direct user edit with no conversation trace (spec §9.3).

After: one-line summary — `added <new_node_id> under <parent_id>: <text>`

### Edit node text (v0.3.1 gap)

**No `update_node_text` tool exists in v0.3.1.** Workaround:

1. Close the existing node with `closure_reason='invalidated'`.
2. Add a new sibling or child node with the corrected text via `add_node(provenance='manual')`.
3. If the old node has children that should transfer, re-add them under the new node.

Flag: this gap is tracked for v0.3.2+. Report to user if they request text-only edit.

---

## Pool operations

### Reject a Pool item (soft suppress)

Marks item with `rejected_at` + `rejected_reason`. Item stays for audit. Excluded from default `pool_list` but visible via `pool_list(include_rejected=True)`.

```
pool_reject(pool_id=<pool_id>, reason=<reason string>)
```

After: `rejected <pool_id>: <text>`

### Unsuppress a Pool item (clear rejected_at)

User wants to re-evaluate a previously rejected item. Returns it to "attach undetermined" state.

**No `pool_unreject` tool exists in v0.3.1.** Workaround:

1. Call `pool_drop(pool_id, reason="superseded by unsuppress workaround")` to remove the rejected entry.
2. Re-add the item: `pool_add(text=<original text>, scope=<scope>)`.

The new pool_id will differ. Inform user of the ID change.

Flag: `pool_unreject` tool is tracked for v0.3.2+.

### Drop a Pool item (physical removal)

Use when the item is surplus and should be permanently removed.

```
pool_drop(pool_id=<pool_id>, reason=<reason string>)
```

After: `dropped <pool_id>: <text>`

---

## Node type vocabulary (reference)

When adding nodes, use one of these types:

| Category | Types |
|---|---|
| Problem (open) | `question`, `plan`, `hypothesis`, `goal`, `problem` |
| Solution (close) | `answer`, `action`, `verification`, `decision`, `resolution` |
| Support | `evidence`, `constraint`, `assumption`, `rationale`, `risk` |
| Structural | `start`, `end` |
| Spec-import (#63) | `claim`, `requirement`, `open_question` |

---

## Notes

- All operations here are **intentional / user-explicit** — not proposed by ambient mode. No confirmation step is required beyond what the user already stated.
- After any mutation, update pending updates in-session memory if any related proposals were buffered.
- **v0.3.1 gaps**: `update_node_text` and `pool_unreject` do not exist. Workarounds documented above. Both are planned for v0.3.2+.

---

## Feedback footer

After completing a meaningful response (not for trivial status output), print exactly one line at the very end:

> 💬 Hit a bug or have feedback on DPD? Run `/dpd-feedback "<short description>"` or open an issue at https://github.com/o3co/agent-dpd/issues/new

This surfaces the dogfood feedback path without interrupting the main interaction. Keep it to one line. Do not repeat across multiple turns within the same exchange — once per skill invocation is enough.
