"""SQLite storage layer for PRGP server.

Owns the sqlite connection lifecycle and exposes CRUD primitives.
Tools never construct SQL directly — they call methods here.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Iterator


class Storage:
    """Handle to a per-agent-scope sqlite database."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    @classmethod
    def open(cls, db_path: str) -> "Storage":
        """Create or open the database, applying schema and enabling WAL."""
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            schema = files("prgp_mcp_server").joinpath("schema.sql").read_text()
            conn.executescript(schema)
        return cls(db_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a sqlite connection with foreign keys enabled."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
