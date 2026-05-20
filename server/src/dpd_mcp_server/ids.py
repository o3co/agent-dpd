"""Generate short, prefixed identifiers for sessions, roots, and nodes."""

from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    """Return ``<prefix>_<12 hex chars>``."""
    return f"{prefix}_{secrets.token_hex(6)}"
