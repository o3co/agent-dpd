"""SQLite storage layer for DPD server.

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
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            schema = files("dpd_mcp_server").joinpath("schema.sql").read_text()
            conn.executescript(schema)
        return cls(db_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a sqlite connection with foreign keys enabled.

        Commits on clean exit, rolls back on exception, always closes.
        Sets busy_timeout to 5000ms so concurrent stdio servers don't
        immediately fail under WAL writer contention.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def insert_session(
        self,
        *,
        session_id: str,
        scope: str | None,
        label: str | None,
        now: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, scope, label, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, scope, label, now, now),
            )

    def get_session(self, *, session_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()

    def insert_root(
        self,
        *,
        root_id: str,
        session_id: str,
        topic: str,
        now: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO roots
                    (id, session_id, topic, lifecycle, spawned_at, last_focused_at)
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (root_id, session_id, topic, now, now),
            )

    def list_active_roots(self, *, session_id: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM roots
                    WHERE session_id = ? AND lifecycle = 'active'
                    ORDER BY spawned_at, id
                    """,
                    (session_id,),
                )
            )

    def insert_node(
        self,
        *,
        node_id: str,
        session_id: str,
        node_type: str,
        text: str,
        parent_id: str,
        parent_kind: str,
        now: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO nodes
                    (id, session_id, type, text, status, closure_reason,
                     parent_id, parent_kind, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'open', NULL, ?, ?, ?, ?)
                """,
                (node_id, session_id, node_type, text,
                 parent_id, parent_kind, now, now),
            )

    def insert_node_under_parent(
        self,
        *,
        node_id: str,
        session_id: str,
        node_type: str,
        text: str,
        parent_id: str,
        now: str,
    ) -> str:
        """Insert a node, atomically classifying its parent kind.

        Returns the parent_kind that was determined ('root' or 'node').
        Raises ValueError if the parent does not exist in this session.

        Classify-and-insert run in a single transaction so a concurrent
        delete between the lookup and the insert cannot create an orphan.
        """
        with self.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM roots WHERE session_id = ? AND id = ?",
                (session_id, parent_id),
            ).fetchone():
                parent_kind = "root"
            elif conn.execute(
                "SELECT 1 FROM nodes WHERE session_id = ? AND id = ?",
                (session_id, parent_id),
            ).fetchone():
                parent_kind = "node"
            else:
                raise ValueError(
                    f"parent_id {parent_id!r} not found in session {session_id!r}"
                )

            conn.execute(
                """
                INSERT INTO nodes
                    (id, session_id, type, text, status, closure_reason,
                     parent_id, parent_kind, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'open', NULL, ?, ?, ?, ?)
                """,
                (node_id, session_id, node_type, text,
                 parent_id, parent_kind, now, now),
            )
            return parent_kind

    def get_node(
        self, *, session_id: str, node_id: str
    ) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM nodes WHERE session_id = ? AND id = ?",
                (session_id, node_id),
            ).fetchone()

    def close_node(
        self,
        *,
        session_id: str,
        node_id: str,
        closure_reason: str,
        now: str,
    ) -> bool:
        """Close a node. Returns True if a row was updated, False if no such node."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE nodes
                SET status = 'closed',
                    closure_reason = ?,
                    updated_at = ?
                WHERE session_id = ? AND id = ?
                """,
                (closure_reason, now, session_id, node_id),
            )
            return cursor.rowcount > 0

    def walk_subtree(
        self, *, session_id: str, root_id: str
    ) -> list[sqlite3.Row]:
        """Return all descendants of a root, depth-first pre-order.

        Iterative DFS to avoid Python recursion limit on deep chains
        (sys.getrecursionlimit() defaults to ~1000; DPD chains can exceed).
        Children at each level are ordered by (created_at, id).
        """
        # Each frontier entry is either:
        #   ("expand", parent_id, parent_kind)  — fetch children, schedule them
        #   ("emit",   node_row)                — append this row to result
        # On pop, "expand" frames fetch children and push, in REVERSE order, a
        # paired (emit, expand) for each child. This gives pre-order DFS.
        with self.connect() as conn:
            result: list[sqlite3.Row] = []
            frontier: list[tuple] = [("expand", root_id, "root")]
            while frontier:
                frame = frontier.pop()
                if frame[0] == "emit":
                    _, row = frame
                    result.append(row)
                    continue
                _, parent_id, parent_kind = frame
                children = conn.execute(
                    """
                    SELECT * FROM nodes
                    WHERE session_id = ?
                      AND parent_id = ?
                      AND parent_kind = ?
                    ORDER BY created_at, id
                    """,
                    (session_id, parent_id, parent_kind),
                ).fetchall()
                # Push children in REVERSE so first child pops first.
                # For each child, push (expand, ...) FIRST and (emit, ...) SECOND
                # so the emit happens BEFORE the expansion — pre-order.
                for child in reversed(children):
                    frontier.append(("expand", child["id"], "node"))
                    frontier.append(("emit", child))
            return result
