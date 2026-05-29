---
name: dpd
description: Bootstrap a DPD (Decompose-Propagate Decision) session under the ambient overlay paradigm — DPD is a passive graph overlay that observes ongoing conversation and proposes graph updates collaboratively. Invoke when the user runs /dpd, or proactively suggest when a conversation has accumulated multiple open threads, unanchored decision verbs, or surfaced contradictions that would benefit from explicit graph tracking. Requires the dpd-mcp-server MCP server to be registered with Claude Code.
---

# DPD v0.3.1: Decompose-Propagate Decision (Ambient Overlay Paradigm)

Announce: "Using dpd skill."

DPD is a graph-based protocol for structuring decision dialogues. Graph state lives in an MCP server (SQLite per agent scope). This skill governs the full operating lifecycle: entry phase, ambient steady-state, end achievement, session resume, and suggestion mode.

---

## What changed in v0.3.1

v0.3.0 was **active mode**: Claude and user consciously issued graph operations together.

v0.3.1 inverts this: **DPD is a passive overlay that observes ongoing conversation and proposes graph updates collaboratively.** User cognitive overhead reduces to two actions:

1. Fire `/dpd` when they sense "this needs organizing" (bottom-up trigger)
2. Confirm Claude's proposed updates at natural pauses

Everything else — signal detection, attachment evaluation, Pool management, End achievement detection — is Claude's background work.

**Role split:**
- **Human**: conversation lead, goal setting, final confirmation
- **Claude (secretary)**: graph bookkeeping, signal detection, Pool management, proposal drafting

Tone cue: Claude's proposals should be custodial — "ここまでを整理させて" — not transactional ("適用?").

---

## Hard rules vs permissive defaults

This document mixes two kinds of guidance. Treat them differently when deciding whether to apply them in a given situation:

- **Hard rules** (always apply, regardless of context) — End modification gate (§5.0), reject suppression (§4.6.1), never auto-resume (§3.3), never auto-decide on user's behalf. Marked with imperative phrasing ("**Stop.**", "must", "never"). Cite these as load-bearing justifications when relevant.

- **Permissive defaults** (starting point, can be overridden on substance) — granularity / phase-ordering / attachment-criterion / End-sizing thresholds / proposal format. Marked inline with **"Permissive default —"** prefix where useful. Substantive considerations (refactor cost, phase-boundary weight, downstream coupling, scope-specific judgment) **can and should override** these. Do not cite a permissive default as the sole justification for a design choice; use it as a prior and re-justify on substance.

Why this distinction exists: when methodology guidance is described as a rule without an explicit permissiveness marker, the agent tends to convert it into a load-bearing justification ("consistent with the granularity policy") rather than treating it as a background prior. That removes the user's leverage on substance.

---

## Invocation pattern (spec §2)

### Bottom-up trigger (§2.1)

`/dpd` fires when the user senses mid-conversation that things need organizing. There is already conversation context by the time `/dpd` fires. The startup ceremony is therefore conversation-rescue, not session initialization from scratch.

**Empty-context edge case**: If `/dpd` fires at session start with no prior conversation, skip §3.1 summarization. Proceed directly to §3.2 goal confirm → generate a minimum Start/End skeleton → enter ambient mode (§3.5). The skeleton is necessary: without an End anchor, `mark_reached` cannot function.

### Claude-suggested invocation (§2.2)

Claude may volunteer a soft suggestion to fire `/dpd` when it detects conversation tangle:

- Multiple open threads accumulating without resolution
- Decision verbs ("じゃあ X で行く", "Y はやめる") without a graph anchor
- Contradictions surface between prior statements

Wording: brief, low-pressure — e.g., "これ /dpd した方が整理しやすいかも". Final decision is always the user's. This mirrors the Entry-phase "propose / user confirm" pattern applied to the trigger itself.

---

## Prerequisites

The `dpd-mcp-server` package must be installed in the Python env Claude Code uses, AND registered as an MCP server. If its tools (e.g., `mcp__dpd-mcp-server__list_sessions`) are not available, stop and instruct the user to set it up:

