"""Database layer: schema, dual-write atomicity, and the frozen archive shape."""

from __future__ import annotations

import sqlite3

import pytest

from program.memory import db


@pytest.fixture
def store(isolated_data_dir):
    """A freshly initialised pair of databases in a temporary directory."""
    db.init_databases()
    return isolated_data_dir


@pytest.fixture
def user(store):
    return db.create_user("Tester", role="admin")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_both_databases_are_created(store):
    assert db.archive_path().exists()
    assert db.working_path().exists()


def test_archive_has_exactly_two_tables(store):
    """The archive's scope is frozen. A third table here is a design change."""
    with db.connection() as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM archive.sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert names == {"users", "messages"}


def test_working_has_the_expected_tables(store):
    with db.connection() as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM main.sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE 'chunks_fts%'"
            )
        }
    assert names == {
        "artifacts",
        "schema_version",
        "users",
        "conversations",
        "messages",
        "chunks",
        "supersedes",
        "settings",
    }


def test_later_phase_tables_are_absent(store):
    """Tables deferred to a later phase of this build.

    Distinct from `test_deferred_features_have_no_tables`, which covers features
    that are out of this build entirely. Both were expected back — `artifacts`
    in Phase 2, a research-candidate table in Phase 5 — just not designed at
    Phase 1. `artifacts` has now landed (task 2.6, migration 2, designed in
    `docs/INGESTION_DESIGN.md`); updating this assertion was part of that task.
    `research_candidates` is still Phase 5's to design fresh.
    """
    with db.connection() as conn:
        names = {
            row["name"] for row in conn.execute("SELECT name FROM main.sqlite_master")
        }
    # `artifacts` arrived on schedule with task 2.6 (migration 2), which is what
    # this test was waiting to see happen deliberately rather than by drift.
    assert "artifacts" in names
    assert "research_candidates" not in names


def test_deferred_features_have_no_tables(store):
    """Tables belonging to features that are out of this build entirely.

    Distinct from `test_later_phase_tables_are_absent`, which covers tables
    deferred to a *later phase of this build*. These are not coming back at all
    within it. Asserted rather than assumed, because a seam is easiest to add by
    accident.
    """
    with db.connection() as conn:
        names = {
            row["name"].lower()
            for row in conn.execute("SELECT name FROM main.sqlite_master")
        }
    for forbidden in (
        "review_items",
        "review_queue",
        # Nothing is ever summarised — history windowing drops turns from the
        # prompt, never from the record (decision #6).
        "summaries",
        # The fabrication gate refuses to persist a bad turn rather than storing
        # it and filtering it later (decisions #1, #16).
        "excluded_chunks",
        # One channel in this build; web credentials live on `users`.
        "channel_identifiers",
    ):
        assert forbidden not in names
    assert not any("self_mod" in n or "selfmod" in n for n in names)
    assert not any("behavioral_guidance" in n for n in names)


def test_journal_mode_is_delete_on_both(store):
    """WAL would break cross-database atomicity. See db.py's docstring."""
    with db.connection() as conn:
        assert conn.execute("PRAGMA main.journal_mode").fetchone()[0] == "delete"
        assert conn.execute("PRAGMA archive.journal_mode").fetchone()[0] == "delete"


def test_foreign_keys_are_enforced(store):
    with pytest.raises(sqlite3.IntegrityError):
        db.start_conversation("no-such-user")


def test_init_is_idempotent(store):
    """Re-running init must not re-record anything.

    Asserts the count does not *grow*, rather than that it equals one: it equals
    one plus the number of migrations, and pinning that number would make every
    future migration fail this test for no reason.
    """
    def versions():
        with db.connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM schema_version"
            ).fetchone()["n"]

    before = versions()
    db.init_databases()
    db.init_databases()
    assert versions() == before


# ---------------------------------------------------------------------------
# The dual write
# ---------------------------------------------------------------------------


