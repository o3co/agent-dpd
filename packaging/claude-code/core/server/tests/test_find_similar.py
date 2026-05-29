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


def test_find_similar_snippet_picks_matching_column(tmp_db_path: str) -> None:
    """Issue #3: snippet must come from the column that actually matched the
    query, not always from anchor_text. When a query matches only body_text
    (decision/rationale/etc.), the snippet must contain the matched term."""
    storage = Storage.open(tmp_db_path)
    now = "2026-05-23T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_snip', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_snip', 'ses_snip', 'r', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('s_snip', 'ses_snip', 'start', 'plain start text', 'closed', "
            "'root_snip', 'root', 'closed', ?, ?, ?)", (now, now, now),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, paired_for, achievement_conditions, state, closed_at, "
            "created_at, updated_at) "
            "VALUES ('e_snip', 'ses_snip', 'end', 'plain end', 'closed', "
            "'s_snip', 'node', 's_snip', 'plain conditions', 'closed', ?, ?, ?)",
            (now, now, now),
        )
        # Decision child = body_text; contains BODYNEEDLE that anchor lacks.
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('d_snip', 'ses_snip', 'decision', "
            "'BODYNEEDLE chosen as final answer', 'closed', 's_snip', 'node', "
            "'closed', ?, ?, ?)", (now, now, now),
        )
    storage._reindex_subgraph(start_node_id="s_snip")

    results = storage.find_similar(query="bodyneedle", top_k=5)
    assert len(results) == 1
    snippet = results[0]["matched_snippet"]
    assert snippet is not None
    assert "bodyneedle" in snippet.lower(), (
        f"snippet should come from body_text where BODYNEEDLE matched; "
        f"got: {snippet!r}"
    )


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


def test_find_similar_filters_by_session_scope(tmp_db_path: str) -> None:
    """scope filter must compare against sessions.scope, not roots.scope.

    Codex P2 #1: roots.scope is NULL for normal roots; sub-scope lives on
    sessions.scope. Without this fix, scope filter returns nothing for
    real-world data.
    """
    storage = Storage.open(tmp_db_path)
    now = "2026-05-24T00:00:00Z"
    with storage.connect() as conn:
        # Session A in sub-scope "alpha"
        conn.execute(
            "INSERT INTO sessions (id, scope, started_at, updated_at) "
            "VALUES ('ses_a', 'alpha', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_a', 'ses_a', 'r', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('s_a', 'ses_a', 'start', 'NEEDLE-ALPHA start', 'closed', "
            "'root_a', 'root', 'closed', ?, ?, ?)", (now, now, now),
        )
        # Session B in sub-scope "beta"
        conn.execute(
            "INSERT INTO sessions (id, scope, started_at, updated_at) "
            "VALUES ('ses_b', 'beta', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_b', 'ses_b', 'r', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('s_b', 'ses_b', 'start', 'NEEDLE-BETA start', 'closed', "
            "'root_b', 'root', 'closed', ?, ?, ?)", (now, now, now),
        )
    storage._reindex_subgraph(start_node_id="s_a")
    storage._reindex_subgraph(start_node_id="s_b")

    # Without scope filter: both found
    all_results = storage.find_similar(query="needle")
    assert {r["start_node_id"] for r in all_results} == {"s_a", "s_b"}

    # With scope='alpha': only s_a (filter via sessions.scope)
    alpha = storage.find_similar(query="needle", scope="alpha")
    assert {r["start_node_id"] for r in alpha} == {"s_a"}
    assert alpha[0]["scope"] == "alpha"

    # With scope='beta': only s_b
    beta = storage.find_similar(query="needle", scope="beta")
    assert {r["start_node_id"] for r in beta} == {"s_b"}
    assert beta[0]["scope"] == "beta"


def test_find_similar_include_open_blob_includes_descendants(tmp_db_path: str) -> None:
    """include_open=True must scan child node text (decisions/rationales),
    mirroring how _reindex_subgraph composes body_text/journey_text for closed.

    Codex P2 #3: currently only start+end+ach are in the blob, so a query that
    matches only a decision child is silently missed.
    """
    storage = Storage.open(tmp_db_path)
    now = "2026-05-24T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_o', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_o', 'ses_o', 'r', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, created_at, updated_at) "
            "VALUES ('s_o', 'ses_o', 'start', 'unrelated start text', 'open', "
            "'root_o', 'root', 'active', ?, ?)", (now, now),
        )
        # Decision child contains the keyword we'll search for.
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, created_at, updated_at) "
            "VALUES ('d_o', 'ses_o', 'decision', 'DEEPCHILD-KEYWORD decision text', "
            "'open', 's_o', 'node', 'active', ?, ?)", (now, now),
        )

    # Without include_open: nothing (subgraph not in FTS)
    closed_only = storage.find_similar(query="deepchild-keyword", include_open=False)
    assert closed_only == []

    # With include_open: subgraph found because decision child text is now in blob
    with_open = storage.find_similar(query="deepchild-keyword", include_open=True)
    assert {r["start_node_id"] for r in with_open} == {"s_o"}


