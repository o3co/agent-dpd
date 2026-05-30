from __future__ import annotations

import pytest

from dpd_mcp_server.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    SUMMARY_FIELDS,
    decode_cursor,
    encode_cursor,
    make_filter_key,
    project,
    seek_and_limit,
    validate_limit,
    validate_projection,
)


def test_validate_limit_defaults_when_none():
    assert validate_limit(None) == DEFAULT_LIMIT


def test_validate_limit_passes_through_in_range():
    assert validate_limit(1) == 1
    assert validate_limit(MAX_LIMIT) == MAX_LIMIT


@pytest.mark.parametrize("bad", [0, -1, MAX_LIMIT + 1, "5", 1.5, True])
def test_validate_limit_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        validate_limit(bad)


def test_cursor_round_trips_under_matching_filter_key():
    fk = make_filter_key(root_id="root_a", state="active", node_type=None)
    token = encode_cursor(42, fk)
    assert isinstance(token, str)
    assert decode_cursor(token, fk) == 42


def test_cursor_rejects_mismatched_filter_key():
    fk1 = make_filter_key(root_id="root_a", state=None, node_type=None)
    fk2 = make_filter_key(root_id="root_b", state=None, node_type=None)
    token = encode_cursor(7, fk1)
    with pytest.raises(ValueError):
        decode_cursor(token, fk2)


def test_cursor_rejects_malformed_token():
    fk = make_filter_key(root_id=None, state=None, node_type=None)
    with pytest.raises(ValueError):
        decode_cursor("not-base64!!", fk)


def test_make_filter_key_distinguishes_none_from_value():
    assert make_filter_key(root_id=None, state=None, node_type=None) != \
        make_filter_key(root_id="", state=None, node_type=None)


def test_make_filter_key_includes_extra_kwargs_for_unblocked():
    a = make_filter_key(root_id=None, state=None, node_type=None, blocker_edge_type="blocks")
    b = make_filter_key(root_id=None, state=None, node_type=None, blocker_edge_type="requires")
    assert a != b


def test_seek_and_limit_filters_by_rowid_and_caps():
    rows = [{"rowid": r} for r in (1, 3, 5, 7, 9)]
    assert [r["rowid"] for r in seek_and_limit(rows, after_rowid=3, limit=2)] == [5, 7]


def test_seek_and_limit_no_seek_no_limit_returns_all():
    rows = [{"rowid": r} for r in (1, 2, 3)]
    assert seek_and_limit(rows, after_rowid=None, limit=None) == rows


def _row(**over):
    base = {
        "rowid": 1, "id": "node_x", "session_id": "ses_1", "type": "question",
        "text": "a fairly long body of text here", "status": "open",
        "closure_reason": None, "parent_id": "root_a", "parent_kind": "root",
        "paired_for": None, "achievement_conditions": None,
        "achievement_conditions_satisfied": 0, "state": "active",
        "severity": None, "provenance": "user", "archived_at": None,
        "closed_at": None, "deletable_at": None,
        "created_at": "2026-05-29T00:00:00Z", "updated_at": "2026-05-29T00:00:00Z",
    }
    base.update(over)
    return base


def test_project_default_is_summary_fields_only_and_strips_rowid():
    out = project(_row(), fields=None, text_preview=None)
    assert set(out) == set(SUMMARY_FIELDS)
    assert "rowid" not in out
    assert "session_id" not in out


def test_project_star_returns_all_columns_except_rowid():
    out = project(_row(), fields="*", text_preview=None)
    assert "session_id" in out and "created_at" in out
    assert "rowid" not in out


def test_project_explicit_fields_subset():
    out = project(_row(), fields=["id", "type"], text_preview=None)
    assert set(out) == {"id", "type"}


def test_project_unknown_explicit_field_raises():
    with pytest.raises(ValueError):
        project(_row(), fields=["id", "no_such_col"], text_preview=None)


def test_project_text_preview_truncates_and_flags():
    out = project(_row(text="x" * 50), fields=None, text_preview=10)
    assert out["text"] == "x" * 10
    assert out["text_truncated"] is True
    assert out["text_len"] == 50


def test_project_text_preview_no_truncation_when_short():
    out = project(_row(text="short"), fields=None, text_preview=10)
    assert out["text"] == "short"
    assert "text_truncated" not in out


@pytest.mark.parametrize("bad", [0, -1, "5", 1.5, True])
def test_project_rejects_bad_text_preview(bad):
    with pytest.raises(ValueError):
        project(_row(), fields=None, text_preview=bad)


def test_project_text_preview_noops_when_text_not_selected():
    out = project(_row(text="x" * 50), fields=["id"], text_preview=10)
    assert out == {"id": "node_x"}
    assert "text_truncated" not in out


def test_validate_projection_accepts_valid_and_rejects_invalid():
    validate_projection(None, None)
    validate_projection("*", None)
    validate_projection(["id", "type"], 10)
    for bad_fields in (["nope"], 123):
        with pytest.raises(ValueError):
            validate_projection(bad_fields, None)
    for bad_tp in (0, -1, True, "5"):
        with pytest.raises(ValueError):
            validate_projection(None, bad_tp)
