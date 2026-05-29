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
[project.scripts]
dpd-mcp-server = "dpd_mcp_server:noop"
[tool.hatch.build.targets.wheel]
packages = ["src/dpd_mcp_server"]
EOF
cat > "$CLAUDE_PLUGIN_ROOT/core/server/src/dpd_mcp_server/__init__.py" <<'EOF'
def noop():
    pass
EOF

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

# Test 3: changing pyproject.toml triggers reinstall (hash advances AND pip
# actually processes the new pyproject — not just a hash-file rewrite).
# Use a version bump because pip's installed dist-info directory is named
# `<name>-<version>.dist-info` and renames atomically on reinstall — that's
# direct evidence pip ran, regardless of pip's wrapper-rewrite optimizations.
# `|| true` guards against `set -euo pipefail` killing the script when grep finds
# no match — we want the explicit FAIL diagnostic below to fire instead.
DIST_BEFORE=$(ls "$CLAUDE_PLUGIN_DATA/.venv/lib"/python*/site-packages/ 2>/dev/null | grep -oE 'dpd_mcp_server-[0-9.]+\.dist-info' | head -1 || true)
[ -n "$DIST_BEFORE" ] || { echo "FAIL: dist-info dir missing after Test 1/2 — pip never ran?"; exit 1; }
# Cross-platform in-place edit (macOS sed wants -i '', GNU sed wants -i alone);
# `-i.bak` with explicit suffix removal works on both.
sed -i.bak 's/version = "0.4.0"/version = "0.4.1"/' "$CLAUDE_PLUGIN_ROOT/core/server/pyproject.toml"
rm -f "$CLAUDE_PLUGIN_ROOT/core/server/pyproject.toml.bak"
"$HOOK"
HASH3=$(cat "$CLAUDE_PLUGIN_DATA/.venv/.requirements-hash")
[ "$HASH3" != "$HASH2" ] || { echo "FAIL: hash unchanged after pyproject.toml edit"; exit 1; }
DIST_AFTER=$(ls "$CLAUDE_PLUGIN_DATA/.venv/lib"/python*/site-packages/ 2>/dev/null | grep -oE 'dpd_mcp_server-[0-9.]+\.dist-info' | head -1 || true)
[ "$DIST_AFTER" = "dpd_mcp_server-0.4.1.dist-info" ] \
  || { echo "FAIL: dist-info did not reflect version bump (was: $DIST_BEFORE, now: $DIST_AFTER) — pip didn't actually run"; exit 1; }
echo "OK: pyproject.toml change triggers reinstall (hash + dist-info both advanced)"

# Test 4: actual packaging/claude-code/ layout resolves core/server/pyproject.toml
# (regression guard for the marketplace install layout — symlink dereference at install
# time means CLAUDE_PLUGIN_ROOT/core/server must resolve from the plugin source dir.)
ACTUAL_ROOT="$(cd "$HOOK_DIR/.." && pwd)"
test -f "$ACTUAL_ROOT/core/server/pyproject.toml" \
  || { echo "FAIL: real packaging/claude-code/core/server/pyproject.toml does not resolve from \$CLAUDE_PLUGIN_ROOT"; exit 1; }
echo "OK: packaging/claude-code/core/server/pyproject.toml resolves (marketplace install bootstrap path)"

# Test 5: broken venv python triggers healthcheck recreate
# (simulates Homebrew updating the underlying python and breaking shebangs.)
# Bust the hash quick-exit by removing the server binary, then break the python
# binary so the healthcheck must `rm -rf` and rebuild from scratch.
# CRITICAL: $VENV/bin/python is a symlink chain into the host python install
# (e.g. /opt/homebrew/Cellar/python@3.11/.../python3.11). A naive `cat >` would
# follow the chain and overwrite the host python binary. Remove the symlinks
# first, then write a regular file in their place.
rm -f "$CLAUDE_PLUGIN_DATA/.venv/bin/dpd-mcp-server"
rm -f "$CLAUDE_PLUGIN_DATA/.venv/bin/python" \
      "$CLAUDE_PLUGIN_DATA/.venv/bin/python3" \
      "$CLAUDE_PLUGIN_DATA/.venv/bin/python3.11"
cat > "$CLAUDE_PLUGIN_DATA/.venv/bin/python" <<'PYEOF'
#!/usr/bin/env bash
exit 1
PYEOF
chmod +x "$CLAUDE_PLUGIN_DATA/.venv/bin/python"
"$HOOK"
"$CLAUDE_PLUGIN_DATA/.venv/bin/python" -c "import sys" >/dev/null 2>&1 \
  || { echo "FAIL: broken venv python not healed by healthcheck"; exit 1; }
test -f "$CLAUDE_PLUGIN_DATA/.venv/bin/dpd-mcp-server" \
  || { echo "FAIL: server binary missing after healthcheck recreate"; exit 1; }
echo "OK: broken venv python triggers healthcheck recreate"

# Test 6: python < 3.11 is rejected with a clear error
# Wipe the venv so the host-python branch is taken, and put a fake python3.11
# (that reports 3.10) at the front of PATH.
rm -rf "$CLAUDE_PLUGIN_DATA/.venv"
FAKE_BIN="$TMPDIR/fake-py-bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/python3.11" <<'PYEOF'
#!/usr/bin/env bash
# Pretend to be Python 3.10 — fails the >= 3.11 version check.
case "${1:-}" in
  --version) echo "Python 3.10.0"; exit 0 ;;
  -c)
    case "${2:-}" in
      *"sys.version_info >= (3, 11)"*) exit 1 ;;
      *) exit 0 ;;
    esac ;;
esac
exit 0
PYEOF
chmod +x "$FAKE_BIN/python3.11"

# Capture to a variable so `set -o pipefail` doesn't trip on the hook's
# expected non-zero exit. The hook is *supposed* to exit non-zero here.
T6_OUTPUT=$(PATH="$FAKE_BIN:$PATH" "$HOOK" 2>&1 || true)
if echo "$T6_OUTPUT" | grep -q "older than required python 3.11"; then
  echo "OK: python < 3.11 rejected with clear error"
else
  echo "FAIL: hook did not emit the expected version error" >&2
  echo "  actual output: $T6_OUTPUT" >&2
  exit 1
fi

# Test 7: marketplace git-subdir extraction is self-contained (#69 regression guard).
# Test 4 above resolves core/ through the FULL repo checkout, where the
# packaging/claude-code/core symlink dereferences fine — so it stayed green while
# the PUBLISHED artifact was broken. The marketplace (`git-subdir`) extracts ONLY
# packaging/claude-code/; anything its `core` entry points at OUTSIDE that subtree
# is left dangling. Reproduce that extraction boundary exactly and assert the
# bootstrap path resolves. This is what Test 4 could not see.
REPO_ROOT="$(git -C "$HOOK_DIR/.." rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || { echo "FAIL: Test 7 must run inside the git repo"; exit 1; }
EXTRACT="$TMPDIR/subdir-extract"
mkdir -p "$EXTRACT"
# git archive ships COMMITTED tracked content exactly as git-subdir would.
git -C "$REPO_ROOT" archive HEAD packaging/claude-code | tar -x -C "$EXTRACT"
test -f "$EXTRACT/packaging/claude-code/core/server/pyproject.toml" \
  || { echo "FAIL: core/server/pyproject.toml does NOT resolve inside an extracted packaging/claude-code/ subtree (#69: core escapes the subtree)"; exit 1; }
echo "OK: extracted packaging/claude-code/ subtree is self-contained (#69 marketplace bootstrap path)"

echo "All session-start.sh tests passed."