def test_message_lands_in_both_stores(store, user):
    cid = db.start_conversation(user)
    mid = db.save_message(cid, user, "user", "hello")

    with db.connection() as conn:
        archived = conn.execute(
            "SELECT * FROM archive.messages WHERE id = ?", (mid,)
        ).fetchone()
        working = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()

    assert archived["content"] == "hello"
    assert working["content"] == "hello"
    assert archived["user_id"] == working["user_id"] == user


def test_failed_write_leaves_neither_store_touched(store, user):
    """The atomicity guarantee, proven rather than asserted.

    A write that reached one store and not the other is a memory the system is
    wrong about, silently. This forces a failure between the two inserts and
    checks that nothing survived it.
    """
    cid = db.start_conversation(user)
    before_archive, before_working = db.count_messages()

    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO archive.messages
                       (id, conversation_id, user_id, role, content, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("m1", cid, user, "user", "half-written", db.now_iso()),
            )
            # Violates the messages.conversation_id foreign key.
            conn.execute(
                """INSERT INTO messages
                       (id, conversation_id, user_id, role, content, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("m1", "no-such-conversation", user, "user", "half-written", db.now_iso()),
            )

    after_archive, after_working = db.count_messages()
    assert (after_archive, after_working) == (before_archive, before_working)
    assert db.get_archive_message("m1") is None


def test_counts_stay_equal_across_many_writes(store, user):
    cid = db.start_conversation(user)
    for i in range(25):
        db.save_message(cid, user, "user" if i % 2 == 0 else "assistant", f"turn {i}")
    archive_count, working_count = db.count_messages()
    assert archive_count == working_count == 25
    assert db.get_conversation(cid)["message_count"] == 25


def test_invalid_role_is_rejected_before_any_write(store, user):
    cid = db.start_conversation(user)
    with pytest.raises(ValueError):
        db.save_message(cid, user, "system", "nope")
    assert db.count_messages() == (0, 0)


def test_role_check_constraint_holds_at_the_schema_level(store, user):
    """Belt and braces: the Python guard above is not the only thing stopping it."""
    cid = db.start_conversation(user)
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO messages
                       (id, conversation_id, user_id, role, content, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("x", cid, user, "system", "nope", db.now_iso()),
            )


def test_tool_trace_round_trips(store, user):
    """The trace is ground truth for the fabrication gate, so it must survive."""
    cid = db.start_conversation(user)
    trace = '{"calls": [{"name": "memory_search", "ok": true}]}'
    mid = db.save_message(cid, user, "assistant", "answer", tool_trace=trace)
    assert db.get_archive_message(mid)["tool_trace"] == trace


# ---------------------------------------------------------------------------
# Chunks, provenance, and the derived FTS index
# ---------------------------------------------------------------------------


def _insert_message(conn, message_id, content, cid, uid, role="user"):
    """A working-store message row, for the link constraints below.

    Links became message → message at migration 5: a chunk packs up to eight
    turns chosen by size, so marking one superseded asserts staleness for content
    the correction says nothing about, and the open trailing group has no chunk to
    link to at all. See ``docs/CORRECTION_DESIGN.md`` C3.
    """
    conn.execute(
        """INSERT INTO messages (id, conversation_id, user_id, role, content, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (message_id, cid, uid, role, content, db.now_iso()),
    )


def _insert_chunk(conn, chunk_id, text, cid=None, uid=None, **overrides):
    values = {
        "id": chunk_id,
        "conversation_id": cid,
        "user_id": uid,
        "text": text,
        "source_type": "conversation",
        "source_trust": "firsthand",
        "text_sha256": "deadbeef",
        "created_at": db.now_iso(),
        "updated_at": db.now_iso(),
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO chunks ({columns}) VALUES ({placeholders})", tuple(values.values()))


def test_chunk_requires_provenance(store):
    """Task 1.7 requires that no chunk can be written without provenance.

    A NOT NULL constraint holds where a convention does not.
    """
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO chunks (id, text, text_sha256, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("c1", "text", "hash", db.now_iso(), db.now_iso()),
            )


def test_fts_index_follows_chunk_insert(store):
    with db.transaction() as conn:
        _insert_chunk(conn, "c1", "the harbour was full of small boats")

    with db.connection() as conn:
        hits = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'harbour'"
        ).fetchall()
    assert len(hits) == 1


def test_fts_index_follows_chunk_delete(store):
    """The index cannot outlive what it indexed — the trigger makes it structural."""
    with db.transaction() as conn:
        _insert_chunk(conn, "c1", "the harbour was full of small boats")
    with db.transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE id = 'c1'")

    with db.connection() as conn:
        hits = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'harbour'"
        ).fetchall()
    assert hits == []


