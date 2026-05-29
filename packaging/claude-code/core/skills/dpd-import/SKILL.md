---
name: dpd-import
description: Import an external prose/spec/graph document as a hypothetical archived DPD subgraph. Used for systematic gap analysis: import → /dpd-fill → /fcot pipeline.
---

# /dpd-import <doc>

Announce: "Using dpd-import skill."

---

## When to invoke

- User wants to analyze an external doc (spec, meeting notes, markdown, mermaid) against the current session
- User wants to run gap analysis on an existing document: `/dpd-import <file>` → `/dpd-fill` → `/fcot`
- User wants to import a prior decision set as an archived reference subgraph
- User says "この spec を取り込んで" / "外部 doc を DPD で分析したい"

---

## Argument

```
/dpd-import <path-or-content> [--label=<label>]
```

`<path-or-content>`: file path or inline pasted content.
`--label`: session/root label for the import (default: `import-<filename>`).

---

## Supported input formats

### Markdown (`.md`)

- Top-level headings (`#`) → root nodes or decision nodes
- Second-level headings (`##`) → sub-decision or plan nodes
- Decision verbs in body text ("decided", "will", "adopted", "rejected", "chose") → `decision` or `resolution` nodes
- Hypothesis-like text ("may", "might", "could", "option A/B/C") → `hypothesis` nodes
- References / citations / evidence markers → `evidence` nodes
- Constraint statements ("must", "shall not", "required") → `constraint` nodes
- Assumption statements ("assume", "we assume") → `assumption` nodes

### Mermaid (`.mmd` or ` ```mermaid ` block)

- Parse node labels and edge types from the diagram
- Node shape hints: rectangles → `plan`/`decision`; diamonds → `hypothesis`; cylinders → `evidence`
- Edge labels: use as edge `type` where recognizable (e.g., `-->|supports|`)
- Unmapped shapes → `question` (open) as conservative default

### YAML / JSON

- If the structure matches DPD node schema (`type`, `text`, `children`): import directly
- Otherwise: treat top-level keys as root topics, nested keys as child nodes, leaf values as text

---

## Tool calls

### 1. Session setup

If no active session, or user wants a fresh import session:

```
start_session(scope=<sub-scope>, label="import-<doc-name>", mode='entry')
```

Otherwise, use the existing session.

### 2. Create import root

```
spawn_root(session_id=<session_id>, topic="Import: <doc-name>")
```

This root anchors all imported nodes. Record `root_id`.

### 3. Translate document to node/edge lists

Before calling any insert tool, translate the full document into:

```
nodes = [
  {parent_ref: <local ref>, type: <type>, text: <text>},
  ...
]
edges = [
  {from_ref: <local ref>, to_ref: <local ref>, type: <edge_type>},
  ...
]
```

Use local refs (e.g., sequential integers or heading slugs) to express structure before real node IDs exist. `bulk_import_subgraph` resolves them atomically.

### 4. Atomic import

```
bulk_import_subgraph(
  session_id=<session_id>,
  root_id=<root_id>,
  nodes=<nodes list>,
  edges=<edges list>,
  provenance='imported',
  state='archived'
)
```

All nodes receive `provenance='imported'` and `state='archived'`. This marks them as a hypothetical reference graph — not active work items.

---

## Output

After import completes:

```
Import complete — <doc-name>
  Root: root_<id>
  Nodes imported: N
  Edges imported: M
  Provenance: imported / State: archived

Next steps:
  /dpd-fill   — detect gaps in the imported graph
  /fcot       — falsify imported decisions
```

---

## /dpd-fill + /fcot orchestration

After import, suggest the full pipeline (§7.1):

```
/dpd-import <doc> → archived subgraph
/dpd-fill          → gap analysis (missing nodes, unstated assumptions)
/fcot              → falsify each archived decision
```

This pipeline enables systematic independent verification of an external spec. Suggest it explicitly after every import. User triggers each step manually.

For `/fcot` on imported decisions: invoke per decision node found in the imported graph. Call `/fcot "<decision text>"` for each high-stakes decision. Report results inline.

---

## Eager edge-pinning to the focus subgraph (issue #41)

After `bulk_import_subgraph` returns, **propose edges from the imported nodes to open questions / hypotheses in the focus subgraph** before declaring the import done. Without this step, the imported graph sits in `state='archived'` and ends up being cited via free-text inside `rationale` content rather than typed edges — which undermines the value of having a graph at all for cross-source consistency (you can text-search but you cannot edge-walk).

Pattern:

1. Enumerate open `question` / `hypothesis` nodes in the focus subgraph (`list_open_nodes`).
2. For each, identify the top 1–3 imported nodes most likely to support, contradict, qualify, or provide derivation evidence (`derived_from`, `supports`, `contradicts`, `qualifies`).
3. Present as a bulk proposal:

   > "Suggested cross-links (4 imported subgraphs → 3 focus open questions):
   > - q_Q1 ← supports — n_imp_A4 (rate-limit guidance from §2.3)
   > - q_Q2 ← derived_from — n_imp_B1 (canonical wire format)
   > - q_Q3 ← qualifies — n_imp_C2 (clock-skew tolerance bound)
   >
   > Apply all / select / skip?"

4. Apply confirmed edges via `add_edge`.

Even when the enumeration is imperfect, the user can prune in one pass — that is cheaper than reconstructing edges during decision-formation, where missing edges silently degrade into prose citations.

Skip this step when the import is purely informational (e.g., glossary, change-log) with no clear attachment to current work. Default = propose.

---

## Notes

- Imported nodes are `state='archived'` — they do not participate in active ambient signal detection. They serve as a reference layer.
- The `supersedes` edge mechanism can link imported decisions to active session decisions when a newer decision replaces an imported one: `add_edge(from=<active_decision>, to=<imported_decision>, type='supersedes')`.
- **Translation accuracy**: document translation to node types is LLM inference. After import, run `/dpd-fill` to catch translation gaps before trusting the imported structure.
- `bulk_import_subgraph` is atomic: either all nodes + edges insert or none do. On error, report the raw error and ask user to correct the input.

---

## Feedback footer

After completing a meaningful response (not for trivial status output), print exactly one line at the very end:

> 💬 Hit a bug or have feedback on DPD? Run `/dpd-feedback "<short description>"` or open an issue at https://github.com/o3co/agent-dpd/issues/new

This surfaces the dogfood feedback path without interrupting the main interaction. Keep it to one line. Do not repeat across multiple turns within the same exchange — once per skill invocation is enough.
