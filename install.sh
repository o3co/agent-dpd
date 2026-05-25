#!/usr/bin/env bash
# DPD installer — sets up venv, installs the MCP server package, links skills,
# and registers the MCP server with Claude Code. Idempotent: running twice is safe.
#
# Usage:
#   ./install.sh                                        (from inside a clone)
#   curl -fsSL https://raw.githubusercontent.com/o3co/agent-dpd/main/install.sh | bash
#                                                        (one-liner; clones if needed)
#
# Env vars:
#   DPD_INSTALL_DIR     where to clone if not already in one (default: $HOME/agent-dpd)
#   DPD_PYTHON          Python interpreter to use (default: python3.11)
#   DPD_NO_REGISTER     set to skip `claude mcp add` (default: not set)
#   DPD_NO_SKILL_LINK   set to skip symlinking skills into ~/.claude/skills/ (default: not set)

set -euo pipefail

DPD_INSTALL_DIR="${DPD_INSTALL_DIR:-$HOME/agent-dpd}"
DPD_PYTHON="${DPD_PYTHON:-python3.11}"
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
  [ -f "$repo_dir/skill/SKILL.md" ] \
    || die "No skill/SKILL.md at $repo_dir/skill — is this a valid DPD checkout?"
  mkdir -p "$skills_home"
  link_one_skill "$repo_dir/skill" "$skills_home/dpd"
  local sub name
  for sub in "$repo_dir"/skill/*/; do
    [ -f "$sub/SKILL.md" ] || continue
    name=$(basename "$sub")
    link_one_skill "${sub%/}" "$skills_home/$name"
  done
}

# --- main flow ---------------------------------------------------------------

main() {
  # 1. Locate or clone the repo
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

  # 2. Verify Python
  if ! command -v "$DPD_PYTHON" >/dev/null 2>&1; then
    die "$DPD_PYTHON not found. Install Python 3.11+ first (e.g., 'brew install python@3.11')."
  fi

  # 3. Create venv + install package
  VENV="$REPO_DIR/mcp/.venv"
  say "Setting up venv at $VENV"
  "$DPD_PYTHON" -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -e "$REPO_DIR/mcp[dev]"

  # 4. Register MCP server with Claude Code
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

  # 5. Link skills into ~/.claude/skills/ so /dpd and sub-commands are discoverable
  if [ -z "${DPD_NO_SKILL_LINK:-}" ]; then
    say "Linking skills into $HOME/.claude/skills/"
    link_skills "$REPO_DIR" "$HOME/.claude/skills"
  else
    say "Skipping skill linking (DPD_NO_SKILL_LINK set)."
  fi

  # 6. Done
  cat <<EOF

✓ DPD installed at $REPO_DIR

Next steps:
  1. Restart Claude Code so it discovers the new MCP server and skills.
  2. From any project, type /dpd to start a session.

EOF
}

# Run main only when executed directly, not when sourced (e.g. for testing).
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
