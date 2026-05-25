---
name: dpd-feedback
description: Submit DPD dogfood feedback as a GitHub issue. Collects system metadata only (version, OS, agent, Python version, optional stack trace) — does NOT send any session content (no node text, scope, label, Pool, rejected hypotheses, or session_id). User free-text description is the primary payload. Graph context is opt-in via manual copy-paste only.
---

# /dpd-feedback — Submit dogfood feedback as a GitHub issue

Announce: "Using dpd-feedback skill."

## Purpose

Surface friction / bugs / feature suggestions you noticed while using DPD, as GitHub issues on the canonical repo. Cheap, low-friction path: `/dpd-feedback "<short description>"` produces a pre-filled issue draft, you confirm, it gets submitted.

## Privacy hard constraint (NON-NEGOTIABLE)

**This skill MUST NOT send any session content under any circumstances.** This includes:

- Node text (any of it — no decision text, hypothesis text, evidence text, rationale text, ANY node text)
- Scope name
- Session label
- Session id (correlation key, exclude)
- Pool item content
- Rejected hypothesis text
- Mermaid / YAML / markdown exports of the graph

Allowed payload:

1. **User free-text description** (the `<short description>` argument and any follow-up the user types when prompted to expand)
2. **System metadata only**:
   - DPD plugin version (read from plugin.json)
   - `dpd-mcp-server` Python package version (`pip show dpd-mcp-server`)
   - Agent name (Claude Code / Cursor / Codex / etc. — detect from env)
   - Python version (`python3 --version`)
   - OS name + version (`uname -a` minimized to OS family)
   - Optional: stack trace if the user opted to attach one for an error case

If the user wants to attach graph context for a bug report, the skill MUST ask them to manually copy-paste the specific part they choose to share.

**Tool-call policy (allow-list, NOT deny-list):**

The skill MUST NOT call ANY `mcp__dpd-mcp-server__*` tool for the purpose of composing the feedback body. This is a categorical prohibition — every DPD MCP tool is forbidden in this skill's scope, including but not limited to `export_mermaid`, `export_yaml`, `get_node`, `get_session_state`, `walk_subtree`, `list_open_nodes`, `list_active_roots`, `list_sessions`, `list_edges`, `find_similar`, `pool_list`, `dump_persist`. The enumeration is illustrative; the rule is the categorical one.

The only data sources allowed for the feedback body are:
1. The user's free-text input (the `<short description>` argument and follow-ups)
2. The system metadata listed under "Allowed payload" above (read via shell, NOT via MCP)
3. Stack trace text the user explicitly pastes in

If you (Claude) find yourself tempted to call a DPD MCP tool here for "convenience" or "useful context", STOP. The convenience is what the constraint exists to prevent — auto-included graph context denies the user the choice of what to share.

**Why this is non-negotiable:**

- Rejected hypotheses and evaluation process are often more sensitive than final decisions (= internal reasoning, candidate strategies, etc.)
- Scope and label names expose project internal structure
- Pool contains raw thought, often trade-secret-level
- Auto-summary would deny the user the choice of what to share
- session_id is a correlation key that could allow cross-session inference if leaked

If at any point the skill is tempted to include graph content as a "convenience", STOP and ask the user. The default is **don't send**.

## Invocation pattern

```text
/dpd-feedback "short description of the issue / suggestion"
```

If no argument is provided: ask the user for a short description (1-2 sentences).

## Flow

### Step 1: Gather system metadata

Collect (in this order):

```bash
# Plugin version: read packaging/claude-code/.claude-plugin/plugin.json if accessible,
# otherwise note as "unknown"
PLUGIN_VERSION=...

# Server package version
SERVER_VERSION=$(pip show dpd-mcp-server 2>/dev/null | awk '/^Version:/ {print $2}')

# Agent: detect (Claude Code sets CLAUDE_CODE env or similar; fallback to "unknown")
AGENT=...

# Python
PYTHON_VERSION=$(python3 --version 2>&1)

# OS family
OS_INFO=$(uname -sr)
```

### Step 2: Compose issue body (draft)

```text
**System metadata**
- DPD plugin version: {PLUGIN_VERSION}
- dpd-mcp-server package: {SERVER_VERSION}
- Agent: {AGENT}
- Python: {PYTHON_VERSION}
- OS: {OS_INFO}

**Description (from user)**
{user_provided_description}

**Stack trace** (only if user opted in)
{optional_stack_trace}

---
Submitted via /dpd-feedback. No session content (node text / scope / label / Pool) included.
```

### Step 3: Present draft + confirm

Show the user the EXACT issue body that will be sent. Ask: "Submit as GitHub issue? (y/N)".

If user says no: print the draft and stop. Let them edit and use `gh issue create` manually if they want.

If user says yes: ask for issue title (or propose one based on first line of description). Confirm title.

### Step 4: Submit via gh CLI

```bash
gh issue create --repo o3co/agent-dpd \
  --title "<confirmed_title>" \
  --body "<confirmed_body>" \
  --label "feedback"
```

If `gh` is not installed or not authenticated: print the body + a `gh issue create` command the user can run manually, then exit.

### Step 5: Report URL

After successful submission, print the issue URL once and stop. Do NOT add the standard feedback footer (would be recursive).

## Edge cases

- **User wants to include graph context**: ask them to paste the specific Mermaid / YAML / quoted node text into the description themselves. Do not call DPD MCP tools for export.
- **User provides multi-line description with sensitive looking text**: do NOT auto-redact, but do warn before submit if the description appears to include path names / API keys / etc.
- **gh CLI missing**: print manual submission command.
- **Network failure**: print body to stdout, prompt user to copy-paste into a new GitHub issue manually.

## Tone

Brief. The whole interaction should be ~3 exchanges (init → preview → submit). Don't editorialize on the user's feedback.

---

## Feedback footer

This skill does NOT print the standard feedback footer (it would be self-referential / recursive).
