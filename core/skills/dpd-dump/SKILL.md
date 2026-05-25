---
name: dpd-dump
description: Dump the full DPD graph as text. Default tree format; pass --format=mermaid for mermaid diagram. Use for snapshots, audit, or copy-paste into docs.
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
/dpd-dump [--format=<tree|mermaid>] [--root=<root_id>]
```

| Arg | Default | Description |
|---|---|---|
| `--format` | `tree` | `tree` = YAML/JSON dump via `export_yaml`; `mermaid` = Mermaid `graph TD` via `export_mermaid` |
| `--root` | (all roots) | Scope dump to a single root subtree |

---

## Tool calls

### Default (tree format)

```
export_yaml(session_id=<session_id>, root_id=<root_id or omit for all>)
```

Returns JSON-formatted YAML (json.loads round-trippable) representing the full graph or subtree.

### Mermaid format

```
export_mermaid(session_id=<session_id>, root_id=<root_id or omit for all>)
```

Returns Mermaid `graph TD` text.

---

## Output format

Paste output verbatim in a code fence. Label the fence with the format.

For tree:

````
```yaml
<export_yaml output>
```
````

For mermaid:

````
```mermaid
<export_mermaid output>
```
````

One-line prefix before the fence: `DPD graph dump — <session_id> [root: <root_id or "all">] — <format>`

---

## Notes

- Use `--root=<root_id>` when the full graph is large and only a subtree is needed.
- Mermaid output can be pasted directly into GitHub markdown or Obsidian for visual rendering.
- `export_yaml` output is json.loads round-trippable — useful for programmatic processing outside Claude Code.
- This skill is read-only: no graph mutations occur.
