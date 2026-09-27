"""Startup creates and migrates the stores. Merged-queue item 12, plan B4.

Nothing called ``db.init_databases()`` at startup; only the seed script did. On a fresh
data directory the first request created a table-less ``working.db`` and the first
login was an unhandled 500, while ``docs/DB_SCHEMA.md`` said the schema re-ran on every
startup and the go-live wipe procedure depended on it.

These enter the application's lifespan (``with TestClient(...)``), the real startup
path. Most other route tests do not, so they never exercised startup at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from program import config
from program.api.app import create_app
from program.memory import db, migrations

SECRET = "test-signing-secret-that-is-long-enough"


@pytest.fixture
def configured(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    config.reload()
    return isolated_data_dir


def _latest() -> int:
    return max(m.version for m in migrations.MIGRATIONS)


def test_a_fresh_data_directory_is_initialised_and_login_fails_cleanly(configured):
    """The reported defect: first login on a fresh install was a 500."""
    assert not db.working_path().exists(), "precondition: a genuinely fresh store"

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.post("/api/login", json={"name": "Lyle", "password": "x" * 12})

    assert response.status_code == 401
    assert migrations.current_version() == _latest()
    with db.connection() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'")}
    assert {"users", "messages", "chunks", "supersedes", "artifacts"} <= tables


def test_an_existing_store_is_left_as_it_is(configured):
    db.init_databases()
    uid = db.create_user("Lyle", role="admin")
    version = migrations.current_version()

    with TestClient(create_app()):
        pass

    assert migrations.current_version() == version
    assert db.get_user(uid) is not None


def test_a_failing_migration_stops_startup_and_changes_nothing(configured, monkeypatch):
    """B3 made migrations transactional; this is the startup half. A store that
    cannot be brought up to date must stop the server rather than serve on a schema
    no version describes."""
    db.init_databases()
    before = migrations.current_version()

    def explode(conn):
        conn.execute("CREATE TABLE half_applied (x INTEGER)")
        raise RuntimeError("migration failed at startup")

    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        [*migrations.MIGRATIONS, migrations.Migration(before + 1, "explodes", explode)],
    )

    with pytest.raises(RuntimeError, match="migration failed at startup"):
        with TestClient(create_app()):
            pass

    assert migrations.current_version() == before
    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name = 'half_applied'"
        ).fetchone()[0] == 0


def test_a_missing_secret_stops_startup_before_the_store_is_touched(
    isolated_data_dir, monkeypatch
):
    """Ordering: the secret check runs first, so an unconfigured server creates
    nothing on disk."""
    monkeypatch.delenv("ANAM_AUTH_SESSION_SECRET", raising=False)
    config.reload()

    with pytest.raises(config.ConfigError):
        with TestClient(create_app()):
            pass

    assert not db.working_path().exists()
    assert not db.archive_path().exists()
