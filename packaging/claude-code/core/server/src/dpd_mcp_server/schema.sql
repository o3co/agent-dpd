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
    severity        TEXT,                                     -- v6: optional proposer-assigned severity (logical/surface/cosmetic or extended)
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
    -- v7 (#42): proof-tree discipline. layer = epistemic status, orthogonal
    -- to type (the relationship kind). NULL = discipline not applied.
    -- CHECK present only on fresh DBs; ALTER-upgraded DBs enforce the closed
    -- taxonomy in app code (Storage.add_edge / set_edge_layer).
    layer       TEXT
        CHECK (layer IS NULL OR layer IN ('necessary','selective','invalid')),
    verification_priority TEXT
        CHECK (verification_priority IS NULL OR
               verification_priority IN ('critical','standard','low')),
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edges_session ON edges(session_id);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(session_id, from_node);

-- v7 (#42): append-only audit of external verification runs for edges
-- (1:many — supports re-verification history). verdict ∈ {holds,
-- holds-with-caveat, refuted}; prompt_hash records the context-stripped
-- prompt for drift audit.
-- method vs verified_by are deliberately distinct, not redundant:
--   method      = transport/tool ('external:codex', 'paste')  — HOW verified
--   verified_by = verifier identity ('codex', 'claude', a human name) — WHO
-- They coincide in auto-invoke (external:codex / codex) but separate in
-- paste-mode (paste / "Alice"). verified_by is free-form (taxonomy unstable).
CREATE TABLE IF NOT EXISTS edge_verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- ON DELETE CASCADE: deleting an edge drops its verification audit too,
    -- so the existing edge-deletion paths (delete_edge / force_delete /
    -- delete_subgraph / purge_session) keep working under foreign_keys=ON.
    edge_id     INTEGER NOT NULL REFERENCES edges(id) ON DELETE CASCADE,
    verified_by TEXT,
    verified_at TEXT,
    method      TEXT,
    verdict     TEXT,
    notes       TEXT,
    prompt_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_edge_verifications_edge
    ON edge_verifications(edge_id);

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

-- v8 (#55): note layer. Anchored long-form narrative — the residue that
-- cannot be structured into the graph (decisions/relations belong in nodes
-- and edges; notes hold the prose that survives extraction). Anchors are
-- polymorphic over nodes OR roots(=subgraph), so anchor_id has no FK; anchor
-- existence is validated in app code (Storage.add_note), mirroring add_edge.
-- The anchor_kind / kind / state CHECKs live here for fresh DBs; ALTER-upgraded
-- DBs enforce the closed taxonomies in app code (Storage.add_note).
CREATE TABLE IF NOT EXISTS notes (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    anchor_kind  TEXT NOT NULL CHECK (anchor_kind IN ('node','root')),
    anchor_id    TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN (
        'narrative','caveat','external-analysis','rejected-alternative'
    )),
    text         TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','archived')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- At most one active note per (anchor, kind): the canonicality invariant that
-- makes the note layer SoT (a second active note on the same axis is a smell).
-- All indexed columns + state are NOT NULL, so SQLite's "NULLs are distinct"
-- rule cannot punch a hole in this partial unique index.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_notes_active_anchor_kind
    ON notes(anchor_kind, anchor_id, kind) WHERE state = 'active';
CREATE INDEX IF NOT EXISTS idx_notes_anchor
    ON notes(session_id, anchor_kind, anchor_id);

PRAGMA user_version = 8;
