"""Read-side payload contract for the listing tools: cursors, limits, projection."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Mapping, Sequence

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

SUMMARY_FIELDS = ("id", "type", "text", "parent_id", "parent_kind", "state", "severity")


def validate_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(f"limit must be an int in 1..{MAX_LIMIT}, got {limit!r}")
    if not (1 <= limit <= MAX_LIMIT):
        raise ValueError(f"limit must be in 1..{MAX_LIMIT}, got {limit}")
    return limit


def make_filter_key(*, root_id: str | None, state: str | None,
                    node_type: str | None, **extra: Any) -> str:
    payload = {"root_id": root_id, "state": state, "type": node_type, **extra}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def encode_cursor(rowid: int, filter_key: str) -> str:
    raw = json.dumps({"r": rowid, "f": filter_key}, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str, filter_key: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw)
        rowid = payload["r"]
        embedded = payload["f"]
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"malformed cursor: {cursor!r}") from exc
    if embedded != filter_key:
        raise ValueError(
            "cursor was issued for different filter arguments; "
            "restart pagination without a cursor when changing filters"
        )
    if not isinstance(rowid, int) or isinstance(rowid, bool):
        raise ValueError(f"malformed cursor rowid: {rowid!r}")
    return rowid


def seek_and_limit(rows: Sequence[Mapping[str, Any]], *,
                   after_rowid: int | None, limit: int | None) -> list[Mapping[str, Any]]:
    out = [r for r in rows if after_rowid is None or r["rowid"] > after_rowid]
    return out[:limit] if limit is not None else out


# Full column set of the nodes table (minus implicit rowid), used to validate
# explicit `fields` requests so an unknown column is a hard error, not a silent drop.
NODE_COLUMNS = frozenset({
    "id", "session_id", "type", "text", "status", "closure_reason",
    "parent_id", "parent_kind", "paired_for", "achievement_conditions",
    "achievement_conditions_satisfied", "state", "severity", "provenance",
    "archived_at", "closed_at", "deletable_at", "created_at", "updated_at",
})


def validate_projection(fields: Any, text_preview: int | None) -> None:
    """Validate projection args at the boundary, before any query runs."""
    if fields is not None and fields != "*" and not isinstance(fields, (list, tuple)):
        raise ValueError(f"fields must be None, '*', or a list of column names, got {fields!r}")
    if isinstance(fields, (list, tuple)):
        unknown = [f for f in fields if f not in NODE_COLUMNS]
        if unknown:
            raise ValueError(f"unknown fields requested: {unknown}")
    if text_preview is not None:
        if isinstance(text_preview, bool) or not isinstance(text_preview, int) or text_preview <= 0:
            raise ValueError(f"text_preview must be a positive int, got {text_preview!r}")


def project(row: Mapping[str, Any], *, fields: Any, text_preview: int | None) -> dict[str, Any]:
    validate_projection(fields, text_preview)
    full = {k: row[k] for k in row.keys() if k != "rowid"}
    if fields is None:
        selected = {k: full[k] for k in SUMMARY_FIELDS if k in full}
    elif fields == "*":
        selected = full
    else:  # list/tuple — already validated
        selected = {k: full[k] for k in fields if k in full}
    if text_preview is not None:
        text = selected.get("text")
        if isinstance(text, str) and len(text) > text_preview:
            selected = {**selected, "text": text[:text_preview],
                        "text_truncated": True, "text_len": len(text)}
    return selected
