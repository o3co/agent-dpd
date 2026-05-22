"""Tests for id generation helpers."""

from __future__ import annotations

import re

from dpd_mcp_server.ids import new_id


def test_new_id_uses_prefix_and_12_hex_chars() -> None:
    value = new_id("ses")
    assert re.fullmatch(r"ses_[0-9a-f]{12}", value)


def test_new_id_is_unique() -> None:
    seen = {new_id("node") for _ in range(100)}
    assert len(seen) == 100


def test_pool_id_format() -> None:
    from dpd_mcp_server.ids import pool_id
    pid = pool_id()
    assert re.fullmatch(r"pool_[0-9a-f]{8}", pid), pid


def test_root_id_format() -> None:
    from dpd_mcp_server.ids import root_id
    rid = root_id()
    assert re.fullmatch(r"root_[0-9a-f]{8}", rid), rid


def test_node_id_format() -> None:
    import re
    from dpd_mcp_server.ids import node_id
    nid = node_id()
    assert re.fullmatch(r"node_[0-9a-f]{8}", nid), nid
