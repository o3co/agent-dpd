-- PRGP server schema (Phase 1).
-- See spec §3 (Data Model) and §5 (Storage Architecture).

-- NOTE: PRAGMA foreign_keys is a per-connection toggle; this line has no
-- persistent effect after schema apply. FK enforcement is set in
-- Storage.connect() on every new connection.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    scope        TEXT,                       -- sub-scope, free-form, nullable
    label        TEXT,
    started_at   TEXT NOT NULL,              -- ISO-8601 UTC
    updated_at   TEXT NOT NULL,
    focus_node_id TEXT                       -- nullable FK validated in app code
);

CREATE TABLE IF NOT EXISTS roots (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    topic           TEXT NOT NULL,
    lifecycle       TEXT NOT NULL CHECK (lifecycle IN ('active','archived','deferred')),
    spawned_at      TEXT NOT NULL,
    last_focused_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_roots_session ON roots(session_id);
CREATE INDEX IF NOT EXISTS idx_roots_lifecycle ON roots(session_id, lifecycle);

CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    type            TEXT NOT NULL CHECK (type IN (
        -- Problem side
        'question','plan','hypothesis','goal','problem',
        -- Solution side
        'answer','action','verification','decision','resolution',
        -- Support
        'evidence','constraint','assumption','rationale','risk'
    )),
    text            TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('open','closed')),
    closure_reason  TEXT
        CHECK (closure_reason IS NULL OR closure_reason IN ('resolved','rejected','invalidated')),
    parent_id       TEXT NOT NULL,           -- references roots(id) OR nodes(id)
    parent_kind     TEXT NOT NULL CHECK (parent_kind IN ('root','node')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_session ON nodes(session_id);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    from_node   TEXT NOT NULL,
    to_node     TEXT NOT NULL,
    type        TEXT NOT NULL,
    reason      TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edges_session ON edges(session_id);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(session_id, from_node);

-- Schema version sentinel; bump in lock-step with `Storage.open()` migrations.
PRAGMA user_version = 2;
