#!/usr/bin/env bash
# DPD install.sh — escape-hatch installer for non-Claude-Code targets.
#
# This script is the install path for:
#   - Cursor (symlinks core/skills/* into ~/.cursor/skills/ + patches ~/.cursor/mcp.json)
#   - CI smoke tests
#   - Developers who want a raw editable install without the Claude Code plugin system
#
# Claude Code users should install via the plugin system instead:
#   /plugin marketplace add o3co/agent-dpd
#   /plugin install dpd@agent-dpd
#
# Usage:
#   ./install.sh                                        (from inside a clone)
#   curl -fsSL https://raw.githubusercontent.com/o3co/agent-dpd/main/install.sh | bash
#                                                        (one-liner; clones if needed)
#
# Env vars:
#   DPD_INSTALL_DIR             where to clone if not in one (default: $HOME/agent-dpd)
#   DPD_PYTHON                  Python interpreter (default: python3.11)
#   DPD_TARGET                  comma-separated targets (default: cursor; options: cursor, venv-only)
#   DPD_CURSOR_HOME             cursor config dir (default: $HOME/.cursor)
#   DPD_NO_CURSOR_SKILL_LINK    skip symlinking into Cursor skills dir
#   DPD_NO_CURSOR_MCP_PATCH     skip patching ~/.cursor/mcp.json

set -euo pipefail

DPD_INSTALL_DIR="${DPD_INSTALL_DIR:-$HOME/agent-dpd}"
DPD_PYTHON="${DPD_PYTHON:-python3.11}"
DPD_TARGET="${DPD_TARGET:-cursor}"
DPD_CURSOR_HOME="${DPD_CURSOR_HOME:-$HOME/.cursor}"
REPO_URL="https://github.com/o3co/agent-dpd.git"

say() { printf '%s\n' "==> $*"; }
die() { printf '%s\n' "ERROR: $*" >&2; exit 1; }

# --- skill linking (pure functions, exercised by tests) ----------------------

link_one_skill() {
  local src="$1"
  local target="$2"
  if [ -L "$target" ]; then
    rm "$target"
  elif [ -e "$target" ]; then
    die "DPD installer refuses to overwrite non-symlink at $target. Remove it manually then re-run install.sh."
  fi
  ln -s "$src" "$target"
}

link_skills() {
  local repo_dir="$1"
  local skills_home="$2"
  [ -f "$repo_dir/core/skills/dpd/SKILL.md" ] \
    || die "No core/skills/dpd/SKILL.md at $repo_dir/core/skills/dpd — is this a valid DPD checkout?"
  mkdir -p "$skills_home"
  local sub name
  for sub in "$repo_dir"/core/skills/*/; do
    [ -f "$sub/SKILL.md" ] || continue
    name=$(basename "$sub")
    link_one_skill "${sub%/}" "$skills_home/$name"
  done
}

# --- Cursor mcp.json patching (pure function, exercised by tests) ------------

patch_cursor_mcp() {
  local mcp_json="$1"
  local server_cmd="$2"
  mkdir -p "$(dirname "$mcp_json")"
  python3 - "$mcp_json" "$server_cmd" <<'PY'
import json
import os
import sys
mcp_path, cmd = sys.argv[1], sys.argv[2]
if os.path.exists(mcp_path):
    with open(mcp_path) as f:
        data = json.load(f)
else:
    data = {}
data.setdefault("mcpServers", {})
data["mcpServers"]["dpd-mcp-server"] = {"command": cmd, "args": []}
with open(mcp_path, "w") as f:
    json.dump(data, f, indent=2)
PY
}

# --- main flow ---------------------------------------------------------------

main() {
  # 1. Locate or clone the repo
  if [ -f "./core/server/pyproject.toml" ] && [ -d "./.git" ]; then
    REPO_DIR="$(pwd)"
    say "Installing in place: $REPO_DIR"
  else
    if [ -d "$DPD_INSTALL_DIR/.git" ]; then
      say "Updating existing clone at $DPD_INSTALL_DIR"
      git -C "$DPD_INSTALL_DIR" pull --ff-only
    else
      say "Cloning to $DPD_INSTALL_DIR"
      git clone --depth 1 "$REPO_URL" "$DPD_INSTALL_DIR"
    fi
    REPO_DIR="$DPD_INSTALL_DIR"
  fi

  # 2. Verify Python
  command -v "$DPD_PYTHON" >/dev/null 2>&1 \
    || die "$DPD_PYTHON not found. Install Python 3.11+ first (e.g., 'brew install python@3.11')."

  # 3. Create venv + install package
  VENV="$REPO_DIR/core/server/.venv"
  say "Setting up venv at $VENV"
  "$DPD_PYTHON" -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -e "$REPO_DIR/core/server[dev]"

  # 4. Cursor target
  if printf '%s\n' "${DPD_TARGET}" | grep -qw cursor; then
    if [ -z "${DPD_NO_CURSOR_SKILL_LINK:-}" ]; then
      say "Linking skills into $DPD_CURSOR_HOME/skills/"
      link_skills "$REPO_DIR" "$DPD_CURSOR_HOME/skills"
    fi
    if [ -z "${DPD_NO_CURSOR_MCP_PATCH:-}" ]; then
      say "Patching $DPD_CURSOR_HOME/mcp.json with dpd-mcp-server"
      patch_cursor_mcp "$DPD_CURSOR_HOME/mcp.json" "$VENV/bin/dpd-mcp-server"
    fi
  fi

  # 5. Done
  cat <<EOF

✓ DPD installed at $REPO_DIR

Cursor users:
  Restart Cursor so it picks up the new skills + MCP server.

Claude Code users:
  install.sh does NOT install for Claude Code. Use the plugin system:
    /plugin marketplace add o3co/agent-dpd
    /plugin install dpd@agent-dpd

EOF
}

# Run main only when executed directly, not when sourced (e.g. for testing).
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
