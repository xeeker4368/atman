"""Migration 8: the Notes schema. Piece 1 of `docs/NOTES_BUILD_PLAN.md`.

Design of record: `docs/NOTES_DESIGN.md` (revision 4) N13, with the rulings of 2026-10-01/02.
Nothing here involves a model. These check the schema itself: that its constraints and
triggers hold **against direct SQL**, which is the point, since the code that will use it does
not exist yet and a rule held only in code is the weaker guarantee.
"""

from __future__ import annotations

import sqlite3

import pytest

from program.memory import db, migrations

NOTES_OBJECTS = {
    "tables": {"notes", "note_proposals", "approval_log"},
    "view": {"active_notes"},
    "fts": {"notes_fts"},
    "triggers": {
        "notes_fts_insert", "notes_fts_update", "notes_no_delete", "notes_inactive_is_frozen",
        "note_proposals_insert_pending", "note_proposals_decided_is_final",
        "note_proposals_decided_no_delete",
        "approval_log_append_only_update", "approval_log_append_only_delete",
        "notes_no_replace", "notes_id_is_final", "note_proposals_no_replace",
        "note_proposals_id_is_final", "approval_log_no_replace",
        "notes_no_negative_rowid", "note_proposals_no_negative_rowid",
        "approval_log_no_negative_rowid",
    },
}
ALL_OBJECT_NAMES = set().union(*NOTES_OBJECTS.values())
TERMINAL = ("approved", "edited", "rejected", "applied_without_review")
INJECTED = "INSERT INTO injected_mid_migration_failure VALUES (1)"


@pytest.fixture
def store(isolated_data_dir):
    db.init_databases()
    return isolated_data_dir


@pytest.fixture
def world(store):
    uid = db.create_user("Lyle", role="admin")
    cid = db.start_conversation(uid)
    mid = db.save_message(cid, uid, "user", "Jodie takes her coffee with oat milk.")
    return type("W", (), {"user": uid, "conv": cid, "msg": mid})


def names(kind=None, like=None):
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT name FROM main.sqlite_master"
            + (f" WHERE type = '{kind}'" if kind else "")).fetchall()
    return {r["name"] for r in rows if like is None or like in r["name"]}


def note(conn, nid, text="oat milk", status="active", subject="Jodie"):
    conn.execute(
        "INSERT INTO notes (id, subject_kind, subject, text, status, version, origin, "
        "created_at, last_confirmed_at) VALUES (?, 'person', ?, ?, ?, 1, 'operator', ?, ?)",
        (nid, subject, text, status, db.now_iso(), db.now_iso()))


def proposal(conn, world, pid="p1", *, action="add", target=None, text="oat milk",
             status="pending", evidence='[{"quote": "oat milk"}]', decided_at=None):
    conn.execute(
        "INSERT INTO note_proposals (id, action, target_note_id, subject_kind, subject, text, "
        "evidence, conversation_id, user_message_id, user_id, status, created_at, decided_at) "
        "VALUES (?, ?, ?, 'person', 'Jodie', ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, action, target, text, evidence, world.conv, world.msg, world.user, status,
         db.now_iso(), decided_at))


EFFECTIVE = ("approved", "edited", "applied_without_review")


def decide(conn, pid, status, result="nres"):
    """Flip a pending proposal to a terminal status the way the real code will: the note it
    created or changed exists first, and the proposal names it exactly when it took effect."""
    note_id = None
    if status in EFFECTIVE:
        if not conn.execute("SELECT 1 FROM notes WHERE id = ?", (result,)).fetchone():
            note(conn, result)
        note_id = result
    conn.execute(
        "UPDATE note_proposals SET status = ?, decided_at = ?, resulting_note_id = ? "
        "WHERE id = ?", (status, db.now_iso(), note_id, pid))


# --- what migration 8 creates -------------------------------------------------


def test_migration_eight_creates_exactly_its_objects(store):
    assert migrations.current_version() >= 8
    assert NOTES_OBJECTS["tables"] <= names("table")
    assert NOTES_OBJECTS["view"] <= names("view")
    assert "notes_fts" in names("table")
    assert NOTES_OBJECTS["triggers"] <= names("trigger")
    with db.connection() as conn:
        on_proposals = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND tbl_name = 'note_proposals'")}
    assert on_proposals == {"note_proposals_insert_pending", "note_proposals_decided_is_final",
                            "note_proposals_decided_no_delete", "note_proposals_no_replace",
                            "note_proposals_id_is_final", "note_proposals_no_negative_rowid"}
    assert "notes_fts_delete" not in names("trigger"), (
        "an AFTER DELETE FTS trigger can never fire when nothing may be deleted")


def test_the_notes_schema_is_not_in_working_sql():
    """Migration 2's rule: `working.sql` stays the version 1 definition."""
    working = (db.SCHEMA_DIR / "working.sql").read_text(encoding="utf-8")
    for word in ("note_proposals", "approval_log", "notes_fts", "active_notes"):
        assert word not in working


