#!/usr/bin/env bash
# Validates hooks/hooks.json against Claude Code's plugin hook-manifest schema.
# Claude Code requires a top-level "hooks" record mapping event names to arrays
# (same shape as settings.json's `hooks` field). A bare top-level event key —
# e.g. {"SessionStart": [...]} with no wrapper — fails to load with:
#   "Invalid input: expected record, received undefined" at path ["hooks"]
# which silently disables the SessionStart bootstrap and breaks the MCP server.
set -euo pipefail

HOOKS_JSON="$(cd "$(dirname "$0")/.." && pwd)/hooks.json"

python3 - "$HOOKS_JSON" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    doc = json.load(f)

assert isinstance(doc.get("hooks"), dict), \
    "hooks.json must have a top-level 'hooks' object (event wrapper) — " \
    "a bare top-level event key fails Claude Code's manifest schema"
assert "SessionStart" in doc["hooks"], \
    "hooks.hooks.SessionStart must be defined (bootstraps the MCP server venv)"
assert isinstance(doc["hooks"]["SessionStart"], list), \
    "hooks.hooks.SessionStart must be a list of matcher blocks"
PY

echo "OK: hooks.json has a valid top-level 'hooks' wrapper with SessionStart"
