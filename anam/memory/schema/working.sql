-- working.db — the operational store.
--
-- Everything here is either a mirror of archive.db or derived from it, and all
-- of it is rebuildable. That is the point of the split: archive.db is protected
-- by being small and frozen, and this database is free to grow and migrate.
--
-- `chunks` is the canonical record of a retrievable unit. ChromaDB and the FTS5
-- index below are BOTH derived from it and can be rebuilt from it. The reference
-- build had no such table — chunks existed only inside Chroma and FTS — which is
-- what made orphaned chunks possible there (rows in the index with nothing behind
-- them) and required a dedicated purge tool to clean up after. A canonical row
-- makes that class of bug a foreign-key violation instead of a maintenance task.
--
-- Deliberately NOT here, each a decision rather than an omission:
--   * review_items       — the review queue is out of this build (decision #14).
--   * anything self-mod  — no seam anywhere, not even a nullable column
--                          (decision #15).
--   * summaries          — nothing is ever summarised; history windowing drops
--                          turns from the prompt, never from the record
--                          (decision #6).
--   * excluded_chunks    — the fabrication gate refuses to persist a bad turn
--                          rather than storing it and filtering it later, and a
--                          full wipe precedes go-live (decisions #1, #16).
--   * channel_identifiers — one channel in this build; iMessage is deferred.
--                          Web credentials live on `users` instead.
--   * artifacts          — belongs to Phase 2 (file/PDF ingestion) and Phase 4
--                          (generated images). Nothing in Phase 1 depends on it,
--                          so Phase 2 builds it against a real ingestion design.
--   * research_candidates — belongs to Phase 5 (self-flag tool, periodic mining
--                          pass). Phase 5 designs it fresh against real
--                          requirements; nothing here should be treated as a
--                          starting shape for it.
--
-- Both were present in an earlier draft of this file and were removed at the
-- Phase 1 checkpoint. Neither had a consumer. Guessing a table's shape a phase
-- or four ahead of the code that uses it is how a schema acquires columns
-- nobody can later justify.

-- ---------------------------------------------------------------------------
-- Migration bookkeeping
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- People
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    role           TEXT NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
    password_hash  TEXT,
    created_at     TEXT NOT NULL,
    last_seen_at   TEXT,
    CHECK (role IN ('admin', 'user'))
);

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- ---------------------------------------------------------------------------
-- Conversations and messages (operational mirror of the archive)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conversations (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    ended_at       TEXT,
    message_count  INTEGER NOT NULL DEFAULT 0,
    -- Set only by the final re-chunk at conversation close. Checkpointing a
    -- live tail deliberately does not set it: a conversation is "chunked" when
    -- it has been chunked in full, not when part of it is retrievable.
    chunked        INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_conversations_user     ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_started  ON conversations(started_at);
CREATE INDEX IF NOT EXISTS idx_conversations_ended    ON conversations(ended_at);
CREATE INDEX IF NOT EXISTS idx_conversations_chunked  ON conversations(chunked);

CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    -- Carried explicitly rather than joined through the conversation. A
    -- conversation has one user, but attribution has to survive onto chunks,
    -- and a join that can be forgotten is worse than a column that cannot.
    user_id          TEXT NOT NULL,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    tool_trace       TEXT,
    timestamp        TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    CHECK (role IN ('user', 'assistant'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp    ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_user         ON messages(user_id);

-- ---------------------------------------------------------------------------
-- Chunks — the canonical retrievable unit
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chunks (
    id               TEXT PRIMARY KEY,
    -- NULL for chunks that do not come from a conversation (an ingested file,
    -- a piece of creative writing). Conversation chunks always carry it.
    conversation_id  TEXT,
    user_id          TEXT,
    text             TEXT NOT NULL,
    -- Provenance. NOT NULL is the enforcement: task 1.7 requires that no chunk
    -- can be written without it, and a constraint holds where a convention
    -- does not. The permitted values are a vocabulary owned by
    -- anam/memory/provenance.py, deliberately not a CHECK constraint here —
    -- adding a source type should not require a schema migration.
    source_type      TEXT NOT NULL,
    source_trust     TEXT NOT NULL,
    -- Ordinal within its conversation. NULL for non-conversation chunks.
    chunk_index      INTEGER,
    -- Message range this chunk was built from, for tracing a chunk back to the
    -- raw turns behind it.
    first_message_id TEXT,
    last_message_id  TEXT,
    -- Detects a chunk whose text changed underneath a derived index.
    text_sha256      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_conversation ON chunks(conversation_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user         ON chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source_type  ON chunks(source_type);
CREATE INDEX IF NOT EXISTS idx_chunks_created_at   ON chunks(created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_conversation_index
    ON chunks(conversation_id, chunk_index)
    WHERE conversation_id IS NOT NULL AND chunk_index IS NOT NULL;

-- FTS5 lexical index over chunks.text, as an EXTERNAL CONTENT table: the text
-- is not stored twice, and the triggers below make the index impossible to
-- desync from `chunks`. Keeping them in step by convention instead is how index
-- rows outlive the thing they indexed.
--
-- Note: the reference build created its FTS table in a separate execute() with
-- a comment saying executescript could not reliably mix virtual-table DDL with
-- regular DDL. That does not reproduce on SQLite 3.53.1 — verified — so it is
-- inline here.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content = 'chunks',
    content_rowid = 'rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;

-- ---------------------------------------------------------------------------
-- Supersession — corrections layered over the record (decision #2)
-- ---------------------------------------------------------------------------

-- A correction never edits or deletes what it corrects. It links to it, and
-- retrieval resolves the link (task 3.5). Both sides are chunks, so a chain of
-- corrections resolves by following the links forward.
CREATE TABLE IF NOT EXISTS supersedes (
    id                    TEXT PRIMARY KEY,
    superseding_chunk_id  TEXT NOT NULL,
    superseded_chunk_id   TEXT NOT NULL,
    -- Model-judged, never keyword-matched (decision #2). Recording which model
    -- and how confident it was is what makes a bad link auditable later.
    classifier_model      TEXT,
    confidence            REAL,
    rationale             TEXT,
    created_at            TEXT NOT NULL,
    FOREIGN KEY (superseding_chunk_id) REFERENCES chunks(id),
    FOREIGN KEY (superseded_chunk_id) REFERENCES chunks(id),
    UNIQUE (superseding_chunk_id, superseded_chunk_id),
    -- A chunk superseding itself would make link resolution non-terminating.
    CHECK (superseding_chunk_id <> superseded_chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_supersedes_superseded
    ON supersedes(superseded_chunk_id);
CREATE INDEX IF NOT EXISTS idx_supersedes_superseding
    ON supersedes(superseding_chunk_id);

-- Cycle guard.
--
-- The CHECK above stops a chunk superseding itself, but says nothing about a
-- loop through two or more rows: UNIQUE(superseding, superseded) treats (A,B)
-- and (B,A) as different tuples, so "A supersedes B" and "B supersedes A" can
-- both exist as valid rows. Retrieval resolves a correction by following links
-- forward, so a loop of any length means resolution never terminates.
--
-- This cannot arise from correct use — it takes a wrong classifier judgment, a
-- restored older store, or a hand-edited row. That is exactly why it belongs in
-- the schema: the guarantee has to hold for writers that are not the classifier,
-- including direct SQL.
--
-- The check walks forward from the proposed superseding chunk and aborts if the
-- proposed superseded chunk is already reachable — which is precisely the
-- condition "this link closes a loop". UNION rather than UNION ALL keeps the
-- walk itself terminating on data that is already cyclic.
--
-- Note this rejects the *link*, never a chunk. No raw experience is touched.
CREATE TRIGGER IF NOT EXISTS supersedes_no_cycle_insert
BEFORE INSERT ON supersedes
WHEN EXISTS (
    WITH RECURSIVE forward(id) AS (
        SELECT new.superseding_chunk_id
        UNION
        SELECT s.superseding_chunk_id
          FROM supersedes s
          JOIN forward f ON s.superseded_chunk_id = f.id
    )
    SELECT 1 FROM forward WHERE id = new.superseded_chunk_id
)
BEGIN
    SELECT RAISE(
        ABORT,
        'supersedes cycle: this link would make correction resolution non-terminating'
    );
END;

CREATE TRIGGER IF NOT EXISTS supersedes_no_cycle_update
BEFORE UPDATE OF superseding_chunk_id, superseded_chunk_id ON supersedes
WHEN EXISTS (
    WITH RECURSIVE forward(id) AS (
        SELECT new.superseding_chunk_id
        UNION
        SELECT s.superseding_chunk_id
          FROM supersedes s
          JOIN forward f ON s.superseded_chunk_id = f.id
         WHERE s.id <> old.id
    )
    SELECT 1 FROM forward WHERE id = new.superseded_chunk_id
)
BEGIN
    SELECT RAISE(
        ABORT,
        'supersedes cycle: this link would make correction resolution non-terminating'
    );
END;

-- ---------------------------------------------------------------------------
-- Settings (decision #8)
-- ---------------------------------------------------------------------------

-- Authoritative at runtime for any key it holds a row for. TOML and env
-- provide the bootstrap default until a row exists; nothing reads both at
-- request time. See anam/config.py.
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    value_type  TEXT NOT NULL,   -- 'str' | 'int' | 'float' | 'bool' | 'json'
    updated_at  TEXT NOT NULL,
    updated_by  TEXT,
    CHECK (value_type IN ('str', 'int', 'float', 'bool', 'json'))
);
