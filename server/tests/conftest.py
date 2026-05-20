"""Shared pytest fixtures for prgp_mcp_server tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest


@pytest.fixture
def tmp_db_path(tmp_path) -> str:
    """A scratch sqlite file path that disappears at test teardown."""
    return str(tmp_path / "graph.sqlite")
