-- NOTE: PRAGMA foreign_keys is a per-connection toggle; this line has no
-- persistent effect after schema apply. FK enforcement is set in
-- Storage.connect() on every new connection.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    scope        TEXT,
    label        TEXT,
    started_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    focus_node_id TEXT,
    mode         TEXT
);

CREATE TABLE IF NOT EXISTS roots (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT REFERENCES sessions(id),     -- NULLABLE for scope_root rows
    scope                TEXT,                              -- scope name for scope_root rows
    scope_root           INTEGER NOT NULL DEFAULT 0
        CHECK (scope_root IN (0,1)),
    migrated_to_start_id TEXT,                              -- pointer to Start node from v2 migration
    topic                TEXT NOT NULL,
    lifecycle            TEXT NOT NULL
        CHECK (lifecycle IN ('active','archived','deferred')),
    spawned_at           TEXT NOT NULL,
    last_focused_at      TEXT,
    -- scope_root rows must always have a non-NULL scope so the partial UNIQUE
    -- index (uniq_roots_scope_root) is meaningful. SQLite treats NULL as distinct
    -- in UNIQUE indexes, so without this CHECK multiple NULL-scope scope_root
    -- rows would be silently allowed.
    -- NOTE: for databases upgraded from v2 via ALTER TABLE, this CHECK is not
    -- retroactively added; the invariant is enforced at runtime by the migration
    -- script (Task 4) which ensures all scope_root inserts supply a non-NULL scope.
    CHECK (scope_root = 0 OR scope IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_roots_session ON roots(session_id);
CREATE INDEX IF NOT EXISTS idx_roots_lifecycle ON roots(session_id, lifecycle);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_roots_scope_root
    ON roots(scope) WHERE scope_root = 1;

CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    type            TEXT NOT NULL CHECK (type IN (
        'question','plan','hypothesis','goal','problem',
        'answer','action','verification','decision','resolution',
        'evidence','constraint','assumption','rationale','risk',
        'start','end'
    )),
    text            TEXT NOT NULL,
    provenance      TEXT NOT NULL DEFAULT 'grounded'
        CHECK (provenance IN ('grounded', 'inferred', 'imported', 'manual')),
    status          TEXT NOT NULL CHECK (status IN ('open','closed')),
    closure_reason  TEXT
        CHECK (closure_reason IS NULL OR
               closure_reason IN ('resolved','rejected','invalidated')),
    parent_id       TEXT NOT NULL,
    parent_kind     TEXT NOT NULL CHECK (parent_kind IN ('root','node')),
    paired_for      TEXT REFERENCES nodes(id),              -- End → Start pairing
    achievement_conditions TEXT,                              -- free-form text
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

CREATE INDEX IF NOT EXISTS idx_nodes_session ON nodes(session_id);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_state ON nodes(session_id, state);

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
    tags              TEXT,
    rejected_at       TEXT,
    rejected_reason   TEXT,
    text_hash         TEXT
);

CREATE INDEX IF NOT EXISTS idx_pool_scope ON pool_items(scope_root_id);
CREATE INDEX IF NOT EXISTS idx_pool_active ON pool_items(scope_root_id)
    WHERE elevated_to IS NULL AND dropped_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pool_rejected ON pool_items(scope_root_id, created_at)
    WHERE rejected_at IS NULL;

CREATE VIRTUAL TABLE IF NOT EXISTS subgraphs_fts USING fts5(
    start_node_id UNINDEXED,
    session_id    UNINDEXED,
    anchor_text,
    body_text,
    journey_text,
    closed_at     UNINDEXED,
    tokenize = 'trigram'
);

PRAGMA user_version = 5;