def test_a_proposal_has_no_reason_column_and_the_applied_status_is_renamed(store):
    """Rulings of 2026-10-02 and N17 #22."""
    with db.connection() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(note_proposals)")}
        [sql] = [r["sql"] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'note_proposals'")]
    assert "reason" not in columns
    assert {"evidence", "conversation_id", "user_message_id", "call_id", "user_id", "status",
            "untrusted_context", "integrity_check", "created_at", "decided_at",
            "resulting_note_id", "target_note_id"} <= columns
    assert "applied_without_review" in sql and "'applied'" not in sql


# --- forced failure, B3's method ---------------------------------------------


def _store_at(version, monkeypatch):
    full = list(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", [m for m in full if m.version <= version])
    db.init_databases()
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    assert migrations.current_version() == version


@pytest.mark.parametrize("before", [
    "CREATE TRIGGER notes_fts_insert",                # N13's point: tables made, FTS triggers not
    "CREATE TRIGGER note_proposals_insert_pending",   # everything but the proposal and log guards
])
def test_migration_eight_failing_part_way_rolls_back_completely(
    isolated_data_dir, monkeypatch, before
):
    _store_at(7, monkeypatch)
    uid = db.create_user("Lyle", role="admin")
    with db.transaction() as conn:  # a version-7 row elsewhere, which must survive untouched
        conn.execute(
            "INSERT INTO artifacts (id, user_id, filename, content_type, size_bytes, sha256, "
            "storage_path, artifact_type, extraction_status, created_at) VALUES "
            "('a1', ?, 'f.md', 'text/markdown', 3, 'x', 'a1/a1', 'creative_writing', "
            "'extracted', ?)", (uid, db.now_iso()))
    assert not names() & ALL_OBJECT_NAMES, "precondition: version 7"

    real = migrations._statements

    def with_failure(script):
        out = []
        for statement in real(script):
            if statement.startswith(before):
                out.append(INJECTED)
            out.append(statement)
        return out

    monkeypatch.setattr(migrations, "_statements", with_failure)
    with pytest.raises(sqlite3.OperationalError, match="injected_mid_migration_failure"):
        migrations.run_working_migrations()

    assert migrations.current_version() == 7, "a failed migration recorded a version"
    leftovers = {n for n in names() if n in ALL_OBJECT_NAMES or n.startswith("notes_fts")
                 or n.startswith("note_proposals") or n.startswith("idx_note")
                 or n.startswith("idx_approval")}
    assert not leftovers, f"objects survived a rolled-back migration: {sorted(leftovers)}"
    assert db.get_artifact("a1")["filename"] == "f.md"

    monkeypatch.setattr(migrations, "_statements", real)   # by hand, never monkeypatch.undo()
    assert str(db.working_path()).startswith(str(isolated_data_dir)), "left the temp store"
    migrations.run_working_migrations()
    assert migrations.current_version() == 8
    assert ALL_OBJECT_NAMES <= names()


# --- the FTS index -----------------------------------------------------------


def indexed_rowids(conn):
    return {r[0] for r in conn.execute("SELECT id FROM notes_fts_docsize")}


def active_rowids(conn):
    return {r[0] for r in conn.execute("SELECT rowid FROM notes WHERE status = 'active'")}


def check_index(conn):
    assert indexed_rowids(conn) == active_rowids(conn)
    # FTS5's own comparison of the index against the content (here, the active-notes view)
    conn.execute("INSERT INTO notes_fts(notes_fts, rank) VALUES ('integrity-check', 1)")


def test_the_index_equals_the_active_set_after_every_transition(store):
    """The N13 test: drive every kind of change and assert the FTS rowid set equals the
    active-note set after each one, and that FTS5's own content check passes."""
    with db.transaction() as conn:
        steps = [
            ("insert active", lambda: note(conn, "n1", "oat milk")),
            ("insert retired (never indexed)", lambda: note(conn, "n2", "gone", "retired")),
            ("insert superseded (never indexed)", lambda: note(conn, "n3", "old", "superseded")),
            ("edit text of an active note",
             lambda: conn.execute("UPDATE notes SET text = 'almond milk' WHERE id = 'n1'")),
            ("edit subject of an active note",
             lambda: conn.execute("UPDATE notes SET subject = 'Jodie H' WHERE id = 'n1'")),
            ("a second active note", lambda: note(conn, "n4", "bike lock code", subject="Lyle")),
            ("supersede the first",
             lambda: conn.execute("UPDATE notes SET status = 'superseded' WHERE id = 'n1'")),
            ("retire the second",
             lambda: conn.execute(
                 "UPDATE notes SET status = 'retired', retired_at = 'now' WHERE id = 'n4'")),
        ]
        for label, step in steps:
            step()
            try:
                check_index(conn)
            except Exception as exc:
                raise AssertionError(f"index drifted after: {label}") from exc


def test_search_finds_only_active_notes_and_follows_edits(store):
    with db.transaction() as conn:
        note(conn, "n1", "oat milk in the morning")
        note(conn, "n2", "oat milk long retired", "retired")
        hits = lambda q: {r[0] for r in conn.execute(  # noqa: E731
            "SELECT n.id FROM notes_fts f JOIN notes n ON n.rowid = f.rowid "
            "WHERE notes_fts MATCH ?", (q,))}
        assert hits("oat") == {"n1"}
        conn.execute("UPDATE notes SET text = 'almond' WHERE id = 'n1'")
        assert hits("oat") == set() and hits("almond") == {"n1"}


def test_a_drifted_index_is_detected_by_the_content_check(store):
    """What `scripts.note check` relies on, and what made the FTS content a VIEW."""
    with db.transaction() as conn:
        note(conn, "n1", "oat milk")
        check_index(conn)
        conn.execute("INSERT INTO notes_fts(notes_fts, rowid, subject, text) "
                     "VALUES ('delete', 1, 'Jodie', 'oat milk')")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("INSERT INTO notes_fts(notes_fts, rank) VALUES ('integrity-check', 1)")


def test_why_the_index_is_over_a_view_not_over_notes():
    """The finding behind the design departure, pinned so it is not reopened from memory:
    with the content table being `notes` itself and only active rows indexed, FTS5's content
    comparison fails as soon as one note is retired. Over a view of the active rows it does not."""
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE notes(id TEXT PRIMARY KEY, subject TEXT, text TEXT, status TEXT);
        CREATE VIRTUAL TABLE plain USING fts5(subject, text, content='notes',
                                              content_rowid='rowid');
        INSERT INTO notes VALUES ('a', 'x', 'hello', 'active');
        INSERT INTO notes VALUES ('b', 'y', 'bye', 'retired');
        INSERT INTO plain(rowid, subject, text) VALUES (1, 'x', 'hello');
        CREATE VIEW active AS SELECT rowid AS rid, subject, text FROM notes
            WHERE status='active';
        CREATE VIRTUAL TABLE viewed USING fts5(subject, text, content='active',
                                               content_rowid='rid');
        INSERT INTO viewed(viewed) VALUES ('rebuild');
    """)
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("INSERT INTO plain(plain, rank) VALUES ('integrity-check', 1)")
    c.execute("INSERT INTO viewed(viewed, rank) VALUES ('integrity-check', 1)")


def test_rebuild_and_integrity_check_hold_after_retire_supersede_and_edit_sequences(store):
    """Ruling of 2026-10-02: both repair and detection are tested after the sequences that
    leave non-active notes behind, which is where a plain content table fails."""
    with db.transaction() as conn:
        for i in range(6):
            note(conn, f"n{i}", f"fact number {i} about coffee", subject=f"S{i}")
        conn.execute("UPDATE notes SET status = 'retired', retired_at = 'now' WHERE id = 'n0'")
        conn.execute("UPDATE notes SET status = 'superseded' WHERE id = 'n1'")
        conn.execute("UPDATE notes SET text = 'edited fact' WHERE id = 'n2'")
        conn.execute("UPDATE notes SET status = 'retired' WHERE id = 'n3'")
        conn.execute("UPDATE notes SET subject = 'Renamed' WHERE id = 'n4'")
        check_index(conn)
        # plain integrity-check (the index against itself) and the content comparison
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('integrity-check')")
        # rebuild leaves it exactly right
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
        check_index(conn)
        assert indexed_rowids(conn) == active_rowids(conn) != set()
        # drift it behind the triggers' back: detected, then repaired by rebuild
        conn.execute("INSERT INTO notes_fts(notes_fts, rowid, subject, text) "
                     "SELECT 'delete', rowid, subject, text FROM notes WHERE id = 'n5'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("INSERT INTO notes_fts(notes_fts, rank) VALUES ('integrity-check', 1)")
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
        check_index(conn)


# --- the proposal status rule, against direct SQL ----------------------------


@pytest.mark.parametrize("status", TERMINAL)
def test_a_proposal_cannot_be_inserted_already_decided(world, status):
    with pytest.raises(sqlite3.IntegrityError, match="inserted pending"):
        with db.transaction() as conn:
            proposal(conn, world, status=status, decided_at=db.now_iso())


@pytest.mark.parametrize("status", TERMINAL)
def test_a_pending_proposal_is_decided_by_an_update_in_the_same_transaction(world, status):
    """The auto-apply shape too: insert pending, flip, all in one transaction."""
    with db.transaction() as conn:
        proposal(conn, world)
        decide(conn, "p1", status)
    with db.connection() as conn:
        row = conn.execute("SELECT status, decided_at FROM note_proposals").fetchone()
    assert row["status"] == status and row["decided_at"]


@pytest.mark.parametrize("old", TERMINAL)
@pytest.mark.parametrize("new", ("pending",) + TERMINAL)
def test_every_edge_out_of_a_decided_state_is_refused(world, old, new):
    with db.transaction() as conn:
        proposal(conn, world)
        decide(conn, "p1", old)
    with pytest.raises(sqlite3.DatabaseError, match="final"):
        with db.transaction() as conn:
            conn.execute("UPDATE note_proposals SET status = ?, decided_at = ?, "
                         "resulting_note_id = ? WHERE id = 'p1'",
                         (new, db.now_iso(), "nres" if new in EFFECTIVE else None))
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM note_proposals").fetchone()[0] == old


@pytest.mark.parametrize("assignment", [
    "text = 'something else'", "evidence = '[]'", "call_id = 'c'", "decided_at = 'later'",
    "resulting_note_id = NULL", "untrusted_context = '[]'", "integrity_check = '{}'",
    "subject = 'Lyle'", "user_id = conversation_id", "created_at = 'x'", "action = 'retire'",
])
def test_every_column_of_a_decided_proposal_is_frozen(world, assignment):
    with db.transaction() as conn:
        proposal(conn, world)
        decide(conn, "p1", "approved")
    with pytest.raises(sqlite3.DatabaseError, match="final"):
        with db.transaction() as conn:
            conn.execute(f"UPDATE note_proposals SET {assignment} WHERE id = 'p1'")


def test_a_pending_proposal_can_still_be_changed_before_it_is_decided(world):
    with db.transaction() as conn:
        proposal(conn, world)
        conn.execute("UPDATE note_proposals SET text = 'edited', call_id = 'c1' WHERE id = 'p1'")
        conn.execute("UPDATE note_proposals SET integrity_check = '{\"status\": \"clean\"}' "
                     "WHERE id = 'p1'")


def test_decided_at_is_set_exactly_when_the_proposal_is_not_pending(world):
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            proposal(conn, world, decided_at=db.now_iso())          # pending with a time
    with db.transaction() as conn:
        proposal(conn, world)
    with db.transaction() as conn:
        note(conn, "nres")
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:                              # decided with no time
            conn.execute("UPDATE note_proposals SET status = 'approved', "
                         "resulting_note_id = 'nres' WHERE id = 'p1'")


def test_action_and_target_must_agree(world):
    with db.transaction() as conn:
        note(conn, "n1")
    bad = [
        dict(action="add", target="n1"),                 # an add names no target
        dict(action="add", text=None),                   # an add needs text
        dict(action="revise", target=None),              # a revise needs a target
        dict(action="revise", target="n1", text=None),   # and text
        dict(action="retire", target=None, text=None),   # a retire needs a target
    ]
    for kwargs in bad:
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                proposal(conn, world, **kwargs)
    with db.transaction() as conn:
        proposal(conn, world, "ok1", action="revise", target="n1", text="new")
        proposal(conn, world, "ok2", action="retire", target="n1", text=None)
        proposal(conn, world, "ok3", action="add")


@pytest.mark.parametrize("evidence", [
    None,                 # NOT NULL
    "not json",           # not JSON at all
    "[]",                 # an empty array: an entity proposal always has evidence
    "{}", '{"quote": "x"}',   # an object, not an array
    "null", "true", "3", '"oat milk"',
])
def test_evidence_must_be_a_non_empty_json_array(world, evidence):
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            proposal(conn, world, evidence=evidence)


@pytest.mark.parametrize("evidence", [
    '["oat milk"]', '[{"quote": "oat milk", "message_id": "m1", "tier": "context"}]', "[1, 2]",
])
def test_a_non_empty_array_is_accepted_as_evidence(world, evidence):
    with db.transaction() as conn:
        proposal(conn, world, evidence=evidence)


def test_foreign_keys_hold(world):
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO note_proposals (id, action, subject_kind, subject, text, evidence, "
                "conversation_id, user_message_id, user_id, status, created_at) VALUES "
                "('x', 'add', 'person', 'J', 't', '[]', 'no-such-conversation', ?, ?, "
                "'pending', ?)", (world.msg, world.user, db.now_iso()))
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            note(conn, "n9")
            conn.execute("UPDATE notes SET previous_note_id = 'ghost' WHERE id = 'n9'")


def test_note_vocabularies_are_closed(store):
    for column, bad in (("subject_kind", "self"), ("status", "deleted"), ("origin", "model")):
        values = {"subject_kind": "person", "status": "active", "origin": "entity"}
        values[column] = bad
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO notes (id, subject_kind, subject, text, status, version, "
                    "origin, created_at, last_confirmed_at) VALUES ('z', ?, 's', 't', ?, 1, ?, "
                    "'x', 'x')", (values["subject_kind"], values["status"], values["origin"]))


# --- the approval log --------------------------------------------------------


def log(conn, decision="approved", detail="{}", lid="l1"):
    conn.execute(
        "INSERT INTO approval_log (id, capability, subject_kind, subject_id, decision, "
        "decided_by, detail, created_at) VALUES (?, 'notes', 'note_proposal', 'p1', ?, "
        "'operator', ?, ?)", (lid, decision, detail, db.now_iso()))


def test_the_approval_log_is_append_only_in_the_schema(store):
    with db.transaction() as conn:
        log(conn)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.transaction() as conn:
            conn.execute("UPDATE approval_log SET decision = 'rejected'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM approval_log")
    with db.connection() as conn:
        assert conn.execute("SELECT decision FROM approval_log").fetchone()[0] == "approved"


def test_the_log_detail_is_json_and_required(store):
    for detail in ("not json", None):
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                log(conn, lid="bad", detail=detail)


def test_the_schema_does_not_close_the_decision_vocabulary(store):
    """Ruling of 2026-10-02: the table is shared across capabilities, so the vocabulary is
    validated in code (`db.APPROVAL_DECISIONS`), and a CHECK would need a migration for every
    capability's new decision. If a CHECK on `decision` returns, this fails and says why."""
    with db.transaction() as conn:
        log(conn, "some_future_capabilitys_decision", lid="future")
    with db.connection() as conn:
        [sql] = [r["sql"] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'approval_log'")]
    assert "decision IN" not in sql


NOTES_DECISIONS = {
    "approved", "edited", "rejected", "applied_without_review", "operator_add",
    "operator_revise", "operator_retire", "approval_required_on", "approval_required_off",
}


def test_the_decision_vocabulary_is_validated_in_code(store):
    assert db.APPROVAL_DECISIONS == {"notes": frozenset(NOTES_DECISIONS)}
    for decision in sorted(NOTES_DECISIONS):
        with db.transaction() as conn:
            db.record_approval(conn, capability="notes", subject_kind="note_proposal",
                               subject_id="p1", decision=decision, decided_by="operator",
                               detail={"why": decision})
    with db.connection() as conn:
        rows = conn.execute("SELECT decision, detail, capability FROM approval_log").fetchall()
    assert {r["decision"] for r in rows} == NOTES_DECISIONS
    assert all(r["capability"] == "notes" for r in rows)
    assert {r["detail"] for r in rows} >= {'{"why": "approved"}'}


@pytest.mark.parametrize("capability, decision", [
    ("notes", "applied"),                 # the pre-rename status, not a decision
    ("notes", "approve"),
    ("notes", ""),
    ("research", "approved"),             # a capability with no registered decisions
])
def test_an_unknown_decision_or_capability_is_refused_before_any_write(store, capability, decision):
    with pytest.raises(ValueError):
        with db.transaction() as conn:
            db.record_approval(conn, capability=capability, subject_kind="note_proposal",
                               subject_id="p1", decision=decision, decided_by="operator")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM approval_log").fetchone()[0] == 0


def test_record_approval_writes_inside_the_callers_transaction(store):
    """Every decision is written in the same transaction as the change it records: if the
    caller's transaction rolls back, so does the log row."""
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            db.record_approval(conn, capability="notes", subject_kind="note", subject_id="n1",
                               decision="operator_add", decided_by="operator")
            raise RuntimeError("the change failed")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM approval_log").fetchone()[0] == 0


def test_the_log_is_shared_not_notes_specific(store):
    """Keyed by capability, per N10: nothing in it names Notes but the rows that say so."""
    with db.connection() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(approval_log)")}
    assert {"capability", "subject_kind", "subject_id"} <= columns


# --- deletes (ruling of 2026-10-02) ------------------------------------------


@pytest.mark.parametrize("status", ("active", "superseded", "retired"))
def test_nothing_can_be_deleted_from_notes(store, status):
    with db.transaction() as conn:
        note(conn, "n1", status=status)
    with pytest.raises(sqlite3.DatabaseError, match="never deleted"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM notes WHERE id = 'n1'")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


@pytest.mark.parametrize("status", TERMINAL)
def test_a_decided_proposal_cannot_be_deleted(world, status):
    with db.transaction() as conn:
        proposal(conn, world)
        decide(conn, "p1", status)
    with pytest.raises(sqlite3.DatabaseError, match="not deleted"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM note_proposals WHERE id = 'p1'")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM note_proposals").fetchone()[0] == 1


def test_a_pending_proposal_may_be_deleted(world):
    with db.transaction() as conn:
        proposal(conn, world)
    with db.transaction() as conn:
        conn.execute("DELETE FROM note_proposals WHERE id = 'p1'")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM note_proposals").fetchone()[0] == 0


# --- resulting_note_id (ruling of 2026-10-02) --------------------------------


def test_resulting_note_id_is_non_null_exactly_when_the_proposal_took_effect(world):
    """The note the proposal created or changed: for a retire, the retired note's id."""
    with db.transaction() as conn:
        note(conn, "n1")
    for status in EFFECTIVE:                       # took effect, but no note named
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                proposal(conn, world, "px")
                conn.execute("UPDATE note_proposals SET status = ?, decided_at = 't' "
                             "WHERE id = 'px'", (status,))
    with pytest.raises(sqlite3.IntegrityError):    # rejected, yet a note named
        with db.transaction() as conn:
            proposal(conn, world, "py")
            conn.execute("UPDATE note_proposals SET status = 'rejected', decided_at = 't', "
                         "resulting_note_id = 'n1' WHERE id = 'py'")
    with pytest.raises(sqlite3.IntegrityError):    # pending, yet a note named
        with db.transaction() as conn:
            proposal(conn, world, "pz")
            conn.execute("UPDATE note_proposals SET resulting_note_id = 'n1' WHERE id = 'pz'")
    with pytest.raises(sqlite3.IntegrityError):    # a note that does not exist
        with db.transaction() as conn:
            proposal(conn, world, "pw")
            conn.execute("UPDATE note_proposals SET status = 'approved', decided_at = 't', "
                         "resulting_note_id = 'ghost' WHERE id = 'pw'")
    with db.transaction() as conn:                 # the three that are fine, a retire included
        proposal(conn, world, "ok1", action="retire", target="n1", text=None)
        decide(conn, "ok1", "approved", result="n1")
        proposal(conn, world, "ok2")
        decide(conn, "ok2", "applied_without_review")
        proposal(conn, world, "ok3")
        decide(conn, "ok3", "rejected")


# --- rowids and VACUUM -------------------------------------------------------


def test_the_fts_rowid_approach_matches_chunks_and_nothing_runs_vacuum():
    """`notes` has a TEXT primary key, so its rowid is implicit, exactly as `chunks`' is under
    `chunks_fts`. An implicit rowid is not guaranteed stable across VACUUM, which could silently
    re-pair index entries with the wrong rows. Both indexes depend on that, so nothing in the
    build may run it. Checked on the source, not on a comment."""
    from program import config

    working = (db.SCHEMA_DIR / "working.sql").read_text(encoding="utf-8")
    assert "content_rowid = 'rowid'" in working and "id               TEXT PRIMARY KEY" in working
    assert "content_rowid = 'rid'" in (config.PROJECT_ROOT / "program" / "memory"
                                       / "migrations.py").read_text(encoding="utf-8")
    import re

    # A statement, not a mention: a string literal that starts with VACUUM, or a sqlite3
    # command line naming it. (This module's own docstring explains why, and says the word.)
    statement = re.compile(r"""(?i)(['"]\s*vacuum\b|\bsqlite3\b.*\bvacuum\b)""")
    offenders = []
    files = [p for root in ("program", "scripts", "ops")
             for p in (config.PROJECT_ROOT / root).rglob("*")
             if p.is_file() and p.suffix in {".py", ".sh", ".sql", ".toml", ".yml"}]
    files += [config.PROJECT_ROOT / "start.sh", config.PROJECT_ROOT / "run_server.py"]
    for path in files:
        if path.exists() and statement.search(path.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(path.relative_to(config.PROJECT_ROOT)))
    assert not offenders, f"VACUUM appears in {offenders}; it can re-number implicit rowids"


# --- REPLACE cannot get round the guards (review, 2026-10-02) -----------------
#
# SQLite fires no DELETE trigger for a row removed to resolve an INSERT OR REPLACE,
# REPLACE INTO or UPDATE OR REPLACE conflict unless `recursive_triggers` is on, and this build
# changes no connection pragma. Measured on a bare table before these guards: the row a BEFORE
# DELETE trigger protects was overwritten. So each table refuses an insert that reuses an id
# (or an explicit rowid) and an update that changes an id or rowid.

REPLACING_INSERTS = (
    "INSERT OR REPLACE INTO {t}({cols}) VALUES ({marks})",
    "REPLACE INTO {t}({cols}) VALUES ({marks})",
    "INSERT OR IGNORE INTO {t}({cols}) VALUES ({marks})",
    "INSERT INTO {t}({cols}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET id = id",
)


def _row_values(table, world, ident, **over):
    """A complete, valid row for `table` with the given id, as a (columns, values) pair."""
    now = db.now_iso()
    if table == "notes":
        row = dict(id=ident, subject_kind="person", subject="Jodie", text="REPLACED",
                   status="active", version=1, origin="operator", created_at=now,
                   last_confirmed_at=now)
    elif table == "note_proposals":
        row = dict(id=ident, action="add", subject_kind="person", subject="Jodie",
                   text="REPLACED", evidence='["q"]', conversation_id=world.conv,
                   user_message_id=world.msg, user_id=world.user, status="pending",
                   created_at=now)
    else:
        row = dict(id=ident, capability="notes", subject_kind="note", subject_id="x",
                   decision="REPLACED", decided_by="op", detail="{}", created_at=now)
    row.update(over)
    return list(row), list(row.values())


def _seed(world, table):
    """One existing row per table, in the state its guards protect."""
    with db.transaction() as conn:
        if table == "notes":
            note(conn, "keep", "original")
        elif table == "note_proposals":
            proposal(conn, world, "keep")
            decide(conn, "keep", "approved")        # a DECIDED proposal: frozen and undeletable
        else:
            log(conn, "approved", lid="keep")


def _survivor(table):
    column = {"notes": "text", "note_proposals": "text", "approval_log": "decision"}[table]
    with db.connection() as conn:
        rows = conn.execute(f"SELECT id, {column} FROM {table}").fetchall()
    return [(r[0], r[1]) for r in rows]


@pytest.mark.parametrize("table", ("notes", "note_proposals", "approval_log"))
@pytest.mark.parametrize("template", REPLACING_INSERTS)
def test_an_insert_that_reuses_an_id_cannot_overwrite_a_guarded_row(world, table, template):
    _seed(world, table)
    before = _survivor(table)
    cols, values = _row_values(table, world, "keep")
    sql = template.format(t=table, cols=", ".join(cols), marks=", ".join("?" * len(cols)))

    with pytest.raises(sqlite3.DatabaseError, match="never replaced"):
        with db.transaction() as conn:
            conn.execute(sql, values)

    assert _survivor(table) == before


@pytest.mark.parametrize("table", ("notes", "note_proposals", "approval_log"))
def test_an_insert_that_reuses_an_explicit_rowid_cannot_overwrite_a_guarded_row(world, table):
    _seed(world, table)
    before = _survivor(table)
    with db.connection() as conn:
        rowid = conn.execute(f"SELECT rowid FROM {table}").fetchone()[0]
    cols, values = _row_values(table, world, "different-id")

    with pytest.raises(sqlite3.DatabaseError, match="never replaced"):
        with db.transaction() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table}(rowid, {', '.join(cols)}) "
                f"VALUES (?, {', '.join('?' * len(cols))})", [rowid, *values])

    assert _survivor(table) == before


@pytest.mark.parametrize("table", ("notes", "note_proposals"))
def test_an_update_cannot_change_an_id_to_collide_with_a_guarded_row(world, table):
    """UPDATE OR REPLACE deletes the row it collides with, again without a delete trigger."""
    _seed(world, table)
    with db.transaction() as conn:           # a second, ordinary row (pending, for proposals)
        if table == "notes":
            note(conn, "other", "other text")
        else:
            proposal(conn, world, "other")
    before = _survivor(table)

    with pytest.raises(sqlite3.DatabaseError, match="keeps its id"):
        with db.transaction() as conn:
            conn.execute(f"UPDATE OR REPLACE {table} SET id = 'keep' WHERE id = 'other'")
    with pytest.raises(sqlite3.DatabaseError, match="keeps its id"):
        with db.transaction() as conn:
            conn.execute(f"UPDATE OR REPLACE {table} SET rowid = "
                         f"(SELECT rowid FROM {table} WHERE id = 'keep') WHERE id = 'other'")

    assert _survivor(table) == before


def test_a_replace_attempt_leaves_the_fts_index_consistent(store):
    with db.transaction() as conn:
        note(conn, "keep", "oat milk")
    cols, values = _row_values("notes", None, "keep", text="almond")
    with pytest.raises(sqlite3.DatabaseError):
        with db.transaction() as conn:
            conn.execute(f"INSERT OR REPLACE INTO notes({', '.join(cols)}) VALUES "
                         f"({', '.join('?' * len(cols))})", values)
    with db.transaction() as conn:
        check_index(conn)


def test_ordinary_inserts_still_work_with_the_replace_guards_in_place(world):
    """The guards refuse reuse, not insertion: new ids and auto-assigned rowids are fine."""
    with db.transaction() as conn:
        for i in range(3):
            note(conn, f"n{i}")
            log(conn, lid=f"l{i}")
            proposal(conn, world, f"p{i}")


# --- a retired or superseded note is frozen (review, 2026-10-02) -------------

NOTE_ASSIGNMENTS = [
    "id = 'changed'", "subject_kind = 'topic'", "subject = 'changed'",
    "subject_user_id = NULL", "text = 'changed'", "status = 'active'",
    "status = 'retired'", "status = 'superseded'", "version = 2",
    "previous_note_id = NULL", "origin = 'entity'", "created_at = 'x'",
    "last_confirmed_at = 'x'", "retired_at = 'x'",
    "text = text",                      # even a no-op update is a change attempt
]


@pytest.mark.parametrize("status", ("retired", "superseded"))
@pytest.mark.parametrize("assignment", NOTE_ASSIGNMENTS)
def test_every_column_of_a_retired_or_superseded_note_is_frozen(store, status, assignment):
    with db.transaction() as conn:
        note(conn, "n1", "what it said", status=status)
    with db.connection() as conn:
        before = tuple(conn.execute("SELECT * FROM notes").fetchone())
    with pytest.raises(sqlite3.DatabaseError, match="frozen|keeps its id"):
        with db.transaction() as conn:
            conn.execute(f"UPDATE notes SET {assignment} WHERE id = 'n1'")
    with db.connection() as conn:
        assert tuple(conn.execute("SELECT * FROM notes").fetchone()) == before


def test_an_active_note_can_still_be_edited_superseded_and_retired(store):
    with db.transaction() as conn:
        note(conn, "n1", "first")
        note(conn, "n2", "second")
        conn.execute("UPDATE notes SET text = 'edited', last_confirmed_at = 'later' "
                     "WHERE id = 'n1'")
        conn.execute("UPDATE notes SET status = 'superseded' WHERE id = 'n1'")
        conn.execute("UPDATE notes SET status = 'retired', retired_at = 'now' WHERE id = 'n2'")
    with db.connection() as conn:
        rows = {r["id"]: (r["status"], r["text"]) for r in conn.execute("SELECT * FROM notes")}
    assert rows == {"n1": ("superseded", "edited"), "n2": ("retired", "second")}


def test_a_frozen_note_stays_out_of_the_index_and_the_index_stays_consistent(store):
    with db.transaction() as conn:
        note(conn, "n1", "oat milk")
        conn.execute("UPDATE notes SET status = 'retired' WHERE id = 'n1'")
        check_index(conn)
    with pytest.raises(sqlite3.DatabaseError):
        with db.transaction() as conn:
            conn.execute("UPDATE notes SET status = 'active' WHERE id = 'n1'")
    with db.transaction() as conn:
        check_index(conn)
        assert indexed_rowids(conn) == set()


# --- two facts the guards depend on, pinned (review, 2026-10-02) -------------


def test_new_rowid_is_minus_one_in_a_before_insert_trigger_when_none_is_given(store):
    """The REPLACE guards test `new.rowid <> -1` to tell "no rowid given" from "an explicit
    rowid that may collide". That depends on SQLite's behaviour, so it is pinned on the real
    table with a temporary trigger rather than assumed."""
    with db.transaction() as conn:
        conn.execute("CREATE TEMP TABLE seen (r INTEGER)")
        conn.execute("CREATE TEMP TRIGGER peek BEFORE INSERT ON main.notes "
                     "BEGIN INSERT INTO temp.seen VALUES (new.rowid); END")
        note(conn, "auto-1")
        note(conn, "auto-2")
        conn.execute(
            "INSERT INTO notes (rowid, id, subject_kind, subject, text, status, version, origin, "
            "created_at, last_confirmed_at) VALUES (4242, 'explicit', 'person', 's', 't', "
            "'active', 1, 'operator', 'x', 'x')")
        assert [r[0] for r in conn.execute("SELECT r FROM seen")] == [-1, -1, 4242]


def raw_connection():
    """A plain sqlite3 connection to the working database, with SQLite's own defaults: no
    helper, so `PRAGMA foreign_keys` is OFF."""
    conn = sqlite3.connect(str(db.working_path()))
    conn.row_factory = sqlite3.Row
    return conn


NOTES_WITH_BAD_REFERENCES = [
    "INSERT INTO notes (id, subject_kind, subject, subject_user_id, text, status, version,"
    " origin, created_at, last_confirmed_at) VALUES ('b1', 'person', 's', 'no-such-user', 't',"
    " 'active', 1, 'operator', 'x', 'x')",
    "INSERT INTO notes (id, subject_kind, subject, text, status, version, previous_note_id,"
    " origin, created_at, last_confirmed_at) VALUES ('b2', 'person', 's', 't', 'active', 2,"
    " 'no-such-note', 'operator', 'x', 'x')",
]


def _bad_proposal_sql(world, column):
    cols = dict(id="bp-" + column, action="revise", target_note_id="existing",
                subject_kind="person", subject="s", text="t", evidence='["q"]',
                conversation_id=world.conv, user_message_id=world.msg, user_id=world.user,
                status="pending", created_at="x")
    cols[column] = "no-such-" + column
    names = ", ".join(cols)
    marks = ", ".join(f"'{v}'" for v in cols.values())
    return f"INSERT INTO note_proposals ({names}) VALUES ({marks})"


def _all_bad_inserts(world):
    sqls = list(NOTES_WITH_BAD_REFERENCES)
    for column in ("conversation_id", "user_message_id", "target_note_id"):
        sqls.append(_bad_proposal_sql(world, column))
    return sqls


def test_the_connection_helpers_foreign_keys_setting_is_what_refuses_a_bad_reference(world):
    """Not the schema alone: `FOREIGN KEY` clauses are inert unless the connection turns the
    pragma on, and SQLite's default is off. The same bad inserts are refused through
    `db.connection()` and **accepted** through a plain `sqlite3` connection. So the guarantee
    is the helper's, and any code that opens its own connection loses it."""
    with db.transaction() as conn:
        note(conn, "existing")
    sqls = _all_bad_inserts(world)

    with db.connection() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    for sql in sqls:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with db.transaction() as conn:
                conn.execute(sql)
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM note_proposals").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1

    raw = raw_connection()
    try:
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        for sql in sqls:
            raw.execute(sql)           # accepted: the helper's pragma was the only barrier
        raw.commit()
        assert raw.execute("SELECT COUNT(*) FROM note_proposals").fetchone()[0] == 3
    finally:
        raw.close()


@pytest.mark.parametrize("table", ("notes", "note_proposals", "approval_log"))
@pytest.mark.parametrize("rowid", (-1, -5))
def test_a_negative_rowid_is_refused_so_minus_one_never_hides_an_explicit_rowid(
        world, table, rowid):
    """`new.rowid` is -1 when none is given, so an explicit -1 looks like "none" to the REPLACE
    guards. Closed by refusing any negative rowid; then a row at -1 can never exist to be
    replaced."""
    _seed(world, table)
    before = _survivor(table)
    cols, values = _row_values(table, world, "neg")
    with pytest.raises(sqlite3.DatabaseError, match="negative rowid"):
        with db.transaction() as conn:
            conn.execute(f"INSERT OR REPLACE INTO {table}(rowid, {', '.join(cols)}) "
                         f"VALUES (?, {', '.join('?' * len(cols))})", [rowid, *values])
    assert _survivor(table) == before
