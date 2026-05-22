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

        Migration chain on pre-existing databases:
          - user_version = 2: run _migrate_v2_to_v3 (structural rebuild),
            then migrate_v3_to_v4 (ALTER TABLE + partial index).
          - user_version = 3: run migrate_v3_to_v4 only.
          - user_version >= 4: no migration needed; schema.sql is applied
            (CREATE TABLE IF NOT EXISTS is idempotent).

        SQLite limitation: ALTER TABLE cannot add CHECK constraints, so the
        table-level CHECK (scope_root = 0 OR scope IS NOT NULL) is only present
        on freshly-created v3+ databases. For upgraded databases, this invariant
        is enforced at runtime by the Task 4 migration script.
        """
        from .migrate_v3_to_v4 import migrate as _migrate_v3_to_v4

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Read user_version BEFORE running schema.sql so we can dispatch to the
        # correct migration path.  schema.sql unconditionally sets user_version=4,
        # so reading the version after executescript() would always return 4 and
        # make the v3→v4 branch unreachable for genuine v3 databases.
        with sqlite3.connect(db_path) as conn:
            pre_schema_version = conn.execute("PRAGMA user_version").fetchone()[0]

        # Run structural migrations in order BEFORE applying schema.sql.
        # schema.sql uses CREATE TABLE IF NOT EXISTS and is idempotent, so it
        # is safe to run after the migration has already added the new columns.
        if 0 < pre_schema_version < 3:
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA busy_timeout = 5000")
                cls._migrate_v2_to_v3(conn)
                # pool_items was introduced in v3 schema.sql.  A genuine v2 DB
                # has no pool_items table yet, so we create it here (v3 shape,
                # without rejected_* columns) so that _migrate_v3_to_v4 can
                # safely run its ALTER TABLE pool_items ADD COLUMN statements.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pool_items (
                        id                TEXT PRIMARY KEY,
                        scope_root_id     TEXT NOT NULL REFERENCES roots(id),
                        origin_session_id TEXT REFERENCES sessions(id),
                        text              TEXT NOT NULL,
                        origin_turn       TEXT,
                        created_at        TEXT NOT NULL,
                        elevated_to       TEXT REFERENCES nodes(id),
                        elevated_at       TEXT,
                        dropped_at        TEXT,
                        tags              TEXT
                    )
                """)
            _migrate_v3_to_v4(db_path=db_path)
        elif pre_schema_version == 3:
            _migrate_v3_to_v4(db_path=db_path)

        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            schema = files("dpd_mcp_server").joinpath("schema.sql").read_text()
            conn.executescript(schema)

        return cls(db_path)

    @staticmethod
    def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
        """Upgrade a v0.2 database to v0.3 by rebuilding constrained tables.

        SQLite cannot ALTER existing column constraints (NOT NULL, CHECK),
        so we rebuild ``roots`` and ``nodes`` with the v3 shape, preserving data.

        The method is idempotent: if the new columns already exist (e.g. from a
        previously interrupted upgrade), the INSERT … SELECT still copies all
        rows correctly and DROP/RENAME recreates the v3-shaped table.

        Called only when user_version < 3; user_version is updated to 3 by
        the PRAGMA at the end of schema.sql (via executescript).
        """
        conn.executescript("""
            -- Disable FK temporarily so we can drop/rename tables freely.
            PRAGMA foreign_keys = OFF;

            -- 1. Rebuild roots: session_id nullable, add scope/scope_root/migrated_to_start_id
            CREATE TABLE IF NOT EXISTS roots_new (
                id                   TEXT PRIMARY KEY,
                session_id           TEXT REFERENCES sessions(id),
                scope                TEXT,
                scope_root           INTEGER NOT NULL DEFAULT 0
                    CHECK (scope_root IN (0,1)),
                migrated_to_start_id TEXT,
                topic                TEXT NOT NULL,
                lifecycle            TEXT NOT NULL
                    CHECK (lifecycle IN ('active','archived','deferred')),
                spawned_at           TEXT NOT NULL,
                last_focused_at      TEXT,
                CHECK (scope_root = 0 OR scope IS NOT NULL)
            );
            INSERT OR IGNORE INTO roots_new
                (id, session_id, topic, lifecycle, spawned_at, last_focused_at)
                SELECT id, session_id, topic, lifecycle, spawned_at, last_focused_at
                FROM roots;
            DROP TABLE roots;
            ALTER TABLE roots_new RENAME TO roots;

            -- 2. Rebuild nodes: extend type CHECK to include start/end; add v3 columns
            CREATE TABLE IF NOT EXISTS nodes_new (
                id              TEXT PRIMARY KEY,
                session_id      TEXT NOT NULL REFERENCES sessions(id),
                type            TEXT NOT NULL CHECK (type IN (
                    'question','plan','hypothesis','goal','problem',
                    'answer','action','verification','decision','resolution',
                    'evidence','constraint','assumption','rationale','risk',
                    'start','end'
                )),
                text            TEXT NOT NULL,
                status          TEXT NOT NULL CHECK (status IN ('open','closed')),
                closure_reason  TEXT
                    CHECK (closure_reason IS NULL OR
                           closure_reason IN ('resolved','rejected','invalidated')),
                parent_id       TEXT NOT NULL,
                parent_kind     TEXT NOT NULL CHECK (parent_kind IN ('root','node')),
                paired_for      TEXT REFERENCES nodes(id),
                achievement_conditions TEXT,
                achievement_conditions_satisfied INTEGER NOT NULL DEFAULT 0
                    CHECK (achievement_conditions_satisfied IN (0,1)),
                state           TEXT NOT NULL DEFAULT 'active'
                    CHECK (state IN ('active','archived','closed','deletable','gone')),
                archived_at     TEXT,
                closed_at       TEXT,
                deletable_at    TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );
            INSERT OR IGNORE INTO nodes_new
                (id, session_id, type, text, status, closure_reason,
                 parent_id, parent_kind, created_at, updated_at)
                SELECT id, session_id, type, text, status, closure_reason,
                       parent_id, parent_kind, created_at, updated_at
                FROM nodes;
            DROP TABLE nodes;
            ALTER TABLE nodes_new RENAME TO nodes;

            PRAGMA foreign_keys = ON;
        """)

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
        mode: str = "entry",
        now: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, scope, label, mode, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, scope, label, mode, now, now),
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
        provenance: str = "grounded",
        state: str = "active",
    ) -> str:
        """Insert a node, atomically classifying its parent kind.

        Returns the parent_kind that was determined ('root' or 'node').
        Raises ValueError if the parent does not exist in this session.

        Classify-and-insert run in a single transaction so a concurrent
        delete between the lookup and the insert cannot create an orphan.
        provenance and state are validated by DB CHECK constraints; invalid
        values cause sqlite3.IntegrityError which propagates to the caller.
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
                     parent_id, parent_kind, provenance, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'open', NULL, ?, ?, ?, ?, ?, ?)
                """,
                (node_id, session_id, node_type, text,
                 parent_id, parent_kind, provenance, state, now, now),
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

        When ``node_id`` is not None, validates that the id exists in the
        session — checking both nodes and roots (so root_id can be used as
        a focus target per dogfood obs #5). Always validates that the session
        exists. Bumps updated_at.
        """
        with self.connect() as conn:
            if node_id is not None:
                exists = conn.execute(
                    "SELECT 1 FROM nodes WHERE session_id = ? AND id = ? "
                    "UNION ALL "
                    "SELECT 1 FROM roots WHERE session_id = ? AND id = ?",
                    (session_id, node_id, session_id, node_id),
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
        state: str | None = None,
    ) -> list[sqlite3.Row]:
        """Return open nodes in the session, optionally restricted to one root
        and/or filtered by the ``state`` column.

        With ``root_id=None``, returns every node with status='open' in the
        session (creation-order). With a root, walks that root's subtree and
        filters to open nodes.

        When ``state`` is provided, the result is further filtered to nodes
        whose ``state`` column matches the given value (e.g. ``'active'``).
        When ``state`` is None (default), no state filter is applied —
        preserving v2 behavior.
        """
        if root_id is None:
            sql = (
                "SELECT * FROM nodes "
                "WHERE session_id = ? AND status = 'open'"
            )
            params: list[Any] = [session_id]
            if state is not None:
                sql += " AND state = ?"
                params.append(state)
            sql += " ORDER BY created_at, id"
            with self.connect() as conn:
                return list(conn.execute(sql, params))
        subtree = self.walk_subtree(session_id=session_id, root_id=root_id)
        nodes = [n for n in subtree if n["status"] == "open"]
        if state is not None:
            nodes = [n for n in nodes if n["state"] == state]
        return nodes

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

    def get_or_create_scope_root(self, *, scope: str | None, now: str) -> sqlite3.Row:
        """Return the singleton scope_root row for the given scope, creating if absent.

        Top-level scope (scope=None) is stored under the empty-string sentinel ``''``
        to satisfy the ``CHECK (scope IS NOT NULL)`` constraint when ``scope_root=1``.
        Callers should pass ``None`` for top-level; storage handles the normalization.

        Uses the UNIQUE partial index on (scope) WHERE scope_root=1 to enforce singleton.
        Generated id format: root_<hex8> via ids.root_id().
        """
        from .ids import root_id  # local import: parameter name `root_id` is used elsewhere in this class
        scope_key = scope if scope is not None else ""
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM roots WHERE scope = ? AND scope_root = 1",
                (scope_key,),
            ).fetchone()
            if existing is not None:
                return existing
            generated_id = root_id()
            conn.execute(
                """
                INSERT INTO roots
                    (id, session_id, scope, scope_root, topic, lifecycle,
                     spawned_at, last_focused_at)
                VALUES (?, NULL, ?, 1, ?, 'active', ?, NULL)
                """,
                (generated_id, scope_key, f"{scope_key or 'top-level'} scope root", now),
            )
            return conn.execute(
                "SELECT * FROM roots WHERE id = ?", (generated_id,)
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
        self,
        *,
        scope_root_id: str,
        active_only: bool = True,
        include_rejected: bool = False,
        rejected_only: bool = False,
    ) -> list[sqlite3.Row]:
        if active_only and rejected_only:
            raise ValueError("active_only and rejected_only are mutually exclusive")
        if active_only and include_rejected:
            raise ValueError("active_only and include_rejected are mutually exclusive")
        with self.connect() as conn:
            if rejected_only:
                # Return only items that have been rejected (and not dropped or elevated).
                cursor = conn.execute(
                    """
                    SELECT * FROM pool_items
                    WHERE scope_root_id = ?
                      AND elevated_to IS NULL
                      AND rejected_at IS NOT NULL
                      AND dropped_at IS NULL
                    ORDER BY created_at, id
                    """,
                    (scope_root_id,),
                )
            elif include_rejected:
                # Return active and rejected items; exclude dropped and elevated.
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
            elif active_only:
                # Default: exclude rejected and dropped items.
                cursor = conn.execute(
                    """
                    SELECT * FROM pool_items
                    WHERE scope_root_id = ?
                      AND elevated_to IS NULL
                      AND rejected_at IS NULL
                      AND dropped_at IS NULL
                    ORDER BY created_at, id
                    """,
                    (scope_root_id,),
                )
            else:
                # active_only=False with no filter flags: return everything.
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

    def reject_pool_item(
        self,
        *,
        pool_id: str,
        reason: str | None = None,
        now: str,
    ) -> dict[str, Any]:
        """Mark a pool item as rejected.

        Orthogonal to drop_pool_item: rejection is a soft suppression signal,
        drop is hard removal.  A dropped item cannot be rejected (raises
        ValueError); a rejected item can still be dropped later.

        Idempotency: re-rejecting updates rejected_reason but keeps the original
        rejected_at timestamp ("first reject wins" for timestamp).

        Raises ValueError if pool_id doesn't exist or is already dropped.
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT rejected_at, dropped_at FROM pool_items WHERE id = ?",
                (pool_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"pool item not found: {pool_id}")
            if row["dropped_at"] is not None:
                raise ValueError(
                    f"pool item is dropped, cannot reject: {pool_id}"
                )
            # Idempotent: keep original rejected_at if already set.
            if row["rejected_at"] is None:
                conn.execute(
                    "UPDATE pool_items SET rejected_at = ?, rejected_reason = ?"
                    " WHERE id = ?",
                    (now, reason, pool_id),
                )
            else:
                conn.execute(
                    "UPDATE pool_items SET rejected_reason = ? WHERE id = ?",
                    (reason, pool_id),
                )
            updated = conn.execute(
                "SELECT * FROM pool_items WHERE id = ?", (pool_id,)
            ).fetchone()
        return dict(updated)

    # ------------------------------------------------------------------
    # v0.3 node insert + state machine
    # ------------------------------------------------------------------

    def insert_node_v3(
        self,
        *,
        node_id: str,
        session_id: str,
        node_type: str,
        text: str,
        parent_id: str,
        paired_for: str | None,
        achievement_conditions: str | None,
        now: str,
        provenance: str = "grounded",
        state: str = "active",
    ) -> None:
        """v3 node insert: supports paired_for + achievement_conditions + provenance + state.

        End nodes must specify paired_for (= the Start node they terminate).

        Classify-and-insert run in a single transaction so a concurrent delete
        between the lookup and the insert cannot create an orphan node row.
        parent_kind is derived internally by querying roots then nodes within
        the same transaction — callers must not pass it.

        Raises ValueError if parent_id is not found in either roots or nodes.
        provenance and state are validated by DB CHECK constraints; invalid
        values cause sqlite3.IntegrityError which propagates to the caller.
        """
        if node_type == "end" and not paired_for:
            raise ValueError("End nodes require paired_for (= paired Start id)")
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
                     parent_id, parent_kind,
                     paired_for, achievement_conditions,
                     achievement_conditions_satisfied, state, provenance,
                     archived_at, closed_at, deletable_at,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 'open', NULL, ?, ?, ?, ?, 0, ?, ?,
                        NULL, NULL, NULL, ?, ?)
                """,
                (node_id, session_id, node_type, text,
                 parent_id, parent_kind,
                 paired_for, achievement_conditions,
                 state, provenance,
                 now, now),
            )
            self._touch_session(conn, session_id=session_id, now=now)

    def _reachable_from_start(
        self, conn: sqlite3.Connection, *, session_id: str,
        start_id: str, target_id: str,
    ) -> bool:
        """Walk parent_id chain from target up to root; return True if start_id encountered."""
        cur = target_id
        seen: set[str] = set()
        while cur and cur not in seen:
            if cur == start_id:
                return True
            seen.add(cur)
            row = conn.execute(
                "SELECT parent_id, parent_kind FROM nodes "
                "WHERE session_id = ? AND id = ?",
                (session_id, cur),
            ).fetchone()
            if row is None:
                return False
            if row["parent_kind"] == "root":
                return False
            cur = row["parent_id"]
        return False

    def mark_reached(
        self, *, session_id: str, end_node_id: str, now: str
    ) -> None:
        """Mark End node as reached. Verify Start→End connectivity, then
        transition entire subgraph to archived → closed (forward-only)."""
        with self.connect() as conn:
            end = conn.execute(
                "SELECT * FROM nodes WHERE session_id = ? AND id = ?",
                (session_id, end_node_id),
            ).fetchone()
            if end is None:
                raise ValueError(f"end_node_id {end_node_id!r} not found")
            if end["type"] != "end":
                raise ValueError(f"node {end_node_id!r} is not type=end")
            if end["paired_for"] is None:
                raise ValueError(f"end node {end_node_id!r} has no paired_for")
            start_id = end["paired_for"]
            if not self._reachable_from_start(
                conn, session_id=session_id,
                start_id=start_id, target_id=end_node_id,
            ):
                raise ValueError(
                    f"end node {end_node_id!r} not reachable from "
                    f"paired start {start_id!r}"
                )
            # Collect all nodes in the subgraph (start + descendants via parent_id forward walk).
            subgraph_ids = self._subgraph_node_ids(
                conn, session_id=session_id, start_id=start_id
            )
            # Mark end's achievement, then archive+close every active member.
            conn.execute(
                "UPDATE nodes SET achievement_conditions_satisfied = 1 "
                "WHERE session_id = ? AND id = ?",
                (session_id, end_node_id),
            )
            placeholders = ",".join("?" * len(subgraph_ids))
            conn.execute(
                f"""
                UPDATE nodes
                SET state = 'closed',
                    status = 'closed',
                    closure_reason = COALESCE(closure_reason, 'resolved'),
                    archived_at = COALESCE(archived_at, ?),
                    closed_at = COALESCE(closed_at, ?),
                    updated_at = ?
                WHERE session_id = ? AND id IN ({placeholders})
                  AND state = 'active'
                """,
                (now, now, now, session_id, *subgraph_ids),
            )
            self._touch_session(conn, session_id=session_id, now=now)

    def _subgraph_node_ids(
        self, conn: sqlite3.Connection, *, session_id: str, start_id: str,
    ) -> list[str]:
        """Return start_id + all descendants reachable via parent_id (forward)."""
        members = [start_id]
        frontier = [start_id]
        while frontier:
            next_frontier = []
            for parent in frontier:
                children = conn.execute(
                    "SELECT id FROM nodes WHERE session_id = ? AND parent_id = ?",
                    (session_id, parent),
                ).fetchall()
                for c in children:
                    if c["id"] not in members:
                        members.append(c["id"])
                        next_frontier.append(c["id"])
            frontier = next_frontier
        return members

    def dump_persist_subgraph(
        self, *, session_id: str, start_node_id: str,
        destination: str | None, now: str,
    ) -> None:
        """closed → deletable for the subgraph rooted at start_node_id.

        `destination` is recorded but the actual file write is the caller's concern;
        storage layer only flips state.
        """
        with self.connect() as conn:
            members = self._subgraph_node_ids(
                conn, session_id=session_id, start_id=start_node_id
            )
            placeholders = ",".join("?" * len(members))
            conn.execute(
                f"""
                UPDATE nodes
                SET state = 'deletable',
                    deletable_at = COALESCE(deletable_at, ?),
                    updated_at = ?
                WHERE session_id = ? AND id IN ({placeholders})
                  AND state = 'closed'
                """,
                (now, now, session_id, *members),
            )
            self._touch_session(conn, session_id=session_id, now=now)

    def delete_subgraph(
        self, *, session_id: str, start_node_id: str, now: str,
    ) -> None:
        """Physical delete for a subgraph in `deletable` state.

        Cleans up edges that reference any node in the subgraph (from either side).
        Nulls out `paired_for` FK references within the subgraph before deletion
        so the bulk DELETE does not violate intra-subgraph FK constraints.
        """
        with self.connect() as conn:
            members = self._subgraph_node_ids(
                conn, session_id=session_id, start_id=start_node_id
            )
            placeholders = ",".join("?" * len(members))
            conn.execute(
                f"""
                DELETE FROM edges
                WHERE session_id = ?
                  AND (from_node IN ({placeholders}) OR to_node IN ({placeholders}))
                """,
                (session_id, *members, *members),
            )
            # Null out paired_for references within the subgraph to avoid FK
            # constraint violations when the referenced Start node is deleted
            # in the same batch as the End node that references it.
            conn.execute(
                f"""
                UPDATE nodes SET paired_for = NULL
                WHERE session_id = ? AND id IN ({placeholders})
                  AND paired_for IN ({placeholders})
                """,
                (session_id, *members, *members),
            )
            # Tombstone pool_items that were elevated into this subgraph.
            # Nulling elevated_to alone would silently reactivate them (active
            # filter: elevated_to IS NULL AND dropped_at IS NULL).  Setting
            # dropped_at preserves the audit trail (elevated_at stays intact)
            # while preventing zombie resurrection.
            conn.execute(
                f"""
                UPDATE pool_items
                SET elevated_to = NULL,
                    dropped_at = ?,
                    tags = COALESCE(NULLIF(tags || ',', ','), '') || ?
                WHERE elevated_to IN ({placeholders})
                """,
                (now, "dropped:subgraph_deleted", *members),
            )
            conn.execute(
                f"DELETE FROM nodes WHERE session_id = ? AND id IN ({placeholders})",
                (session_id, *members),
            )
            self._touch_session(conn, session_id=session_id, now=now)

    # ------------------------------------------------------------------
    # v0.3.1 Task 6: session mode lifecycle
    # ------------------------------------------------------------------

    _ALLOWED_TRANSITIONS: dict = {
        None: {"entry", "ambient"},
        "entry": {"ambient", "idle"},
        "ambient": {"idle"},
        "idle": {"entry"},
    }

    def set_session_mode(
        self, *, session_id: str, mode: str, now: str
    ) -> dict:
        """Transition session.mode per the v0.3.1 lifecycle table (§9.1.1).

        Allowed transitions:
          null  → entry | ambient   (legacy migration)
          entry → ambient | idle
          ambient → idle
          idle  → entry

        Self-transitions (same mode) are idempotent (no-op, returns current row).

        Raises ValueError for:
          - ``mode`` not in {entry, ambient, idle}
          - session not found
          - transition not in the allowed table
        """
        if mode not in {"entry", "ambient", "idle"}:
            raise ValueError(
                f"invalid mode: {mode!r}. Must be one of entry/ambient/idle."
            )
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"session not found: {session_id!r}")
            current = row["mode"]
            if current == mode:
                # Idempotent self-transition — return current state unchanged.
                return {key: row[key] for key in row.keys()}
            allowed = self._ALLOWED_TRANSITIONS.get(current, set())
            if mode not in allowed:
                raise ValueError(
                    f"invalid transition: {current!r} → {mode!r}. "
                    f"Allowed from {current!r}: {sorted(allowed)}"
                )
            conn.execute(
                "UPDATE sessions SET mode = ?, updated_at = ? WHERE id = ?",
                (mode, now, session_id),
            )
            updated = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return {key: updated[key] for key in updated.keys()}

    def force_delete_node(
        self, *, session_id: str, node_id: str, now: str,
    ) -> None:
        """Physical delete a single node regardless of state.

        Handles paired_for self-FK and pool_items.elevated_to FK.

        WARNING: This is a single-node force delete intended for emergency cleanup.
        If the target node has children (= nodes with parent_id pointing to it),
        those children will be left with dangling parent_id references. Children
        are NOT reparented, NOT cascade-deleted, and NOT validated. The caller
        is responsible for ensuring the target has no children, OR for explicitly
        handling orphans afterward.

        For safe subgraph deletion, prefer ``delete_subgraph`` (requires state=deletable).

        Steps:
        1. NULLs paired_for on any End node paired to this node (if target is a Start).
        2. Tombstones pool_items elevated into this node (NULL + dropped_at + tag)
           to prevent silent reactivation of the pool item.
        3. Deletes referencing edges, then the node itself.
        """
        with self.connect() as conn:
            # Null paired_for references TO this node (e.g. its paired End node)
            conn.execute(
                "UPDATE nodes SET paired_for = NULL "
                "WHERE session_id = ? AND paired_for = ?",
                (session_id, node_id),
            )
            # Tombstone any pool items elevated into this node
            conn.execute(
                "UPDATE pool_items SET elevated_to = NULL, dropped_at = ?, "
                "tags = COALESCE(NULLIF(tags || ',', ','), '') || ? "
                "WHERE elevated_to = ?",
                (now, "dropped:node_force_deleted", node_id),
            )
            conn.execute(
                "DELETE FROM edges WHERE session_id = ? "
                "AND (from_node = ? OR to_node = ?)",
                (session_id, node_id, node_id),
            )
            conn.execute(
                "DELETE FROM nodes WHERE session_id = ? AND id = ?",
                (session_id, node_id),
            )
            self._touch_session(conn, session_id=session_id, now=now)
