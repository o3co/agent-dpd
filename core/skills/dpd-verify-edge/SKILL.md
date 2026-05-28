---
name: dpd-verify-edge
description: Externally verify a DPD edge that claims a logically-necessary implication (layer='necessary'), using a context-stripped prompt so an independent verifier judges the implication on its own merits rather than rubber-stamping the agent's framing. Records the verdict via record_edge_verification. Invoke when the user runs /dpd-verify-edge, or proactively suggest at a natural pause when list_unverified_edges is non-empty. Requires the dpd-mcp-server MCP server.
---

# /dpd-verify-edge — External verification of necessary edges (#42, v0.6)

Announce: "Using dpd-verify-edge skill."

This skill implements the verification half of **proof-tree discipline** (see the main `/dpd` skill, "Proof-tree discipline" section). It checks edges classified `layer='necessary'` — the claim "these premises *necessarily* imply this conclusion" — by handing a **context-stripped** prompt to an independent verifier.

It is the opposite-incentive companion to `/fcot`: `/fcot` *constructs* an argument using full context; this skill *checks* one necessary step in isolation, skeptically. **Compose them, don't merge** — run `/fcot` while building, `/dpd-verify-edge` after, on the necessary edges that resulted.

## When to invoke

- User invokes `/dpd-verify-edge [edge_id?]`.
- Claude MAY suggest it at a natural pause when `list_unverified_edges` returns a non-empty set — custodially ("necessary edge が N 本未検証です。verify する?"), never auto-firing the external call.

## Step 1 — Select the target edge(s)

- If `edge_id` is given, use it (confirm it is `layer='necessary'`; if not, ask whether to proceed anyway).
- Otherwise call `list_unverified_edges(session_id)` (necessary edges with no verification record, ordered critical→standard→low→unset). Present the queue and confirm which edge to verify. Default to the highest-priority one.

## Step 2 — Build the context-stripped prompt (the core asset)

Fetch the edge and its endpoints (`list_edges` / `get_node`). Build a prompt for an independent verifier. **One prompt_builder** — this exact construction is reused whether delivered by paste or auto-invoke.

The stripping line: **include what is needed to UNDERSTAND the proposition; strip what ARGUES for it.**

- **INCLUDE**: the from-node text, the to-node text, the claimed relationship (`from` necessarily implies / supports / requires `to`), the edge `type`, local definitions needed to interpret the terms, and any explicit domain assumptions already encoded as nodes in the graph. If a node is shorthand for a formal premise, include that premise.
- **STRIP**: the agent's chain-of-thought or proof attempt, the surrounding sibling/parent derivation, *why the agent believes* the edge holds, author confidence / `verification_priority` / prior verdicts, and downstream consequences.

Prompt skeleton:

```text
You are verifying a single logical-implication claim, in isolation.
Do not assume the claim is true; judge it on its own merits.

CLAIM: <from-node text>  ⟹ (necessarily implies)  <to-node text>
[edge type: <type>]

DEFINITIONS / EXPLICIT ASSUMPTIONS (only those needed to read the claim):
- <local definition or graph-encoded assumption>
- ...

QUESTION: Does the premise NECESSARILY imply the conclusion as stated?

Answer with a first line exactly of the form:
  VERDICT: holds | holds-with-caveat | refuted
If holds-with-caveat, add a second line:
  CAVEAT: <the explicit assumption/definition/qualifier it depends on>
Then a short justification.
```

## Step 3 — Deliver (paste baseline, optional auto-invoke)

Two transports, **same prompt, same parser** — they differ only at the edge:

- **Paste (always available)**: emit the rendered prompt in a code block and ask the user to run it in whatever model they trust, then paste back the verifier's reply.
- **Auto-invoke (optional)**: if an external verifier CLI is configured/available (e.g. `codex exec`), you MAY run the prompt directly and capture the reply. If it is missing or errors, **fall back to paste** — do not hard-fail. Not vendor-hardwired.

Record which transport was used: `method = 'external:<tool>'` (auto-invoke) or `method = 'paste'`. `verified_by` is the verifier identity (the model name, or a human name in paste mode) — distinct from `method`.

## Step 4 — Parse the verdict

Parse the reply's first line: `VERDICT:` then one of `holds` / `holds-with-caveat` / `refuted` (case-insensitive). If a `CAVEAT:` line is present, fold it into the notes. Everything after is the justification → notes.

`holds-with-caveat` is **distinct** from `holds` — never collapse it to "holds + a note". A caveated necessary edge is operationally different (it follows only under an explicit assumption) and warrants a structural fix in Step 5.

## Step 5 — Record + act on the verdict

Always: `record_edge_verification(session_id, edge_id, verified_by, method, verdict, notes, prompt_hash)`. Append-only — re-verification adds a row, preserving history. Compute `prompt_hash` from the rendered Step-2 prompt (drift audit).

Then, by verdict — **propose, never auto-mutate the graph's epistemic structure**:

- **holds** → done. The necessary claim stands.
- **holds-with-caveat** → propose one of: (a) rewrite the from-node to fold in the assumption, (b) add an intermediate `assumption` node + edge making the dependency explicit, or (c) weaken the edge to `layer='selective'` via `set_edge_layer`. Let the user choose.
- **refuted** → the necessary claim failed. **Do NOT auto-downgrade** (a verifier can misread terms, miss an implicit assumption, or be weaker than the original model). Instead: the record is written (marking the edge as having a refutation = "needs adjudication"), and you *propose* downgrading via `set_edge_layer(layer='selective')` or `set_edge_layer(layer='invalid')` (kept for audit, not deleted). Only auto-apply a downgrade if the user has set an explicit policy (e.g. two independent refutations).

## Composition & boundaries

- Separate from `/fcot` (construct vs. check have opposite incentives). A higher-level flow may chain construct→verify, but the primitives stay separate.
- This skill never edits End nodes (§5.0 End modification gate still applies to any proposed structural fix).
- The verification obligation is **edge-local**: this skill only ever concerns the specific edge(s) selected, never an inferred surrounding region.

---

## Feedback footer

After completing a meaningful response (not for trivial status output), print exactly one line at the very end:

> 💬 Hit a bug or have feedback on DPD? Run `/dpd-feedback "<short description>"` or open an issue at https://github.com/o3co/agent-dpd/issues/new

Keep it to one line. Once per skill invocation is enough.
