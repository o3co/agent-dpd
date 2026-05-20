"""Unit tests for server module helpers (not requiring stdio subprocess)."""
from __future__ import annotations

import pytest

from dpd_mcp_server import server


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
