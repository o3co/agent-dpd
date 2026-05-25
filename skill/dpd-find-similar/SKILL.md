---
name: dpd-find-similar
description: Retrieve past closed/archived DPD subgraphs whose FTS5 index matches a query, then distill selected results into graph-candidate proposals. User-pull only — Claude must not auto-invoke. Requires the dpd-mcp-server MCP server.
---

# /dpd-find-similar — Retrieval-augmented proposal (v0.3.2)

Announce: "Using dpd-find-similar skill."

This skill is the **user-pull entry point** for DPD's retrieval-augmented proposal pipeline (v0.3.2 spec §6).

## When to invoke

User invokes `/dpd-find-similar [query?]`. Claude may NOT auto-fire this skill (v0.3.2 §3.4: user-pull only).

## Step 1 — Query resolution

If `query` argument is present, use it directly.

Otherwise, propose a query candidate in this priority and explicitly confirm before running the search:

1. **focus_node text** (call `get_session_state` to fetch).
2. **Recent conversation topic** (cluster the last few turns around the most prominent anchor).
3. **Active Pool item cluster** (call `pool_list(active_only=True)` and look for a shared keyword among items whose attach point is undetermined).

Wording: "query: `<candidate>` で検索する、OK?" — Wait for explicit confirmation. Do not silently proceed.

## Step 2 — Run `find_similar`

```text
find_similar(query="<confirmed>", top_k=5, include_open=False)
  → { "results": [SubgraphSummary, ...] }
```

`SubgraphSummary` fields: `start_node_id`, `session_id`, `root_id`, `scope`, `start_text`, `end_text`, `achievement_conditions`, `state` (`closed` / `archived`), `score`, `matched_snippet`, `closed_at`.

## Step 3 — Present results

Render in conversation prose (no Mermaid). Format:

```text
過去類似 subgraph (state=closed/archived):

1. [score=8.21] scope=<scope> root=<root_id> start=<start_node_id> "<start_text>"
   End: "<end_text>"
   Conditions: "<achievement_conditions>"
   Snippet: "...<matched_snippet>..."

2. ...
```

**Allowed prose**: factual descriptions of what was retrieved (above format counts).
**Forbidden prose**: lesson-style claims like "past X did Y, so we should Y" — those belong in Step 4 as graph operands.

If `results` is empty: offer "該当なし、include_open=True で再検索しますか?" and on confirmation re-run with `include_open=True`.

## Step 4 — Distill into graph candidate (D3)

After the user selects one or more past subgraphs:

1. Read each selected subgraph's Start / End / decisions / rationales.
2. Hypothesize what to add to the *current focus subgraph*: which nodes, which edges, which neighboring modifications.
3. Format as a hierarchical list (the v0.3.1 §4.5 ambient-mode proposal format), with inline annotations:
   - `(NEW) decision ← "..."`
   - `[→ add edge type=derived_from from=X to=Y]`
   - `(NEW) rationale ← "..."`
4. Reuse v0.3.1 §4.6 reaction handling: Full OK applies all; Partial reject calls `pool_reject` on rejected items; Full reject calls `pool_reject` on all and stays in ambient.

**Strict prose ban**: do NOT write lesson-style commentary. If a justification belongs in the proposal, encode it as a `rationale` node, not as prose.

## Tone

Custodial, not transactional. Past judgments are inputs the user gets to inspect — they are not authoritative.

## Sub-skill boundaries

- This skill is invoked only by the user. Other skills (`/dpd-fill`, `/dpd-import`) may call `find_similar` internally when their own user-pull semantics already justify it.
- This skill never touches End nodes directly (v0.3.1 §5.3 End modification gate still applies to any End edits in the distilled candidate — confirm before applying).
- This skill never auto-consults retrieval during ambient mode (§3.4 user-pull discipline).
