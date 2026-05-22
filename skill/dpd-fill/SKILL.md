---
name: dpd-fill
description: Generate inferred nodes for the current DPD graph: missing decompositions, unstated assumptions, gap candidates. Each inferred node requires user opt-in. Often paired with /fcot for falsification.
---

# /dpd-fill

Announce: "Using dpd-fill skill."

---

## When to invoke

- User wants gap analysis on the current graph
- User asks "何か抜けてる?" / "穴はある?" / "assumptions をチェックして"
- After `/dpd-import`, to detect gaps in the imported doc
- Before finalizing a subgraph (pre-`mark_reached` sanity check)

---

## Tool calls

### 1. Get session baseline

```
get_session_state(session_id=<session_id>)
```

Returns session mode, active roots, focus node.

### 2. Walk the graph

For each active root (or focus root if scoped):

```
walk_subtree(session_id=<session_id>, root_id=<root_id>)
```

Collect all nodes. Note: `state`, `type`, `provenance`, `text` for each.

### 3. Inspect Pool

```
pool_list(active_only=True, scope=<sub-scope>)
```

Pool items are observations not yet attached to the graph. Check for items that imply graph gaps.

---

## Inference pass

With the full graph + Pool in context, run the following prompts internally:

1. **Missing decompositions**: "Are there nodes that should have children but don't? Which open questions remain undecomposed?"
2. **Unstated assumptions**: "What assumptions are implicit in the existing decisions/answers that have no `assumption` node recorded?"
3. **Unexamined hypotheses**: "Are there hypotheses that were never explored or closed? Are there alternative hypotheses not surfaced?"
4. **Gap candidates**: "What relevant considerations appear absent from this graph entirely?"
5. **Pool signals**: "Do any Pool items imply a node or edge missing from the graph?"

Collect inferences. For each:
- Draft node: `type`, `text`, target `parent_id`, rationale for why it's missing
- Classify stakes: **high** (structural gap, affects decisions) / **low** (supplementary detail)

---

## User opt-in flow

Present all inferred additions as a numbered list **before** calling any tool:

```
/dpd-fill found N candidate inferred nodes:

1. [high] assumption under <parent>: "<text>"
   Why: <rationale>

2. [low] hypothesis under <parent>: "<text>"
   Why: <rationale>

...

Apply all? (Y/N/select numbers)
```

Wait for user response. Do NOT call `add_node` before user confirms.

On confirmation (full or partial), for each approved node:

```
add_node(
  session_id=<session_id>,
  parent_id=<parent_id>,
  type=<type>,
  text=<text>,
  provenance='inferred'
)
```

After each: one-line `added <node_id> [inferred]: <text>`

---

## /fcot orchestration

For each **high-stakes** inferred node (stakes = "high"), invoke `/fcot` after proposing but before applying:

```
/fcot "<inferred node text>"
```

`/fcot` will attempt to falsify the inference. If `/fcot` finds the inference unsound, downgrade or drop that candidate. Report `/fcot` verdict inline with the proposal.

Pattern:
```
1. [high] assumption: "<text>"
   Why: <rationale>
   /fcot result: <Confirmed sound | Falsified — <reason> | Quick check only>
```

For low-stakes nodes, `/fcot` is optional (user may request it explicitly).

---

## Notes

- All inferred nodes get `provenance='inferred'` — this distinguishes them from conversation-grounded (`grounded`) and manual edits (`manual`) in the graph audit trail.
- `/dpd-fill` is safe to run multiple times. Re-running after new conversation will surface new gaps.
- If run after `/dpd-import`, the imported graph (provenance='imported', state='archived') is included in the walk. This enables systematic gap analysis of external docs (§7.1 pipeline).
- **Advanced `/dpd-fill`** (goal-driven auto-decomposition) is deferred to v0.3.2+. Current implementation is manual inference pass + user opt-in.
