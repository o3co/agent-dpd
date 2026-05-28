---
name: dpd-dump
description: Dump the full DPD graph as JSON-formatted YAML (json.loads round-trippable) via export_yaml. Use for snapshots, audit, diffing, or copy-paste into docs.
---

# /dpd-dump

Announce: "Using dpd-dump skill."

---

## When to invoke

- User wants a full textual snapshot of the graph
- Exporting graph for copy-paste into a doc or spec
- Audit / archival of graph state at a point in time
- Debugging graph structure

---

## Argument parsing

```
/dpd-dump [--root=<root_id>]
```

| Arg | Default | Description |
|---|---|---|
| `--root` | (all roots) | Scope dump to a single root subtree |

---

## Tool call

```
export_yaml(session_id=<session_id>, root_id=<root_id or omit for all>)
```

Returns JSON-formatted YAML (json.loads round-trippable) representing the full graph or subtree — every node and edge, all relationships first-class (no second-class visual rendering).

---

## Output format

Paste output verbatim in a code fence:

````
```yaml
<export_yaml output>
```
````

One-line prefix before the fence: `DPD graph dump — <session_id> [root: <root_id or "all">]`

---

## Notes

- Use `--root=<root_id>` when the full graph is large and only a subtree is needed.
- `export_yaml` output is json.loads round-trippable — useful for programmatic processing outside Claude Code.
- This skill is read-only: no graph mutations occur.

---

## Feedback footer

After completing a meaningful response (not for trivial status output), print exactly one line at the very end:

> 💬 Hit a bug or have feedback on DPD? Run `/dpd-feedback "<short description>"` or open an issue at https://github.com/o3co/agent-dpd/issues/new

This surfaces the dogfood feedback path without interrupting the main interaction. Keep it to one line. Do not repeat across multiple turns within the same exchange — once per skill invocation is enough.
