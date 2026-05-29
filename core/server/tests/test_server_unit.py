"""Unit tests for server module helpers (not requiring stdio subprocess)."""
from __future__ import annotations

import asyncio

import pytest

from dpd_mcp_server import server


def _call_list_tools(server_module):
    """Call list_tools regardless of sync/async — here it is async."""
    return asyncio.run(server_module.list_tools())


def _call_call_tool(server_module, name, arguments):
    """Call call_tool regardless of sync/async — here it is async."""
    return asyncio.run(server_module.call_tool(name, arguments))


@pytest.mark.asyncio
async def test_get_storage_uses_explicit_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DPD_DATA_DIR", str(tmp_path))
    # Reset cache between tests.
    server._storages.clear()

    storage = await server._get_storage({"agent_scope": "test-encoded"})

    assert storage is not None
    expected_db = tmp_path / "test-encoded" / "graph.sqlite"
    assert expected_db.exists()


@pytest.mark.asyncio
async def test_get_storage_caches_per_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("DPD_DATA_DIR", str(tmp_path))
    server._storages.clear()

    s1 = await server._get_storage({"agent_scope": "scope-a"})
    s2 = await server._get_storage({"agent_scope": "scope-a"})
    s3 = await server._get_storage({"agent_scope": "scope-b"})

    assert s1 is s2  # cache hit on same scope
    assert s1 is not s3  # different scope => different Storage


def test_legacy_alias_forwards_and_warns(caplog) -> None:
    """A populated LEGACY_ALIASES entry should expose the old name in
    list_tools with [DEPRECATED:] prefix, and call_tool should forward
    the old name to the new handler with a deprecation warning.

    Uses a temporary alias via patch.dict to avoid coupling to specific
    historic names.
    """
    import logging
    from unittest.mock import patch
    from dpd_mcp_server import server as server_module
    from dpd_mcp_server import tool_aliases

    with patch.dict(tool_aliases.LEGACY_ALIASES, {"foo_legacy": "get_node"}, clear=False):
        # 1. list_tools should expose foo_legacy with [DEPRECATED:] prefix
        tools_list = _call_list_tools(server_module)

        names = {t.name for t in tools_list}
        assert "foo_legacy" in names, (
            f"expected foo_legacy in tool list, got {sorted(names)}"
        )

        legacy_tool = next(t for t in tools_list if t.name == "foo_legacy")
        assert legacy_tool.description.startswith("[DEPRECATED:"), (
            f"expected DEPRECATED prefix, got {legacy_tool.description!r}"
        )

        # 2. call_tool with foo_legacy must emit warning and forward to get_node
        caplog.set_level(logging.WARNING)
        try:
            _call_call_tool(
                server_module,
                "foo_legacy",
                {"session_id": "ses_missing", "node_id": "n_missing"},
            )
        except Exception:
            # We're verifying forwarding + warning, not get_node's success path.
            pass

        assert any(
            "deprecated" in rec.message.lower() and "get_node" in rec.message
            for rec in caplog.records
        ), (
            f"expected deprecation warning mentioning get_node, "
            f"got {[r.message for r in caplog.records]}"
        )


def test_find_similar_in_tool_registry() -> None:
    import asyncio
    from dpd_mcp_server.server import list_tools
    tools = asyncio.run(list_tools())
    names = {t.name for t in tools}
    assert "find_similar" in names

    tool = next(t for t in tools if t.name == "find_similar")
    assert "query" in tool.inputSchema["properties"]
    assert "scope" in tool.inputSchema["properties"]
    assert "top_k" in tool.inputSchema["properties"]
    assert "include_open" in tool.inputSchema["properties"]
    assert "query" in tool.inputSchema["required"]


def test_find_similar_tool_schema_declares_agent_scope() -> None:
    """find_similar inputSchema declares agent_scope (optional routing override)."""
    import asyncio
    from dpd_mcp_server.server import list_tools
    tools = asyncio.run(list_tools())
    tool = next(t for t in tools if t.name == "find_similar")
    assert "agent_scope" in tool.inputSchema["properties"]
    assert "agent_scope" not in tool.inputSchema.get("required", [])


def test_delete_edge_in_tool_registry() -> None:
    """Issue #10: delete_edge tool must be advertised with edge_id schema."""
    import asyncio
    from dpd_mcp_server.server import list_tools
    tools = asyncio.run(list_tools())
    names = {t.name for t in tools}
    assert "delete_edge" in names

    tool = next(t for t in tools if t.name == "delete_edge")
    assert "session_id" in tool.inputSchema["properties"]
    assert "edge_id" in tool.inputSchema["properties"]
    assert tool.inputSchema["properties"]["edge_id"]["type"] == "integer"
    assert set(tool.inputSchema["required"]) == {"session_id", "edge_id"}


