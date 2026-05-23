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
