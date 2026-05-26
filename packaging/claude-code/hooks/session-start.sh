#!/usr/bin/env bash
# DPD plugin session-start hook: lazy-bootstrap a venv at ${CLAUDE_PLUGIN_DATA}/.venv
# with the dpd-mcp-server package installed. Idempotent: only reinstalls when
# core/server/pyproject.toml hash changes.

set -euo pipefail

: "${CLAUDE_PLUGIN_DATA:?CLAUDE_PLUGIN_DATA must be set by Claude Code}"
: "${CLAUDE_PLUGIN_ROOT:?CLAUDE_PLUGIN_ROOT must be set by Claude Code}"

VENV="$CLAUDE_PLUGIN_DATA/.venv"
SERVER_SRC="$CLAUDE_PLUGIN_ROOT/core/server"
PYPROJECT="$SERVER_SRC/pyproject.toml"
HASH_FILE="$VENV/.requirements-hash"

if [ ! -f "$PYPROJECT" ]; then
  echo "session-start.sh: ERROR: $PYPROJECT not found" >&2
  exit 1
fi

# Compute hash of pyproject.toml (the dependency source of truth)
# shasum is macOS-native; sha256sum is Linux. Try both.
if command -v shasum >/dev/null 2>&1; then
  CURRENT_HASH=$(shasum -a 256 "$PYPROJECT" | awk '{print $1}')
elif command -v sha256sum >/dev/null 2>&1; then
  CURRENT_HASH=$(sha256sum "$PYPROJECT" | awk '{print $1}')
else
  echo "session-start.sh: ERROR: neither shasum nor sha256sum found in PATH" >&2
  exit 1
fi

# Quick exit if venv exists + hash matches + binary present
if [ -f "$HASH_FILE" ] && [ "$(cat "$HASH_FILE")" = "$CURRENT_HASH" ] && [ -f "$VENV/bin/dpd-mcp-server" ]; then
  exit 0
fi

# Healthcheck: if the venv's python exists but no longer runs (e.g. Homebrew
# upgraded the underlying python and broke shebangs), nuke the venv so the
# create-if-missing branch below rebuilds it from scratch.
if [ -f "$VENV/bin/python" ] && ! "$VENV/bin/python" -c "import sys" >/dev/null 2>&1; then
  rm -rf "$VENV"
fi

# Find python3.11 (or any 3.11+ python)
PY=""
for candidate in python3.11 python3.12 python3.13 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "session-start.sh: ERROR: no python3.11+ found in PATH" >&2
  exit 1
fi

# Enforce python 3.11+: bare `python3` can resolve to 3.10 or older on some
# distros, which would otherwise surface as a confusing pip resolver error.
if ! "$PY" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1; then
  PY_VER=$("$PY" --version 2>&1 || echo "unknown")
  echo "session-start.sh: ERROR: $PY is older than required python 3.11 (found: $PY_VER)" >&2
  exit 1
fi

# Create or recreate venv
mkdir -p "$CLAUDE_PLUGIN_DATA"
if [ ! -f "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi

# Install / upgrade dpd-mcp-server in editable mode pointing at the plugin's bundled src
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$SERVER_SRC"

# Record hash for next-run idempotency check
echo "$CURRENT_HASH" > "$HASH_FILE"