def test_add_edge_schema_enumerates_canonical_types() -> None:
    """Issue #10: add_edge schema must restrict type to the canonical vocabulary."""
    import asyncio
    from dpd_mcp_server.server import list_tools
    tools = asyncio.run(list_tools())
    tool = next(t for t in tools if t.name == "add_edge")
    enum = tool.inputSchema["properties"]["type"].get("enum")
    assert enum is not None
    assert set(enum) == {
        "derived_from", "requires", "blocks", "supports", "contradicts",
        "contributes_to", "supersedes", "qualifies", "invalidates",
        # #57: realization-axis (instantiates/illustrates) + grounding (justifies)
        "instantiates", "illustrates", "justifies",
    }


def test_bulk_import_edge_schema_declares_layer_and_priority() -> None:
    """#57/#42: export_yaml emits edge layer + verification_priority, so the
    bulk_import_subgraph input schema must advertise them on edge items —
    otherwise schema-driven clients cannot round-trip a load-bearing edge."""
    import asyncio
    from dpd_mcp_server.server import list_tools
    tools = asyncio.run(list_tools())
    tool = next(t for t in tools if t.name == "bulk_import_subgraph")
    edge_props = (
        tool.inputSchema["properties"]["edges"]["items"]["properties"]
    )
    assert "layer" in edge_props
    assert set(edge_props["layer"]["enum"]) == {
        "necessary", "selective", "invalid", None,
    }
    assert "verification_priority" in edge_props
    assert set(edge_props["verification_priority"]["enum"]) == {
        "critical", "standard", "low", None,
    }


def test_purge_session_in_tool_registry() -> None:
    """Issue #12: purge_session + force_purge_session must be advertised."""
    import asyncio
    from dpd_mcp_server.server import list_tools
    tools = asyncio.run(list_tools())
    names = {t.name for t in tools}
    assert "purge_session" in names
    assert "force_purge_session" in names

    purge = next(t for t in tools if t.name == "purge_session")
    assert purge.inputSchema["required"] == ["session_id"]


def test_add_node_schema_advertises_severity() -> None:
    """Issue #32: severity must be present in add_node schema as a nullable string."""
    import asyncio
    from dpd_mcp_server.server import list_tools
    tools = asyncio.run(list_tools())
    tool = next(t for t in tools if t.name == "add_node")
    props = tool.inputSchema["properties"]
    assert "severity" in props
    assert "string" in props["severity"]["type"]
    assert "severity" not in tool.inputSchema.get("required", [])


def test_add_edge_schema_advertises_layer_and_priority() -> None:
    """#42: add_edge must expose optional layer + verification_priority enums."""
    import asyncio
    from dpd_mcp_server.server import list_tools
    tools = asyncio.run(list_tools())
    tool = next(t for t in tools if t.name == "add_edge")
    props = tool.inputSchema["properties"]
    assert {"necessary", "selective", "invalid"} <= set(props["layer"]["enum"])
    assert {"critical", "standard", "low"} <= set(
        props["verification_priority"]["enum"])
    assert "layer" not in tool.inputSchema["required"]
    assert "verification_priority" not in tool.inputSchema["required"]


def test_proof_tree_discipline_tools_in_registry() -> None:
    """#42: the discipline tool family must be advertised."""
    import asyncio
    from dpd_mcp_server.server import list_tools
    tools = asyncio.run(list_tools())
    names = {t.name for t in tools}
    for n in ("set_edge_layer", "set_edge_verification_priority",
              "record_edge_verification", "list_unverified_edges",
              "list_edge_verifications"):
        assert n in names, f"missing tool {n}"

    record = next(t for t in tools if t.name == "record_edge_verification")
    assert set(record.inputSchema["properties"]["verdict"]["enum"]) == {
        "holds", "holds-with-caveat", "refuted"}
    assert set(record.inputSchema["required"]) == {
        "session_id", "edge_id", "verdict"}

    setlayer = next(t for t in tools if t.name == "set_edge_layer")
    assert set(setlayer.inputSchema["required"]) == {"session_id", "edge_id"}


