#!/usr/bin/env bash
# Tests for hooks/session-start.sh — verifies venv is created/refreshed correctly.
set -euo pipefail

# Setup: temp CLAUDE_PLUGIN_DATA + CLAUDE_PLUGIN_ROOT pointing at a fake src
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

export CLAUDE_PLUGIN_DATA="$TMPDIR/data"
export CLAUDE_PLUGIN_ROOT="$TMPDIR/root"

# Fake plugin root with a minimal core/server (just pyproject.toml as marker for hash)
mkdir -p "$CLAUDE_PLUGIN_ROOT/core/server/src/dpd_mcp_server"
cat > "$CLAUDE_PLUGIN_ROOT/core/server/pyproject.toml" <<'EOF'
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "dpd-mcp-server"
version = "0.4.0"
requires-python = ">=3.11"
[tool.hatch.build.targets.wheel]
packages = ["src/dpd_mcp_server"]
EOF
touch "$CLAUDE_PLUGIN_ROOT/core/server/src/dpd_mcp_server/__init__.py"

HOOK_DIR="$(dirname "$0")/.."
HOOK="$HOOK_DIR/session-start.sh"

# Test 1: fresh run creates venv
"$HOOK"
test -f "$CLAUDE_PLUGIN_DATA/.venv/bin/python" || { echo "FAIL: venv not created"; exit 1; }
test -f "$CLAUDE_PLUGIN_DATA/.venv/.requirements-hash" || { echo "FAIL: hash file not written"; exit 1; }
echo "OK: fresh run creates venv + hash file"

# Test 2: second run with same pyproject.toml is no-op (does not reinstall)
HASH1=$(cat "$CLAUDE_PLUGIN_DATA/.venv/.requirements-hash")
"$HOOK"
HASH2=$(cat "$CLAUDE_PLUGIN_DATA/.venv/.requirements-hash")
[ "$HASH1" = "$HASH2" ] || { echo "FAIL: hash changed across identical runs"; exit 1; }
echo "OK: idempotent on identical state"

# Test 3: changing pyproject.toml triggers reinstall
echo "# trivial comment edit" >> "$CLAUDE_PLUGIN_ROOT/core/server/pyproject.toml"
"$HOOK"
HASH3=$(cat "$CLAUDE_PLUGIN_DATA/.venv/.requirements-hash")
[ "$HASH3" != "$HASH2" ] || { echo "FAIL: hash unchanged after pyproject.toml edit"; exit 1; }
echo "OK: pyproject.toml change triggers reinstall"

echo "All session-start.sh tests passed."