def test_fts_index_follows_chunk_update(store):
    with db.transaction() as conn:
        _insert_chunk(conn, "c1", "the harbour was full of small boats")
    with db.transaction() as conn:
        conn.execute("UPDATE chunks SET text = 'the field was full of sheep' WHERE id = 'c1'")

    with db.connection() as conn:
        stale = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'harbour'"
        ).fetchall()
        fresh = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'sheep'"
        ).fetchall()
    assert stale == []
    assert len(fresh) == 1


def test_chunk_index_is_unique_per_conversation(store, user):
    cid = db.start_conversation(user)
    with db.transaction() as conn:
        _insert_chunk(conn, "c1", "first", cid=cid, uid=user, chunk_index=0)
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            _insert_chunk(conn, "c2", "second", cid=cid, uid=user, chunk_index=0)


def test_non_conversation_chunks_may_share_a_null_index(store):
    """Ingested files and creative writing have no conversation or ordinal."""
    with db.transaction() as conn:
        _insert_chunk(conn, "c1", "from a file", source_type="uploaded_file")
        _insert_chunk(conn, "c2", "from another file", source_type="uploaded_file")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 2


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------
#
# Links are message -> message as of migration 5. The properties below are
# unchanged — real referents, no self-link, no duplicate, no cycle of any
# length, and the original never altered — but the unit is a message, because a
# chunk packs up to eight turns chosen by size and the open trailing group has
# no chunk at all. See `docs/CORRECTION_DESIGN.md` C3.


@pytest.fixture
def conversation(user):
    return db.start_conversation(user)


def _messages(conn, conversation_id, user_id, *ids):
    for message_id in ids:
        _insert_message(conn, message_id, f"claim {message_id}", conversation_id, user_id)


def _link(conn, link_id, superseding, superseded, replacement="replaced"):
    conn.execute(
        """INSERT INTO supersedes
               (id, superseding_message_id, superseded_message_id, replacement,
                created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (link_id, superseding, superseded, replacement, db.now_iso()),
    )


def test_the_replacement_state_is_required_and_constrained(conversation, user):
    """Migration 6. `replaced` vs `contradicted` is what task 3.5 renders
    differently, and CO8 means a link no longer implies a new value exists — so
    NULL and a typo both have to be refused by the schema rather than by the one
    writer that happens to exist today."""
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A", "B")

    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO supersedes
                       (id, superseding_message_id, superseded_message_id, created_at)
                   VALUES ('s0', 'B', 'A', ?)""",
                (db.now_iso(),),
            )

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with db.transaction() as conn:
            _link(conn, "s1", "B", "A", replacement="probably")

    with db.transaction() as conn:
        _link(conn, "s2", "B", "A", replacement="contradicted")
    [row] = db.get_supersedes_links("A")
    assert row["replacement"] == "contradicted"


def test_supersedes_link_requires_real_messages(store):
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            _link(conn, "s1", "nope", "also-nope")


def test_message_cannot_supersede_itself(conversation, user):
    """A self-link would make forward link resolution non-terminating."""
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A")
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            _link(conn, "s1", "A", "A")


def test_duplicate_supersedes_link_is_rejected(conversation, user):
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A", "B")
        _link(conn, "s1", "B", "A")
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            _link(conn, "s2", "B", "A")


