"""Migration runner behaviour."""

from __future__ import annotations

import pytest

from program.memory import db, migrations


@pytest.fixture
def store(isolated_data_dir):
    db.init_databases()
    return isolated_data_dir


def test_initial_schema_is_recorded_as_version_1(store):
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM schema_version WHERE version = 1").fetchone()
    assert row["name"] == migrations.INITIAL_NAME
    # Not `== INITIAL_VERSION`: real migrations land on top of it, and this test
    # is about version 1 being *recorded*, not about it being the latest.
    assert migrations.current_version() >= migrations.INITIAL_VERSION


def test_running_migrations_again_applies_nothing(store):
    assert migrations.run_working_migrations() == []


def test_no_duplicate_versions_declared(store):
    """Two migrations at the same version produce two schemas claiming one number."""
    migrations.verify_no_duplicate_versions()


def next_free_version() -> int:
    """One past the highest real migration.

    Test migrations used to hard-code 2 and 3, which silently stopped applying
    the moment a real migration 2 landed — `run_working_migrations` skips a
    version already recorded, so the test asserted on an empty result. Deriving
    it means these keep testing the runner as the real list grows.
    """
    return max(
        [migrations.INITIAL_VERSION] + [m.version for m in migrations.MIGRATIONS]
    ) + 1


def test_pending_migration_applies_and_records(store, monkeypatch):
    version = next_free_version()
    def add_column(conn):
        conn.execute("ALTER TABLE settings ADD COLUMN note TEXT")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        [migrations.Migration(version=version, name="add_settings_note",
                              apply=add_column)],
    )

    applied = migrations.run_working_migrations()
    assert applied == ["add_settings_note"]
    assert migrations.current_version() == version

    with db.connection() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(settings)")}
    assert "note" in columns

    # Second run is a no-op.
    assert migrations.run_working_migrations() == []


def test_migrations_apply_in_version_order(store, monkeypatch):
    first = next_free_version()
    second = first + 1
    order: list[int] = []
    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        [
            migrations.Migration(second, "later", lambda c: order.append(second)),
            migrations.Migration(first, "earlier", lambda c: order.append(first)),
        ],
    )
    migrations.run_working_migrations()
    assert order == [first, second]


def test_failed_migration_records_no_version(store, monkeypatch):
    """A half-applied migration that still recorded its version would make the
    database claim a schema it does not have."""

    def explode(conn):
        conn.execute("ALTER TABLE settings ADD COLUMN ok TEXT")
        raise RuntimeError("migration failed midway")

    before = migrations.current_version()
    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        [migrations.Migration(next_free_version(), "explodes", explode)],
    )

    with pytest.raises(RuntimeError):
        migrations.run_working_migrations()

    # Unchanged, rather than equal to INITIAL_VERSION: what matters is that the
    # failed migration recorded nothing, whatever had legitimately applied before.
    assert migrations.current_version() == before
    with db.connection() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(settings)")}
    assert "ok" not in columns


def test_migration_colliding_with_initial_version_is_rejected(store, monkeypatch):
    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        [migrations.Migration(1, "collides", lambda c: None)],
    )
    # Version 1 is already recorded, so it is skipped rather than reapplied.
    assert migrations.run_working_migrations() == []

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        [migrations.Migration(0, "too-low", lambda c: None)],
    )
    with pytest.raises(ValueError, match="collides"):
        migrations.run_working_migrations()


def test_archive_has_no_migration_path(store):
    """The archive's shape is frozen; there is deliberately no runner for it."""
    assert not hasattr(migrations, "run_archive_migrations")


def test_migration_four_adds_the_advisory_column_without_touching_the_verdict(store):
    """Two meanings, two columns. A non-authoritative signal sharing the
    verdict's column would invite a reader to take one for the other — exactly
    migration 3's own argument against folding it into `tool_trace`.

    **`store` was missing here and the test passed with migration 4 deleted.**
    Without the fixture, `init_databases()` ran against whatever store the ambient
    data directory already held — already at version 4, so nothing migrated and the
    assertions inspected a schema built earlier. Found while writing migration 6's
    test, which had inherited the same shape. Verified by deleting the `ALTER
    TABLE` and re-running: it now fails.
    """
    with db.connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}

    assert {"integrity_check", "integrity_advisory"} <= columns
    assert migrations.current_version() >= 4


