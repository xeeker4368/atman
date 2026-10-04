"""Commands other than the server never migrate a store (`db.require_store_not_migrating`).

Demonstrated before this guard existed (2026-10-03): `write_journal --show-records` took a scratch
store from schema version 6 to 8, because the script called `db.init_databases()` first. The real
store is at version 6 until the server's first startup, so the same command would have migrated it.
"""

from __future__ import annotations

import sqlite3
import sys

import pytest

from program.engine import ollama
from program.memory import chunking, db, migrations
from program.ops import seed
from scripts import seed_dataset, write_journal


def _store_at(version, monkeypatch):
    """A fresh store with migrations applied up to `version` (tests/test_migrations.py's approach).
    Restored by hand, never with `monkeypatch.undo()`, which would also undo the data directory."""
    full = list(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", [m for m in full if m.version <= version])
    db.init_databases()
    monkeypatch.setattr(migrations, "MIGRATIONS", full)


def _version_read_only() -> int:
    conn = sqlite3.connect(f"file:{db.working_path()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        conn.close()


def _journal(monkeypatch, *args) -> int:
    monkeypatch.setattr(sys, "argv", ["write_journal", *args])
    return write_journal.main()


@pytest.mark.parametrize("args", [("--show-records", "2026-10-01"),
                                  ("--dry-run", "--date", "2026-10-01")])
def test_write_journal_refuses_an_older_store_and_leaves_it_at_its_version(
        monkeypatch, capsys, args):
    _store_at(6, monkeypatch)
    assert _version_read_only() == 6

    assert _journal(monkeypatch, *args) == 2
    assert "never migrates" in capsys.readouterr().err
    assert _version_read_only() == 6


def test_seed_refuses_an_older_store_and_leaves_it_at_its_version(monkeypatch):
    _store_at(6, monkeypatch)

    with pytest.raises(db.StoreWouldMigrateError, match="never migrates"):
        seed.seed()
    assert _version_read_only() == 6


def test_seed_still_creates_a_fresh_store(monkeypatch):
    def fake_embed(text, **kwargs):
        return [0.01] * 768

    monkeypatch.setattr(ollama, "embed", fake_embed)
    monkeypatch.setattr(chunking.ollama, "embed", fake_embed)
    assert not db.working_path().exists()

    result = seed.seed()

    assert result.conversations and result.messages > 0
    assert _version_read_only() == migrations.current_version()


def test_a_store_at_the_latest_version_passes(monkeypatch, capsys):
    db.init_databases()
    latest = _version_read_only()

    db.require_store_not_migrating()
    assert _journal(monkeypatch, "--show-records", "2026-10-01") == 0
    assert _version_read_only() == latest


def test_a_store_newer_than_the_code_is_refused():
    db.init_databases()
    with db.transaction() as conn:
        conn.execute("INSERT INTO schema_version (version, name, applied_at) VALUES (999, 'x', ?)",
                     (db.now_iso(),))

    with pytest.raises(db.StoreWouldMigrateError, match="above this code's version"):
        db.require_store_not_migrating()


def test_seed_dataset_prints_one_refused_line_and_exits_non_zero(monkeypatch, capsys):
    _store_at(6, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["seed_dataset"])

    assert seed_dataset.main() == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("refused: ") and "never migrates" in err[0]
    assert _version_read_only() == 6


def test_a_file_with_no_schema_version_table_reads_as_version_0_and_is_refused():
    db.working_path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db.working_path())
    conn.execute("CREATE TABLE unrelated (x)")
    conn.commit()
    conn.close()

    with pytest.raises(db.StoreWouldMigrateError, match="schema version 0, below"):
        db.require_store_not_migrating()


def test_any_other_read_error_is_refused_with_its_own_message(monkeypatch):
    """Locked, unable to open, disk I/O: none of them is a version, so none is reported as one."""
    db.init_databases()

    class Broken:
        def execute(self, *args):
            raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            pass

    real = sqlite3.connect
    monkeypatch.setattr(db.sqlite3, "connect",
                        lambda *a, **k: Broken() if k.get("uri") else real(*a, **k))

    with pytest.raises(db.StoreWouldMigrateError) as exc:
        db.require_store_not_migrating()
    assert "disk I/O error" in str(exc.value)
    assert "is at schema version" not in str(exc.value)   # no version is claimed
