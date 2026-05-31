"""SQLite storage layer for DPD server.

Owns the sqlite connection lifecycle and exposes CRUD primitives.
Tools never construct SQL directly — they call methods here.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator


def _schema_version() -> int:
    """The current schema version, parsed from ``schema.sql``'s trailing
    ``PRAGMA user_version = N``.

    schema.sql is the single source of truth for the version a freshly-opened
    DB carries; deriving the constant here keeps the restore guard in
    ``migrate.py`` from drifting when the schema bumps (#74 review).
    """
    schema = files("dpd_mcp_server").joinpath("schema.sql").read_text()
    matches = re.findall(r"PRAGMA\s+user_version\s*=\s*(\d+)", schema)
    if not matches:
        raise RuntimeError("schema.sql has no `PRAGMA user_version = N`")
    return int(matches[-1])


SCHEMA_VERSION = _schema_version()


class Storage:
    """Handle to a per-agent-scope sqlite database."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        """Filesystem path of the backing sqlite database."""
        return self._db_path

    @classmethod
    def open(cls, db_path: str) -> "Storage":
        """Create or open the database, applying schema and enabling WAL.

        Migration chain on pre-existing databases:
          - user_version = 2: run _migrate_v2_to_v3 (structural rebuild),
            then migrate_v3_to_v4 (ALTER TABLE + partial index),
            then migrate_v4_to_v5 (FTS table + backfill),
            then migrate_v5_to_v6 (ALTER TABLE: nodes.severity),
            then migrate_v6_to_v7 (ALTER TABLE: edges.layer +
            verification_priority; CREATE edge_verifications),
            then migrate_v7_to_v8 (CREATE notes + partial unique index),
            then migrate_v8_to_v9 (rebuild nodes WITHOUT the type CHECK).
          - user_version = 3: run v3→v4, v4→v5, v5→v6, v6→v7, v7→v8, v8→v9.
          - user_version = 4: run v4→v5, v5→v6, v6→v7, v7→v8, v8→v9.
          - user_version = 5: run v5→v6, v6→v7, v7→v8, v8→v9.
          - user_version = 6: run v6→v7, v7→v8, v8→v9.
          - user_version = 7: run v7→v8, v8→v9.
          - user_version = 8: run v8→v9 only.
          - user_version >= 9: no migration needed; schema.sql is applied
            (CREATE TABLE IF NOT EXISTS is idempotent).

        SQLite limitation: ALTER TABLE cannot add CHECK constraints, so the
        table-level CHECK (scope_root = 0 OR scope IS NOT NULL) and the
        column-level edges.layer / verification_priority CHECKs are only
        present on freshly-created databases. For upgraded databases, these
        invariants are enforced at runtime by the migration scripts / app code.
        """
        from .migrate_v3_to_v4 import migrate as _migrate_v3_to_v4
        from .migrate_v4_to_v5 import migrate as _migrate_v4_to_v5
        from .migrate_v5_to_v6 import migrate as _migrate_v5_to_v6
        from .migrate_v6_to_v7 import migrate as _migrate_v6_to_v7
        from .migrate_v7_to_v8 import migrate as _migrate_v7_to_v8
        from .migrate_v8_to_v9 import migrate as _migrate_v8_to_v9

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Read user_version BEFORE running schema.sql so we can dispatch to the
        # correct migration path.  schema.sql unconditionally sets the current
        # user_version, so reading the version after executescript() would
        # always return the latest and make the upgrade branches unreachable
        # for genuine older databases.
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
            _migrate_v4_to_v5(db_path=db_path)
            _migrate_v5_to_v6(db_path=db_path)
            _migrate_v6_to_v7(db_path=db_path)
            _migrate_v7_to_v8(db_path=db_path)
            _migrate_v8_to_v9(db_path=db_path)
        elif pre_schema_version == 3:
            _migrate_v3_to_v4(db_path=db_path)
            _migrate_v4_to_v5(db_path=db_path)
            _migrate_v5_to_v6(db_path=db_path)
            _migrate_v6_to_v7(db_path=db_path)
            _migrate_v7_to_v8(db_path=db_path)
            _migrate_v8_to_v9(db_path=db_path)
        elif pre_schema_version == 4:
            _migrate_v4_to_v5(db_path=db_path)
            _migrate_v5_to_v6(db_path=db_path)
            _migrate_v6_to_v7(db_path=db_path)
            _migrate_v7_to_v8(db_path=db_path)
            _migrate_v8_to_v9(db_path=db_path)
        elif pre_schema_version == 5:
            _migrate_v5_to_v6(db_path=db_path)
            _migrate_v6_to_v7(db_path=db_path)
            _migrate_v7_to_v8(db_path=db_path)
            _migrate_v8_to_v9(db_path=db_path)
        elif pre_schema_version == 6:
            _migrate_v6_to_v7(db_path=db_path)
            _migrate_v7_to_v8(db_path=db_path)
            _migrate_v8_to_v9(db_path=db_path)
        elif pre_schema_version == 7:
            _migrate_v7_to_v8(db_path=db_path)
            _migrate_v8_to_v9(db_path=db_path)
        elif pre_schema_version == 8:
            _migrate_v8_to_v9(db_path=db_path)

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

    _VALID_SESSION_MODES = frozenset({"entry", "ambient", "idle"})

    def list_sessions(
        self,
        *,
        scope: str | None,
        mode_filter: str | list[str] | None = None,
    ) -> list[sqlite3.Row]:
        """Return sessions for the given sub-scope, most-recently-updated first.

        ``scope=None`` matches only top-level sessions (rows with NULL scope),
        not "all sessions" — sub-scope and top-level are kept distinct so the
        skill startup flow (§8.3 step 3) can list exactly the candidates for
        the cwd-resolved scope.

        ``mode_filter`` optionally restricts results to sessions whose ``mode``
        column is in the given set. Accepts a single string or a list of strings.
        Valid values: ``entry``, ``ambient``, ``idle``. Raises ``ValueError``
        for any unrecognised value. When ``None`` (default), no mode filter is
        applied — preserving backward-compatible behavior.
        """
        # Validate and normalise mode_filter.
        modes: list[str] | None = None
        if mode_filter is not None:
            if isinstance(mode_filter, str):
                candidates = [mode_filter]
            else:
                candidates = list(mode_filter)
            for m in candidates:
                if m not in self._VALID_SESSION_MODES:
                    raise ValueError(
                        f"invalid mode_filter value: {m!r}. "
                        f"Must be one of {sorted(self._VALID_SESSION_MODES)}."
                    )
            modes = candidates

        with self.connect() as conn:
            sql = "SELECT * FROM sessions WHERE "
            params: list[Any] = []

            if scope is None:
                sql += "scope IS NULL"
            else:
                sql += "scope = ?"
                params.append(scope)

            if modes is not None:
                placeholders = ",".join("?" * len(modes))
                sql += f" AND mode IN ({placeholders})"
                params.extend(modes)

            sql += " ORDER BY updated_at DESC, id"
            cursor = conn.execute(sql, params)
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

    # ------------------------------------------------------------------
    # Note layer (#55): anchored long-form narrative.
    # ------------------------------------------------------------------

    NOTE_ANCHOR_KINDS = frozenset({"node", "root"})
    NOTE_KINDS = frozenset({
        "narrative", "caveat", "external-analysis", "rejected-alternative",
    })

    def add_note(
        self,
        *,
        session_id: str,
        anchor_kind: str,
        anchor_id: str,
        kind: str,
        text: str,
        note_id: str,
        now: str,
    ) -> dict[str, Any]:
        """Attach a note to an anchor, superseding any existing active note.

        The anchor (``anchor_kind`` ∈ node/root, ``anchor_id``) must already
        exist in the same session. Existence — not active state — is the bar:
        a note may be attached to an archived/closed anchor (carry-forward and
        /dpd-import both rely on this), mirroring ``add_edge``'s endpoint rule.

        Growth is append-only by supersession (the canonicality invariant from
        the note-layer spec): if an active note already exists for this
        ``(anchor_kind, anchor_id, kind)``, it is archived (``state='archived'``,
        ``updated_at=now``) and the new note inserted as the single active row.
        The partial unique index ``uniq_notes_active_anchor_kind`` is the hard
        guarantee that at most one active note exists per axis; this method is
        the cooperative path that keeps callers from tripping it.

        ``anchor_kind`` and ``kind`` are validated here (not just by the schema
        CHECKs) so ALTER-upgraded databases reject bad values identically.
        Returns ``{"note_id": <new>, "superseded_note_id": <old or None>}``.
        """
        if anchor_kind not in self.NOTE_ANCHOR_KINDS:
            raise ValueError(
                f"anchor_kind {anchor_kind!r} not in "
                f"{sorted(self.NOTE_ANCHOR_KINDS)}"
            )
        if kind not in self.NOTE_KINDS:
            raise ValueError(
                f"note kind {kind!r} not in canonical vocabulary "
                f"{sorted(self.NOTE_KINDS)}"
            )
        if not text:
            raise ValueError("note text must be non-empty")
        anchor_table = "nodes" if anchor_kind == "node" else "roots"
        with self.connect() as conn:
            exists = conn.execute(
                f"SELECT 1 FROM {anchor_table} WHERE session_id = ? AND id = ?",
                (session_id, anchor_id),
            ).fetchone()
            if exists is None:
                raise ValueError(
                    f"anchor {anchor_kind} {anchor_id!r} not found "
                    f"in session {session_id!r}"
                )
            existing = conn.execute(
                "SELECT id FROM notes "
                "WHERE session_id = ? AND anchor_kind = ? AND anchor_id = ? "
                "AND kind = ? AND state = 'active'",
                (session_id, anchor_kind, anchor_id, kind),
            ).fetchone()
            superseded_id = existing["id"] if existing is not None else None
            if superseded_id is not None:
                conn.execute(
                    "UPDATE notes SET state = 'archived', updated_at = ? "
                    "WHERE id = ?",
                    (now, superseded_id),
                )
            try:
                conn.execute(
                    "INSERT INTO notes "
                    "(id, session_id, anchor_kind, anchor_id, kind, text, "
                    "state, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                    (note_id, session_id, anchor_kind, anchor_id, kind, text,
                     now, now),
                )
            except sqlite3.IntegrityError as exc:
                # Uniqueness collision (e.g. a concurrent writer inserted an
                # active note on this axis between our archive and insert) or a
                # CHECK violation on an ALTER-upgraded DB. Lock contention may
                # instead surface as sqlite3.OperationalError, which we let
                # propagate rather than mislabel as a value error.
                raise ValueError(
                    f"cannot add note for {anchor_kind} {anchor_id!r} "
                    f"(kind={kind!r}): {exc}"
                ) from exc
            self._touch_session(conn, session_id=session_id, now=now)
            return {"note_id": note_id, "superseded_note_id": superseded_id}

    def list_notes(
        self,
        *,
        session_id: str,
        anchor_kind: str | None = None,
        anchor_id: str | None = None,
        kind: str | None = None,
        include_archived: bool = False,
    ) -> list[sqlite3.Row]:
        """List notes in a session, oldest first.

        Filters are additive and all optional: ``anchor_kind`` + ``anchor_id``
        narrow to a single anchor, ``kind`` to one axis. ``include_archived``
        defaults to False so callers see only the live (active) notes; pass
        True to walk the supersession history. The ``anchor_kind``/``anchor_id``
        pairing contract (both or neither) is enforced at the tool layer.
        """
        sql = "SELECT * FROM notes WHERE session_id = ?"
        params: list[Any] = [session_id]
        if anchor_kind is not None:
            sql += " AND anchor_kind = ?"
            params.append(anchor_kind)
        if anchor_id is not None:
            sql += " AND anchor_id = ?"
            params.append(anchor_id)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if not include_archived:
            sql += " AND state = 'active'"
        sql += " ORDER BY created_at, id"
        with self.connect() as conn:
            return list(conn.execute(sql, params))

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
        self._check_node_type(node_type)
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
        self._check_node_type(node_type)
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
        node_type: str | None = None,
        after_rowid: int | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        """Return open nodes (creation-order via rowid), with the implicit
        ``rowid`` exposed for keyset pagination.

        ``root_id`` restricts to that root's subtree; ``state`` / ``node_type``
        filter the columns; ``after_rowid`` + ``limit`` page by rowid.

        Root path uses ``walk_subtree`` + Python filtering/seek so there is no
        ``IN (?, ?, ...)`` clause and no SQLITE_MAX_VARIABLE_NUMBER ceiling.
        Root-less path stays as a single SQL query (no subtree to walk).
        """
        if root_id is None:
            params: list[Any] = [session_id]
            sql = (
                "SELECT rowid, * FROM nodes "
                "WHERE session_id = ? AND status = 'open'"
            )
            if state is not None:
                sql += " AND state = ?"
                params.append(state)
            if node_type is not None:
                sql += " AND type = ?"
                params.append(node_type)
            if after_rowid is not None:
                sql += " AND rowid > ?"
                params.append(after_rowid)
            # Keyset pagination relies on rowid being stable & monotonic; never
            # VACUUM this DB or cursors break (nodes.id is a TEXT PK, so rowid is a
            # separate implicit counter that VACUUM would renumber).
            sql += " ORDER BY rowid"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            with self.connect() as conn:
                return list(conn.execute(sql, params))
        else:
            from .pagination import seek_and_limit
            rows = [
                n for n in self.walk_subtree(
                    session_id=session_id, root_id=root_id, include_rowid=True
                )
                if n["status"] == "open"
            ]
            if state is not None:
                rows = [n for n in rows if n["state"] == state]
            if node_type is not None:
                rows = [n for n in rows if n["type"] == node_type]
            rows.sort(key=lambda r: r["rowid"])
            return seek_and_limit(rows, after_rowid=after_rowid, limit=limit)

    # The node-type vocabulary. Public contract for the `type` values a node may
    # carry — consumers validate against this, never against a DB CHECK. Mirrors
    # EDGE_TYPES: enforcement is app-code (see _check_node_type and the insert
    # paths), so adding a type is a frozenset edit, not a schema migration. Schema
    # v9 (#63) dropped the nodes.type CHECK in favor of this.
    NODE_TYPES = frozenset({
        "question", "plan", "hypothesis", "goal", "problem",
        "answer", "action", "verification", "decision", "resolution",
        "evidence", "constraint", "assumption", "rationale", "risk",
        "start", "end",
        # #63 spec-import primitives:
        #   claim         — a propositional/factual assertion that IS spec content
        #   requirement   — a normative MUST/SHOULD obligation
        #   open_question — a recorded unresolved question (spec record, not a
        #                   live decomposition prompt)
        "claim", "requirement", "open_question",
    })

    @classmethod
    def _check_node_type(cls, node_type: str) -> None:
        """Validate a caller-supplied node type against ``NODE_TYPES``.

        Raises ``ValueError`` (same shape as ``add_edge``'s vocabulary check).
        After schema v9 dropped the DB CHECK, this is the sole node-type
        enforcement, so every caller-supplied-type insert path must call it.
        Fixed-literal insert paths (``resolve_branch`` /
        ``resolve_hypothesis_branch``) do not call it — their literals are
        instead pinned to ``NODE_TYPES`` by a drift-guard test.
        """
        if node_type not in cls.NODE_TYPES:
            raise ValueError(
                f"node_type {node_type!r} not in canonical vocabulary "
                f"{sorted(cls.NODE_TYPES)}"
            )

    EDGE_TYPES = frozenset({
        "derived_from", "requires", "blocks", "supports", "contradicts",
        "contributes_to", "supersedes", "qualifies", "invalidates",
        # #57: the overloaded ``supports`` is refined into precise relations.
        # Directional contracts (from -> to):
        #   instantiates: concrete artifact -> abstract claim (realization axis)
        #   illustrates:  example/scenario  -> claim          (realization axis)
        #   justifies:    rationale         -> claim          (grounding axis)
        # ``supports`` is retained as the generic / not-yet-refined edge.
        "instantiates", "illustrates", "justifies",
    })

    # #57: a code-defined (read-only) registry tagging an edge type with the
    # semantic axis it lives on. Only the new refinements are classified for
    # now; the full taxonomy of the pre-existing types is deferred to the
    # Traverser / named-policy work, so they answer ``unclassified``. The axis
    # is a pure function of the type (never serialized) and is the public
    # contract for the axis concept; consume it via ``edge_axis()`` rather than
    # reading this dict directly.
    EDGE_TYPE_AXES = {
        "instantiates": "realization",
        "illustrates": "realization",
        "justifies": "grounding",
    }

    @classmethod
    def edge_axis(cls, edge_type: str) -> str:
        """Return the semantic axis of ``edge_type``.

        One of ``"realization"`` / ``"grounding"`` for classified types, else
        ``"unclassified"``. Answers for every input (including unknown
        strings) so callers never special-case missing keys.
        """
        return cls.EDGE_TYPE_AXES.get(edge_type, "unclassified")

    # #42 proof-tree discipline. layer = epistemic status of an edge,
    # orthogonal to type. verification verdicts come from /dpd-verify-edge.
    EDGE_LAYERS = frozenset({"necessary", "selective", "invalid"})
    VERIFICATION_PRIORITIES = frozenset({"critical", "standard", "low"})
    VERIFICATION_VERDICTS = frozenset({"holds", "holds-with-caveat", "refuted"})

    def add_edge(
        self,
        *,
        session_id: str,
        from_node: str,
        to_node: str,
        edge_type: str,
        reason: str | None,
        now: str,
        layer: str | None = None,
        verification_priority: str | None = None,
    ) -> int:
        """Insert an edge row, validating type, endpoints, and shape.

        Both ``from_node`` and ``to_node`` must reference an existing row in
        the same session — either a node (nodes.id) OR a root (roots.id).
        The edges schema has no FK constraint (parent-kind polymorphism makes
        a literal FK impossible), so validation is enforced in app code.

        ``edge_type`` must be in ``EDGE_TYPES``. Self-loops (from_node ==
        to_node) are rejected — every edge type in the vocabulary is
        directional between distinct entities.

        ``layer`` (#42) optionally classifies the edge into the proof-tree
        discipline taxonomy (``EDGE_LAYERS``); tagging an edge is the
        edge-local opt-in. ``verification_priority`` optionally orders the
        list_unverified_edges queue (``VERIFICATION_PRIORITIES``). Both are
        validated here because ALTER-upgraded DBs lack the column CHECK.
        """
        if edge_type not in self.EDGE_TYPES:
            raise ValueError(
                f"edge_type {edge_type!r} not in canonical vocabulary "
                f"{sorted(self.EDGE_TYPES)}"
            )
        if from_node == to_node:
            raise ValueError(
                f"self-loop rejected: from_node and to_node are both "
                f"{from_node!r}"
            )
        if layer is not None and layer not in self.EDGE_LAYERS:
            raise ValueError(
                f"layer {layer!r} not in {sorted(self.EDGE_LAYERS)}"
            )
        if (verification_priority is not None
                and verification_priority not in self.VERIFICATION_PRIORITIES):
            raise ValueError(
                f"verification_priority {verification_priority!r} not in "
                f"{sorted(self.VERIFICATION_PRIORITIES)}"
            )
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
                "(session_id, from_node, to_node, type, reason, "
                "layer, verification_priority, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, from_node, to_node, edge_type, reason,
                 layer, verification_priority, now),
            )
            self._touch_session(conn, session_id=session_id, now=now)
            return cursor.lastrowid

    def set_edge_layer(
        self,
        *,
        session_id: str,
        edge_id: int,
        layer: str | None,
        now: str,
    ) -> None:
        """Set or clear an edge's proof-tree ``layer`` (#42).

        ``layer=None`` retracts the edge from the discipline (out of scope).
        Raises ValueError on an unknown edge or an out-of-taxonomy value.
        """
        if layer is not None and layer not in self.EDGE_LAYERS:
            raise ValueError(
                f"layer {layer!r} not in {sorted(self.EDGE_LAYERS)}"
            )
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE edges SET layer = ? WHERE session_id = ? AND id = ?",
                (layer, session_id, edge_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"edge_id {edge_id!r} not found in session {session_id!r}"
                )
            self._touch_session(conn, session_id=session_id, now=now)

    def set_edge_verification_priority(
        self,
        *,
        session_id: str,
        edge_id: int,
        verification_priority: str | None,
        now: str,
    ) -> None:
        """Set or clear an edge's ``verification_priority`` (#42).

        ``verification_priority=None`` drops the edge's queue pressure.
        Raises ValueError on an unknown edge or an out-of-taxonomy value.
        """
        if (verification_priority is not None
                and verification_priority not in self.VERIFICATION_PRIORITIES):
            raise ValueError(
                f"verification_priority {verification_priority!r} not in "
                f"{sorted(self.VERIFICATION_PRIORITIES)}"
            )
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE edges SET verification_priority = ? "
                "WHERE session_id = ? AND id = ?",
                (verification_priority, session_id, edge_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"edge_id {edge_id!r} not found in session {session_id!r}"
                )
            self._touch_session(conn, session_id=session_id, now=now)

    def record_edge_verification(
        self,
        *,
        session_id: str,
        edge_id: int,
        verified_by: str | None,
        method: str | None,
        verdict: str,
        notes: str | None,
        prompt_hash: str | None,
        now: str,
    ) -> int:
        """Append an external-verification record for an edge (#42).

        ``verdict`` must be in ``VERIFICATION_VERDICTS``. The edge must exist
        in the session. Append-only (1:many) — re-verification adds rows
        rather than overwriting, preserving history.
        """
        if verdict not in self.VERIFICATION_VERDICTS:
            raise ValueError(
                f"verdict {verdict!r} not in {sorted(self.VERIFICATION_VERDICTS)}"
            )
        with self.connect() as conn:
            edge = conn.execute(
                "SELECT 1 FROM edges WHERE session_id = ? AND id = ?",
                (session_id, edge_id),
            ).fetchone()
            if edge is None:
                raise ValueError(
                    f"edge_id {edge_id!r} not found in session {session_id!r}"
                )
            cursor = conn.execute(
                "INSERT INTO edge_verifications "
                "(edge_id, verified_by, verified_at, method, verdict, notes, "
                "prompt_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (edge_id, verified_by, now, method, verdict, notes, prompt_hash),
            )
            self._touch_session(conn, session_id=session_id, now=now)
            return cursor.lastrowid

    def list_edge_verifications(
        self,
        *,
        session_id: str,
        edge_id: int,
    ) -> list[sqlite3.Row]:
        """Return all verification records for an edge, oldest first."""
        with self.connect() as conn:
            return list(conn.execute(
                "SELECT v.* FROM edge_verifications v "
                "JOIN edges e ON e.id = v.edge_id "
                "WHERE e.session_id = ? AND v.edge_id = ? "
                "ORDER BY v.id",
                (session_id, edge_id),
            ))

    def list_unverified_edges(
        self,
        *,
        session_id: str,
        verification_priority: str | None = None,
    ) -> list[sqlite3.Row]:
        """Return necessary edges that have no verification record (#42).

        The verification obligation is edge-local and keyed off
        ``layer = 'necessary'`` (NOT merely a non-NULL layer): selective and
        invalid edges carry no obligation. Ordered by verification_priority
        (critical → standard → low → unset), then edge id. Optionally
        restricted to a single priority bucket.

        Note: the queue means "not yet verified", so ANY verdict — including
        ``refuted`` — drops the edge from this list (it now has a row in
        edge_verifications). A refuted necessary edge therefore leaves this
        queue while still tagged ``necessary``; acting on the refutation
        (downgrade via set_edge_layer) is a separate, explicit step, not
        driven by this query.
        """
        sql = (
            "SELECT * FROM edges e "
            "WHERE e.session_id = ? AND e.layer = 'necessary' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM edge_verifications v WHERE v.edge_id = e.id"
            ") "
        )
        params: list[Any] = [session_id]
        if verification_priority is not None:
            sql += "AND e.verification_priority = ? "
            params.append(verification_priority)
        sql += (
            "ORDER BY CASE e.verification_priority "
            "  WHEN 'critical' THEN 0 WHEN 'standard' THEN 1 "
            "  WHEN 'low' THEN 2 ELSE 3 END, e.id"
        )
        with self.connect() as conn:
            return list(conn.execute(sql, params))

    def delete_edge(
        self,
        *,
        session_id: str,
        edge_id: int,
        now: str,
    ) -> None:
        """Delete a single edge by id within the session.

        Raises ValueError if no edge with that id exists in the session.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM edges WHERE session_id = ? AND id = ?",
                (session_id, edge_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"edge_id {edge_id!r} not found in session {session_id!r}"
                )
            self._touch_session(conn, session_id=session_id, now=now)

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

            justifies_edge_id: int | None = None
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
                # #57: the rationale grounds the decision. Emit a 'justifies'
                # edge (rationale -> decision/claim) so grounding queries find
                # rationales created via the first-class resolution path, not
                # only those added with an explicit add_edge.
                j_cursor = conn.execute(
                    "INSERT INTO edges "
                    "(session_id, from_node, to_node, type, reason, created_at) "
                    "VALUES (?, ?, ?, 'justifies', NULL, ?)",
                    (session_id, rationale_id, decision_id, now),
                )
                justifies_edge_id = j_cursor.lastrowid

            self._touch_session(conn, session_id=session_id, now=now)

            return {
                "hyp_id": hyp_id,
                "decision_id": decision_id,
                "rationale_id": rationale_id,
                "closed_siblings": closed_siblings,
                "derived_from_edge_id": derived_from_edge_id,
                "justifies_edge_id": justifies_edge_id,
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
                # #57: rationale grounds the decision -> 'justifies' edge
                # (rationale -> decision/claim). Appended to edges_created
                # alongside any derived_from edges.
                j_cur = conn.execute(
                    "INSERT INTO edges "
                    "(session_id, from_node, to_node, type, reason, created_at) "
                    "VALUES (?, ?, ?, 'justifies', NULL, ?)",
                    (session_id, rationale_id, decision_id, now),
                )
                j_row = conn.execute(
                    "SELECT * FROM edges WHERE id = ?", (j_cur.lastrowid,)
                ).fetchone()
                edges_created.append({k: j_row[k] for k in j_row.keys()})

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
        state: str | None = None,
        node_type: str | None = None,
        after_rowid: int | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        """Open nodes not blocked by a still-live endpoint via ``blocker_edge_type``.

        Pagination (``after_rowid`` + ``limit``) applies to the FINAL unblocked
        set so a page is never short-changed by candidate-side cutting.
        """
        # Function-local by design (not a circular-import workaround): keeps the
        # read-side payload layer out of storage's module-level import surface.
        from .pagination import seek_and_limit

        candidates = self.list_open_nodes(
            session_id=session_id, root_id=root_id,
            state=state, node_type=node_type,
        )
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
        unblocked = [n for n in candidates if n["id"] not in blocked_ids]
        return seek_and_limit(unblocked, after_rowid=after_rowid, limit=limit)

    def walk_subtree(
        self, *, session_id: str, root_id: str, include_rowid: bool = False
    ) -> list[sqlite3.Row]:
        """Return all descendants of a root, depth-first pre-order.

        Iterative DFS to avoid Python recursion limit on deep chains
        (sys.getrecursionlimit() defaults to ~1000; DPD chains can exceed).
        Children at each level are ordered by (created_at, id).

        ``include_rowid=True`` selects ``rowid, *`` so callers that need
        rowid-based keyset pagination (``list_open_nodes`` root path) can
        receive the implicit rowid without a second query. Default ``False``
        preserves the original ``SELECT *`` behavior for all other callers
        (they never see a ``rowid`` key).
        """
        # Each frontier entry is either:
        #   ("expand", parent_id, parent_kind)  — fetch children, schedule them
        #   ("emit",   node_row)                — append this row to result
        # On pop, "expand" frames fetch children and push, in REVERSE order, a
        # paired (emit, expand) for each child. This gives pre-order DFS.
        col_clause = "rowid, *" if include_rowid else "*"
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
                    f"""
                    SELECT {col_clause} FROM nodes
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
    # v0.3.2: FTS5 reindex helper
    # -----------------------------------------------------------------------

    _BODY_TYPES = frozenset({
        "decision", "resolution", "answer", "rationale", "evidence",
        # #63: claim/requirement are spec-content assertions (close to
        # evidence/rationale), so their text feeds body_text for FTS ranking.
        # open_question stays out — it is a journey-flavored open prompt.
        "claim", "requirement",
    })

    def _reindex_subgraph(self, *, start_node_id: str) -> None:
        """Rebuild the subgraphs_fts row for one subgraph (DELETE then INSERT).

        Indexed iff start_node.state IN ('closed', 'archived'). 'active' starts
        produce only the DELETE (active subgraphs are not in the FTS index).

        Called by mutation hooks (mark_reached, bulk_import_subgraph,
        delete_subgraph, force_delete_node) and by the v4->v5 backfill.

        Opens its own connection. For callers inside an existing transaction,
        use ``_reindex_subgraph_on`` directly with the existing conn.
        """
        with self.connect() as conn:
            self._reindex_subgraph_on(conn, start_node_id=start_node_id)

    def _reindex_subgraph_on(
        self, conn: sqlite3.Connection, *, start_node_id: str
    ) -> None:
        """Same logic as _reindex_subgraph but operates on a caller-owned conn.

        Performs DELETE + optional INSERT on the given connection. The caller
        owns the transaction lifecycle (no commit or rollback is issued here).

        Used by the v4→v5 migration to run the backfill inside a single
        BEGIN/COMMIT so a backfill failure rolls back the version bump too.
        """
        conn.execute(
            "DELETE FROM subgraphs_fts WHERE start_node_id = ?",
            (start_node_id,),
        )
        start = conn.execute(
            "SELECT id, session_id, type, text, state, archived_at, "
            "closed_at FROM nodes WHERE id = ?",
            (start_node_id,),
        ).fetchone()
        if start is None:
            return
        if start["type"] != "start":
            return
        if start["state"] not in ("closed", "archived"):
            return

        session_id = start["session_id"]
        anchor_parts: list[str] = [start["text"] or ""]
        body_parts: list[str] = []
        journey_parts: list[str] = []

        members = self._subgraph_node_ids(
            conn, session_id=session_id, start_id=start_node_id
        )
        placeholders = ",".join("?" * len(members))
        rows = conn.execute(
            f"SELECT id, type, text, paired_for, achievement_conditions "
            f"FROM nodes WHERE session_id = ? AND id IN ({placeholders}) "
            f"ORDER BY created_at, id",
            (session_id, *members),
        ).fetchall()
        for r in rows:
            if r["id"] == start_node_id:
                continue
            text = r["text"] or ""
            if r["type"] == "end" and r["paired_for"] == start_node_id:
                anchor_parts.append(text)
                if r["achievement_conditions"]:
                    anchor_parts.append(r["achievement_conditions"])
            elif r["type"] in self._BODY_TYPES:
                body_parts.append(text)
            else:
                journey_parts.append(text)

        closed_at = start["closed_at"] or start["archived_at"]
        conn.execute(
            """
            INSERT INTO subgraphs_fts
                (start_node_id, session_id, anchor_text, body_text,
                 journey_text, closed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                start_node_id,
                session_id,
                " ".join(p for p in anchor_parts if p),
                " ".join(p for p in body_parts if p),
                " ".join(p for p in journey_parts if p),
                closed_at,
            ),
        )

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Strip, lowercase, and reject queries shorter than 3 chars.

        Empty return means "skip search" — callers return an empty result list.
        """
        if query is None:
            return ""
        cleaned = query.strip().lower()
        if len(cleaned) < 3:
            return ""
        return cleaned

    def find_similar(
        self,
        *,
        query: str,
        scope: str | None = None,
        top_k: int = 5,
        include_open: bool = False,
    ) -> list[dict]:
        """Retrieve up to top_k subgraphs whose FTS document matches query.

        Default: state IN ('closed', 'archived'). With include_open=True,
        an additional dynamic LIKE scan covers state='active' subgraphs and
        results are concatenated (closed/archived first).

        bm25 returns lower-is-better; we return -bm25 so caller sees
        higher-is-better scores.
        """
        normalized = self._normalize_query(query)
        if not normalized:
            return []

        match_expr = '"' + normalized.replace('"', '""') + '"'
        eligible_rows: list[dict] = []
        with self.connect() as conn:
            fts_rows = conn.execute(
                """
                SELECT
                    f.start_node_id AS start_node_id,
                    f.session_id    AS session_id,
                    f.anchor_text   AS anchor_text,
                    -bm25(subgraphs_fts, 3.0, 2.0, 1.0) AS score,
                    snippet(subgraphs_fts, -1, '[', ']', '…', 16) AS snippet
                FROM subgraphs_fts f
                JOIN sessions s ON f.session_id = s.id
                WHERE subgraphs_fts MATCH ?
                  AND (? IS NULL OR s.scope = ?)
                ORDER BY bm25(subgraphs_fts, 3.0, 2.0, 1.0)
                LIMIT ?
                """,
                (match_expr, scope, scope, top_k),
            ).fetchall()

            for r in fts_rows:
                start_node_id = r["start_node_id"]
                node = conn.execute(
                    "SELECT id, state, archived_at, closed_at, text, paired_for "
                    "FROM nodes WHERE id = ?",
                    (start_node_id,),
                ).fetchone()
                if node is None:
                    continue
                root_row = conn.execute(
                    "SELECT roots.id AS root_id, sessions.scope AS scope "
                    "FROM nodes "
                    "JOIN roots ON nodes.parent_id = roots.id "
                    "JOIN sessions ON nodes.session_id = sessions.id "
                    "WHERE nodes.id = ? AND nodes.parent_kind = 'root'",
                    (start_node_id,),
                ).fetchone()
                if root_row is None:
                    continue
                # Scope filter is applied in SQL (JOIN sessions + AND predicate).
                # No redundant Python-side check needed.
                end_row = conn.execute(
                    "SELECT text, achievement_conditions FROM nodes "
                    "WHERE paired_for = ? AND type = 'end' "
                    "ORDER BY created_at LIMIT 1",
                    (start_node_id,),
                ).fetchone()
                eligible_rows.append({
                    "start_node_id": start_node_id,
                    "session_id": r["session_id"],
                    "root_id": root_row["root_id"],
                    "scope": root_row["scope"] or None,
                    "start_text": node["text"],
                    "end_text": end_row["text"] if end_row else None,
                    "achievement_conditions": (
                        end_row["achievement_conditions"] if end_row else None
                    ),
                    "state": node["state"],
                    "score": float(r["score"]),
                    "matched_snippet": r["snippet"],
                    "closed_at": node["closed_at"] or node["archived_at"],
                })
                if len(eligible_rows) >= top_k:
                    break

        if not include_open:
            return eligible_rows[:top_k]

        # Open fallback: dynamic LIKE scan over active start nodes.
        remaining = top_k - len(eligible_rows)
        if remaining <= 0:
            return eligible_rows
        like_expr = "%" + normalized.replace("\\", "\\\\")\
                                    .replace("%", "\\%")\
                                    .replace("_", "\\_") + "%"
        with self.connect() as conn:
            active_starts = conn.execute(
                """
                SELECT n.id AS start_node_id, n.session_id, n.text AS start_text,
                       roots.id AS root_id, sessions.scope AS scope
                FROM nodes n
                JOIN roots ON n.parent_id = roots.id
                JOIN sessions ON n.session_id = sessions.id
                WHERE n.type = 'start'
                  AND n.state = 'active'
                  AND n.parent_kind = 'root'
                """
            ).fetchall()
            open_results: list[dict] = []
            for r in active_starts:
                if scope is not None and (r["scope"] or None) != scope:
                    continue
                end_row = conn.execute(
                    "SELECT text, achievement_conditions FROM nodes "
                    "WHERE paired_for = ? AND type = 'end' "
                    "ORDER BY created_at LIMIT 1",
                    (r["start_node_id"],),
                ).fetchone()
                end_text = end_row["text"] if end_row else ""
                ach = end_row["achievement_conditions"] if end_row else ""

                # Mirror _reindex_subgraph: include all descendant text in the
                # searchable blob so a match in a decision/rationale child is
                # discoverable in include_open mode.
                descendant_ids = self._subgraph_node_ids(
                    conn, session_id=r["session_id"], start_id=r["start_node_id"]
                )
                descendant_texts: list[str] = []
                if descendant_ids:
                    placeholders = ",".join("?" * len(descendant_ids))
                    descendant_rows = conn.execute(
                        f"SELECT text FROM nodes "
                        f"WHERE session_id = ? AND id IN ({placeholders})",
                        (r["session_id"], *descendant_ids),
                    ).fetchall()
                    descendant_texts = [
                        (row["text"] or "").lower() for row in descendant_rows
                    ]

                blob = " ".join([
                    (r["start_text"] or "").lower(),
                    (end_text or "").lower(),
                    (ach or "").lower(),
                    *descendant_texts,
                ])
                if normalized in blob:
                    open_results.append({
                        "start_node_id": r["start_node_id"],
                        "session_id": r["session_id"],
                        "root_id": r["root_id"],
                        "scope": r["scope"] or None,
                        "start_text": r["start_text"],
                        "end_text": end_text or None,
                        "achievement_conditions": ach or None,
                        "state": "active",
                        "score": float(blob.count(normalized)),
                        "matched_snippet": None,
                        "closed_at": None,
                    })
            open_results.sort(key=lambda x: x["score"], reverse=True)
            return eligible_rows + open_results[:remaining]

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
        text_hash: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pool_items
                    (id, scope_root_id, origin_session_id, text,
                     origin_turn, created_at, elevated_to, elevated_at,
                     dropped_at, tags, text_hash)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (pool_id, scope_root_id, origin_session_id, text,
                 origin_turn, now, tags, text_hash),
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
        severity: str | None = None,
    ) -> None:
        """v3 node insert: supports paired_for + achievement_conditions + provenance + state + severity.

        End nodes must specify paired_for (= the Start node they terminate).

        Classify-and-insert run in a single transaction so a concurrent delete
        between the lookup and the insert cannot create an orphan node row.
        parent_kind is derived internally by querying roots then nodes within
        the same transaction — callers must not pass it.

        ``severity`` is optional proposer-assigned classification (e.g.
        'logical' / 'surface' / 'cosmetic' on question nodes) used by §4.5
        to group natural-pause proposals. Free-form string; no DB-level
        CHECK so the vocabulary can extend without migration.

        Raises ValueError if parent_id is not found in either roots or nodes.
        provenance and state are validated by DB CHECK constraints; invalid
        values cause sqlite3.IntegrityError which propagates to the caller.
        """
        self._check_node_type(node_type)
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
                     severity,
                     archived_at, closed_at, deletable_at,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 'open', NULL, ?, ?, ?, ?, 0, ?, ?, ?,
                        NULL, NULL, NULL, ?, ?)
                """,
                (node_id, session_id, node_type, text,
                 parent_id, parent_kind,
                 paired_for, achievement_conditions,
                 state, provenance, severity,
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
                    f"paired start {start_id!r} via parent_id chain. "
                    f"Canonical layout: End must be a descendant of Start "
                    f"in the parent_id tree (e.g. root → Start → ... → End). "
                    f"Re-parent End so its parent_id chain reaches Start "
                    f"(use force_delete on End and re-add with parent_id="
                    f"{start_id!r} or under a Start-descendant node)."
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
            self._reindex_subgraph_on(conn, start_node_id=start_id)

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

    def export_sql(self, *, now: str, exported_by: str | None = None) -> str:
        """Serialize the whole database to faithful SQL text. Non-destructive.

        The source DB is read but never written (the dump is built from an
        in-memory copy). The FTS index (``subgraphs_fts`` + its internal shadow
        tables) is excluded — it is derived and rebuilt on restore via
        ``python -m dpd_mcp_server.migrate``. A synthetic ``export_meta``
        manifest row (schema version, scopes, session/root ids, counts,
        timestamp) is embedded so the artifact is self-describing.

        Forward-only portability: the dump carries ``PRAGMA user_version`` so a
        dump from an older schema, restored into a fresh sqlite and routed
        through ``Storage.open``, runs the existing migration chain up to the
        current schema (#60).
        """
        import json as _json

        with self.connect() as conn:
            mem = sqlite3.connect(":memory:")
            try:
                conn.backup(mem)
                # Compute the manifest from the in-memory SNAPSHOT, not the live
                # connection. If another DPD server writes to this WAL database
                # between the metadata queries and conn.backup(), live-side
                # counts/ids could describe a different state than the dumped
                # body. Querying the snapshot makes the self-description exactly
                # match the SQL it ships with (#60 / #74 review).
                user_version = mem.execute(
                    "PRAGMA user_version").fetchone()[0]
                session_ids = [r[0] for r in mem.execute(
                    "SELECT id FROM sessions ORDER BY id")]
                root_ids = [r[0] for r in mem.execute(
                    "SELECT id FROM roots ORDER BY id")]
                scopes = [r[0] for r in mem.execute(
                    "SELECT DISTINCT scope FROM ("
                    " SELECT scope FROM sessions WHERE scope IS NOT NULL"
                    " UNION SELECT scope FROM roots WHERE scope IS NOT NULL"
                    ") ORDER BY scope")]
                node_count = mem.execute(
                    "SELECT COUNT(*) FROM nodes").fetchone()[0]
                edge_count = mem.execute(
                    "SELECT COUNT(*) FROM edges").fetchone()[0]
                # Exclude the derived FTS index (rebuilt on restore) and any
                # prior manifest, then embed a fresh manifest into the copy so
                # iterdump emits a correctly-escaped INSERT for it.
                mem.execute("DROP TABLE IF EXISTS subgraphs_fts")
                mem.execute("DROP TABLE IF EXISTS export_meta")
                mem.execute(
                    "CREATE TABLE export_meta ("
                    " schema_version INTEGER NOT NULL,"
                    " exported_at    TEXT NOT NULL,"
                    " exported_by    TEXT,"
                    " scopes         TEXT,"
                    " session_ids    TEXT,"
                    " root_ids       TEXT,"
                    " node_count     INTEGER,"
                    " edge_count     INTEGER)"
                )
                mem.execute(
                    "INSERT INTO export_meta (schema_version, exported_at, "
                    "exported_by, scopes, session_ids, root_ids, node_count, "
                    "edge_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_version, now, exported_by,
                     _json.dumps(scopes), _json.dumps(session_ids),
                     _json.dumps(root_ids), node_count, edge_count),
                )
                mem.commit()
                body = "\n".join(mem.iterdump())
            finally:
                mem.close()

        header = (
            "-- DPD faithful SQL export (#60 portability)\n"
            f"-- exported_at: {now}\n"
            f"-- schema_version (user_version): {user_version}\n"
            f"-- scopes: {', '.join(scopes) or '(none)'}\n"
            f"-- sessions: {len(session_ids)}  roots: {len(root_ids)}  "
            f"nodes: {node_count}  edges: {edge_count}\n"
            "-- NOTE: FTS index (subgraphs_fts) excluded — derived; "
            "rebuilt on restore.\n"
            "-- Restore (whole-DB replace): sqlite3 NEW.sqlite < THIS.sql "
            "&& <python> -m dpd_mcp_server.migrate NEW.sqlite\n"
            "--   (use the interpreter that has dpd_mcp_server installed; "
            "the import_sql tool emits the exact command)\n"
            f"PRAGMA user_version = {user_version};\n"
        )
        return header + body + "\n"

    def dump_persist_subgraph(
        self, *, session_id: str, start_node_id: str,
        destination: str | None, now: str,
    ) -> None:
        """closed → deletable for the subgraph rooted at start_node_id.

        `destination` is recorded but the actual file write is the caller's concern;
        storage layer only flips state.

        Drops the subgraphs_fts row atomically with the state transition so
        find_similar (which only returns state IN ('closed', 'archived')) does
        not surface deletable subgraphs.
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
            conn.execute(
                "DELETE FROM subgraphs_fts WHERE start_node_id = ?",
                (start_node_id,),
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
            # Cascade notes anchored to any member node (child-first; anchor_id
            # has no FK so the cleanup is app-side, like the edges delete above).
            conn.execute(
                f"DELETE FROM notes WHERE session_id = ? "
                f"AND anchor_kind = 'node' AND anchor_id IN ({placeholders})",
                (session_id, *members),
            )
            conn.execute(
                f"DELETE FROM nodes WHERE session_id = ? AND id IN ({placeholders})",
                (session_id, *members),
            )
            conn.execute(
                "DELETE FROM subgraphs_fts WHERE start_node_id = ?",
                (start_node_id,),
            )
            self._touch_session(conn, session_id=session_id, now=now)

    def purge_session(self, *, session_id: str, now: str) -> None:
        """Physically remove a finished session and its scaffolding rows.

        Preconditions:
          - session exists
          - session.mode is null or 'idle' (active work is never silently dropped)
          - no nodes remain in the session (caller is expected to have run
            ``delete_subgraph`` for each subgraph first)

        Cleanup order respects the FK graph:
          1. drop edges in the session (no nodes left to anchor them)
          2. null ``pool_items.origin_session_id`` for items captured by this
             session (pool items belong to the scope, not the session)
          3. drop roots whose ``session_id`` points here
          4. drop the session row

        Use ``force_purge_session`` to bypass preconditions in an emergency.
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT mode FROM sessions WHERE id = ?", (session_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"session {session_id!r} not found")
            if row["mode"] not in (None, "idle"):
                raise ValueError(
                    f"session {session_id!r} mode is {row['mode']!r}; "
                    f"must be 'idle' (or null) to purge. Use "
                    f"force_purge_session to bypass."
                )
            remaining = conn.execute(
                "SELECT COUNT(*) AS c FROM nodes WHERE session_id = ?",
                (session_id,),
            ).fetchone()["c"]
            if remaining > 0:
                raise ValueError(
                    f"session {session_id!r}: {remaining} nodes remain. "
                    f"Run delete_subgraph for each subgraph first, or use "
                    f"force_purge_session to cascade-delete."
                )
            self._purge_session_impl(conn, session_id=session_id, now=now)

    def force_purge_session(self, *, session_id: str, now: str) -> None:
        """Cascade-delete a session and everything attached to it.

        Emergency / cleanup use. Skips the mode + no-nodes checks
        ``purge_session`` enforces. Removes FTS rows for any subgraph in
        the session, breaks intra-session ``paired_for`` references, then
        proceeds through the same FK-respecting cleanup as ``purge_session``.
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"session {session_id!r} not found")
            start_ids = [
                r["id"] for r in conn.execute(
                    "SELECT id FROM nodes "
                    "WHERE session_id = ? AND type = 'start'",
                    (session_id,),
                ).fetchall()
            ]
            for sid in start_ids:
                conn.execute(
                    "DELETE FROM subgraphs_fts WHERE start_node_id = ?", (sid,),
                )
            conn.execute(
                "UPDATE nodes SET paired_for = NULL WHERE session_id = ?",
                (session_id,),
            )
            conn.execute(
                "UPDATE pool_items SET elevated_to = NULL "
                "WHERE elevated_to IN (SELECT id FROM nodes WHERE session_id = ?)",
                (session_id,),
            )
            conn.execute(
                "DELETE FROM nodes WHERE session_id = ?", (session_id,),
            )
            self._purge_session_impl(conn, session_id=session_id, now=now)

    def _purge_session_impl(
        self, conn: sqlite3.Connection, *, session_id: str, now: str,
    ) -> None:
        """Shared tail of purge_session / force_purge_session.

        Assumes no nodes remain in the session (caller's responsibility).
        """
        conn.execute(
            "DELETE FROM edges WHERE session_id = ?", (session_id,),
        )
        # Cascade every note left in the session before the session row goes:
        # notes.session_id is a real FK, so deleting the session with notes
        # still attached would raise. This single session-scoped delete is the
        # chokepoint for BOTH purge paths — it also catches root-anchored notes
        # that purge_session's "no nodes remain" precondition would otherwise
        # leave behind (node-anchored notes are already gone via the node
        # delete paths). Must run before DELETE FROM sessions.
        conn.execute(
            "DELETE FROM notes WHERE session_id = ?", (session_id,),
        )
        conn.execute(
            "UPDATE pool_items SET origin_session_id = NULL "
            "WHERE origin_session_id = ?",
            (session_id,),
        )
        conn.execute(
            "DELETE FROM roots WHERE session_id = ?", (session_id,),
        )
        conn.execute(
            "DELETE FROM sessions WHERE id = ?", (session_id,),
        )

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

    # ------------------------------------------------------------------
    # v0.3.1 Task 7: bulk_import_subgraph
    # ------------------------------------------------------------------

    def bulk_import_subgraph(
        self,
        *,
        session_id: str,
        root_id: str,
        nodes: list[dict],
        edges: list[dict],
        provenance: str = "imported",
        state: str = "archived",
        now: str,
    ) -> dict:
        """Atomically import a multi-node + edge subgraph under an existing root.

        Algorithm:
        1. Build a dependency graph from the nodes list (parent_id → child).
        2. Topological sort to determine insert order (parents before children).
        3. Detect cycles — raise ValueError if any cycle is found.
        4. Within a single transaction:
           a. Validate root_id exists in this session.
           b. Pre-flight + INSERT nodes in topo order. For each node:
              - parent_id resolves (root_id / inserted earlier in batch /
                pre-existing in DB) AND parent_kind matches the actual table
                where parent_id was found.
              - paired_for resolves (in batch or pre-existing).
           c. INSERT edges (from/to must exist at this point).
        5. Return all inserted rows.

        Raises ValueError for:
          - root_id not found in session
          - Cycle in parent_id chain
          - parent_id ref that doesn't resolve
          - parent_kind mismatch with actual parent table
          - paired_for ref that doesn't resolve
          - edge endpoint that doesn't resolve
          - Duplicate node id in import set
          - Insert-time IntegrityError (sqlite3.IntegrityError → ValueError)
        """
        # Steps 1-3: topological sort with cycle detection (pure Python, no DB).
        # Detect duplicate ids first.
        seen_ids: set[str] = set()
        for node in nodes:
            nid = node["id"]
            if nid in seen_ids:
                raise ValueError(
                    f"bulk_import: duplicate node id {nid!r} in import set"
                )
            seen_ids.add(nid)
            # #63: validate node type pre-flight (sole enforcement after v9
            # dropped the DB CHECK), so an invalid type fails before any insert.
            self._check_node_type(node["type"])

        import_ids: set[str] = {n["id"] for n in nodes}
        node_map: dict[str, dict] = {n["id"]: n for n in nodes}

        # Kahn's algorithm: compute in-degree within import set.
        # Edge: parent_id → child (if parent_id is in import set).
        in_degree: dict[str, int] = {nid: 0 for nid in import_ids}
        children_of: dict[str, list[str]] = {nid: [] for nid in import_ids}

        for node in nodes:
            pid = node.get("parent_id")
            if pid is not None and pid in import_ids:
                in_degree[node["id"]] += 1
                children_of[pid].append(node["id"])

        queue = [nid for nid in import_ids if in_degree[nid] == 0]
        topo_order: list[str] = []
        while queue:
            nid = queue.pop(0)
            topo_order.append(nid)
            for child in children_of[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(topo_order) != len(import_ids):
            raise ValueError(
                "cycle detected in parent_id chain within imported nodes"
            )

        # Step 4: single transaction — root validation + node + edge inserts.
        # Root validation is INSIDE the tx so a concurrent root delete between
        # validation and insert cannot create a phantom root reference.
        import sqlite3 as _sqlite3

        with self.connect() as conn:
            # Step 4a: validate root_id (inside the import tx).
            root_row = conn.execute(
                "SELECT 1 FROM roots WHERE session_id = ? AND id = ?",
                (session_id, root_id),
            ).fetchone()
            if root_row is None:
                raise ValueError(
                    f"root_id {root_id!r} not found in session {session_id!r}"
                )

            # Track ids that are now available as parents (inserted or pre-existing).
            inserted_node_ids: set[str] = set()

            for nid in topo_order:
                node = node_map[nid]
                pid = node.get("parent_id")
                pkind = node.get("parent_kind")
                paired_for = node.get("paired_for") or None
                achievement_conditions = node.get("achievement_conditions") or None
                node_type = node["type"]
                text = node["text"]

                # Validate parent_id resolves AND parent_kind matches actual
                # parent table. Mismatched parent_kind would silently create
                # invisible nodes — walk_subtree filters children by both
                # parent_id AND parent_kind, so a wrong kind detaches the node
                # from normal tree traversal.
                if pid is None:
                    raise ValueError(
                        f"node {nid!r}: parent_id is required"
                    )
                if pid in inserted_node_ids:
                    expected_kind: str = "node"
                elif pid == root_id:
                    expected_kind = "root"
                else:
                    # Pre-existing in DB: check both tables to determine kind.
                    node_exists = conn.execute(
                        "SELECT 1 FROM nodes WHERE session_id = ? AND id = ?",
                        (session_id, pid),
                    ).fetchone()
                    if node_exists is not None:
                        expected_kind = "node"
                    else:
                        root_exists = conn.execute(
                            "SELECT 1 FROM roots WHERE session_id = ? AND id = ?",
                            (session_id, pid),
                        ).fetchone()
                        if root_exists is not None:
                            expected_kind = "root"
                        else:
                            raise ValueError(
                                f"node {nid!r}: parent_id {pid!r} not found "
                                f"in import set or DB"
                            )

                if pkind != expected_kind:
                    raise ValueError(
                        f"node {nid!r}: declared parent_kind={pkind!r} but "
                        f"parent {pid!r} is actually a {expected_kind!r}"
                    )

                # Validate paired_for resolves.
                if paired_for is not None:
                    if paired_for not in import_ids and paired_for not in inserted_node_ids:
                        # Must be pre-existing in DB
                        pf_existing = conn.execute(
                            "SELECT 1 FROM nodes WHERE session_id = ? AND id = ?",
                            (session_id, paired_for),
                        ).fetchone()
                        if pf_existing is None:
                            raise ValueError(
                                f"node {nid!r}: paired_for {paired_for!r} not found "
                                f"in import set or DB"
                            )

                try:
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
                        (nid, session_id, node_type, text,
                         pid, pkind,
                         paired_for, achievement_conditions,
                         state, provenance,
                         now, now),
                    )
                except _sqlite3.IntegrityError as exc:
                    raise ValueError(
                        f"bulk_import: failed to insert node {nid!r}: {exc}"
                    ) from exc

                inserted_node_ids.add(nid)

            # Insert edges
            inserted_edges: list[dict] = []
            for edge_spec in edges:
                from_node = edge_spec["from"]
                to_node = edge_spec["to"]
                edge_type = edge_spec["type"]
                reason = edge_spec.get("reason") or None
                # #57/#42: accept proof-tree discipline so load-bearing edges
                # round-trip (export emits these; without import support they
                # would decay to plain edges).
                layer = edge_spec.get("layer") or None
                verification_priority = (
                    edge_spec.get("verification_priority") or None
                )

                if edge_type not in self.EDGE_TYPES:
                    raise ValueError(
                        f"bulk_import: edge_type {edge_type!r} not in "
                        f"canonical vocabulary {sorted(self.EDGE_TYPES)}"
                    )
                if layer is not None and layer not in self.EDGE_LAYERS:
                    raise ValueError(
                        f"bulk_import: layer {layer!r} not in "
                        f"{sorted(self.EDGE_LAYERS)}"
                    )
                if (verification_priority is not None
                        and verification_priority
                        not in self.VERIFICATION_PRIORITIES):
                    raise ValueError(
                        f"bulk_import: verification_priority "
                        f"{verification_priority!r} not in "
                        f"{sorted(self.VERIFICATION_PRIORITIES)}"
                    )
                if from_node == to_node:
                    raise ValueError(
                        f"bulk_import: self-loop rejected: "
                        f"from and to are both {from_node!r}"
                    )

                # Validate both endpoints
                for label, endpoint in (("from", from_node), ("to", to_node)):
                    if endpoint in inserted_node_ids:
                        continue
                    exists = conn.execute(
                        "SELECT 1 FROM nodes WHERE session_id = ? AND id = ? "
                        "UNION ALL "
                        "SELECT 1 FROM roots WHERE session_id = ? AND id = ?",
                        (session_id, endpoint, session_id, endpoint),
                    ).fetchone()
                    if exists is None:
                        raise ValueError(
                            f"edge {label} endpoint {endpoint!r} not found "
                            f"in import set or DB"
                        )

                try:
                    cur = conn.execute(
                        "INSERT INTO edges "
                        "(session_id, from_node, to_node, type, reason, "
                        " layer, verification_priority, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (session_id, from_node, to_node, edge_type, reason,
                         layer, verification_priority, now),
                    )
                except _sqlite3.IntegrityError as exc:
                    raise ValueError(
                        f"bulk_import: failed to insert edge {from_node!r}→{to_node!r}: {exc}"
                    ) from exc

                edge_row = conn.execute(
                    "SELECT * FROM edges WHERE id = ?", (cur.lastrowid,)
                ).fetchone()
                inserted_edges.append({k: edge_row[k] for k in edge_row.keys()})

            self._touch_session(conn, session_id=session_id, now=now)

            # Fetch inserted node rows
            imported_nodes: list[dict] = []
            for nid in topo_order:
                row = conn.execute(
                    "SELECT * FROM nodes WHERE session_id = ? AND id = ?",
                    (session_id, nid),
                ).fetchone()
                imported_nodes.append({k: row[k] for k in row.keys()})

            # Reindex any imported start nodes whose state is closed or archived.
            # Inside the same transaction so a reindex failure rolls the import back.
            for n in nodes:
                if n["type"] == "start":
                    self._reindex_subgraph_on(conn, start_node_id=n["id"])

        return {
            "imported_nodes": imported_nodes,
            "imported_edges": inserted_edges,
        }

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

        Steps (all within one transaction):
        1. NULLs paired_for on any End node paired to this node (if target is a Start).
        2. Tombstones pool_items elevated into this node (NULL + dropped_at + tag)
           to prevent silent reactivation of the pool item.
        3. Deletes referencing edges, then the node itself.
        4. If the target IS a start node, drops its FTS row atomically with the
           node delete (subgraph identity gone).

        After commit (separate connection):
        5. If the target was a non-start node whose containing subgraph is
           closed/archived, rebuilds that subgraph's FTS row so the deleted
           node's text no longer matches find_similar (= staleness prevention).
        """
        ancestor_start_id: str | None = None
        with self.connect() as conn:
            node_info = conn.execute(
                "SELECT type, parent_id, parent_kind FROM nodes "
                "WHERE session_id = ? AND id = ?",
                (session_id, node_id),
            ).fetchone()
            if node_info is None:
                # Nothing to delete; preserve original behavior (silent no-op).
                return

            is_start = node_info["type"] == "start"
            if not is_start:
                # Walk up parent_id chain (kind='node') to find the start node.
                current_id = node_info["parent_id"]
                current_kind = node_info["parent_kind"]
                visited: set[str] = set()
                while current_kind == "node" and current_id not in visited:
                    visited.add(current_id)
                    parent = conn.execute(
                        "SELECT id, type, parent_id, parent_kind FROM nodes "
                        "WHERE session_id = ? AND id = ?",
                        (session_id, current_id),
                    ).fetchone()
                    if parent is None:
                        break
                    if parent["type"] == "start":
                        ancestor_start_id = parent["id"]
                        break
                    current_id = parent["parent_id"]
                    current_kind = parent["parent_kind"]

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
            if is_start:
                # Drop the subgraph's FTS row atomically with the node delete.
                conn.execute(
                    "DELETE FROM subgraphs_fts WHERE start_node_id = ?",
                    (node_id,),
                )
            # Cascade notes anchored to this node (child-first: no note may
            # outlive its anchor). anchor_id has no FK, so this is app-side.
            conn.execute(
                "DELETE FROM notes WHERE session_id = ? "
                "AND anchor_kind = 'node' AND anchor_id = ?",
                (session_id, node_id),
            )
            conn.execute(
                "DELETE FROM nodes WHERE session_id = ? AND id = ?",
                (session_id, node_id),
            )
            self._touch_session(conn, session_id=session_id, now=now)

            # If the deleted node was a non-start child of a closed/archived
            # subgraph, rebuild that subgraph's FTS row inside the same
            # transaction so the FTS row never reflects a node row that no
            # longer exists.
            if ancestor_start_id is not None:
                self._reindex_subgraph_on(conn, start_node_id=ancestor_start_id)
