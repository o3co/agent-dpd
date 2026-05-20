"""Resolve the agent scope from MCP-supplied workspace roots.

Per spec §6.3 + §6.5: the MCP `roots/list` request returns the agent
scope root (not sub-scopes). This module locates the agent scope and
encodes its absolute path into the per-machine sqlite directory name
used under `~/.claude/dpd-server/data/`.
"""

from __future__ import annotations

from pathlib import Path


class AgentScopeResolutionError(RuntimeError):
    """Raised when no usable root is supplied and no override is set."""


def resolve_agent_scope(roots: list[str]) -> str:
    """Return the encoded agent-scope path used as sqlite directory name.

    Walks up from roots[0] looking for a `Makefile + AGENTS.md` marker pair,
    but bounded: never matches a marker at `Path.home()` or above. This
    prevents silent misrouting when a user happens to have those files
    in their home directory (or further up the tree).
    """
    if not roots:
        raise AgentScopeResolutionError(
            "client exposed no roots; cannot determine agent scope"
        )

    head = roots[0]
    if head.startswith("file://"):
        head = head[len("file://"):]
    start = Path(head)

    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None

    def _below_home(p: Path) -> bool:
        if home is None:
            return True  # cannot determine; allow
        try:
            resolved = p.resolve()
        except (OSError, RuntimeError):
            return False
        return resolved != home and home in resolved.parents

    for candidate in [start, *start.parents]:
        if (candidate / "Makefile").exists() and (candidate / "AGENTS.md").exists():
            if _below_home(candidate):
                return encode_agent_scope_path(candidate)
            # Match at/above home: refuse, fall through to fallback.
            break
        # Stop walking once we reach home (no point looking higher).
        try:
            if home is not None and candidate.resolve() == home:
                break
        except (OSError, RuntimeError):
            break

    return encode_agent_scope_path(start)


def encode_agent_scope_path(path: Path) -> str:
    """Encode an absolute path into the directory-safe form used for storage."""
    if not path.is_absolute():
        raise ValueError(f"path must be absolute, got: {path}")
    return str(path).replace("/", "-")