def test_the_advisory_column_is_not_in_working_sql():
    """Same rule migration 2 established: `working.sql` stays the version 1
    definition, so a fresh store and an existing one reach the same schema by the
    same path — and the migration is exercised on every test run."""
    from program.memory import db

    assert "integrity_advisory" not in (
        db.SCHEMA_DIR / "working.sql").read_text(encoding="utf-8")


def test_migration_six_adds_the_replacement_column_and_keeps_the_cycle_guards(store):
    """Migration 6 recreates `supersedes` to get a NOT NULL column with no default.

    The column is the easy half. The half worth testing is that **both cycle
    triggers survived the recreate** — a recursive-CTE trigger is exactly the kind
    of SQL that looks right when it has been transcribed wrongly, and if one were
    lost nothing else here would notice until correction resolution hung.

    **Takes `store`, and that is not cosmetic.** Written without it, this test
    called `init_databases()` against whatever store the ambient data directory
    already held — already at version 6, so no migration ran and the assertions
    inspected a schema built by *earlier* code. It passed in 0.01 s with
    migration 6's update trigger deliberately deleted. The fixture forces a fresh
    database so the assertions describe what this migration actually produces.
    """
    with db.connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(supersedes)")}
        triggers = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'supersedes'")
        }
        [sql] = [row["sql"] for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'supersedes'")]

    assert "replacement" in columns
    assert triggers == {"supersedes_no_cycle_insert", "supersedes_no_cycle_update"}
    assert migrations.current_version() >= 6
    assert "replacement IN ('replaced', 'contradicted')" in sql
    assert "superseding_message_id <> superseded_message_id" in sql
    assert "UNIQUE (superseding_message_id, superseded_message_id)" in sql


def test_the_replacement_column_is_not_in_working_sql():
    """`working.sql` stays the version 1 definition — and version 1's `supersedes`
    is still the chunk-level table, which migration 5 replaces on every fresh
    store."""
    working = (db.SCHEMA_DIR / "working.sql").read_text(encoding="utf-8")
    assert "replacement" not in working
    assert "superseding_chunk_id" in working, (
        "version 1's chunk-level definition is the historical record; migrations 5 "
        "and 6 are what bring a fresh store up to message granularity"
    )


# --- a multi-statement migration that fails part-way (merged-queue item 8, B3) ---
#
# The existing failure test above uses a migration built from `conn.execute`, which
# was never the broken path. Migrations 5 and 6 used `executescript`, which COMMITs
# the open transaction before running, so a failure part-way left the store
# half-migrated and `db.transaction()`'s ROLLBACK raised "cannot rollback - no
# transaction is active" in place of the real error. These tests drive the failure
# through the real migrations, at the exact point the plan named: after the table
# recreate, before the cycle-guard triggers.

import sqlite3  # noqa: E402

INJECTED = "INSERT INTO injected_mid_migration_failure VALUES (1)"


def _store_at(version, monkeypatch):
    """A fresh store with migrations applied up to and including `version`."""
    full = list(migrations.MIGRATIONS)
    monkeypatch.setattr(
        migrations, "MIGRATIONS", [m for m in full if m.version <= version])
    db.init_databases()
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    assert migrations.current_version() == version


def _inject_before_triggers(monkeypatch):
    """Returns the real splitter so the caller can put it back.

    Restored by hand, never with `monkeypatch.undo()`: `isolated_data_dir` shares
    the same monkeypatch, so undoing it re-points the data directory at the REAL
    store. The first draft of the recovery check below did exactly that and passed
    against the real (already version 6) store without testing anything."""
    real = migrations._statements

    def with_failure(script):
        out = []
        for statement in real(script):
            if statement.startswith("CREATE TRIGGER supersedes_no_cycle_insert"):
                out.append(INJECTED)
            out.append(statement)
        return out

    monkeypatch.setattr(migrations, "_statements", with_failure)
    return real


def _supersedes_shape():
    with db.connection() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(supersedes)")}
        triggers = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'trigger' "
            "AND tbl_name = 'supersedes'")}
        rows = conn.execute("SELECT COUNT(*) FROM supersedes").fetchone()[0]
    return columns, triggers, rows


