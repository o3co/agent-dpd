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
