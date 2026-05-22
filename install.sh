#!/usr/bin/env bash
# DPD installer — sets up venv, installs the MCP server package, and registers
# it with Claude Code. Idempotent: running twice is safe.
#
# Usage:
#   ./install.sh                                        (from inside a clone)
#   curl -fsSL https://raw.githubusercontent.com/o3co/agent-dpd/main/install.sh | bash
#                                                        (one-liner; clones if needed)
#
# Env vars:
#   DPD_INSTALL_DIR   where to clone if not already in one (default: $HOME/agent-dpd)
#   DPD_PYTHON        Python interpreter to use (default: python3.11)
#   DPD_NO_REGISTER   set to skip `claude mcp add` (default: not set)

set -euo pipefail

DPD_INSTALL_DIR="${DPD_INSTALL_DIR:-$HOME/agent-dpd}"
DPD_PYTHON="${DPD_PYTHON:-python3.11}"
REPO_URL="https://github.com/o3co/agent-dpd.git"

say() { printf '%s\n' "==> $*"; }
die() { printf '%s\n' "ERROR: $*" >&2; exit 1; }

# --- 1. Locate or clone the repo ---------------------------------------------

if [ -f "./mcp/pyproject.toml" ] && [ -d "./.git" ]; then
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

# --- 2. Verify Python --------------------------------------------------------

if ! command -v "$DPD_PYTHON" >/dev/null 2>&1; then
  die "$DPD_PYTHON not found. Install Python 3.11+ first (e.g., 'brew install python@3.11')."
fi

# --- 3. Create venv + install package ----------------------------------------

VENV="$REPO_DIR/mcp/.venv"
say "Setting up venv at $VENV"
"$DPD_PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$REPO_DIR/mcp[dev]"

# --- 4. Register MCP server with Claude Code ---------------------------------

if [ -z "${DPD_NO_REGISTER:-}" ]; then
  if command -v claude >/dev/null 2>&1; then
    say "Registering dpd-mcp-server with Claude Code"
    claude mcp remove dpd-mcp-server 2>/dev/null || true
    claude mcp add dpd-mcp-server -- "$VENV/bin/dpd-mcp-server"
  else
    say "Skipping MCP registration: 'claude' CLI not found."
    say "Register manually later with:"
    say "  claude mcp add dpd-mcp-server -- $VENV/bin/dpd-mcp-server"
  fi
else
  say "Skipping MCP registration (DPD_NO_REGISTER set)."
fi

# --- 5. Done -----------------------------------------------------------------

cat <<EOF

✓ DPD installed at $REPO_DIR

Next steps:
  1. Restart Claude Code so it discovers the new MCP server.
  2. From any project, type /dpd to start a session.

EOF
