PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    scope        TEXT,
    label        TEXT,
    started_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    focus_node_id TEXT
);

CREATE TABLE IF NOT EXISTS roots (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT REFERENCES sessions(id),     -- v3: NULLABLE for scope_root rows
    scope                TEXT,                              -- v3: scope name for scope_root rows
    scope_root           INTEGER NOT NULL DEFAULT 0
        CHECK (scope_root IN (0,1)),
    migrated_to_start_id TEXT,                              -- v3: pointer to Start node from v2 migration
    topic                TEXT NOT NULL,
    lifecycle            TEXT NOT NULL
        CHECK (lifecycle IN ('active','archived','deferred')),
    spawned_at           TEXT NOT NULL,
    last_focused_at      TEXT
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
        'start','end'                                           -- v3 additions
    )),
    text            TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('open','closed')),
    closure_reason  TEXT
        CHECK (closure_reason IS NULL OR
               closure_reason IN ('resolved','rejected','invalidated')),
    parent_id       TEXT NOT NULL,
    parent_kind     TEXT NOT NULL CHECK (parent_kind IN ('root','node')),
    paired_for      TEXT REFERENCES nodes(id),              -- v3: End → Start
    achievement_conditions TEXT,                              -- v3: free-form text
    achievement_conditions_satisfied INTEGER NOT NULL DEFAULT 0
        CHECK (achievement_conditions_satisfied IN (0,1)),    -- v3
    state           TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','archived','closed','deletable','gone')),  -- v3
    archived_at     TEXT,                                     -- v3
    closed_at       TEXT,                                     -- v3
    deletable_at    TEXT,                                     -- v3
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
    tags              TEXT
);

CREATE INDEX IF NOT EXISTS idx_pool_scope ON pool_items(scope_root_id);
CREATE INDEX IF NOT EXISTS idx_pool_active ON pool_items(scope_root_id)
    WHERE elevated_to IS NULL AND dropped_at IS NULL;

PRAGMA user_version = 3;
