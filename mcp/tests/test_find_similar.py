"""Tests for Storage.find_similar (FTS5 + dynamic open fallback)."""

from __future__ import annotations

import sqlite3

import pytest

from dpd_mcp_server.storage import Storage


def test_normalize_query_strips_and_lowers() -> None:
    assert Storage._normalize_query("  Hello WORLD  ") == "hello world"


def test_normalize_query_returns_empty_when_too_short() -> None:
    assert Storage._normalize_query("ab") == ""
    assert Storage._normalize_query("  a ") == ""
    assert Storage._normalize_query("") == ""


def test_normalize_query_keeps_unicode() -> None:
    assert Storage._normalize_query("  日本語クエリ  ") == "日本語クエリ"


def _seed_two_closed_subgraphs(storage: Storage) -> None:
    """Two closed subgraphs in the default (top-level) scope, with distinct keywords."""
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, scope, started_at, updated_at) "
            "VALUES ('ses_x', NULL, ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_x1', 'ses_x', 'r1', 'active', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_x2', 'ses_x', 'r2', 'active', ?)",
            (now,),
        )
        # Subgraph 1 — about FTS5 trigram tokenizer
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('s1', 'ses_x', 'start', 'FTS5 trigram start', 'closed', "
            "'root_x1', 'root', 'closed', ?, ?, ?)",
            (now, now, now),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, paired_for, achievement_conditions, state, closed_at, "
            "created_at, updated_at) "
            "VALUES ('e1', 'ses_x', 'end', 'tokenizer trigram', 'closed', 's1', "
            "'node', 's1', 'trigram chosen', 'closed', ?, ?, ?)",
            (now, now, now),
        )
        # Subgraph 2 — about ambient overlay paradigm
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('s2', 'ses_x', 'start', 'ambient overlay start', 'closed', "
            "'root_x2', 'root', 'closed', ?, ?, ?)",
            (now, now, now),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, paired_for, achievement_conditions, state, closed_at, "
            "created_at, updated_at) "
            "VALUES ('e2', 'ses_x', 'end', 'ambient overlay design', 'closed', "
            "'s2', 'node', 's2', 'design done', 'closed', ?, ?, ?)",
            (now, now, now),
        )
    storage._reindex_subgraph(start_node_id="s1")
    storage._reindex_subgraph(start_node_id="s2")


def test_find_similar_closed_only_default(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_closed_subgraphs(storage)

    results = storage.find_similar(query="trigram", top_k=5)

    assert len(results) == 1
    assert results[0]["start_node_id"] == "s1"
    assert results[0]["state"] == "closed"
    assert results[0]["root_id"] == "root_x1"
    assert results[0]["session_id"] == "ses_x"
    assert results[0]["score"] > 0


def test_find_similar_returns_empty_when_query_too_short(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_closed_subgraphs(storage)

    assert storage.find_similar(query="ab", top_k=5) == []
    assert storage.find_similar(query="", top_k=5) == []


def test_find_similar_top_k_respected(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_closed_subgraphs(storage)

    # Both contain 'start' in their start text; restrict to 1 result
    results = storage.find_similar(query="start", top_k=1)
    assert len(results) <= 1


def test_find_similar_returns_archived_subgraphs(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_arch', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_arch', 'ses_arch', 'r', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, archived_at, created_at, updated_at) "
            "VALUES ('s_arch', 'ses_arch', 'start', 'ARCHIVED-KEYWORD here', "
            "'closed', 'root_arch', 'root', 'archived', ?, ?, ?)",
            (now, now, now),
        )
    storage._reindex_subgraph(start_node_id="s_arch")

    results = storage.find_similar(query="archived-keyword", top_k=5)
    assert len(results) == 1
    assert results[0]["state"] == "archived"


def test_find_similar_include_open_adds_active(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_two_closed_subgraphs(storage)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_open', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_open', 'ses_open', 'r', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, created_at, updated_at) "
            "VALUES ('s_open', 'ses_open', 'start', 'trigram in active start', "
            "'open', 'root_open', 'root', 'active', ?, ?)",
            (now, now),
        )

    closed_only = storage.find_similar(query="trigram", include_open=False)
    with_open = storage.find_similar(query="trigram", include_open=True)

    assert "s_open" not in {r["start_node_id"] for r in closed_only}
    assert "s_open" in {r["start_node_id"] for r in with_open}
    # Eligible (closed/archived) must come before open in the merged list.
    indices_eligible = [
        i for i, r in enumerate(with_open) if r["state"] != "active"
    ]
    indices_open = [i for i, r in enumerate(with_open) if r["state"] == "active"]
    assert all(ie < io for ie in indices_eligible for io in indices_open)