GUARDS = {"supersedes_no_cycle_insert", "supersedes_no_cycle_update"}


def test_migration_six_failing_before_its_triggers_rolls_back_completely(
    isolated_data_dir, monkeypatch
):
    _store_at(5, monkeypatch)
    uid = db.create_user("Lyle", role="admin")
    cid = db.start_conversation(uid)
    old = db.save_message(cid, uid, "user", "It was Tuesday.")
    new = db.save_message(cid, uid, "user", "Actually Wednesday.")
    with db.transaction() as conn:  # a version-5 link, written in version 5's shape
        conn.execute(
            "INSERT INTO supersedes (id, superseding_message_id, superseded_message_id, "
            "created_at) VALUES ('l1', ?, ?, ?)", (new, old, db.now_iso()))
    before = _supersedes_shape()
    assert "replacement" not in before[0] and before[1] == GUARDS and before[2] == 1

    real = _inject_before_triggers(monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="injected_mid_migration_failure"):
        migrations.run_working_migrations()

    assert migrations.current_version() == 5, "a failed migration recorded a version"
    assert _supersedes_shape() == before, (
        "the table was left dropped, recreated, or without its cycle guards")

    # and the failure is recoverable: the same migration applies cleanly next time
    monkeypatch.setattr(migrations, "_statements", real)
    assert str(db.working_path()).startswith(str(isolated_data_dir)), "left the temp store"
    migrations.run_working_migrations()
    assert migrations.current_version() == max(m.version for m in migrations.MIGRATIONS)
    columns, triggers, _ = _supersedes_shape()
    assert "replacement" in columns and triggers == GUARDS


def test_migration_five_failing_before_its_triggers_rolls_back_completely(
    isolated_data_dir, monkeypatch
):
    _store_at(4, monkeypatch)
    before = _supersedes_shape()
    assert "superseding_chunk_id" in before[0], "precondition: version 4's chunk links"

    _inject_before_triggers(monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="injected_mid_migration_failure"):
        migrations.run_working_migrations()

    assert migrations.current_version() == 4
    assert _supersedes_shape() == before


def test_the_statement_splitter_keeps_trigger_bodies_whole():
    """A naive split on ';' would cut `BEGIN … END;` in half."""
    script = """
        CREATE TABLE t (a INTEGER);
        CREATE TRIGGER tr BEFORE INSERT ON t
        BEGIN
            SELECT RAISE(ABORT, 'no; really');
        END;
        CREATE INDEX i ON t(a);
    """
    statements = migrations._statements(script)

    assert len(statements) == 3
    assert statements[1].startswith("CREATE TRIGGER") and statements[1].endswith("END;")