def test_find_similar_dispatched_by_call_tool(tmp_path, monkeypatch) -> None:
    """call_tool routes name='find_similar' to tools.find_similar."""
    import asyncio
    from dpd_mcp_server import server as server_mod

    captured: dict = {}

    def fake_find_similar(*, storage, arguments):
        captured["storage"] = storage
        captured["arguments"] = arguments
        return {"results": []}

    monkeypatch.setattr(server_mod.tools, "find_similar", fake_find_similar)

    # Bypass roots resolution by passing explicit agent_scope.
    monkeypatch.setenv("DPD_DATA_DIR", str(tmp_path))
    result = asyncio.run(server_mod.call_tool(
        "find_similar",
        {"query": "anything", "agent_scope": "test-scope"},
    ))
    assert result == {"results": []}
    assert captured["arguments"] == {"query": "anything"}


def test_record_edge_verification_dispatched_by_call_tool(
    tmp_path, monkeypatch,
) -> None:
    """#42: call_tool routes record_edge_verification (a now-taking tool)."""
    import asyncio
    from dpd_mcp_server import server as server_mod

    captured: dict = {}

    def fake_record(*, storage, arguments, now):
        captured["arguments"] = arguments
        return {"verification_id": 1}

    monkeypatch.setattr(server_mod.tools, "record_edge_verification", fake_record)
    monkeypatch.setenv("DPD_DATA_DIR", str(tmp_path))
    result = asyncio.run(server_mod.call_tool(
        "record_edge_verification",
        {"session_id": "s", "edge_id": 1, "verdict": "holds",
         "agent_scope": "test-scope"},
    ))
    assert result == {"verification_id": 1}
    assert captured["arguments"]["verdict"] == "holds"


def test_list_unverified_edges_dispatched_by_call_tool(
    tmp_path, monkeypatch,
) -> None:
    """#42: call_tool routes list_unverified_edges (a read-only tool)."""
    import asyncio
    from dpd_mcp_server import server as server_mod

    def fake_list(*, storage, arguments):
        return {"edges": []}

    monkeypatch.setattr(server_mod.tools, "list_unverified_edges", fake_list)
    monkeypatch.setenv("DPD_DATA_DIR", str(tmp_path))
    result = asyncio.run(server_mod.call_tool(
        "list_unverified_edges",
        {"session_id": "s", "agent_scope": "test-scope"},
    ))
    assert result == {"edges": []}


def test_note_tools_registered_in_list_tools() -> None:
    """add_note / list_notes are exposed with their required fields."""
    from dpd_mcp_server import server as server_module

    tools = _call_list_tools(server_module)
    names = {t.name for t in tools}
    assert {"add_note", "list_notes"} <= names

    add = next(t for t in tools if t.name == "add_note")
    assert set(add.inputSchema["required"]) == {
        "session_id", "anchor_kind", "anchor_id", "kind", "text",
    }
    assert add.inputSchema["properties"]["anchor_kind"]["enum"] == ["node", "root"]

    listing = next(t for t in tools if t.name == "list_notes")
    assert listing.inputSchema["required"] == ["session_id"]


def test_add_note_dispatched_by_call_tool(tmp_path, monkeypatch) -> None:
    """call_tool routes add_note (a now + new_id taking tool)."""
    import asyncio
    from dpd_mcp_server import server as server_mod

    captured: dict = {}

    def fake_add_note(*, storage, arguments, now, new_id):
        captured["arguments"] = arguments
        return {"note_id": "note_x", "superseded_note_id": None}

    monkeypatch.setattr(server_mod.tools, "add_note", fake_add_note)
    monkeypatch.setenv("DPD_DATA_DIR", str(tmp_path))
    result = asyncio.run(server_mod.call_tool(
        "add_note",
        {"session_id": "s", "anchor_kind": "node", "anchor_id": "n",
         "kind": "narrative", "text": "body", "agent_scope": "test-scope"},
    ))
    assert result == {"note_id": "note_x", "superseded_note_id": None}
    assert captured["arguments"]["kind"] == "narrative"
    assert "agent_scope" not in captured["arguments"]


def test_list_notes_dispatched_by_call_tool(tmp_path, monkeypatch) -> None:
    """call_tool routes list_notes (a read-only tool)."""
    import asyncio
    from dpd_mcp_server import server as server_mod

    def fake_list(*, storage, arguments):
        return {"notes": []}

    monkeypatch.setattr(server_mod.tools, "list_notes", fake_list)
    monkeypatch.setenv("DPD_DATA_DIR", str(tmp_path))
    result = asyncio.run(server_mod.call_tool(
        "list_notes",
        {"session_id": "s", "agent_scope": "test-scope"},
    ))
    assert result == {"notes": []}
