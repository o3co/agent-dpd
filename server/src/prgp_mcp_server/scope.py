"""Resolve the agent scope from MCP-supplied workspace roots.

Per spec §6.3 + §6.5: the MCP `roots/list` request returns the agent
scope root (not sub-scopes). This module locates the agent scope and
encodes its absolute path into the per-machine sqlite directory name
used under `~/.claude/prgp-server/data/`.
"""

from __future__ import annotations

from pathlib import Path


class AgentScopeResolutionError(RuntimeError):
    """Raised when no usable root is supplied and no override is set."""


def resolve_agent_scope(roots: list[str]) -> str:
    """Return the encoded agent-scope path used as sqlite directory name."""
    if not roots:
        raise AgentScopeResolutionError(
            "client exposed no roots; cannot determine agent scope"
        )

    head = roots[0]
    if head.startswith("file://"):
        head = head[len("file://"):]
    start = Path(head)

    for candidate in [start, *start.parents]:
        if (candidate / "Makefile").exists() and (candidate / "AGENTS.md").exists():
            return encode_agent_scope_path(candidate)

    return encode_agent_scope_path(start)


def encode_agent_scope_path(path: Path) -> str:
    """Encode an absolute path into the directory-safe form used for storage."""
    if not path.is_absolute():
        raise ValueError(f"path must be absolute, got: {path}")
    return str(path).replace("/", "-")
