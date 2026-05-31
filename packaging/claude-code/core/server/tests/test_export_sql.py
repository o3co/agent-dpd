"""Tests for faithful SQL export + restore (#60).

Covers Storage.export_sql (non-destructive whole-DB serialize), the standalone
migrate entry point (forward-only chain + FTS rebuild), and the command-emitting
import_sql tool.
"""

from __future__ import annotations

import sqlite3

import pytest

from dpd_mcp_server import migrate as migrate_mod
from dpd_mcp_server.storage import Storage
from dpd_mcp_server.tools import export_sql as export_sql_tool
from dpd_mcp_server.tools import import_sql as import_sql_tool

NOW = "2026-05-30T00:00:00Z"


def _seed_full_graph(storage: Storage) -> None:
    """One closed subgraph plus an edge, edge_verification, pool item, and note."""
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, scope, started_at, updated_at) "
            "VALUES ('ses_x', 'dev.dpd', ?, ?)",
            (NOW, NOW),
        )
        conn.execute(
            "INSERT INTO roots (id, session_id, scope, topic, lifecycle, spawned_at) "
            "VALUES ('root_x1', 'ses_x', 'dev.dpd', 'r1', 'active', ?)",
            (NOW,),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, state, closed_at, created_at, updated_at) "
            "VALUES ('s1', 'ses_x', 'start', 'FTS5 trigram start', 'closed', "
            "'root_x1', 'root', 'closed', ?, ?, ?)",
            (NOW, NOW, NOW),
        )
        conn.execute(
            "INSERT INTO nodes (id, session_id, type, text, status, parent_id, "
            "parent_kind, paired_for, achievement_conditions, state, closed_at, "
            "created_at, updated_at) "
            "VALUES ('e1', 'ses_x', 'end', 'tokenizer trigram', 'closed', 's1', "
            "'node', 's1', 'trigram chosen', 'closed', ?, ?, ?)",
            (NOW, NOW, NOW),
        )
        cur = conn.execute(
            "INSERT INTO edges (session_id, from_node, to_node, type, reason, "
            "layer, created_at) VALUES ('ses_x', 's1', 'e1', 'derived_from', "
            "'because', 'necessary', ?)",
            (NOW,),
        )
        edge_id = cur.lastrowid
        conn.execute(
            "INSERT INTO edge_verifications (edge_id, verified_by, verified_at, "
            "method, verdict, notes) VALUES (?, 'codex', ?, 'external:codex', "
            "'holds', 'ok')",
            (edge_id, NOW),
        )
        conn.execute(
            "INSERT INTO pool_items (id, scope_root_id, origin_session_id, text, "
            "created_at) VALUES ('pool_1', 'root_x1', 'ses_x', 'a stray idea', ?)",
            (NOW,),
        )
        conn.execute(
            "INSERT INTO notes (id, session_id, anchor_kind, anchor_id, kind, "
            "text, state, created_at, updated_at) VALUES ('note_1', 'ses_x', "
            "'node', 's1', 'narrative', 'long form prose', 'active', ?, ?)",
            (NOW, NOW),
        )
    storage._reindex_subgraph(start_node_id="s1")


