"""Versioned migrations for working.db.

Built with the first schema rather than retrofitted when the first migration is
needed. Retrofitting means the first few schema changes happen by hand, and the
runner arrives without knowing what state any given database is actually in.

**The archive is never migrated.** Its shape is frozen (see
``schema/archive.sql``), so there is deliberately no archive equivalent of this
module. If a change seems to require altering the archive, that is a signal the
field belongs in working.db instead.

Adding a migration: append a ``Migration`` to ``MIGRATIONS`` with the next
version number. Never edit or renumber an applied migration — a database that
already ran version N will not run it again, so changing it silently produces
two different schemas that both claim to be version N.

Migrations run inside a transaction and record themselves in ``schema_version``
as part of that same transaction, so a failure leaves neither the change nor the
version row. **A multi-statement migration must use ``_execute_script``, never
``conn.executescript``**, which commits the open transaction first and so voids
that guarantee — see ``_execute_script``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from program.memory import db


@dataclass(frozen=True)
class Migration:
    """One forward schema change.

    ``apply`` receives an open connection inside a transaction. There are no
    down-migrations: reversing a schema change on a store holding real memory is
    a restore-from-backup operation, not a routine one.
    """

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _statements(script: str) -> list[str]:
    """Split a multi-statement script into complete statements, in order.

    Uses ``sqlite3.complete_statement`` rather than splitting on ``;``, because a
    trigger body carries its own semicolons (``BEGIN … END;``) and a naive split
    would cut it in half.
    """
    statements: list[str] = []
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if pending.strip():
                statements.append(pending.strip())
            pending = ""
    if pending.strip():
        raise ValueError(f"incomplete SQL statement at end of script: {pending.strip()[:80]!r}")
    return statements


def _execute_script(conn: sqlite3.Connection, script: str) -> None:
    """Run a multi-statement script **inside** the caller's transaction.

    Never ``conn.executescript()`` in a migration. ``executescript`` issues an
    implicit ``COMMIT`` of any open transaction before it runs, so inside
    ``run_working_migrations()``'s transaction it silently committed every earlier
    migration *and* the destructive steps before it, then ran the rest outside any
    transaction. A failure part-way left the database half-migrated with no
    version recorded, and ``db.transaction()``'s ``ROLLBACK`` then raised
    *"cannot rollback - no transaction is active"*, replacing the real error
    (merged-queue item 8, plan B3). Measured before the fix: ``in_transaction``
    True before ``executescript``, False after. Migration 6 drops and recreates
    ``supersedes`` before creating its two cycle-guard triggers, so a failure in
    between left the table with no cycle guards and nothing reporting it.
    """
    for statement in _statements(script):
        conn.execute(statement)
    if not conn.in_transaction:  # pragma: no cover - a guard, not a path
        raise RuntimeError("a migration statement ended the transaction it ran in")


# Version 1 is the initial schema in working.sql, recorded as applied when the
# database is first created. New changes start at 2.
INITIAL_VERSION = 1
INITIAL_NAME = "initial_schema"

def _v2_artifacts(conn: sqlite3.Connection) -> None:
    """Version 2 — ingested files. Design of record: ``docs/INGESTION_DESIGN.md``.

    **Not added to ``working.sql``**, deliberately. That file is the version 1
    definition and stays that way; a change made in both places would apply
    twice on a fresh store, and ``ALTER TABLE ADD COLUMN`` is not idempotent.
    ``init_databases()`` runs ``working.sql`` and then this, so a brand-new
    database and a database created before this task both arrive at the same
    schema by the same path — which also means this migration is exercised on
    every fresh store, including every test run, rather than once in production.
    """
    conn.execute(
        """
        CREATE TABLE artifacts (
            id                TEXT PRIMARY KEY,
            -- Uploader. NOT NULL: an artifact with nobody behind it is not a
            -- state this system should be able to represent.
            user_id           TEXT NOT NULL,
            -- As supplied by the client. UNTRUSTED — never used to build a
            -- path (see program/artifacts/ingest.py). Kept because it is what
            -- the person calls the file.
            filename          TEXT NOT NULL,
            -- Detected from content, not taken from the upload header, which
            -- the client controls.
            content_type      TEXT NOT NULL,
            size_bytes        INTEGER NOT NULL,
            -- Of the stored bytes. Same role as chunks.text_sha256: detects a
            -- file changing underneath the rows derived from it.
            sha256            TEXT NOT NULL,
            -- RELATIVE to config.artifact_dir(). An absolute path breaks the
            -- moment the store moves, and a backup is restored elsewhere.
            storage_path      TEXT NOT NULL,
            -- Decision #10 already uses this word for creative writing; Phase 4
            -- adds generated images. Deliberately not CHECK-constrained, for
            -- chunks.source_type's reason: a new kind should not need a
            -- migration.
            artifact_type     TEXT NOT NULL,
            -- Whether the content was actually read. CHECK-constrained because
            -- the vocabulary is closed and small, as users.role is. This is what
            -- makes "was this file read?" answerable structurally instead of
            -- inferred from whether extracted_text happens to be empty — the
            -- distinction task 3.1 needs, and the same one ToolResult draws
            -- between TIMEOUT and TOOL_ERROR.
            extraction_status TEXT NOT NULL
                CHECK (extraction_status IN ('extracted', 'metadata_only', 'failed')),
            -- NULL unless extraction_status = 'extracted'.
            extracted_text    TEXT,
            -- Why, when a file was not read. A file that was not read must be
            -- able to say why.
            extraction_note   TEXT,
            created_at        TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute("CREATE INDEX idx_artifacts_user ON artifacts(user_id)")
    conn.execute("CREATE INDEX idx_artifacts_sha256 ON artifacts(sha256)")
    conn.execute("CREATE INDEX idx_artifacts_type ON artifacts(artifact_type)")

    # The link a document chunk needs, symmetric with conversation_id. Without
    # it a retrieved file chunk cannot say which file it came from, and
    # first_message_id/last_message_id are meaningless for a document — so
    # provenance would be returned (D6) with nothing in it.
    conn.execute("ALTER TABLE chunks ADD COLUMN artifact_id TEXT REFERENCES artifacts(id)")
    conn.execute("CREATE INDEX idx_chunks_artifact ON chunks(artifact_id)")


def _v3_integrity_check(conn: sqlite3.Connection) -> None:
    """Version 3 — where the fabrication gate's verdict lives.

    Design of record: ``docs/FABRICATION_GATE_DESIGN.md`` O1. Nullable JSON on
    the assistant message the verdict is about.

    **Not folded into ``tool_trace``.** That column is the tool trace; a turn
    that called no tools would otherwise carry a "tool trace" describing an
    integrity check, and the fabrication gate's own evidence would share a column
    with the thing it reasons over.

    **Not log-only.** A verdict that exists only in the log is unqueryable, and
    the eval harness (a separate Tier 2 task) could never replay what production
    actually decided.

    NULL means *no verdict recorded* — a message written before this migration,
    or by a path that does not run the gate. It does not mean "clean"; the gate
    writes an explicit ``unavailable`` status for that case, which is the whole
    point of the column existing.
    """
    conn.execute("ALTER TABLE messages ADD COLUMN integrity_check TEXT")


def _v4_integrity_advisory(conn: sqlite3.Connection) -> None:
    """Version 4 — the advisory channel. Design revision 7, F33.

    Nullable JSON holding what the classifier said about **tool** claims, which
    it has no authority over: the deterministic rules own that class, and
    revision 5 made that a property of the code rather than a prompt
    instruction. This records the judgment without letting it gate anything.

    **Its own column, not a key inside ``integrity_check``.** Exactly migration
    3's own argument one layer on: that column is the authoritative verdict, and
    a non-authoritative signal sharing it invites a future reader to take one for
    the other. Here the boundary is visible in the schema, and "the advisory
    fired while the verdict was clean" is a trivial query.

    NULL means *no advisory recorded*. An empty list means *the classifier was
    asked and said nothing the rules had missed* — per O17, only misses are
    recorded, so the channel stays additive rather than duplicating the verdict.
    """
    conn.execute("ALTER TABLE messages ADD COLUMN integrity_advisory TEXT")


def _v5_supersedes_by_message(conn: sqlite3.Connection) -> None:
    """Version 5 — `supersedes` moves from chunk granularity to message.

    Design of record: ``docs/CORRECTION_DESIGN.md`` C3. Three reasons, each
    measured against the build as it is:

    * **A chunk holds content the correction says nothing about.** Up to eight
      turns packed to 2,500 characters, boundaries chosen by size. Marking one
      superseded asserts staleness for all of it.
    * **The timing gap dissolves.** Chunking never indexes the open trailing
      group, so the normal case — correcting something said a minute ago — has no
      chunk to link to yet. Messages exist the moment they are saved.
    * **Chunks are derived and rebuildable; links to them are not.** A re-chunk,
      a restore, or a change to ``chunking.target_chars`` leaves a chunk-level
      link pointing at something that no longer means what it meant.

    **This is destructive, and that is acceptable here specifically**: the table
    has no production rows — no classifier has ever written to it — and decision
    #16 wipes the database before go-live with no carve-outs. Keeping the old
    table alongside would leave two link tables, one dead, and a future reader
    guessing which one retrieval honours.

    The constraints and the cycle guards come across unchanged in substance,
    rewritten against the new columns. They still reject the *link*, never a
    message: no raw experience is touched by a correction.
    """
    conn.execute("DROP TRIGGER IF EXISTS supersedes_no_cycle_insert")
    conn.execute("DROP TRIGGER IF EXISTS supersedes_no_cycle_update")
    conn.execute("DROP TABLE IF EXISTS supersedes")
    _execute_script(conn, """
        CREATE TABLE supersedes (
            id                      TEXT PRIMARY KEY,
            superseding_message_id  TEXT NOT NULL,
            superseded_message_id   TEXT NOT NULL,
            classifier_model        TEXT,
            confidence              REAL,
            rationale               TEXT,
            created_at              TEXT NOT NULL,
            FOREIGN KEY (superseding_message_id) REFERENCES messages(id),
            FOREIGN KEY (superseded_message_id) REFERENCES messages(id),
            UNIQUE (superseding_message_id, superseded_message_id),
            CHECK (superseding_message_id <> superseded_message_id)
        );

        CREATE INDEX idx_supersedes_superseded
            ON supersedes(superseded_message_id);
        CREATE INDEX idx_supersedes_superseding
            ON supersedes(superseding_message_id);

        CREATE TRIGGER supersedes_no_cycle_insert
        BEFORE INSERT ON supersedes
        WHEN EXISTS (
            WITH RECURSIVE forward(id) AS (
                SELECT new.superseding_message_id
                UNION
                SELECT s.superseding_message_id
                  FROM supersedes s
                  JOIN forward f ON s.superseded_message_id = f.id
            )
            SELECT 1 FROM forward WHERE id = new.superseded_message_id
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'supersedes cycle: this link would make correction resolution non-terminating'
            );
        END;

        CREATE TRIGGER supersedes_no_cycle_update
        BEFORE UPDATE OF superseding_message_id, superseded_message_id ON supersedes
        WHEN EXISTS (
            WITH RECURSIVE forward(id) AS (
                SELECT new.superseding_message_id
                UNION
                SELECT s.superseding_message_id
                  FROM supersedes s
                  JOIN forward f ON s.superseded_message_id = f.id
                 WHERE s.id <> old.id
            )
            SELECT 1 FROM forward WHERE id = new.superseded_message_id
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'supersedes cycle: this link would make correction resolution non-terminating'
            );
        END;
    """)


def _v6_supersedes_replacement(conn: sqlite3.Connection) -> None:
    """Version 6 — `supersedes.replacement`: did the correction give a new value?

    Design of record: ``docs/RETRIEVAL_SUPERSESSION_DESIGN.md`` R4, approved at
    review 2026-09-18 (RO1). Required by CO8, which broadened what counts as a
    correction: a message may now state that an earlier claim is wrong **without
    saying what is true instead**, so a link no longer implies a replacement value
    exists. Task 3.5 has to render those two cases differently, and nothing in the
    store could tell them apart.

    ``replaced`` — the correction supplied the new value.
    ``contradicted`` — it said the earlier statement is wrong and gave no value.

    **NOT NULL with no default, deliberately.** A default would let a writer omit
    the label and silently get whichever state is cheaper to render; a nullable
    column would make "the classifier did not say" a third state that reads as a
    missing feature rather than a failure. The single writer
    (``corrections.record()``) can always supply it, because
    ``corrections._parse()`` refuses a reply that does not.

    **Recreated rather than ALTERed, and the reason is SQLite's**, not taste:
    ``ALTER TABLE ADD COLUMN`` with ``NOT NULL`` *requires* a non-null default,
    which is precisely the failure above. The alternative — a nullable column plus
    a ``BEFORE INSERT`` trigger enforcing both the NOT NULL and the vocabulary —
    would put one constraint in two mechanisms and leave the schema not saying what
    it means. Destructive on migration 5's own grounds: this build's data is
    disposable, decision #16 wipes before go-live, and any rows here are dev links
    written since 3.3 landed.

    The constraints and both cycle guards come across unchanged. They are verified
    by the existing cycle tests rather than by reading — a transcription error in a
    recursive trigger is exactly the kind that looks right.
    """
    conn.execute("DROP TRIGGER IF EXISTS supersedes_no_cycle_insert")
    conn.execute("DROP TRIGGER IF EXISTS supersedes_no_cycle_update")
    conn.execute("DROP TABLE IF EXISTS supersedes")
    _execute_script(conn, """
        CREATE TABLE supersedes (
            id                      TEXT PRIMARY KEY,
            superseding_message_id  TEXT NOT NULL,
            superseded_message_id   TEXT NOT NULL,
            replacement             TEXT NOT NULL,
            classifier_model        TEXT,
            confidence              REAL,
            rationale               TEXT,
            created_at              TEXT NOT NULL,
            FOREIGN KEY (superseding_message_id) REFERENCES messages(id),
            FOREIGN KEY (superseded_message_id) REFERENCES messages(id),
            UNIQUE (superseding_message_id, superseded_message_id),
            CHECK (superseding_message_id <> superseded_message_id),
            CHECK (replacement IN ('replaced', 'contradicted'))
        );

        CREATE INDEX idx_supersedes_superseded
            ON supersedes(superseded_message_id);
        CREATE INDEX idx_supersedes_superseding
            ON supersedes(superseding_message_id);

        CREATE TRIGGER supersedes_no_cycle_insert
        BEFORE INSERT ON supersedes
        WHEN EXISTS (
            WITH RECURSIVE forward(id) AS (
                SELECT new.superseding_message_id
                UNION
                SELECT s.superseding_message_id
                  FROM supersedes s
                  JOIN forward f ON s.superseded_message_id = f.id
            )
            SELECT 1 FROM forward WHERE id = new.superseded_message_id
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'supersedes cycle: this link would make correction resolution non-terminating'
            );
        END;

        CREATE TRIGGER supersedes_no_cycle_update
        BEFORE UPDATE OF superseding_message_id, superseded_message_id ON supersedes
        WHEN EXISTS (
            WITH RECURSIVE forward(id) AS (
                SELECT new.superseding_message_id
                UNION
                SELECT s.superseding_message_id
                  FROM supersedes s
                  JOIN forward f ON s.superseded_message_id = f.id
                 WHERE s.id <> old.id
            )
            SELECT 1 FROM forward WHERE id = new.superseded_message_id
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'supersedes cycle: this link would make correction resolution non-terminating'
            );
        END;
    """)


def _v7_artifact_integrity_check(conn: sqlite3.Connection) -> None:
    """Version 7 — the fabrication gate's verdict on an artifact.

    Design of record: ``docs/REFLECTION_JOURNAL_DESIGN.md`` J7. Its first writer is
    the reflection journal, whose entries are the most confabulation-prone text
    this system produces: the entity looking back on a day is the exact question
    the prior build confabulated on. A log-only verdict there would be the
    unmounted-gate shape, so it is persisted.

    **Migration 3's semantics, unchanged.** Nullable JSON, and **NULL means no
    verdict recorded, never clean**: every existing artifact, and every kind that
    does not run the gate, stays NULL. The gate writes an explicit
    ``unavailable`` when its classifier cannot run, which is what keeps NULL from
    ever standing in for "checked and fine".

    **On ``artifacts``, under the same name as on ``messages``**, so "flagged
    journal entries" and "flagged turns" are the same query shape. Generic rather
    than journal-specific: which other kinds should ever write it is a separate
    decision, and creative writing should not (fiction is not a truth claim,
    ``kinds.py``).

    **No advisory column beside it.** The journal's identity-only gate mode (J8)
    produces no advisory notes; that channel exists for tool claims.

    One statement, inside the runner's transaction like every migration. SQLite's
    ``ALTER TABLE`` is transactional, so a failure later in the same run removes
    the column again; the forced-failure test asserts exactly that.
    """
    conn.execute("ALTER TABLE artifacts ADD COLUMN integrity_check TEXT")


def _v8_notes(conn: sqlite3.Connection) -> None:
    """Version 8 — Notes. Design of record: ``docs/NOTES_DESIGN.md`` (revision 4, approved
    2026-10-01) N13; build plan ``docs/NOTES_BUILD_PLAN.md``, piece 1.

    Three tables, a view and an FTS index, all in ``working.db`` (nothing in the archive, on
    this module's rule), in **one migration** run through ``_execute_script`` inside the
    runner's transaction (B3). **Nothing in ``working.sql``.**

    Rulings of 2026-10-01/02 that shape it:

    * ``note_proposals.status`` is ``pending | approved | edited | rejected |
      applied_without_review`` (N17 #22).
    * **A proposal is always inserted ``pending`` and decided by an UPDATE in the same
      transaction, auto-apply included**, so nothing needs an insert exception: a
      ``BEFORE INSERT`` trigger refuses any other starting status (approved), and **every
      column of a decided proposal is frozen** by a ``BEFORE UPDATE`` trigger (approved at
      review; it also holds N17 #21's one-way status rule, which it subsumes).
    * ``approval_log`` is append-only in the schema: ``BEFORE UPDATE`` and ``BEFORE DELETE``
      raise (approved).
    * **Deletes (ruling of 2026-10-02):** a decided proposal cannot be deleted (a pending one
      may be), and **nothing can be deleted from ``notes``**: retiring and superseding change
      ``status`` and keep the text. So there is no ``AFTER DELETE`` FTS trigger: it could never
      fire.
    * ``resulting_note_id`` is **the note the proposal created or changed** (for a retire, the
      retired note's id) and is non-null exactly when ``status`` is ``approved``, ``edited`` or
      ``applied_without_review``. The note row therefore exists before the proposal is flipped,
      in the same transaction.
    * **``approval_log.decision`` is NOT closed in the schema**: the table is shared across
      capabilities, so the vocabulary is validated in code (``db.APPROVAL_DECISIONS`` and
      ``db.record_approval``), where a new capability can add its own without a migration.
    * No ``reason`` column on proposals; a reviewer's reason lives in
      ``approval_log.detail``.
    * ``evidence`` is ``NOT NULL`` and must be a **non-empty JSON array** (CHECK).
    * **REPLACE cannot get round the guards** (found by testing, 2026-10-02): SQLite skips
      DELETE triggers for the row an ``INSERT OR REPLACE`` or ``UPDATE OR REPLACE`` removes
      unless ``recursive_triggers`` is on, so those statements overwrote rows the delete guards
      protect. ``BEFORE INSERT`` triggers refuse an existing id (or explicit rowid) on all three
      tables, and ``BEFORE UPDATE`` triggers refuse changing an id or rowid on ``notes`` and
      ``note_proposals`` (``approval_log`` refuses every update). No connection pragma is
      changed.

    **The FTS index is external content over a VIEW of the active notes, not over ``notes``.**
    Found while building: with the content table being ``notes`` itself and triggers indexing
    only ``status = 'active'`` rows, FTS5's content-comparing check
    (``INSERT INTO notes_fts(notes_fts, rank) VALUES('integrity-check', 1)``) reports
    "database disk image is malformed" as soon as one note is retired, because the index
    legitimately lacks a row the table has. Over ``active_notes`` the comparison is exact, and a
    deliberately drifted index is detected (both measured, SQLite 3.53.1). That check is what
    ``scripts.note check`` needs, so this departs from N13's "over notes(subject, text)" in
    the table named, not in the behaviour.

    **Constraints beyond N13's list, flagged for the schema review rather than assumed:**
    the ``action``/``target_note_id`` consistency CHECK (add has no target and needs text;
    revise needs both; retire needs a target), ``json_valid`` on every JSON column, and
    ``decided_at`` set exactly when the proposal is no longer pending.
    **Rowids.** ``notes`` has a ``TEXT`` primary key, so its rowid is implicit, exactly as
    ``chunks``' is under ``chunks_fts``. An implicit rowid is not guaranteed stable across a
    ``VACUUM``, which could silently re-pair index entries with the wrong rows, so **nothing in
    this build runs ``VACUUM``** (a test scans for it).
    """
    _execute_script(conn, """
        CREATE TABLE notes (
            id                 TEXT PRIMARY KEY,
            subject_kind       TEXT NOT NULL
                CHECK (subject_kind IN ('person', 'topic', 'project')),
            subject            TEXT NOT NULL,
            subject_user_id    TEXT REFERENCES users(id),
            text               TEXT NOT NULL,
            status             TEXT NOT NULL
                CHECK (status IN ('active', 'superseded', 'retired')),
            version            INTEGER NOT NULL CHECK (version >= 1),
            previous_note_id   TEXT REFERENCES notes(id),
            origin             TEXT NOT NULL CHECK (origin IN ('entity', 'operator')),
            created_at         TEXT NOT NULL,
            last_confirmed_at  TEXT NOT NULL,
            retired_at         TEXT
        );
        CREATE INDEX idx_notes_status ON notes(status);
        CREATE INDEX idx_notes_subject_user ON notes(subject_user_id);
        CREATE INDEX idx_notes_previous ON notes(previous_note_id);

        CREATE TABLE note_proposals (
            id                 TEXT PRIMARY KEY,
            action             TEXT NOT NULL CHECK (action IN ('add', 'revise', 'retire')),
            target_note_id     TEXT REFERENCES notes(id),
            subject_kind       TEXT NOT NULL
                CHECK (subject_kind IN ('person', 'topic', 'project')),
            subject            TEXT NOT NULL,
            subject_user_id    TEXT REFERENCES users(id),
            text               TEXT,
            -- A non-empty JSON array: an entity proposal always has evidence (N4).
            -- json_array_length is 0 for anything that is not an array (an object, null, a
            -- scalar), so one test covers both; the CASE keeps it from running on non-JSON.
            evidence           TEXT NOT NULL CHECK (
                CASE WHEN json_valid(evidence) THEN json_array_length(evidence) >= 1
                     ELSE 0 END
            ),
            conversation_id    TEXT NOT NULL REFERENCES conversations(id),
            user_message_id    TEXT NOT NULL REFERENCES messages(id),
            call_id            TEXT,
            user_id            TEXT NOT NULL REFERENCES users(id),
            status             TEXT NOT NULL
                CHECK (status IN ('pending', 'approved', 'edited', 'rejected',
                                  'applied_without_review')),
            untrusted_context  TEXT
                CHECK (untrusted_context IS NULL OR json_valid(untrusted_context)),
            integrity_check    TEXT
                CHECK (integrity_check IS NULL OR json_valid(integrity_check)),
            created_at         TEXT NOT NULL,
            decided_at         TEXT,
            resulting_note_id  TEXT REFERENCES notes(id),
            CHECK (
                (action = 'add' AND target_note_id IS NULL AND text IS NOT NULL)
                OR (action = 'revise' AND target_note_id IS NOT NULL AND text IS NOT NULL)
                OR (action = 'retire' AND target_note_id IS NOT NULL)
            ),
            CHECK ((status = 'pending') = (decided_at IS NULL)),
            CHECK ((status IN ('approved', 'edited', 'applied_without_review'))
                   = (resulting_note_id IS NOT NULL))
        );
        CREATE INDEX idx_note_proposals_status ON note_proposals(status);
        CREATE INDEX idx_note_proposals_target ON note_proposals(target_note_id);
        CREATE INDEX idx_note_proposals_conversation ON note_proposals(conversation_id);

        CREATE TABLE approval_log (
            id            TEXT PRIMARY KEY,
            capability    TEXT NOT NULL,
            subject_kind  TEXT NOT NULL,
            subject_id    TEXT NOT NULL,
            decision      TEXT NOT NULL,
            decided_by    TEXT NOT NULL,
            detail        TEXT NOT NULL CHECK (json_valid(detail)),
            created_at    TEXT NOT NULL
        );
        CREATE INDEX idx_approval_log_capability ON approval_log(capability, created_at);
        CREATE INDEX idx_approval_log_subject ON approval_log(subject_kind, subject_id);

        CREATE VIEW active_notes AS
            SELECT rowid AS rid, subject, text FROM notes WHERE status = 'active';

        CREATE VIRTUAL TABLE notes_fts USING fts5(
            subject,
            text,
            content = 'active_notes',
            content_rowid = 'rid',
            tokenize = 'unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER notes_fts_insert AFTER INSERT ON notes
        WHEN new.status = 'active'
        BEGIN
            INSERT INTO notes_fts(rowid, subject, text)
                VALUES (new.rowid, new.subject, new.text);
        END;

        CREATE TRIGGER notes_fts_update AFTER UPDATE ON notes
        BEGIN
            INSERT INTO notes_fts(notes_fts, rowid, subject, text)
                SELECT 'delete', old.rowid, old.subject, old.text WHERE old.status = 'active';
            INSERT INTO notes_fts(rowid, subject, text)
                SELECT new.rowid, new.subject, new.text WHERE new.status = 'active';
        END;

        CREATE TRIGGER notes_no_delete BEFORE DELETE ON notes
        BEGIN
            SELECT RAISE(ABORT, 'notes are never deleted: retire or supersede them');
        END;

        CREATE TRIGGER note_proposals_decided_no_delete BEFORE DELETE ON note_proposals
        WHEN old.status <> 'pending'
        BEGIN
            SELECT RAISE(ABORT, 'a decided note proposal is final and is not deleted');
        END;

        CREATE TRIGGER note_proposals_insert_pending BEFORE INSERT ON note_proposals
        WHEN new.status <> 'pending'
        BEGIN
            SELECT RAISE(
                ABORT,
                'a note proposal is inserted pending and decided by an update in the same '
                || 'transaction'
            );
        END;

        CREATE TRIGGER note_proposals_decided_is_final BEFORE UPDATE ON note_proposals
        WHEN old.status <> 'pending'
        BEGIN
            SELECT RAISE(
                ABORT,
                'a decided note proposal is final: a change of mind is a new proposal'
            );
        END;

        -- REPLACE bypasses the delete triggers above: with recursive_triggers off (SQLite's
        -- default, and this build changes no connection pragma), a row deleted to resolve an
        -- INSERT OR REPLACE, REPLACE INTO or UPDATE OR REPLACE conflict fires no DELETE trigger.
        -- Measured: it overwrote a row guarded by a BEFORE DELETE trigger. These close it: an
        -- insert may not reuse an existing id (or an explicit existing rowid; ``new.rowid`` is -1
        -- when none was given), and an update may not change an id or rowid.
        CREATE TRIGGER notes_no_replace BEFORE INSERT ON notes
        WHEN EXISTS (SELECT 1 FROM notes WHERE id = new.id)
          OR (new.rowid <> -1 AND EXISTS (SELECT 1 FROM notes WHERE rowid = new.rowid))
        BEGIN
            SELECT RAISE(ABORT, 'a note is never replaced: its id or rowid exists');
        END;

        CREATE TRIGGER notes_id_is_final BEFORE UPDATE ON notes
        WHEN new.id <> old.id OR new.rowid <> old.rowid
        BEGIN
            SELECT RAISE(ABORT, 'a note keeps its id and rowid');
        END;

        CREATE TRIGGER note_proposals_no_replace BEFORE INSERT ON note_proposals
        WHEN EXISTS (SELECT 1 FROM note_proposals WHERE id = new.id)
          OR (new.rowid <> -1 AND EXISTS (SELECT 1 FROM note_proposals WHERE rowid = new.rowid))
        BEGIN
            SELECT RAISE(ABORT, 'a note proposal is never replaced: its id or rowid exists');
        END;

        CREATE TRIGGER note_proposals_id_is_final BEFORE UPDATE ON note_proposals
        WHEN new.id <> old.id OR new.rowid <> old.rowid
        BEGIN
            SELECT RAISE(ABORT, 'a note proposal keeps its id and rowid');
        END;

        CREATE TRIGGER approval_log_no_replace BEFORE INSERT ON approval_log
        WHEN EXISTS (SELECT 1 FROM approval_log WHERE id = new.id)
          OR (new.rowid <> -1 AND EXISTS (SELECT 1 FROM approval_log WHERE rowid = new.rowid))
        BEGIN
            SELECT RAISE(ABORT, 'an approval_log row is never replaced: its id (or rowid) exists');
        END;

        CREATE TRIGGER approval_log_append_only_update BEFORE UPDATE ON approval_log
        BEGIN
            SELECT RAISE(ABORT, 'approval_log is append-only');
        END;

        CREATE TRIGGER approval_log_append_only_delete BEFORE DELETE ON approval_log
        BEGIN
            SELECT RAISE(ABORT, 'approval_log is append-only');
        END;
    """)


MIGRATIONS: list[Migration] = [
    Migration(version=2, name="artifacts_and_chunk_link", apply=_v2_artifacts),
    Migration(version=3, name="message_integrity_check", apply=_v3_integrity_check),
    Migration(version=4, name="message_integrity_advisory", apply=_v4_integrity_advisory),
    Migration(version=5, name="supersedes_by_message", apply=_v5_supersedes_by_message),
    Migration(version=6, name="supersedes_replacement", apply=_v6_supersedes_replacement),
    Migration(version=7, name="artifact_integrity_check", apply=_v7_artifact_integrity_check),
    Migration(version=8, name="notes", apply=_v8_notes),
]


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    return {row["version"] for row in rows}


def _record(conn: sqlite3.Connection, version: int, name: str) -> None:
    conn.execute(
        "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
        (version, name, db.now_iso()),
    )


def current_version(conn: sqlite3.Connection | None = None) -> int:
    """Highest applied version, or 0 if the database has no schema yet."""

    def _query(c: sqlite3.Connection) -> int:
        row = c.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return row["v"] or 0

    if conn is not None:
        return _query(conn)
    with db.connection() as own:
        return _query(own)


def run_working_migrations() -> list[str]:
    """Apply every pending migration in order. Returns the names applied."""
    applied: list[str] = []

    with db.transaction() as conn:
        known = _applied_versions(conn)

        if INITIAL_VERSION not in known:
            # working.sql has just been executed, so the initial schema exists;
            # this records that fact rather than performing it.
            _record(conn, INITIAL_VERSION, INITIAL_NAME)
            known.add(INITIAL_VERSION)
            applied.append(INITIAL_NAME)

        for migration in sorted(MIGRATIONS, key=lambda m: m.version):
            if migration.version in known:
                continue
            if migration.version <= INITIAL_VERSION:
                raise ValueError(
                    f"migration {migration.version} ({migration.name}) collides with "
                    f"the initial schema version {INITIAL_VERSION}"
                )
            migration.apply(conn)
            _record(conn, migration.version, migration.name)
            applied.append(migration.name)

    return applied


def verify_no_duplicate_versions() -> None:
    """Fail loudly if two migrations claim the same version.

    Called by the test suite. A duplicate version means one of them will be
    skipped on a database that has already seen the other, producing two
    different schemas that both report the same version.
    """
    seen: dict[int, str] = {}
    for migration in MIGRATIONS:
        if migration.version in seen:
            raise ValueError(
                f"duplicate migration version {migration.version}: "
                f"{seen[migration.version]} and {migration.name}"
            )
        seen[migration.version] = migration.name
