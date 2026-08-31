-- archive.db — the durable record. APPEND-ONLY. SHAPE FROZEN.
--
-- This database answers one question: what actually happened, and who said it.
-- Nothing derived lives here. Nothing here is ever updated or deleted in normal
-- operation, and nothing here is migrated — if a later phase needs a new field,
-- it belongs in working.db, not in this file.
--
-- Two tables, deliberately. Every additional table is another thing that can be
-- wrong about the past. Conversation boundaries, chunking state, retrieval
-- metadata and every other derived fact live in working.db and are rebuildable
-- from this record; this record is not rebuildable from anything.
--
-- Deliberately NOT here, each considered and rejected:
--   * a conversations table — start/end times are derivable from message
--     timestamps grouped by conversation_id, so storing them here would be
--     duplicating a derived fact into the frozen store.
--   * a channel column — this build has one channel (web). iMessage is
--     deferred entirely (PROJECT.md), and user_id already resolves to a person.
--   * anything about chunks, embeddings or retrieval — all derived.

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL      -- ISO-8601 UTC
);

CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    user_id          TEXT NOT NULL,   -- attribution; who this turn belongs to
    role             TEXT NOT NULL,   -- 'user' | 'assistant'
    content          TEXT NOT NULL,
    -- The turn's tool trace as JSON, or NULL for turns that called no tools.
    -- Stored here rather than only in working.db because the trace is part of
    -- what happened: it is the ground truth the fabrication gate checks claims
    -- against, and a claim about a tool call is only checkable for as long as
    -- the record of that call survives.
    tool_trace       TEXT,
    timestamp        TEXT NOT NULL,   -- ISO-8601 UTC
    CHECK (role IN ('user', 'assistant'))
);

CREATE INDEX IF NOT EXISTS idx_archive_messages_conversation
    ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_archive_messages_timestamp
    ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_archive_messages_user
    ON messages(user_id);
