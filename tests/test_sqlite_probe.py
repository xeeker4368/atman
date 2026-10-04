"""Piece 4a: startup checks that this SQLite can build the schema, instead of comparing a version.

Migration 8 needs FTS5, the JSON functions and (for ``scripts/note.py check``) the rank form of
FTS5's ``integrity-check``. A distribution can compile a feature out, and the release that added
each is easy to misremember, so the server builds the real schema in memory and refuses with a
plain message if that fails. No older SQLite was available to run it on; a build missing a feature
is simulated here with SQLite's own authorizer, which makes the real engine refuse the statement.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from program import config
from program.api.app import create_app
from program.memory import capability, db, migrations

SECRET = "test-signing-secret-that-is-long-enough"


class Recording(sqlite3.Connection):
    statements: list[str]

    def execute(self, sql, *args, **kwargs):
        if not hasattr(self, "statements"):
            self.statements = []
        self.statements.append(sql)
        return super().execute(sql, *args, **kwargs)


def _lacking(feature: str):
    """A ``connect`` whose SQLite refuses one feature, as a build without it would."""
    def connect(target):
        conn = sqlite3.connect(target)

        def authorizer(action, arg1, arg2, dbname, source):
            if feature == "fts5" and action == sqlite3.SQLITE_CREATE_VTABLE and arg2 == "fts5":
                return sqlite3.SQLITE_DENY
            if feature == "json" and action == sqlite3.SQLITE_FUNCTION and arg2 == "json_valid":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorizer)
        return conn
    return connect


def test_the_probe_passes_here_and_builds_the_whole_schema():
    result = capability.probe()

    assert result.version == sqlite3.sqlite_version
    assert result.migrations_applied == len(migrations.MIGRATIONS)
    assert {"chunks_fts", "notes_fts", "notes", "note_proposals", "approval_log",
            "active_notes", "supersedes", "artifacts"} <= result.tables


def test_the_probe_runs_every_migration_and_the_notes_index_check():
    seen: list[Recording] = []

    def connect(target):
        conn = sqlite3.connect(target, factory=Recording)
        seen.append(conn)
        return conn

    capability.probe(connect)
    ran = [sql for conn in seen for sql in getattr(conn, "statements", [])]
    assert any("CREATE VIRTUAL TABLE" in sql and "notes_fts" in sql for sql in ran)
    assert any("integrity-check" in sql for sql in ran)


def test_the_probe_touches_no_file(isolated_data_dir):
    before = sorted(p.name for p in isolated_data_dir.iterdir())
    capability.probe()
    assert sorted(p.name for p in isolated_data_dir.iterdir()) == before


@pytest.mark.parametrize("feature", ["fts5", "json"])
def test_a_sqlite_missing_a_feature_is_refused_with_its_own_error(feature, monkeypatch):
    # Values that cannot be mistaken for the running version.
    monkeypatch.setattr(capability, "TESTED_SQLITE_VERSION", "0.0.1-tested")
    monkeypatch.setattr(capability, "KNOWN_FAILING_SQLITE_VERSION", "0.0.0-failing")
    with pytest.raises(capability.SqliteCapabilityError) as raised:
        capability.probe(_lacking(feature))

    message = str(raised.value)
    assert f"version {sqlite3.sqlite_version}" in message
    assert "not authorized" in message  # the engine's own reason for the failing statement
    assert "builds on SQLite 3.51.1 and 0.0.1-tested" in message
    assert "fails on 0.0.0-failing" in message
    assert "Nothing was written to disk" in message


def test_a_syntax_error_from_a_trigger_is_reported_as_what_it_was(monkeypatch):
    """The real failure on SQLite 3.45.1 was ``near "||": syntax error`` in migration 8's trigger,
    which is neither FTS5 nor JSON: the message must carry that text and must not claim a missing
    feature."""
    class OldEngine(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if "CREATE TRIGGER" in sql and "notes" in sql:
                raise sqlite3.OperationalError('near "||": syntax error')
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(capability, "KNOWN_FAILING_SQLITE_VERSION", "0.0.0-failing")
    with pytest.raises(capability.SqliteCapabilityError) as raised:
        capability.probe(lambda target: sqlite3.connect(target, factory=OldEngine))

    message = str(raised.value)
    assert 'failing statement: near "||": syntax error' in message
    assert "needs a SQLite with FTS5" not in message
    assert "fails on 0.0.0-failing" in message


@pytest.fixture
def configured(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    config.reload()
    return isolated_data_dir


def test_startup_refuses_before_touching_the_store(configured, monkeypatch):
    monkeypatch.setattr(capability, "probe", lambda *a, **k: (_ for _ in ()).throw(
        capability.SqliteCapabilityError("no FTS5 here")))

    with pytest.raises(capability.SqliteCapabilityError, match="no FTS5 here"):
        with TestClient(create_app()):
            pass

    assert not db.working_path().exists() and not db.archive_path().exists()


def test_startup_logs_the_running_and_the_tested_versions(configured, caplog, monkeypatch):
    monkeypatch.setattr(capability, "TESTED_SQLITE_VERSION", "0.0.1-tested")
    with caplog.at_level(logging.INFO, logger="program.memory.capability"):
        with TestClient(create_app()):
            pass

    line = next(r.getMessage() for r in caplog.records if "built the schema" in r.getMessage())
    assert sqlite3.sqlite_version in line and "last tested on 0.0.1-tested" in line