def test_two_link_cycle_is_rejected(conversation, user):
    """A->B and B->A are two distinct tuples, so UNIQUE does not stop them.

    Retrieval resolves corrections by following links forward, so a loop of any
    length means resolution never terminates. The schema has to stop it, because
    the writer is not always the classifier.
    """
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A", "B")
        _link(conn, "s1", "B", "A")
    with pytest.raises(sqlite3.IntegrityError, match="cycle"):
        with db.transaction() as conn:
            _link(conn, "s2", "A", "B")


def test_longer_cycle_is_rejected(conversation, user):
    """Four hops. The guard walks forward, so length does not matter."""
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A", "B", "C", "D")
        _link(conn, "s1", "B", "A")
        _link(conn, "s2", "C", "B")
        _link(conn, "s3", "D", "C")
    with pytest.raises(sqlite3.IntegrityError, match="cycle"):
        with db.transaction() as conn:
            _link(conn, "s4", "A", "D")


def test_non_cycle_links_are_still_allowed(conversation, user):
    """The guard must not reject an ordinary chain of corrections."""
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A", "B", "C")
        _link(conn, "s1", "B", "A")
        _link(conn, "s2", "C", "B")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM supersedes").fetchone()["n"] == 2


def test_cycle_guard_also_covers_updates(conversation, user):
    """Repointing an existing link must not be a way around the insert guard.

    Starting state is B->A. Repointing the second link to A->B closes the loop:
    resolving B reaches A, and resolving A reaches B.
    """
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A", "B", "C")
        _link(conn, "s1", "B", "A")
        _link(conn, "s2", "C", "B")
    with pytest.raises(sqlite3.IntegrityError, match="cycle"):
        with db.transaction() as conn:
            conn.execute(
                "UPDATE supersedes SET superseding_message_id = 'A', "
                "superseded_message_id = 'B' WHERE id = 's2'"
            )


def test_update_to_a_non_cyclic_link_is_allowed(conversation, user):
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A", "B", "C")
        _link(conn, "s1", "B", "A")
        _link(conn, "s2", "B", "C")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supersedes SET superseding_message_id = 'A', "
            "superseded_message_id = 'C' WHERE id = 's2'"
        )
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM supersedes WHERE id = 's2'").fetchone()
    assert row["superseding_message_id"] == "A"
    assert row["superseded_message_id"] == "C"


def test_rejected_cycle_leaves_the_messages_untouched(conversation, user):
    """The trigger rejects the *link*, never a message. No raw experience is
    touched by a correction — that is the whole premise of supersession."""
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A", "B")
        _link(conn, "s1", "B", "A")
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            _link(conn, "s2", "A", "B")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) AS n FROM supersedes").fetchone()["n"] == 1


def test_forward_resolution_terminates_on_a_clean_chain(conversation, user):
    """What task 3.5 will do at read time, on data the guard has kept acyclic."""
    with db.transaction() as conn:
        _messages(conn, conversation, user, "A", "B", "C")
        _link(conn, "s1", "B", "A")
        _link(conn, "s2", "C", "B")
    with db.connection() as conn:
        rows = conn.execute(
            """WITH RECURSIVE forward(id) AS (
                   SELECT 'A'
                   UNION
                   SELECT s.superseding_message_id FROM supersedes s
                     JOIN forward f ON s.superseded_message_id = f.id
               )
               SELECT id FROM forward"""
        ).fetchall()
    assert {row["id"] for row in rows} == {"A", "B", "C"}


def test_correction_does_not_alter_the_original(conversation, user):
    """Provenance is sacred: the corrected message keeps its own words."""
    with db.transaction() as conn:
        _insert_message(conn, "A", "the meeting was on Tuesday", conversation, user)
        _insert_message(conn, "B", "actually Wednesday", conversation, user)
        _link(conn, "s1", "B", "A")
    with db.connection() as conn:
        original = conn.execute(
            "SELECT content FROM messages WHERE id = 'A'").fetchone()
    assert original["content"] == "the meeting was on Tuesday"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_value_type_is_constrained(store):
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO settings (key, value, value_type, updated_at)
                   VALUES (?, ?, ?, ?)""",
                ("k", "v", "complex", db.now_iso()),
            )
