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
