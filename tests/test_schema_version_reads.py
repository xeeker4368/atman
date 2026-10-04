"""Piece 4a: a read of the schema version that fails is not a schema version.

``note_admin.require_schema`` caught every ``OperationalError`` and called the store version 0, so
a locked store was refused as "schema version 0; Notes needs 8". ``backup._schema_version`` caught
every one and wrote ``null`` into the manifest. ``db.require_store_not_migrating`` already drew the
line correctly (only "no such table" means no schema); these two now draw it the same way.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from program.memory import db, migrations, note_admin
from program.ops import backup


class _Locked:
    def execute(self, *args):
        raise sqlite3.OperationalError("database is locked")

    def close(self):
        pass


def _read_only_reads_fail(monkeypatch, module):
    real = sqlite3.connect
    monkeypatch.setattr(module.sqlite3, "connect",
                        lambda *a, **k: _Locked() if k.get("uri") else real(*a, **k))


def test_require_schema_reports_a_locked_store_as_unreadable_not_as_version_0(monkeypatch):
    db.init_databases()
    _read_only_reads_fail(monkeypatch, note_admin)

    with pytest.raises(note_admin.AdminError) as raised:
        note_admin.require_schema()

    assert "database is locked" in str(raised.value)
    assert "schema version 0" not in str(raised.value)


def test_require_schema_still_reads_a_file_with_no_version_table_as_version_0():
    db.working_path().parent.mkdir(parents=True, exist_ok=True)
    for path in (db.working_path(), db.archive_path()):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE unrelated (x)")
        conn.commit()
        conn.close()

    with pytest.raises(note_admin.AdminError, match="schema version 0"):
        note_admin.require_schema()


def test_the_manifest_schema_version_comes_from_the_captured_copy(monkeypatch):
    """With every live read failing as locked, the backup still records the true version."""
    db.init_databases()
    real_connection = db.connection

    def locked_after_the_snapshot(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    # The snapshot itself must work; only a read of the live store *for the manifest* would fail.
    original = backup._write_manifest

    def manifest_with_a_locked_live_store(result):
        monkeypatch.setattr(db, "connection", locked_after_the_snapshot)
        try:
            original(result)
        finally:
            monkeypatch.setattr(db, "connection", real_connection)

    monkeypatch.setattr(backup, "_write_manifest", manifest_with_a_locked_live_store)
    result = backup.create_backup()

    manifest = json.loads((result.directory / backup.MANIFEST_NAME).read_text())
    assert manifest["schema_version"] == migrations.current_version()


def test_an_unreadable_copy_fails_the_backup_rather_than_recording_null(monkeypatch):
    db.init_databases()
    _read_only_reads_fail(monkeypatch, backup)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        backup._schema_version(db.working_path().parent)


def test_a_copy_with_no_version_table_gives_none(tmp_path):
    conn = sqlite3.connect(tmp_path / "working.db")
    conn.execute("CREATE TABLE unrelated (x)")
    conn.commit()
    conn.close()

    assert backup._schema_version(tmp_path) is None