**1. Install the package** (from this monorepo's `mcp/` dir):

```bash
pip install -e ./mcp
```

**2. Register with Claude Code**:

```bash
claude mcp add dpd-mcp-server -- dpd-mcp-server
# alternative: python -m dpd_mcp_server
# or edit .mcp.json directly
```

**3. Restart Claude Code** so the tools become discoverable. If `mcp__dpd-mcp-server__list_sessions` still doesn't appear, re-verify both steps before continuing.

---

## Startup sequence (spec §8.3)

### Step 1: Detect sub-scope

Resolve in this priority (first match wins):

1. **Explicit `--scope=<name>` argument**: use verbatim when provided.
2. **cwd walk-up to `.dpdrc`**: read its `scope=<name>` line.
3. **Fallback**: sub-scope = null (top-level session).

**Note (override priority matters)**: when Claude is launched from a workspace *above* the intended sub-scope, walk-up alone returns null and silently routes work to top-level. Always pass `--scope=<name>` explicitly in this case.

```bash
# walk-up implementation (only when explicit override absent)
dir="$(pwd)"
while [ "$dir" != "/" ]; do
  if [ -f "$dir/.dpdrc" ]; then
    name=$(grep -E '^[[:space:]]*scope[[:space:]]*=' "$dir/.dpdrc" | head -1 \
             | sed 's/^[[:space:]]*scope[[:space:]]*=[[:space:]]*//; s/[[:space:]]*$//')
    [ -n "$name" ] && { echo "$name"; break; }
  fi
  dir="$(dirname "$dir")"
done
```

**`.dpdrc` schema** (minimal — single `scope=<name>` line):

```ini
# .dpdrc — DPD marker for sub-scope auto-detection
scope=my-scope-name
```

Whitespace around `=` is tolerated. Lines starting with `#` are comments. An empty `.dpdrc` (no `scope=` line) is valid and only marks the agent-scope root for the server (see server-side resolution).

### Step 2: List existing sessions

Call `list_sessions(scope=<sub-scope>, mode_filter=...)` with the detected scope. Omit `scope` argument entirely for top-level. Do NOT pass `agent_scope`.

### Step 3: Resume vs new (NEVER auto-resume)

- **0 existing sessions** → call `start_session(scope=<sub-scope>, mode='entry')`.
- **1+ existing sessions** → ask the user:
  - (a) Resume most recent (show label + started_at + current mode)
  - (b) Resume a specific session (show full list with modes)
  - (c) Start a new session

Auto-resume is forbidden. It pollutes a stale session with unrelated work.

### Step 4: On resume — brief with state and detect mode

Call `get_session_state(session_id=<chosen>)`. Returns `{session, active_roots, focus_node}`.

Summarize internally:

```
sub_scope   = <session.scope or "(top-level)">
session_id  = <session.id>
label       = <session.label or "(unlabeled)">
mode        = <session.mode>   ← null means legacy session
focus       = <focus_node.text or "(none set)">
active_roots: [{id, topic, lifecycle}, ...]
```

**Mode-dependent resume behavior:**

| `session.mode` | Action |
|---|---|
| `entry` | Continue Entry phase — bootstrap was not completed. Resume at §3.2 or wherever user left off. |
| `ambient` | Propose: "前回 ambient 中だった、復帰しますか?" → user confirm → resume ambient mode (§4). |
| `idle` | Subgraph completed. Ask: "前回 subgraph は完結済み。新しい /dpd を開始する? 別 root に focus を移す? idle のまま続ける?" |
| `null` (legacy) | Heuristic: if active_roots > 0 and Pool items exist → suggest ambient resume. Otherwise treat as fresh entry. Always confirm before proceeding (§6.1). |

**Resume edge case (§5.1.2):** If `focus_node` points to a subgraph with `state='closed'`, do NOT suggest ambient resume. Instead ask: "前回 subgraph は完結済み。新 entry / idle 継続 / 他 root へ focus 変更、どれ?"

### Step 5: Graph-mode loop

After session is established or resumed, enter the appropriate operating phase (Entry or Ambient). See §§ below for per-phase behavior.

---

## Entry phase (5 steps per spec §3)

The entry phase bootstraps the graph from existing conversation. Execute in sequence.

### §3.1 Conversation summarization

- Window candidate: all turns since session start.
- Selection: topic clustering anchored on the `/dpd` invocation turn + immediately prior topic.
- Report: "session 全 N turn のうち K turn を選んだ" — user may broaden or narrow.
- Skip this step if invoked at session start with no prior conversation (empty-context edge case, §2.1).

### §3.2 Goal always-confirm (silent assume 禁止)

Interpret `/dpd <argument>` as a **hint**, not a confirmed goal. Two readings exist:
- α: the argument itself is the goal
- β: the goal is derived from what the argument implies

Always present a candidate goal and ask explicitly: "こういうゴールで DPD モード開始する、OK?" Do not assume. Do not proceed until user confirms.

#### §3.2.1 Aggressive End narrowing [v0.3.1]

Before confirming an End anchor (`add_node(type='end', ...)`), aggressively narrow the End:

- If goal text mentions ≥3 distinct outcomes (e.g., "X AND Y AND Z") → propose splitting into multiple narrower Ends or ask user to pick the highest-priority one.
- If `achievement_conditions` would have ≥6 items → **propose a concrete split** (not a vague flag). Identify intent-clusters in the conditions and present named alternatives with the conditions partitioned. Example:

  > "End has 8 conditions crossing 2 distinct intents (Q-resolution × 4, cross-spec × 4). Propose splitting:
  > - End A: Q1–Q4 individually resolved (4 conditions)
  > - End B: cross-spec consistency verified (4 conditions)
  >
  > Each becomes an independent drift gate. Apply / modify / proceed with single End?"

  Detecting intent-clusters is an LLM judgment call — the proposal does not need to be perfect, but it must be **concrete and partitioned** (not a flag-then-acknowledge-then-proceed). Acknowledging the size flag inside the End text itself ("NOTE: wide End") is not a substitute for the split proposal.

- End = smallest achievable outcome. "全部含めたい" → use multiple sequential Ends or a multi-subgraph structure.

Rationale: an over-scoped End permits silent drift in all subsequent nodes (spec §3.2.1). Narrow End = accurate drift gate. A flag without a concrete alternative is too easy to dismiss, then the End has to be retroactively split mid-flight — which defeats the gate.

### §3.3 Initial graph construction (adaptive fidelity)

Build the graph iteratively, not in one pass. Apply epistemic stratification:

| Tier | Source | When to include |
|---|---|---|
| **Grounded** | Direct conversation utterances | Auto-include in initial graph |
| **Inferred** | Claude's extrapolation beyond conversation | Only with explicit user opt-in |

Flow:
1. Build grounded graph from conversation material.
2. Present to user (§3.4 format).
3. Ask: "推測 node を自動算出しますか?"
4. If YES: run an inferred pass. Mark each inferred node with `provenance='inferred'` when calling `add_node`.

Confidently classifiable observations → graph nodes. Ambiguous observations → Pool (see §4.2 attachment criterion).

### §3.4 Proposal format: C + minimal B

Primary: a **hierarchical text outline** of the proposed structure (parent → child indentation, edges noted inline as `[→ type from→to]`). Accompany with **minimal classification notes** — one line per node/group explaining Claude's categorization decision (e.g., "これは decided として graph 化", "これは open Q").

Rationale: structure alone risks Claude deciding structure too early. Notes alone leave classification work to the user. C + minimal B shows the shape while surfacing the reasoning. (The outline mirrors the §4.5 ambient proposal format; the authoritative machine-readable graph is always `export_yaml`.)

### §3.5 Explicit transition to ambient mode

Wait for user's explicit "OK / これで進めて" before entering ambient mode. Call `set_session_mode(session_id, 'ambient')` on confirmation.

Rationale: The Entry phase final step is the last intentionally active moment. Silent transition is forbidden (same rule as §3.2 goal confirm).

---

## Ambient mode (steady-state per spec §4)

User converses normally. Claude observes and proposes graph updates. This is the primary operating state in v0.3.1.

### §4.1 Detection signals

| Signal | Example triggers | DPD operation |
|---|---|---|
| **Decision verb** | 「じゃあ X で行く」「Y はやめる」「Z に切り替える」 | `close_node` / `resolve_hypothesis_branch` |
| **Hypothesis surface** | 「X かもしれない」「A 案と B 案がある」 | `add_node(type='hypothesis')` × N |
| **Closure** | Question answered, discussion petered out | `close_node` |

Do NOT use mechanical triggers (turn count, token count). Density mismatch makes them imprecise.

### §4.2 Attachment criterion (Pool vs graph)

For each detected signal, determine where it attaches:

| Attachment | Action |
|---|---|
| Confirmed attach within primary focus root | Add to pending update list → propose at next natural pause |
| Attach falls in another active root | Routing confirmation per §4.3 |
| Attach undetermined | Add to Pool via `pool_add`. Include a tentative attach hint in the text to reduce future elevate cost. |

Pool semantic is unified across phases:
- Entry Pool: no End anchor yet → all observations are "attach undetermined"
- Ambient Pool: End exists but specific update has no clear attach point

#### §4.2.1 Pool usage decision rule [v0.3.1]

**Permissive default** — substantive considerations (user workflow preference, scope-specific friction, current confidence in attachment) can override this table. The "Tangent catch" row is the closest to a hard rule (Pool prevents stealth attachment), but the short-session / long-session split is just a starting point.

| Session characteristics | Pool strategy |
|---|---|
| Short (< 1h), single-session, clearly directed | Direct `add_node` is fine. Pool is optional. |
| Multi-day marination / multi-session / multi-participant raw ideas | Pool first: `pool_add` → `pool_elevate` |
| Tangent catch (= off-topic observation surfaces mid-session) | **Always Pool** regardless of session length — park first, route later |

#### §4.2.2 `contributes_to` norm when Pool is not used [v0.3.1]

When Pool is skipped (direct `add_node`), subgraph membership already implies contribution. Do NOT fanout `contributes_to` to the End from many nodes.

Rule:
- Only add explicit `contributes_to` edges from nodes that are **logically central** to the subgraph — "removing this node would collapse the End's justification."
- ≥6 simultaneous `contributes_to` edges to one End = **self-check trigger**: is implicit membership sufficient here?
- When Pool *is* used: existing norm applies (explicit edges on `pool_elevate` + End confirmation).

### §4.3 Multi-root cross-root detection

Session may have multiple active roots. Attend to one primary focus root. When a signal appears to relate to a different active root:

→ Confirm: "これ root_X 関連かも、振り分ける?" Final routing decision is the user's.

Do not silently route cross-root signals. Do not ignore them either. The hybrid confirmation is the intended pattern.

#### §4.3.1 Meta-subgraph isolation [v0.3.1]

Within a single session, separate **topic observations** from **meta-observations**:

| Signal kind | Routing |
|---|---|
| "WHAT we're working on" — about the subject itself | Attach to current (topic) subgraph |
| "HOW we're working" — about methodology, tooling, DPD itself, process feedback | Spawn or route to a **meta-subgraph** (separate root, same session) |

**Decision prompt** (ask yourself each turn): "Is this observation about the topic, or about how we're approaching the topic?"

**Routing prompt to user** (when meta signal detected):
> "これは作業方法 / DPD 自体への観察のように見えます。meta-subgraph を別 root で spawn しますか?"

**Default for tool-feedback / process-feedback signals = Yes** (meta-subgraph). Only attach to topic subgraph if user explicitly requests it.

Rationale: meta-observations contaminate the topic subgraph and accelerate End drift (spec §4.3.1). Independent Ends per subgraph = independent drift gates.

### §4.4 Natural pause detection

Accumulate pending updates **in-memory** (not persisted — lost on Claude Code restart). Propose at natural pauses. Pause triggers (OR'd):

- **(a) Topic shift** (primary): user's topic visibly transitions.
- **(c) Count threshold** (safety net): pending update count reaches N (default: ~5 items).
- **(b) Exchange completion** (opportunistic): only fire when confidence in exchange completion is high.

(a) OR (c) as baseline. (b) as supplemental when confident.

### §4.5 Proposal format at natural pause

Tone: **custodial** — "ここまでを整理させて" — not transactional ("適用?").

Format: **hierarchical list + local subgraph context**. For each pending update, show the target node's local neighborhood (parent K levels + child M levels + siblings, default depth 2). Annotate proposed changes inline.

```text
ここまでを整理させてください:

root_abc (TBD 4)
  - hypothesis: H1
  - hypothesis: H2 ← [→ close as resolved]
    - rationale: X
  - hypothesis: H3 ← [→ close as rejected]
  - (NEW) decision ← 「Y で行く」

これを適用してよいですか?
```

Rationale: a full graph dump at every pause is expensive. Numbered lists lose spatial context. Hierarchical list gives both spatial anchoring and text clarity.

#### §4.5.1 Severity-aware grouping (questions / cross-doc review) [v0.4]

When surfacing many `question` nodes at one pause — typical in cross-document or spec-review sessions — pre-classify each with `severity` (`'logical'` / `'surface'` / `'cosmetic'`) on `add_node`, then group the §4.5 proposal listing by severity:

```text
ここまでを整理させてください:

logical (要対応):
  - q_xx ← <real logical break>
  - q_yy ← <numerical claim contradicts §3>

surface (確認のみ、まとめて dismiss 可):
  - q_aa ← <rhetorical phrasing drift>
  - q_bb ← <threshold vs evidence gap, claim still stands>

cosmetic:
  - q_cc ← <typo / formatting>

「surface 以下まとめて却下」「logical を 1 件ずつ確認」など指示してください。
```

Severity is optional and free-form (the schema accepts any string). Skip it for single-question or low-volume sessions — the grouping overhead doesn't pay off then.

#### §4.5.2 Sibling-granularity check (skill-only, transient) [v0.4]

**Permissive default** — apply when surfacing nodes accumulated under one parent over a non-trivial session (typically ≥5 new siblings, or visible flatten/atomic asymmetry in the candidates). Skip on small batches.

Before listing pending updates at a natural pause, Claude inspects each new node against its **existing siblings under the same `parent_id`**. If granularity is markedly inconsistent, the node is flagged inline in the proposal so the user can request a split or merge without leaving the §4.5 flow.

**Comparison metric (cheap, no tool call):**

1. **Enumeration-marker count** (primary). Count occurrences of list markers in `text`: `(C1)` / `(N)` style, `1.` / `2.` numbered, `-` / `*` bullets. ≥3 markers signals pre-flattened "N concerns in one node" — already a §4.8 self-check #5 anti-pattern.
2. **Sentence count** (fallback for prose). Split on `.` / `。` / `?` / `？` / `!` / `！`. Use when neither side has enumeration markers.
3. **Character length** (last resort). Compare `len(text)` ratios when both other signals are zero.

**Flag rule:** if `ratio(candidate, max_existing_sibling) > 5` on any of (1)–(3), mark the candidate `[granularity review pending]` in the §4.5 listing. The reverse direction (`< 0.2×`) is not flagged in v1 — atomicity drift is a different problem.

**Surfacing format:**

```text
ここまでを整理させてください:

root_abc (TBD 4)
  - hypothesis: H1 (5 ratio markers; sibling avg = 1)  ← [granularity review pending]
    Consider split into: <preview of (C1)…>, <preview of (C2)…>, …
  - hypothesis: H2
  - (NEW) decision ← 「Y で行く」

apply / modify / split-H1-then-apply?
```

**Persistence:** none. The flag is recomputed each pause from the current sibling set; it is **not** stored on the node. If a session restarts, the next pause re-evaluates. This is deliberate (cheapest path): persisting granularity flags requires a schema change, and the value of the flag is mostly in the *surfacing moment*. If empirical use shows the marker needs to survive across sessions, promote to a persistent column (issue #33 axis 1 option A).

**Out of scope here:** automated split/merge (`pool_reject` + re-`add_node` is the manual path); cross-parent granularity comparisons (subgraph-wide consistency is a different invariant).

### §4.6 User reaction handling

| Reaction | Processing |
|---|---|
| Full OK | Apply all pending updates. |
| Partial reject ("2 番は違う") | Call `pool_reject(pool_id, reason)` for the rejected items. Apply the rest. |
| Full reject | Call `pool_reject` for all pending items. Continue ambient. |
| Partial modify + apply | Claude revises → re-presents → confirm loop. |

#### §4.6.1 Reject suppression (signal identity)

Before re-proposing a similar update, check whether an identical signal was already rejected. Identity is defined on **three dimensions** (per spec §4.6.1):

- **Target node id** (for operations on existing nodes)
- **Canonical text hash** (for new node additions): `lower(strip(text))` SHA-256 prefix 16 hex chars
- **Operation kind** (`close_node` / `add_node` / `add_edge` etc.) — same target but different operation is NOT a duplicate

Suppression check: compare against `pool_list(rejected_only=True)`. All three dimensions must match for auto-suppress. Partial match → propose; if user rejects again, record new `pool_reject`.

#### §4.6.2 Pool visibility

| Call | Returns |
|---|---|
| `pool_list()` (default `active_only=True`) | Active items only, excludes rejected |
| `pool_list(include_rejected=True)` | Active + rejected |
| `pool_list(rejected_only=True)` | Rejected only (for `/dpd-status` "pending rejects" view) |

#### §4.6.3 Unsuppress

User-driven: `/dpd-edit <pool_id>` sets `rejected_at` / `rejected_reason` to NULL. Item returns to "attach undetermined" state and re-enters Claude's evaluation scope.

---

## Per-turn self-checks (spec §4.8) [v0.3.1]

Run these checks **internally each turn** before proposing any graph update. Each check is informational — it produces a self-correction or a user-confirmation prompt, not a hard error.

| # | Check | Action if true |
|---|---|---|
| 1 | Am I about to modify the End (text / `achievement_conditions` / `paired_for`)? | **Stop.** Apply End modification gate (§5.3 below): ask user for explicit confirmation before proceeding. |
| 2 | Would the proposed node extend the subgraph beyond the End's original scope? | Stop. Propose splitting the End or moving the signal to a new subgraph. |
| 3 | Am I about to write a factual / vendor-spec claim as node text ("X supports Y", "Z is available in repo W")? | Verify via WebSearch / WebFetch before asserting. Do not add unverified claims to the graph. |
| 4 | Am I about to add a `decision`-type node? | Identify the source evidence. Add a `derived_from` edge from the decision to its source simultaneously. |
| 5 | Am I about to flatten N≥3 distinct concerns into one node? | Consider creating an intermediate parent node + sub-tree. Rule: sub-tree if each sub-item could be independently discussed, closed, or revised. **Related:** §4.5.2 surfaces the same anti-pattern when it appears across siblings rather than within one node. |
| 6 | Am I about to fanout ≥6 `contributes_to` edges to one End? | Apply §4.2.2 norm: keep only logically central nodes. Subgraph membership is implicit contribution. |

**Self-check timing**: before proposing an update at a natural pause. Not after every sentence — at the proposal-formation step.

**Failing any check** ≠ do nothing. It means: correct, split, verify, or ask — then proceed.

---

## End achievement (per spec §5)

### §5.0 End modification gate (hard rule) [v0.3.1]

End is the subgraph's semantic anchor. Modifying it without user consent enables silent drift.

**Before any of the following operations, stop and request explicit user confirmation:**

| Operation | Why gate applies |
|---|---|
| Adding to `achievement_conditions` (expanding End scope) | End scope expansion = drift entry point |
| Refining End `text` | Changes the anchor's meaning |
| Changing `paired_for` (re-anchoring End to different Start) | Alters the subgraph's entire logic |
| Creating a new End node in the same subgraph | Dual-anchor contamination |

**Confirmation prompt template:**
> "End を変更したいのですが、確認させてください。
> 現在: [current End text + achievement_conditions]
> 変更案: [proposed change]
> 理由: [reason]
> 適用してよいですか?"

**Scope**: applies only to End nodes. `question` / `hypothesis` / `decision` / `evidence` etc. may be proposed by Claude unilaterally.

**Principle**: End is joint authorship (user + Claude). The initial End was user-confirmed in §3.2. Any modification requires the same explicit consent.

### §5.1 mark_reached trigger

**Hybrid (a) + (b):**

- **(a) Primary**: Claude evaluates `achievement_conditions` against the current subgraph state (closed/open nodes, decisions, open hypotheses). If satisfied → propose `mark_reached`.
- **(b) Fallback**: user signals "終わったね" / "完了" / "OK" → propose `mark_reached` even if conditions are not fully evaluated.

Evaluation is LLM inference against natural language conditions. When satisfied → propose + await user confirmation. Do not proactively alert on unsatisfied conditions (ambient overlay philosophy: do not interrupt).

#### §5.1.1 Single end_node scope

Each `mark_reached` proposal targets exactly one `end_node_id`. For sessions with multiple active roots:

- Focus root End → propose normally.
- Non-focus root End → routing confirm: "root_X の End も到達した可能性、mark_reached を提案しますか?" — independent from focus root proposal.
- Never batch multiple mark_reached in one proposal. Each requires independent user confirmation.

#### §5.1.2 Resume into closed subgraph

If resuming and `focus_node` points to `state='closed'` subgraph, do NOT suggest ambient resume. Ask: "前回 subgraph は完結済み。新 entry / idle 継続 / 他 root へ focus 変更、どれ?"

On `mark_reached` confirmation: call `set_session_mode(session_id, 'idle')` after Pool disposition is complete (§5.2).

#### §5.1.3 Canonical subgraph layout (required for `mark_reached`)

`mark_reached` verifies Start→End connectivity by walking the **`parent_id` chain upward** from End. End must therefore live in Start's `parent_id` descendant subtree. Typed edges (`contributes_to`, `derived_from`, …) do **not** satisfy reachability — only `parent_id` ancestry does.

```text
root → Start (parent_id=root)
         ├─ work_nodes (parent_id=Start or descendant)
         └─ End (parent_id=Start or any Start-descendant, paired_for=Start)
```

Anti-pattern (will fail with "not reachable" error):

```text
root
  ├─ Start (parent_id=root)         ← sibling
  └─ End   (parent_id=root)         ← sibling  ❌
       └─ work_nodes
```

Why parent_id only: the subgraph that `mark_reached` archives is defined by parent_id descendants of Start. Accepting edge paths would close Start+End without archiving work nodes parented under End. Edges remain valuable for semantic justification (`contributes_to`) and phase ordering (`blocks`); they just aren't subgraph-membership signals.

When the empty-context skeleton (§3.2 / Edge cases) creates Start + End, add **End with `parent_id=<start_id>`** explicitly. If End ends up in the wrong place, the recovery is `force_delete(end_id)` then re-add under the correct parent.

### §5.2 Pool disposition on mark_reached

**Ask the user — never auto-drop.** Present three options for each remaining Pool item:

| Option | Processing |
|---|---|
| **(i) 漏れ** (should have been in graph) | `pool_elevate` → if subgraph is archived, create `supersedes` subgraph (never reactivate archived). |
| **(ii) 不要** (surplus discussion) | `pool_drop(pool_id, reason)` |
| **(iii) 次 DPD 話題** | Carry forward — note for next session or new root. |

The supersedes path for option (i) preserves state machine monotonicity (`active → archived` is forward-only; no `archived → active` re-open in v0.3.1).

---

## Edge cases

**Empty-context invocation (§2.1):**
1. Interpret `/dpd <argument>` as goal hint → present candidate → confirm (§3.2 flow).
2. On goal confirmed: `spawn_root` → `add_node(type='start', parent_id=root_id)` + `add_node(type='end', parent_id=start_id, paired_for=start_id, achievement_conditions=<goal text>)`. End must be under Start in the parent_id chain — see §5.1.3.
3. Call `set_session_mode(session_id, 'ambient')` on §3.5 explicit OK.
4. Subsequent signals: all Pool direct, or as children of the skeleton depending on §4.2 attachment criterion.

**Cross-root mark_reached (§5.1.1):** See above — each End requires independent proposal and confirmation.

---

## Node type vocabulary (spec §2.2)

The server enforces these via CHECK constraint:

| Side | Examples |
|---|---|
| **Problem (open-flavor)** | `question`, `plan`, `hypothesis`, `goal`, `problem` |
| **Solution (close-flavor)** | `answer`, `action`, `verification`, `decision`, `resolution` |
| **Support** | `evidence`, `constraint`, `assumption`, `rationale`, `risk` |

Special structural types: `start`, `end` (subgraph anchors).

`closure_reason` is one of `resolved` / `rejected` / `invalidated`. Per-type intent:

| Type group | `resolved` | `rejected` | `invalidated` |
|---|---|---|---|
| `hypothesis` | adopted as decision | ruled out (sibling of accepted) | later found incoherent |
| `decision` / `answer` / `resolution` | final | (rarely applicable) | revoked / superseded |
| `question` / `plan` / `goal` / `problem` | closed (answered / done) | abandoned without answer | question itself was malformed |
| `evidence` / `rationale` / `constraint` / `assumption` | articulated and stands | (rarely applicable) | later found incorrect |
| `verification` / `action` | done | abandoned | later invalidated by new info |
| `risk` | mitigated / accepted | rejected (no longer a risk) | re-evaluated as different risk |

`resolve_hypothesis_branch` encodes the most common closure: target = `resolved`, siblings = `rejected`. Use `close_node` for everything else.

---

## Tool reference (`dpd-mcp-server`)

Full tool list. New tools added in v0.3.1 Phase 2 are marked **[v0.3.1]**.

| Tool | Purpose |
|---|---|
| `start_session(scope?, label?, mode?)` | Begin new session. **[v0.3.1]** `mode` defaults to `'entry'`. Returns `session_id`. |
| `list_sessions(scope?, mode_filter?)` | List sessions for sub-scope, most recent first. **[v0.3.1]** `mode_filter` narrows by session.mode (`'entry'`/`'ambient'`/`'idle'`). |
| `get_session_state(session_id)` | Session + active_roots + focus_node. |
| `set_session_mode(session_id, mode)` | **[v0.3.1]** Transition session.mode per §9.1.1 table. Valid modes: `'entry'`, `'ambient'`, `'idle'`. Call on §3.5 OK (→ ambient), §5 completion (→ idle), resume into idle. |
| `spawn_root(session_id, topic, reason?)` | Create new root topic → `{root: {...}}` (full row). |
| `add_node(session_id, parent_id, type, text, paired_for?, achievement_conditions?, provenance?, state?, severity?)` | Add child node. **[v0.3.1]** `provenance` ∈ `'grounded'`/`'inferred'`/`'imported'`/`'manual'` (default `'grounded'`). `state` allows `'archived'` for `/dpd-import` use. End nodes require `paired_for=<start_node_id>`. **[v0.4]** `severity` is optional proposer-assigned classification (conventional values: `'logical'`/`'surface'`/`'cosmetic'`) used by §4.5 grouping. |
| `close_node(session_id, node_id, closure_reason)` | Mark resolved / rejected / invalidated. |
| `resolve_hypothesis_branch(session_id, hyp_id, decision_text, rationale_text?)` | **Atomic**: close target resolved + open siblings rejected + insert decision + auto-insert `derived_from` edge (decision → accepted hypothesis) + insert rationale if any + auto-insert `justifies` edge (rationale → decision) when a rationale is given (#57). |
| `resolve_branch(session_id, parent_id, parent_kind, results, decision_text?, rationale_text?, derived_from_node_ids?)` | Atomically close N sibling nodes with per-node closure_reason. Generic counterpart to `resolve_hypothesis_branch`; also auto-inserts the rationale → decision `justifies` edge when a rationale is given (#57). |
| `set_focus(session_id, node_id?)` | Set/clear `focus_node_id`. Pass `node_id=null` to clear. Accepts regular node id or root_id. |
| `set_root_lifecycle(session_id, root_id, lifecycle)` | Transition `active` ↔ `archived` ↔ `deferred`. |
| `list_open_nodes(session_id, root_id?, state?)` | Open nodes in session or within one root. `state` filter narrows by node state string. |
| `list_unblocked_open_nodes(session_id, root_id?, blocker_edge_type?)` | Open nodes that no open node is blocking via the given edge type (default `'blocks'`). |
| `add_edge(session_id, from_node, to_node, type, reason?, layer?, verification_priority?)` | Insert an edge between nodes. `type` is enforced against the canonical vocabulary (see Edge type table below). Self-loops rejected. **[v0.6]** `layer` ∈ `'necessary'`/`'selective'`/`'invalid'` (proof-tree discipline, see that section); `verification_priority` ∈ `'critical'`/`'standard'`/`'low'`. Both optional/nullable. |
| `delete_edge(session_id, edge_id)` | Delete a single edge by id. Use to clean up mis-typed or stale edges (e.g., wrong direction). |
| `list_edges(session_id, from_node?, to_node?, type?)` | List edges with optional filters (AND'd). |
| `set_edge_layer(session_id, edge_id, layer?)` | **[v0.6]** Set/clear an edge's proof-tree `layer`. `layer=null` retracts from the discipline. Use for downgrade (refuted `necessary` → `selective`/`invalid`). |
| `set_edge_verification_priority(session_id, edge_id, verification_priority?)` | **[v0.6]** Set/clear an edge's `verification_priority`. `null` drops queue pressure without changing layer. |
| `record_edge_verification(session_id, edge_id, verdict, verified_by?, method?, notes?, prompt_hash?)` | **[v0.6]** Append an external-verification record (`verdict` ∈ `holds`/`holds-with-caveat`/`refuted`). Append-only; `refuted` does NOT auto-downgrade. Produced by `/dpd-verify-edge`. |
| `list_unverified_edges(session_id, verification_priority?)` | **[v0.6]** Necessary edges with no verification record yet (obligation keyed off `layer='necessary'`), ordered critical→standard→low→unset. |
| `list_edge_verifications(session_id, edge_id)` | **[v0.6]** All verification records for one edge, oldest first (re-verification history). |
| `export_yaml(session_id, root_id?)` | JSON dump (a strict subset of YAML; json.loads round-trippable). **[v0.10]** Includes a top-level `notes` array (active notes anchored to rendered nodes/roots, #64); archived notes omitted. |
| `get_node(session_id, node_id)` | Fetch single node. |
| `walk_subtree(session_id, root_id)` | All descendants of root (pre-order). |
| `list_active_roots(session_id)` | Roots with lifecycle=active. |
| `pool_add(text, scope?, tags?, origin_session_id?)` | Append raw thought to scope's Pool. Auto-creates scope_root if needed. |
| `pool_list(active_only?=true, scope?, include_rejected?, rejected_only?)` | List Pool items. **[v0.3.1]** `include_rejected=True` returns active + rejected. `rejected_only=True` returns rejected only. Default excludes rejected. |
| `pool_elevate(pool_id, target_end_node_id, type, session_id, text?, scope?)` | Elevate Pool item to DPD subgraph as child of End node. |
| `pool_drop(pool_id, reason?, scope?)` | Mark Pool item as dropped (physical capture drop). |
| `pool_reject(pool_id, reason?)` | **[v0.3.1]** Soft-suppress a Pool item: sets `rejected_at` + `rejected_reason`. Distinct from `pool_drop` — item remains for audit and unsuppress. Use when user rejects a proposed update. |
| `mark_reached(session_id, end_node_id)` | Signal End achievement. Server verifies Start→End connectivity and transitions subgraph to closed. |
| `dump_persist(session_id, start_node_id, destination?)` | Record externalization of a closed subgraph → transitions to deletable. |
| `delete(session_id, start_node_id)` | Physical delete of a deletable subgraph. |
| `force_delete(session_id, node_id)` | Single-node force delete (emergency only). |
| `purge_session(session_id)` | Remove the session row + roots + edge/pool back-refs once all subgraphs were `delete`d. Precondition: session is `idle` (or null) and no nodes remain. Pool items survive (origin_session_id nulled). |
| `force_purge_session(session_id)` | Cascade-delete an entire session — nodes, edges, roots, the session row. Emergency only; bypasses preconditions. |
| `bulk_import_subgraph(session_id, root_id, nodes, edges, provenance?, state?)` | **[v0.3.1]** Atomic batch insert of multiple nodes + edges with FK validation + full rollback. `provenance`/`state` default to `'imported'`/`'archived'` for the `/dpd-import` hypothetical-subgraph case. **[#61]** For **active fine-graph extension** — decomposing one parent into many siblings + edges in a single atomic op instead of N sequential `add_node` calls — pass `state="active", provenance="grounded"`: the nodes land in the live graph (status `open`, visible to `list_open_nodes`), identical to what per-call `add_node` would produce. Prefer this over sequential `add_node` once a decomposition exceeds a handful of siblings. |
| `find_similar(query, scope?, top_k?, include_open?)` | **[v0.3.2]** Retrieve closed/archived subgraphs whose FTS5 index matches the query. `scope` narrows to a sub-scope (None = all). `include_open=True` also covers active subgraphs via dynamic LIKE. Returns `{results: [SubgraphSummary, ...]}`. |
| `add_note(session_id, anchor_kind, anchor_id, kind, text)` | **[v0.9]** (#55) Attach a long-form note to an anchor (`anchor_kind` ∈ `'node'`/`'root'`; root = a subgraph). `kind` ∈ `'narrative'`/`'caveat'`/`'external-analysis'`/`'rejected-alternative'`. At most one active note per `(anchor, kind)`: a second one archives the first (append-only lineage). Returns `{note_id, superseded_note_id}`. See Note layer section. |
| `list_notes(session_id, anchor_kind?, anchor_id?, kind?, include_archived?)` | **[v0.9]** (#55) List notes oldest-first. `anchor_kind`+`anchor_id` filter to one anchor (supply both or neither); `kind` to one axis; `include_archived=true` walks supersession history (default active only). |

**Session mode transition table (§9.1.1):**

| From mode | Event | To mode |
|---|---|---|
| (new) | `start_session` called | `entry` |
| `entry` | User explicit OK (§3.5) → `set_session_mode` | `ambient` |
| `entry` | User aborts (`/dpd-abort` etc.) | `idle` |
| `ambient` | `mark_reached` + Pool disposition complete | `idle` |
| `ambient` | User explicit abandon | `idle` |
| `idle` | New `/dpd` invocation | `entry` |
| `null` (legacy) | `/dpd` resume | `entry` or `ambient` (heuristic, see §6.1) |

---

## Edge type vocabulary

`add_edge` rejects types outside this table and rejects self-loops (`from == to`). Use `delete_edge(session_id, edge_id)` to clean up an edge added in error.

| Type | Direction (from → to) | Use |
|---|---|---|
| `derived_from` | derived → source | Decision/evidence derived from earlier node (e.g., `decision → hypothesis`) |
| `requires` | requirer → required | Hard dependency relation (distinct from `blocks` which is phase-ordering) |
| `supports` | supporter → supported | Generic / not-yet-refined support. Prefer the precise `instantiates` / `illustrates` / `justifies` below when the relation is clear |
| `instantiates` | concrete → abstract | Concrete artifact (formula/code/example) realizes an abstract claim (#57, realization axis) |
| `illustrates` | example → claim | Example/scenario demonstrates a claim's behavior (#57, realization axis) |
| `justifies` | rationale → claim | Rationale grounds a claim — removing it leaves the claim without premise (#57, grounding axis) |
| `contradicts` | contradictor → contradicted | Observation contradicts a decision/hypothesis |
| `qualifies` | qualifier → qualified | Finding limits or scopes a target without overturning it |
| `invalidates` | invalidator → invalidated | Finding shows target's premise no longer holds |
| `blocks` | blocker → blocked | Phase ordering: blocker must close before blocked can proceed (`list_unblocked_open_nodes` reads this) |
| `contributes_to` | contributor → End | Explicit semantic justification anchor for an End node |
| `supersedes` | new → old | New subgraph supersedes an older one (monotonic forward-only) |

**Direction rule**: from-side is the "active" side (supporting, contradicting, deriving); to-side is the target.

**Semantic axis [#57]**: `instantiates` / `illustrates` carry the *realization* axis (concrete realizes/exemplifies abstract), `justifies` the *grounding* axis (premise → claim). The remaining types are currently `unclassified` — the full axis taxonomy is deferred. The axis is a pure function of the type (queryable via `Storage.edge_axis(type)`), never stored on the edge. Refining `supports` into these did **not** migrate existing `supports` edges — they remain generic, non-breaking.

**Extending the vocabulary** is a deliberate spec change — propose a new type with use case and direction rule in an issue rather than introducing it ad-hoc. Free-form types would fragment cross-session semantics (e.g., `find_similar` retrieval, `list_unblocked_open_nodes` queries).

---

## Proof-tree discipline [v0.6] (#42)

An **optional, opt-in** rigor mode for stretches of reasoning that are meant to be proof-like — where it matters whether a step *logically follows* versus is *a choice*. It is orthogonal to everything above: it adds an epistemic `layer` to edges and an external-verification path. Most of a session is exploratory and should NOT carry it; rigor is usually needed late, near a decision.

### The `layer` dimension (orthogonal to edge `type`)

`add_edge(..., layer?, verification_priority?)` and `set_edge_layer` classify an edge's epistemic status, independent of its relationship `type`:

| `layer` | Meaning |
|---|---|
| `necessary` | A logical/mathematical implication — externally verifiable. The premise(s) *necessarily* yield the conclusion. |
| `selective` | A choice among logically-allowed alternatives (a judgement call, not forced). |
| `invalid` | A claim shown to be unsupported — **kept, not deleted**, for audit. |
| (unset / NULL) | Discipline not applied to this edge. |

`type` stays the relationship kind (`supports`, `requires`, …); `layer` says whether that relationship is forced, chosen, or refuted. A single `supports` edge can be `necessary` in one proof and `selective` in another.

### Opt-in is edge-local and implicit (hard rule)

**Tagging an edge with a `layer` IS the opt-in.** There is no session/region "discipline flag". This has one strict invariant:

> The discipline is **edge-local**. A tagged edge opts *that edge* in. Untagged edges are explicitly **outside** the discipline. **Never infer** that a neighbouring/surrounding region is "under discipline" because one edge in it is tagged.

Mid-session escalation is therefore free: start tagging when rigor begins, nothing to declare. The signal lives in the DB, so it survives context compaction.

### Multi-premise proofs (layout)

Keep one structural `parent_id` per node under Start (the spanning tree `mark_reached` archives — see §5.1.3). Express *additional* premises as `layer='necessary'` edges riding on top. The proof's full derivation DAG is the union of the spanning tree + the necessary edges.

### Verification flow

The verification obligation is **edge-local and keyed off `layer='necessary'`** (selective/invalid carry none):

1. `list_unverified_edges(session_id, verification_priority?)` → necessary edges with no verification record, ordered critical→standard→low→unset.
2. `/dpd-verify-edge [edge_id?]` builds a context-stripped prompt, gets an independent verdict, and calls `record_edge_verification`.
3. Verdicts: `holds` / `holds-with-caveat` (follows only under an explicit assumption — propose a structural fix) / `refuted` (does **not** follow).
4. **`refuted` never auto-downgrades** the edge. Record it, then *propose* `set_edge_layer(layer='selective'|'invalid')`. A verifier can be wrong; the user adjudicates.

### Retraction (cheap, explicit)

- `set_edge_layer(edge_id, layer=null)` — retract the edge from the discipline entirely.
- `set_edge_layer(edge_id, layer='selective'|'invalid')` — keep the classification, drop the verification obligation.
- `set_edge_verification_priority(edge_id, verification_priority=null)` — drop queue pressure without changing the layer.

### Deferred

Completion-% reporting ("this region is N% classified") is intentionally **unavailable** — there is no declared region boundary by design. If an explicit region model is later needed, it layers on top (a `discipline_regions` table) without reinterpreting existing tagged edges.

---

## Note layer [v0.9] (#55)

The graph is the source of truth for **structure** — decisions, dependencies, contradictions, realization, grounding, verdicts. But some content is irreducibly *narrative*: the body of an external analysis, the feel of a rejected alternative, a background caveat. The note layer is where that residue lives, **anchored** to the graph node or subgraph it belongs to — so the source of truth is `graph + notes`, not `graph + a conversation that evaporates`.

### Sufficiency invariant (the discipline core — hard rule)

> At any decision point, the graph **alone** must let you (1) re-justify the decision and (2) derive the spec text. If you cannot answer without replaying the conversation, that reasoning has **leaked** — capture it (structure → graph, narrative → note).

### (a)/(b) — what goes where

- **(a) merely not-yet-structured → graph.** A dependency, a realization, a *verdict/conclusion*. These get nodes and typed edges (and `record_edge_verification` for verification verdicts). The moment a relation or conclusion crystallizes, it is (a).
- **(b) inherently narrative → note.** Long-form prose that no node/edge captures without loss.

| Content | Class | Lands as |
|---|---|---|
| "A requires B" | (a) | `requires` edge |
| Verdict / counter-arg / dismissal **conclusion** | (a) | evidence/verification node + `contradicts`/`justifies` edge + `record_edge_verification` |
| Body / argument-flow of an external (e.g. Codex) analysis | (b) | note, `kind='external-analysis'` (extract only the conclusion to (a)) |
| Facts+reasons a path was rejected | (a) | `supersedes`/`invalidates` edge + rationale |
| The *tone/feel* of that rejected path | (b) | note, `kind='rejected-alternative'` |
| Background prose; a qualifier | (b) | note, `kind='narrative'` / `'caveat'` |

**Anti-pattern (hard rule):** never bury a verdict/counter-argument/dismissal *conclusion* in a note body. That just moves the leak from `text`→`note`. Conclusions are always graph-queryable; notes carry only the long-form narrative around them.

### kind vocabulary (closed, like edge types)

`narrative` · `caveat` · `external-analysis` · `rejected-alternative`. Closed on purpose — a free-form kind would drift and turn notes into a dumping ground. Extending it is a deliberate spec change (propose in an issue), exactly like the edge-type vocabulary.

### Canonicality + supersession

At most **one active note per `(anchor, kind)`** — structurally enforced by a partial unique index. A second note on the same axis is a *smell* (it means the note should be consolidated, not duplicated). `add_note` on an axis that already has an active note **archives the old one and inserts the new** (append-only lineage); the old id comes back as `superseded_note_id`. Walk the history with `list_notes(..., include_archived=true)`. The unique constraint forbids duplicate *rows*; it does not police prose bloat **inside** a note — that stays a discipline (a future completeness-sweep skill will help).

### Lifecycle

Notes are independent of their anchor's state: archiving/closing a node leaves its notes intact (lineage and the sufficiency invariant may still need them). **Physical deletion** of an anchor (`force_delete`, subgraph `delete`, session purge) cascade-deletes its notes — no note ever outlives its anchor.

### Not yet (deferred, tracked under #55)

(a)-migration helper / completeness-sweep skill, note-aware FTS dedup, edge & verification anchors, `migrate_note`. The supersession/frontier *mechanism* for graph evolution is #16; the note layer does not depend on it.

---

## Cross-TBD post-hoc evidence (canonical form)

When working under one root reveals a finding that strengthens, qualifies, or undermines a decision in a different root:

**Step 1 — Decide node-or-edge-only**

Ask: "Could this finding later be refined, extended, or objected to?"
- **YES** → make a node (Steps 2–4)
- **NO** → 1 edge only: `add_edge(from=origin_decision, to=target_decision, type=qualifies|invalidates|supports|contradicts)`. Done.

**Step 2 — Add the evidence node under the target root**

```text
add_node(
  session_id,
  parent_id = <target_root_id>,
  type      = "evidence",  # or "rationale" when appropriate
  text      = "<finding> (Discovered in <origin_root> during <origin_node>)"
)
→ new_node_id
```

**Step 3 — Valence edge to the target decision**

```text
add_edge(session_id, from=new_node_id, to=<target_decision_id>,
         type=qualifies|invalidates|supports|contradicts, reason="<short label>")
```

**Step 4 — Provenance edge from new node to origin decision**

```text
add_edge(session_id, from=new_node_id, to=<origin_decision_id>,
         type="derived_from", reason="post-hoc finding from <origin_root>")
```

To trace provenance: `list_edges(session_id, from_node=<new_node_id>, type="derived_from")`

To find all contradicting findings: `list_edges(session_id, type="contradicts")`

---

## v0.3.1 lifecycle recap (Pool → DPD → state machine)

DPD uses a 2-phase model: free-thinking is staged in **Pool** (`pool_add`), then elevated to the DPD subgraph (`pool_elevate`) once a goal (End) is clear.

Each subgraph has a **Start** (entry point) and **End** (goal anchor, `paired_for`-linked to Start). State machine is monotonic forward-only:

```
active → archived → closed → deletable → gone
```

Use `mark_reached(end_node_id)` to signal End achievement (server verifies Start→End connectivity). Use `dump_persist` to record externalization. Use `delete` to physically remove.

Pool also serves as the **reject suppression source** in v0.3.1: `pool_reject` marks items with `rejected_at` + `rejected_reason`. These are excluded from default `pool_list` but visible via `include_rejected` / `rejected_only`.

---

## Tone

Graph mode is a structural overlay on conversation. Responses should be tight.

After each tool call: one-line `<verb> <node-id>: <short text>` summary, not narration.

At natural pauses: custodial tone — "ここまでを整理させてください" — followed by hierarchical list proposal. Not transactional, not verbose.

The structure is the value. Keep prose minimal.

---

## v0.3.2 additions

Four methodology additions land in v0.3.2. None change existing tool signatures or state machine. All are additive layers on top of v0.3.1 ambient overlay.

### Phase ordering via `blocks` edge (D1')

For sequential multi-goal work (spec → design → impl → audit), express each phase as its own subgraph (Start_Pn / End_Pn pair) and connect them with the existing `blocks` edge:

```text
add_edge(from_node=<P1_End>, to_node=<P2_Start>, type="blocks",
         reason="<why P2 cannot proceed until P1 is reached>")
```

Convention: edge `from` is the blocker, `to` is the blocked. `list_unblocked_open_nodes(blocker_edge_type='blocks')` surfaces what is currently unblocked. **Enforcement is soft** — `mark_reached` does NOT verify preconditions; phase discipline is a SKILL.md concern, not a server check.

Each phase's deliverables go in the End's `achievement_conditions` text (existing v0.3 §5.3 field). No new vocabulary, no schema change.

### Multi-goal methodology pattern (D2)

Parallel multi-goal (multiple independent goals in the same scope) is already supported by v0.3: spawn multiple roots under the same scope_root, each with its own End. `mark_reached` fires per End independently.

A "meta-Goal G*" pattern — combining all goals into one super-End — is *available*: the user can spawn a new subgraph whose `achievement_conditions` reads "G1 reached ∧ G2 reached ∧ …". **Claude MUST NOT auto-generate G\*.** The user must propose it explicitly; only then does Claude help build it. This protects §1.1 (no prescription to AI thought).

Optional goals and tradeoffs between goals belong in `achievement_conditions` prose. Emergent goals are handled by v0.3 §3.7 End re-classification (no new mechanism needed).

### Retrieval-augmented proposal (D3, H3)

`/dpd-find-similar` (user-pull only — see next subsection) returns past closed/archived subgraphs ranked by FTS5. Claude then **distills** selected past subgraphs into a graph candidate — additions, edges, neighboring modifications — and proposes them via the v0.3.1 §4.5 hierarchical-list format. The user-confirm loop in §4.6 applies as usual.

**Distillation discipline (D3):**
- ❌ DO NOT write lesson-style prose ("past X did Y, so we should Y").
- ✅ DO write graph operands: `[→ add]`, `(NEW) decision ← "…"`, `(NEW) rationale ← "…"`.
- Justifications belong inside the graph (as `rationale` nodes), never in prose.

§6.3 of the v0.3.2 spec spells out an exception: describing *what was retrieved* (factual summary of the result list) is allowed prose. Distilling *lessons* from it is forbidden prose.

### User-pull only discipline (H2)

`find_similar` is a **user-pull** tool. Claude MUST NOT auto-consult it.

- ✅ Allowed firings: `/dpd-find-similar`, user explicit "any similar past judgment?", and within other user-pull skills (`/dpd-fill`, `/dpd-import`) when they need it.
- ❌ Forbidden firings: ambient-mode signal detection (§4.1), per-turn self-checks (§4.8), End achievement evaluation (§5.1) — none of these may include "consult find_similar" as a step.

Auto-consulting `find_similar` would seed AI reasoning with bias from past judgments, directly violating §1.1.

---

## Related sub-skills (Phase 4)

These skills are planned for Phase 4 and will each have their own SKILL.md:

| Skill | Role |
|---|---|
| `/dpd-import` | Parse external prose/spec/graph → hypothetical archived DPD subgraph (uses `bulk_import_subgraph`, provenance=`'imported'`, state=`'archived'`) |
| `/dpd-fill` | Generate inferred nodes + detect missing arguments / gaps (uses `add_node` with provenance=`'inferred'`). Auto-invokes `/fcot` on high-stakes inferred nodes; user-invoked elsewhere. |
| `/dpd-status` | Current graph + Pool + pending updates view (uses `pool_list(include_rejected=True)` for full visibility) |
| `/dpd-dump` | Full graph dump as JSON (YAML-compatible; wraps `export_yaml`) |
| `/dpd-summary-md` | Export decided/closed items as markdown summary |
| `/dpd-edit <node\|pool_id>` | Manual node/pool mutation. Also used for unsuppress: clear `rejected_at` / `rejected_reason` on a pool item. |
| `/dpd-find-similar` | **[v0.3.2]** Retrieval-augmented proposal. User-pull only — Claude may NOT auto-invoke. Returns past closed/archived subgraphs matching a query, then distills selected ones into graph-candidate proposals (no prose lessons). |

**`/fcot` orchestration**: `/dpd-fill` and `/dpd-import` SKILL.md prompts instruct Claude to invoke `/fcot` *automatically on high-stakes inferred / imported nodes*; on low-stakes nodes `/fcot` stays optional (user-invoked). This is stakes-based opt-in per `docs/spec` §10 — automatic pre-verification on every inferred node would break the ambient overlay philosophy. No code-level integration needed; the skill prompt instruction encodes the threshold.

---

## Feedback footer

After completing a meaningful response (not for trivial status output), print exactly one line at the very end:

> 💬 Hit a bug or have feedback on DPD? Run `/dpd-feedback "<short description>"` or open an issue at https://github.com/o3co/agent-dpd/issues/new

This surfaces the dogfood feedback path without interrupting the main interaction. Keep it to one line. Do not repeat across multiple turns within the same exchange — once per skill invocation is enough.
