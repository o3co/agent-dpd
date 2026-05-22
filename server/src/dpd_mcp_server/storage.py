"""SQLite storage layer for DPD server.

Owns the sqlite connection lifecycle and exposes CRUD primitives.
Tools never construct SQL directly — they call methods here.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator


class Storage:
    """Handle to a per-agent-scope sqlite database."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    @classmethod
    def open(cls, db_path: str) -> "Storage":
        """Create or open the database, applying schema and enabling WAL.

        For databases created by v0.2 (user_version = 2), a pre-migration step
        adds the new v3 columns via ALTER TABLE before the main schema script
        runs. This prevents the CREATE UNIQUE INDEX statements in schema.sql
        from failing with "no such column" on pre-existing databases.

        SQLite limitation: ALTER TABLE cannot add CHECK constraints, so the
        table-level CHECK (scope_root = 0 OR scope IS NOT NULL) is only present
        on freshly-created v3 databases. For upgraded databases, this invariant
        is enforced at runtime by the Task 4 migration script.
        """
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")

            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            if 0 < user_version < 3:
                cls._migrate_v2_to_v3(conn)

            schema = files("dpd_mcp_server").joinpath("schema.sql").read_text()
            conn.executescript(schema)
        return cls(db_path)

    @staticmethod
    def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
        """Add v3 columns to a v2-shaped database via ALTER TABLE.

        Each ALTER TABLE is wrapped in a try/except so that partially-migrated
        databases (e.g. a previous interrupted upgrade) are handled gracefully —
        SQLite raises OperationalError when the column already exists.

        Called only when user_version < 3; user_version is updated to 3 by
        the PRAGMA at the end of schema.sql (via executescript).
        """
        existing_root_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(roots)")
        }
        existing_node_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(nodes)")
        }

        root_alters = [
            "ALTER TABLE roots ADD COLUMN scope TEXT",
            "ALTER TABLE roots ADD COLUMN scope_root INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE roots ADD COLUMN migrated_to_start_id TEXT",
        ]
        node_alters = [
            "ALTER TABLE nodes ADD COLUMN paired_for TEXT REFERENCES nodes(id)",
            "ALTER TABLE nodes ADD COLUMN achievement_conditions TEXT",
            "ALTER TABLE nodes ADD COLUMN achievement_conditions_satisfied "
            "INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE nodes ADD COLUMN state TEXT NOT NULL DEFAULT 'active'",
            "ALTER TABLE nodes ADD COLUMN archived_at TEXT",
            "ALTER TABLE nodes ADD COLUMN closed_at TEXT",
            "ALTER TABLE nodes ADD COLUMN deletable_at TEXT",
        ]

        for stmt in root_alters:
            col = stmt.split("ADD COLUMN")[1].strip().split()[0]
            if col not in existing_root_cols:
                conn.execute(stmt)

        for stmt in node_alters:
            col = stmt.split("ADD COLUMN")[1].strip().split()[0]
            if col not in existing_node_cols:
                conn.execute(stmt)

        conn.commit()

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

    def list_sessions(self, *, scope: str | None) -> list[sqlite3.Row]:
        """Return sessions for the given sub-scope, most-recently-updated first.

        ``scope=None`` matches only top-level sessions (rows with NULL scope),
        not "all sessions" — sub-scope and top-level are kept distinct so the
        skill startup flow (§8.3 step 3) can list exactly the candidates for
        the cwd-resolved scope.
        """
        with self.connect() as conn:
            if scope is None:
                cursor = conn.execute(
                    "SELECT * FROM sessions WHERE scope IS NULL "
                    "ORDER BY updated_at DESC, id"
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM sessions WHERE scope = ? "
                    "ORDER BY updated_at DESC, id",
                    (scope,),
                )
            return list(cursor)

    @staticmethod
    def _touch_session(conn: sqlite3.Connection, *, session_id: str, now: str) -> None:
        """Bump sessions.updated_at within an existing transaction.

        Called from every graph mutator so list_sessions' "most recent" ordering
        reflects actual activity rather than session-row creation time.
        """
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )

    def get_root(
        self, *, session_id: str, root_id: str
    ) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM roots WHERE session_id = ? AND id = ?",
                (session_id, root_id),
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
            self._touch_session(conn, session_id=session_id, now=now)

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
            self._touch_session(conn, session_id=session_id, now=now)

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
            self._touch_session(conn, session_id=session_id, now=now)
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
            if cursor.rowcount > 0:
                self._touch_session(conn, session_id=session_id, now=now)
            return cursor.rowcount > 0

    def set_focus(
        self,
        *,
        session_id: str,
        node_id: str | None,
        now: str,
    ) -> None:
        """Set or clear sessions.focus_node_id.

        When ``node_id`` is not None, validates that the node exists in the
        session. Always validates that the session exists. Bumps updated_at.
        """
        with self.connect() as conn:
            if node_id is not None:
                exists = conn.execute(
                    "SELECT 1 FROM nodes WHERE session_id = ? AND id = ?",
                    (session_id, node_id),
                ).fetchone()
                if exists is None:
                    raise ValueError(
                        f"node {node_id!r} not found in session {session_id!r}"
                    )
            cursor = conn.execute(
                "UPDATE sessions SET focus_node_id = ?, updated_at = ? WHERE id = ?",
                (node_id, now, session_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"session {session_id!r} not found")

    def set_root_lifecycle(
        self,
        *,
        session_id: str,
        root_id: str,
        lifecycle: str,
        now: str,
    ) -> bool:
        """Change a root's lifecycle. Returns True if a row was updated.

        Lifecycle vocabulary is enforced by the DB-level CHECK constraint
        (raises sqlite3.IntegrityError on invalid value).
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE roots SET lifecycle = ? WHERE session_id = ? AND id = ?",
                (lifecycle, session_id, root_id),
            )
            if cursor.rowcount > 0:
                self._touch_session(conn, session_id=session_id, now=now)
            return cursor.rowcount > 0

    def list_open_nodes(
        self,
        *,
        session_id: str,
        root_id: str | None = None,
    ) -> list[sqlite3.Row]:
        """Return open nodes in the session, optionally restricted to one root.

        With ``root_id=None``, returns every node with status='open' in the
        session (creation-order). With a root, walks that root's subtree and
        filters to open nodes.
        """
        if root_id is None:
            with self.connect() as conn:
                return list(
                    conn.execute(
                        "SELECT * FROM nodes "
                        "WHERE session_id = ? AND status = 'open' "
                        "ORDER BY created_at, id",
                        (session_id,),
                    )
                )
        subtree = self.walk_subtree(session_id=session_id, root_id=root_id)
        return [n for n in subtree if n["status"] == "open"]

    def add_edge(
        self,
        *,
        session_id: str,
        from_node: str,
        to_node: str,
        edge_type: str,
        reason: str | None,
        now: str,
    ) -> int:
        """Insert an edge row, validating both endpoints first.

        Both ``from_node`` and ``to_node`` must reference an existing row in
        the same session — either a node (nodes.id) OR a root (roots.id).
        The edges schema has no FK constraint (parent-kind polymorphism makes
        a literal FK impossible), so validation is enforced in app code.
        Raises ValueError if either endpoint is missing.
        """
        with self.connect() as conn:
            for label, endpoint in (("from_node", from_node), ("to_node", to_node)):
                exists = conn.execute(
                    "SELECT 1 FROM nodes WHERE session_id = ? AND id = ? "
                    "UNION ALL "
                    "SELECT 1 FROM roots WHERE session_id = ? AND id = ?",
                    (session_id, endpoint, session_id, endpoint),
                ).fetchone()
                if exists is None:
                    raise ValueError(
                        f"{label} {endpoint!r} not found "
                        f"in session {session_id!r}"
                    )
            cursor = conn.execute(
                "INSERT INTO edges "
                "(session_id, from_node, to_node, type, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, from_node, to_node, edge_type, reason, now),
            )
            self._touch_session(conn, session_id=session_id, now=now)
            return cursor.lastrowid

    def resolve_hypothesis_branch(
        self,
        *,
        session_id: str,
        hyp_id: str,
        decision_id: str,
        decision_text: str,
        rationale_id: str | None,
        rationale_text: str | None,
        now: str,
    ) -> dict[str, Any]:
        """Atomically resolve a hypothesis branch.

        Steps run in a single transaction:
            1. Close the target hypothesis as 'resolved'.
            2. Close every sibling hypothesis (same parent, type='hypothesis',
               status='open') as 'rejected'.
            3. Insert a closed 'decision' node under the same parent (resolved).
            4. Insert a ``derived_from`` edge from the new decision to the
               accepted hypothesis, so the structural link between them is
               queryable (not only textually implied by tree adjacency).
            5. If rationale_text is given, insert a closed 'rationale' node
               under the decision (resolved).
            6. Bump sessions.updated_at.

        Returns ``{hyp_id, decision_id, rationale_id, closed_siblings,
        derived_from_edge_id}``.

        Raises ValueError when the target is missing, not a hypothesis, or
        not currently open (preventing duplicate decisions on retry).
        Raises sqlite3.IntegrityError when ``decision_id`` / ``rationale_id``
        collide with an existing node id (caller owns id-uniqueness).
        """
        with self.connect() as conn:
            hyp_row = conn.execute(
                "SELECT parent_id, parent_kind, type, status FROM nodes "
                "WHERE session_id = ? AND id = ?",
                (session_id, hyp_id),
            ).fetchone()
            if hyp_row is None:
                raise ValueError(
                    f"hypothesis {hyp_id!r} not found in session {session_id!r}"
                )
            if hyp_row["type"] != "hypothesis":
                raise ValueError(
                    f"node {hyp_id!r} is type {hyp_row['type']!r}, not 'hypothesis'"
                )
            if hyp_row["status"] != "open":
                raise ValueError(
                    f"hypothesis {hyp_id!r} is not open (status="
                    f"{hyp_row['status']!r}); cannot accept a closed hypothesis"
                )
            parent_id = hyp_row["parent_id"]
            parent_kind = hyp_row["parent_kind"]

            conn.execute(
                "UPDATE nodes SET status='closed', closure_reason='resolved', "
                "updated_at = ? WHERE session_id = ? AND id = ?",
                (now, session_id, hyp_id),
            )

            sibling_rows = conn.execute(
                "SELECT id FROM nodes "
                "WHERE session_id = ? AND parent_id = ? AND parent_kind = ? "
                "AND type = 'hypothesis' AND status = 'open' AND id != ?",
                (session_id, parent_id, parent_kind, hyp_id),
            ).fetchall()
            closed_siblings: list[str] = []
            for row in sibling_rows:
                conn.execute(
                    "UPDATE nodes SET status='closed', closure_reason='rejected', "
                    "updated_at = ? WHERE session_id = ? AND id = ?",
                    (now, session_id, row["id"]),
                )
                closed_siblings.append(row["id"])

            conn.execute(
                "INSERT INTO nodes "
                "(id, session_id, type, text, status, closure_reason, "
                " parent_id, parent_kind, created_at, updated_at) "
                "VALUES (?, ?, 'decision', ?, 'closed', 'resolved', ?, ?, ?, ?)",
                (decision_id, session_id, decision_text,
                 parent_id, parent_kind, now, now),
            )

            edge_cursor = conn.execute(
                "INSERT INTO edges "
                "(session_id, from_node, to_node, type, reason, created_at) "
                "VALUES (?, ?, ?, 'derived_from', NULL, ?)",
                (session_id, decision_id, hyp_id, now),
            )
            derived_from_edge_id = edge_cursor.lastrowid

            if rationale_id is not None and rationale_text is not None:
                conn.execute(
                    "INSERT INTO nodes "
                    "(id, session_id, type, text, status, closure_reason, "
                    " parent_id, parent_kind, created_at, updated_at) "
                    "VALUES (?, ?, 'rationale', ?, 'closed', 'resolved', "
                    "        ?, 'node', ?, ?)",
                    (rationale_id, session_id, rationale_text,
                     decision_id, now, now),
                )

            self._touch_session(conn, session_id=session_id, now=now)

            return {
                "hyp_id": hyp_id,
                "decision_id": decision_id,
                "rationale_id": rationale_id,
                "closed_siblings": closed_siblings,
                "derived_from_edge_id": derived_from_edge_id,
            }

    def resolve_branch(
        self,
        *,
        session_id: str,
        parent_id: str,
        parent_kind: str,  # "root" | "node"
        results: list[dict],  # [{"node_id": str, "closure_reason": str}]
        decision_id: str | None,
        decision_text: str | None,
        rationale_id: str | None,
        rationale_text: str | None,
        derived_from_node_ids: list[str] | None,
        now: str,
    ) -> dict[str, Any]:
        """Atomically close N sibling nodes with per-node closure_reason and
        optionally insert decision + rationale + derived_from edges.

        Validation (all rolled back atomically on failure):
        - Each results[].node_id exists in session, status=open,
          is a direct child of parent_id with matching parent_kind.
        - results may be empty only when decision_text is provided.
        - If decision_text is None, rationale_text must also be None.
        """
        if decision_text is None and rationale_text is not None:
            raise ValueError(
                "rationale_text requires decision_text to be set"
            )
        if decision_text is None and derived_from_node_ids:
            raise ValueError(
                "derived_from_node_ids requires decision_text to be set"
            )
        if not results and decision_text is None:
            raise ValueError(
                "resolve_branch requires either results or decision_text"
            )
        if decision_text is not None and decision_id is None:
            raise ValueError(
                "decision_id required when decision_text is provided"
            )
        if rationale_text is not None and rationale_id is None:
            raise ValueError(
                "rationale_id required when rationale_text is provided"
            )

        with self.connect() as conn:
            # Validate parent_id + parent_kind exist in session.
            # Polymorphic parent: roots table for parent_kind="root",
            # nodes table for parent_kind="node".
            if parent_kind == "root":
                parent_exists = conn.execute(
                    "SELECT 1 FROM roots WHERE session_id = ? AND id = ?",
                    (session_id, parent_id),
                ).fetchone()
            elif parent_kind == "node":
                parent_exists = conn.execute(
                    "SELECT 1 FROM nodes WHERE session_id = ? AND id = ?",
                    (session_id, parent_id),
                ).fetchone()
            else:
                raise ValueError(
                    f"parent_kind must be 'root' or 'node', got {parent_kind!r}"
                )
            if parent_exists is None:
                raise ValueError(
                    f"parent {parent_kind} {parent_id!r} not found in "
                    f"session {session_id!r}"
                )

            closed_nodes: list[dict] = []
            for item in results:
                node_id = item["node_id"]
                closure_reason = item["closure_reason"]
                row = conn.execute(
                    "SELECT parent_id, parent_kind, status FROM nodes "
                    "WHERE session_id = ? AND id = ?",
                    (session_id, node_id),
                ).fetchone()
                if row is None:
                    raise ValueError(
                        f"node {node_id!r} not found in session {session_id!r}"
                    )
                if row["status"] != "open":
                    raise ValueError(
                        f"node {node_id!r} is not open (status={row['status']!r})"
                    )
                if row["parent_id"] != parent_id or row["parent_kind"] != parent_kind:
                    raise ValueError(
                        f"node {node_id!r} is not a direct child of "
                        f"{parent_kind} {parent_id!r}"
                    )
                conn.execute(
                    "UPDATE nodes SET status='closed', closure_reason=?, "
                    "updated_at = ? WHERE session_id = ? AND id = ?",
                    (closure_reason, now, session_id, node_id),
                )

            if decision_text is not None and decision_id is not None:
                conn.execute(
                    "INSERT INTO nodes "
                    "(id, session_id, type, text, status, closure_reason, "
                    " parent_id, parent_kind, created_at, updated_at) "
                    "VALUES (?, ?, 'decision', ?, 'closed', 'resolved', ?, ?, ?, ?)",
                    (decision_id, session_id, decision_text,
                     parent_id, parent_kind, now, now),
                )

            edges_created: list[dict] = []
            if derived_from_node_ids and decision_id is not None:
                for target in derived_from_node_ids:
                    exists = conn.execute(
                        "SELECT 1 FROM nodes WHERE session_id = ? AND id = ? "
                        "UNION ALL "
                        "SELECT 1 FROM roots WHERE session_id = ? AND id = ?",
                        (session_id, target, session_id, target),
                    ).fetchone()
                    if exists is None:
                        raise ValueError(
                            f"derived_from target {target!r} not found "
                            f"in session {session_id!r}"
                        )
                    cur = conn.execute(
                        "INSERT INTO edges "
                        "(session_id, from_node, to_node, type, reason, created_at) "
                        "VALUES (?, ?, ?, 'derived_from', NULL, ?)",
                        (session_id, decision_id, target, now),
                    )
                    edge_row = conn.execute(
                        "SELECT * FROM edges WHERE id = ?", (cur.lastrowid,)
                    ).fetchone()
                    edges_created.append({k: edge_row[k] for k in edge_row.keys()})

            if (
                rationale_id is not None
                and rationale_text is not None
                and decision_id is not None
            ):
                conn.execute(
                    "INSERT INTO nodes "
                    "(id, session_id, type, text, status, closure_reason, "
                    " parent_id, parent_kind, created_at, updated_at) "
                    "VALUES (?, ?, 'rationale', ?, 'closed', 'resolved', "
                    "        ?, 'node', ?, ?)",
                    (rationale_id, session_id, rationale_text,
                     decision_id, now, now),
                )

            self._touch_session(conn, session_id=session_id, now=now)

            # Re-fetch closed node rows after UPDATE so we return post-close state.
            for item in results:
                node_row = conn.execute(
                    "SELECT * FROM nodes WHERE session_id = ? AND id = ?",
                    (session_id, item["node_id"]),
                ).fetchone()
                closed_nodes.append({k: node_row[k] for k in node_row.keys()})

            decision_node: dict | None = None
            if decision_text is not None and decision_id is not None:
                d_row = conn.execute(
                    "SELECT * FROM nodes WHERE session_id = ? AND id = ?",
                    (session_id, decision_id),
                ).fetchone()
                decision_node = {k: d_row[k] for k in d_row.keys()}

            rationale_node: dict | None = None
            if (
                rationale_id is not None
                and rationale_text is not None
                and decision_id is not None
            ):
                r_row = conn.execute(
                    "SELECT * FROM nodes WHERE session_id = ? AND id = ?",
                    (session_id, rationale_id),
                ).fetchone()
                rationale_node = {k: r_row[k] for k in r_row.keys()}

            return {
                "closed_nodes": closed_nodes,
                "decision_node": decision_node,
                "rationale_node": rationale_node,
                "edges_created": edges_created,
            }

    def list_edges(
        self,
        *,
        session_id: str,
        from_node: str | None = None,
        to_node: str | None = None,
        edge_type: str | None = None,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM edges WHERE session_id = ?"
        params: list[Any] = [session_id]
        if from_node is not None:
            sql += " AND from_node = ?"
            params.append(from_node)
        if to_node is not None:
            sql += " AND to_node = ?"
            params.append(to_node)
        if edge_type is not None:
            sql += " AND type = ?"
            params.append(edge_type)
        sql += " ORDER BY id"
        with self.connect() as conn:
            return list(conn.execute(sql, params))

    def list_unblocked_open_nodes(
        self,
        *,
        session_id: str,
        root_id: str | None = None,
        blocker_edge_type: str = "blocks",
    ) -> list[sqlite3.Row]:
        """Return open nodes that no still-live endpoint is blocking via the
        given edge type (directional convention: edge.from blocks edge.to).

        A "live endpoint" is either a node with ``status='open'`` or a root
        with ``lifecycle='active'``. Roots and nodes are treated symmetrically
        as blocker candidates so users can express dependencies from either.

        ``blocker_edge_type`` defaults to ``"blocks"`` but is overridable so a
        caller using a different vocabulary (e.g., ``"requires"``) can opt in.
        """
        candidates = self.list_open_nodes(session_id=session_id, root_id=root_id)
        if not candidates:
            return []
        with self.connect() as conn:
            blocked = conn.execute(
                """
                SELECT DISTINCT e.to_node
                FROM edges e
                WHERE e.session_id = ?
                  AND e.type = ?
                  AND (
                      EXISTS (
                          SELECT 1 FROM nodes n
                          WHERE n.session_id = e.session_id
                            AND n.id = e.from_node
                            AND n.status = 'open'
                      )
                      OR EXISTS (
                          SELECT 1 FROM roots r
                          WHERE r.session_id = e.session_id
                            AND r.id = e.from_node
                            AND r.lifecycle = 'active'
                      )
                  )
                """,
                (session_id, blocker_edge_type),
            ).fetchall()
        blocked_ids = {row["to_node"] for row in blocked}
        return [n for n in candidates if n["id"] not in blocked_ids]

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

    # -----------------------------------------------------------------------
    # v0.3 Task 2: scope_root resolution + pool_items CRUD
    # -----------------------------------------------------------------------

    def get_or_create_scope_root(self, *, scope: str, now: str) -> sqlite3.Row:
        """Return the singleton scope_root row for the given scope, creating if absent.

        Uses the UNIQUE partial index on (scope) WHERE scope_root=1 to enforce singleton.
        Generated id format: root_<hex8> via ids.root_id().
        """
        from .ids import root_id  # local import to avoid top-of-file churn
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM roots WHERE scope = ? AND scope_root = 1",
                (scope,),
            ).fetchone()
            if existing is not None:
                return existing
            new_id = root_id()
            conn.execute(
                """
                INSERT INTO roots
                    (id, session_id, scope, scope_root, topic, lifecycle,
                     spawned_at, last_focused_at)
                VALUES (?, NULL, ?, 1, ?, 'active', ?, NULL)
                """,
                (new_id, scope, f"{scope} scope root", now),
            )
            return conn.execute(
                "SELECT * FROM roots WHERE id = ?", (new_id,)
            ).fetchone()

    def insert_pool_item(
        self,
        *,
        pool_id: str,
        scope_root_id: str,
        text: str,
        origin_session_id: str | None,
        origin_turn: str | None,
        tags: str | None,
        now: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pool_items
                    (id, scope_root_id, origin_session_id, text,
                     origin_turn, created_at, elevated_to, elevated_at,
                     dropped_at, tags)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
                """,
                (pool_id, scope_root_id, origin_session_id, text,
                 origin_turn, now, tags),
            )

    def list_pool_items(
        self, *, scope_root_id: str, active_only: bool = True
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            if active_only:
                cursor = conn.execute(
                    """
                    SELECT * FROM pool_items
                    WHERE scope_root_id = ?
                      AND elevated_to IS NULL
                      AND dropped_at IS NULL
                    ORDER BY created_at, id
                    """,
                    (scope_root_id,),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM pool_items WHERE scope_root_id = ? "
                    "ORDER BY created_at, id",
                    (scope_root_id,),
                )
            return list(cursor)

    def mark_pool_elevated(
        self, *, pool_id: str, elevated_to: str, now: str
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE pool_items SET elevated_to = ?, elevated_at = ? "
                "WHERE id = ?",
                (elevated_to, now, pool_id),
            )

    def drop_pool_item(
        self, *, pool_id: str, reason: str | None, now: str
    ) -> None:
        # reason is recorded by appending to tags as "dropped:<reason>" — keeps
        # schema minimal for MVP. Future v0.4 may add a dropped_reason column.
        with self.connect() as conn:
            conn.execute(
                "UPDATE pool_items SET dropped_at = ?, "
                "tags = COALESCE(NULLIF(tags || ',', ','), '') || ? "
                "WHERE id = ?",
                (now, f"dropped:{reason}" if reason else "dropped", pool_id),
            )