def test_no_migration_uses_executescript():
    """The structural half: `executescript` commits the open transaction, so it can
    never appear in a migration. Checked on the code, not on a comment — the
    docstrings name it, so this looks for the call."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(migrations))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "executescript"
    ]
    assert calls == []


# --- migration 7: artifacts.integrity_check (REFLECTION_JOURNAL_DESIGN J7) ----


def _artifact_columns():
    with db.connection() as conn:
        return {r["name"]: r for r in conn.execute("PRAGMA table_info(artifacts)")}


def test_migration_seven_adds_a_nullable_verdict_column_to_artifacts(store):
    """Migration 3's semantics on a second table: nullable, no default. A NOT NULL
    or a default would let a row read as checked when nothing checked it."""
    column = _artifact_columns()["integrity_check"]

    assert column["type"] == "TEXT"
    assert column["notnull"] == 0
    assert column["dflt_value"] is None
    assert migrations.current_version() >= 7


def test_the_artifact_verdict_column_is_not_in_working_sql():
    """`working.sql` stays the version 1 definition (migration 2's rule), so the
    migration runs on every fresh store, including every test run."""
    working = (db.SCHEMA_DIR / "working.sql").read_text(encoding="utf-8")
    assert "integrity_check" not in working


def test_migration_seven_failing_after_its_alter_rolls_back_completely(
    isolated_data_dir, monkeypatch
):
    """The ALTER runs, then the migration fails. The column must be gone again,
    no version recorded, and a version-6 row untouched: proof the ALTER ran
    inside the runner's transaction rather than committing on its own."""
    _store_at(6, monkeypatch)
    uid = db.create_user("Lyle", role="admin")
    with db.transaction() as conn:  # a version-6 row, written in version 6's shape
        conn.execute(
            "INSERT INTO artifacts (id, user_id, filename, content_type, size_bytes, "
            "sha256, storage_path, artifact_type, extraction_status, created_at) "
            "VALUES ('a1', ?, 'f.md', 'text/markdown', 3, 'x', 'a1/a1', "
            "'creative_writing', 'extracted', ?)", (uid, db.now_iso()))
    assert "integrity_check" not in _artifact_columns(), "precondition: version 6"

    real = list(migrations.MIGRATIONS)
    seventh = next(m for m in real if m.version == 7)

    def alter_then_fail(conn):
        seventh.apply(conn)
        assert "integrity_check" in {
            r["name"] for r in conn.execute("PRAGMA table_info(artifacts)")}
        conn.execute(INJECTED)

    # Restored by assignment, never monkeypatch.undo() (see _inject_before_triggers).
    monkeypatch.setattr(migrations, "MIGRATIONS", [
        m if m.version != 7 else migrations.Migration(
            version=7, name=seventh.name, apply=alter_then_fail)
        for m in real])
    with pytest.raises(sqlite3.OperationalError, match="injected_mid_migration_failure"):
        migrations.run_working_migrations()

    assert migrations.current_version() == 6, "a failed migration recorded a version"
    assert "integrity_check" not in _artifact_columns(), "the ALTER survived a rollback"
    assert db.get_artifact("a1")["filename"] == "f.md"

    monkeypatch.setattr(migrations, "MIGRATIONS", real)
    assert str(db.working_path()).startswith(str(isolated_data_dir)), "left the temp store"
    migrations.run_working_migrations()
    assert migrations.current_version() == 7
    assert db.get_artifact("a1")["integrity_check"] is None, (
        "an artifact that existed before the gate ran must read 'no verdict', not a value")


def test_insert_artifact_writes_the_verdict_in_the_same_row(store):
    uid = db.create_user("Lyle", role="admin")
    verdict = '{"status": "flagged", "findings": []}'
    for aid, value in (("with", verdict), ("without", None)):
        kwargs = {} if value is None else {"integrity_check": value}
        db.insert_artifact(
            artifact_id=aid, user_id=uid, filename="f.md", content_type="text/markdown",
            size_bytes=1, sha256=aid, storage_path=f"{aid}/{aid}",
            artifact_type="creative_writing", extraction_status="extracted", **kwargs)

    assert db.get_artifact("with")["integrity_check"] == verdict
    assert db.get_artifact("without")["integrity_check"] is None


def test_an_existing_writer_leaves_the_verdict_null(store, monkeypatch):
    """Creative writing does not run the gate (fiction is not a truth claim), so
    its rows must read 'no verdict recorded' rather than anything else."""
    from program.artifacts import writing

    monkeypatch.setattr(
        writing.indexing.ollama, "embed", lambda text, *a, **k: [0.1] * 768)
    uid = db.create_user("Lyle", role="admin")
    stored = writing.store("A short piece about a kettle.", uid)

    assert db.get_artifact(stored.artifact_id)["integrity_check"] is None