def test_find_similar_scope_filter_pushed_into_sql(tmp_db_path: str) -> None:
    """Bug #8: scope filter must apply BEFORE LIMIT, not after.

    Seed many out-of-scope matches that rank higher than the single in-scope
    match (noise docs repeat the keyword many times, inflating BM25 score).
    With the old Python-side filter (LIMIT top_k*4 then filter), the in-scope
    match is truncated before the filter sees it because top_k*4 = 20 < 30
    noise rows, all of which score higher.
    """
    storage = Storage.open(tmp_db_path)
    now = "2026-05-25T00:00:00Z"
    # Noise keyword repeated many times so BM25 ranks noise rows higher.
    noise_text = ("BUCKET-MATCH " * 20).strip()
    with storage.connect() as conn:
        # 30 noise sessions in scope "other", each with high-frequency keyword
        for i in range(30):
            sid = f"ses_n{i:02d}"
            rid = f"root_n{i:02d}"
            nid = f"node_n{i:02d}"
            conn.execute(
                "INSERT INTO sessions (id, scope, started_at, updated_at) "
                "VALUES (?, 'other', ?, ?)", (sid, now, now),
            )
            conn.execute(
                "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
                "VALUES (?, ?, 'r', 'active', ?)", (rid, sid, now),
            )
            conn.execute(
                "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
                "parent_kind, state, closed_at, created_at, updated_at) "
                "VALUES (?, ?, 'start', ?, 'closed', ?, 'root', 'closed', ?, ?, ?)",
                (nid, sid, noise_text, rid, now, now, now),
            )
        # One in-scope target — keyword appears only once (lower BM25)
        conn.execute(
            "INSERT INTO sessions (id, scope, started_at, updated_at) "
            "VALUES ('ses_tgt', 'target', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_tgt', 'ses_tgt', 'r', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('node_tgt', 'ses_tgt', 'start', 'BUCKET-MATCH target unique', "
            "'closed', 'root_tgt', 'root', 'closed', ?, ?, ?)", (now, now, now),
        )
    # Reindex all 31 subgraphs (noise first so target is inserted last into FTS)
    for i in range(30):
        storage._reindex_subgraph(start_node_id=f"node_n{i:02d}")
    storage._reindex_subgraph(start_node_id="node_tgt")

    # Without scope: top 5 should all be noise (higher BM25 due to repetition)
    all_results = storage.find_similar(query="bucket-match", top_k=5)
    assert len(all_results) == 5
    assert all(r["scope"] == "other" for r in all_results), (
        "Without scope filter, all top-5 should be noise rows with higher BM25"
    )

    # With scope='target': must find the single in-scope match even though
    # 30 noise rows score higher and fill the old top_k*4=20 fetch budget.
    # Old impl (LIMIT 20 then Python filter) would return 0 results;
    # correct impl (JOIN + SQL filter then LIMIT) returns exactly 1.
    target_results = storage.find_similar(
        query="bucket-match", scope="target", top_k=5
    )
    assert len(target_results) == 1, (
        f"Expected 1 in-scope result, got {len(target_results)} — "
        "scope filter must apply before LIMIT"
    )
    assert target_results[0]["start_node_id"] == "node_tgt"
    assert target_results[0]["scope"] == "target"


def test_find_similar_excludes_deletable_state(tmp_db_path: str) -> None:
    """Bug #9: subgraphs transitioned to 'deletable' via dump_persist_subgraph
    must NOT appear in find_similar results. Tool contract: state IN ('closed', 'archived').
    """
    storage = Storage.open(tmp_db_path)
    now = "2026-05-25T00:00:00Z"
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, updated_at) "
            "VALUES ('ses_d', ?, ?)", (now, now),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, topic, lifecycle, spawned_at) "
            "VALUES ('root_d', 'ses_d', 'r', 'active', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('s_d', 'ses_d', 'start', 'DUMPABLE-KEYWORD start', 'closed', "
            "'root_d', 'root', 'closed', ?, ?, ?)", (now, now, now),
        )
    storage._reindex_subgraph(start_node_id="s_d")

    # Pre: closed subgraph is searchable
    pre = storage.find_similar(query="dumpable-keyword")
    assert {r["start_node_id"] for r in pre} == {"s_d"}

    # Transition: dump_persist (closed → deletable)
    storage.dump_persist_subgraph(
        session_id="ses_d", start_node_id="s_d", destination=None, now=now
    )

    # Post: deletable subgraph must NOT appear
    post = storage.find_similar(query="dumpable-keyword")
    assert post == [], (
        f"Deletable subgraph leaked into find_similar results: {post}"
    )
