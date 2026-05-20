"""Tests for id generation helpers."""

from __future__ import annotations

import re

from prgp_mcp_server.ids import new_id


def test_new_id_uses_prefix_and_12_hex_chars() -> None:
    value = new_id("ses")
    assert re.fullmatch(r"ses_[0-9a-f]{12}", value)


def test_new_id_is_unique() -> None:
    seen = {new_id("node") for _ in range(100)}
    assert len(seen) == 100