def _counts(db_path: str) -> dict[str, int]:
    out: dict[str, int] = {}
    with sqlite3.connect(db_path) as conn:
        for table in (
            "sessions", "roots", "nodes", "edges",
            "edge_verifications", "pool_items", "notes",
        ):
            out[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
    return out


def test_export_sql_carries_pragma_and_manifest(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_full_graph(storage)

    sql = storage.export_sql(now=NOW, exported_by="tester")

    assert "PRAGMA user_version = 9;" in sql
    assert "CREATE TABLE export_meta" in sql
    assert "tester" in sql
    # Manifest summarizes origin.
    assert "dev.dpd" in sql


def test_export_sql_excludes_fts(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_full_graph(storage)

    sql = storage.export_sql(now=NOW)

    # No FTS DDL/DML or shadow tables in the dump (the name may still appear in
    # the explanatory header comment).
    assert "CREATE VIRTUAL TABLE" not in sql
    assert "INSERT INTO subgraphs_fts" not in sql
    assert "subgraphs_fts_data" not in sql
    assert "subgraphs_fts_idx" not in sql


def test_export_sql_is_non_destructive(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_full_graph(storage)
    before = _counts(tmp_db_path)

    storage.export_sql(now=NOW)

    assert _counts(tmp_db_path) == before
    # The manifest must NOT have leaked into the source DB.
    with sqlite3.connect(tmp_db_path) as conn:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "export_meta" not in names
    assert "subgraphs_fts" in names  # source FTS untouched
    # Source FTS still serves queries.
    assert storage.find_similar(query="trigram", top_k=5)


def test_roundtrip_restore_via_migrate(tmp_db_path: str, tmp_path) -> None:
    src = Storage.open(tmp_db_path)
    _seed_full_graph(src)
    before = _counts(tmp_db_path)

    sql = src.export_sql(now=NOW)

    restored_path = str(tmp_path / "restored.sqlite")
    with sqlite3.connect(restored_path) as conn:
        conn.executescript(sql)

    # The freshly-restored DB carries the origin schema version but no FTS index.
    with sqlite3.connect(restored_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9

    migrate_mod.migrate(db_path=restored_path)

    assert _counts(restored_path) == before
    restored = Storage(restored_path)
    # FTS was rebuilt by migrate → find_similar works on the restored DB.
    results = restored.find_similar(query="trigram", top_k=5)
    assert len(results) == 1
    assert results[0]["start_node_id"] == "s1"
    # Faithful fields survived (state/closure/provenance/edge layer/verification).
    with sqlite3.connect(restored_path) as conn:
        conn.row_factory = sqlite3.Row
        node = conn.execute("SELECT * FROM nodes WHERE id='s1'").fetchone()
        assert node["state"] == "closed"
        edge = conn.execute("SELECT * FROM edges").fetchone()
        assert edge["layer"] == "necessary"
        ver = conn.execute("SELECT * FROM edge_verifications").fetchone()
        assert ver["verdict"] == "holds"
        note = conn.execute("SELECT * FROM notes WHERE id='note_1'").fetchone()
        assert note["text"] == "long form prose"


def test_migrate_forward_ports_older_schema(tmp_db_path: str) -> None:
    """A restored dump that pins an older schema is migrated up on restore.

    Simulates an old (v7, pre-notes) dump landing in a fresh sqlite: the notes
    table is absent and user_version is 7. migrate() must route through the
    chain and create the notes table (#60 forward-only portability).
    """
    Storage.open(tmp_db_path)
    with sqlite3.connect(tmp_db_path) as conn:
        conn.execute("DROP TABLE notes")
        conn.execute("PRAGMA user_version = 7")

    migrate_mod.migrate(db_path=tmp_db_path)

    with sqlite3.connect(tmp_db_path) as conn:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert "notes" in names
    assert version == 9


def test_migrate_rejects_newer_schema(tmp_db_path: str) -> None:
    Storage.open(tmp_db_path)
    with sqlite3.connect(tmp_db_path) as conn:
        conn.execute("PRAGMA user_version = 99")

    with pytest.raises(ValueError, match="forward-only"):
        migrate_mod.migrate(db_path=tmp_db_path)


def test_import_sql_emits_commands_without_executing(
    tmp_db_path: str, tmp_path
) -> None:
    storage = Storage.open(tmp_db_path)
    dump_path = str(tmp_path / "dump.sql")

    result = import_sql_tool(
        storage=storage, arguments={"dump_path": dump_path}
    )

    assert result["destructive"] is True
    assert result["db_path"] == tmp_db_path
    assert any(dump_path in c for c in result["commands"])
    assert any("dpd_mcp_server.migrate" in c for c in result["commands"])
    # Nothing was executed: no restored artifact created.
    import os
    assert not os.path.exists(f"{tmp_db_path}.import.sqlite")


def test_export_sql_tool_returns_text_and_size(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)
    _seed_full_graph(storage)

    result = export_sql_tool(storage=storage, arguments={}, now=NOW)

    assert "PRAGMA user_version = 9;" in result["sql"]
    assert result["bytes"] == len(result["sql"].encode("utf-8"))


def test_import_sql_uses_running_interpreter_not_bare_python(
    tmp_db_path: str, tmp_path
) -> None:
    """#74 review (P2): the migrate command must invoke the current interpreter.

    Under the plugin install, `dpd_mcp_server` lives only in the plugin venv, so
    a bare `python` on PATH would fail with `No module named dpd_mcp_server`.
    """
    import shlex
    import sys

    storage = Storage.open(tmp_db_path)
    result = import_sql_tool(
        storage=storage, arguments={"dump_path": str(tmp_path / "dump.sql")}
    )

    migrate_cmd = next(
        c for c in result["commands"] if "dpd_mcp_server.migrate" in c
    )
    assert migrate_cmd.startswith(shlex.quote(sys.executable) + " ")
    # Must not invoke a bare `python`/`python3` token as the interpreter.
    assert not migrate_cmd.startswith("python ")
    assert not migrate_cmd.startswith("python3 ")


def test_export_meta_counts_match_dumped_body(tmp_db_path: str) -> None:
    """#74 review (P3): the manifest is computed from the backup snapshot, so its
    counts/ids describe exactly the database the dump restores.

    Round-trips the dump into a fresh DB, reads the embedded export_meta row, and
    asserts its counts equal the actually-restored table counts.
    """
    import json
    import sqlite3 as _sqlite3

    storage = Storage.open(tmp_db_path)
    _seed_full_graph(storage)

    sql = storage.export_sql(now=NOW)

    restored_path = tmp_db_path + ".meta_check.sqlite"
    with _sqlite3.connect(restored_path) as conn:
        conn.executescript(sql)
        conn.row_factory = _sqlite3.Row
        meta = conn.execute("SELECT * FROM export_meta").fetchone()
        actual_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        actual_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        actual_sessions = [
            r[0] for r in conn.execute("SELECT id FROM sessions ORDER BY id")
        ]
        actual_roots = [
            r[0] for r in conn.execute("SELECT id FROM roots ORDER BY id")
        ]
    assert meta["node_count"] == actual_nodes
    assert meta["edge_count"] == actual_edges
    assert json.loads(meta["session_ids"]) == actual_sessions
    assert json.loads(meta["root_ids"]) == actual_roots


def test_current_schema_version_tracks_storage_schema(tmp_db_path: str) -> None:
    """#74 review (drift): the restore guard's version must equal the version a
    freshly-opened DB actually carries, so a schema bump can't make migrate()
    reject a legitimate current-schema dump."""
    Storage.open(tmp_db_path)
    with sqlite3.connect(tmp_db_path) as conn:
        live_version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert migrate_mod.CURRENT_SCHEMA_VERSION == live_version
