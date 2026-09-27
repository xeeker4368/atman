"""The test-isolation guard's open-time layer. B15.

The session guard used to fingerprint the real runtime directories and compare at
the end, which sees only what a test *changed*. A test that lost its isolation and
merely *read* the real store passed silently — and at B3 one did: a test draft
called ``monkeypatch.undo()``, which also reverted the isolation fixture, and ran its
recovery checks against the real ``working.db`` without testing anything.

These tests lose isolation on purpose and assert the guard stops them at the moment
the store is opened. Each one removes its own recorded violation afterwards, since
the session guard re-reports every recorded violation at the end of the run — that
re-report is what makes a violation swallowed by ``except BaseException`` still fail.
"""

from __future__ import annotations

import sqlite3

import pytest

from program import config
from program.memory import db, vectors
from tests import conftest


@pytest.fixture
def consume_violations():
    """Let a test raise violations deliberately without failing the session."""
    before = len(conftest._OPEN_VIOLATIONS)
    yield
    del conftest._OPEN_VIOLATIONS[before:]


def _lose_isolation(monkeypatch):
    for var in ("ANAM_DATA_DIR", "ANAM_BACKUP_DIR", "ANAM_ARTIFACT_DIR",
                "ANAM_WORKSPACE_DIR"):
        monkeypatch.delenv(var, raising=False)
    config.reload()
    vectors.reset_vector_store()


def test_every_test_is_isolated_by_default():
    """Autouse since B15: a test that asks for nothing still runs on a temp store."""
    real = conftest.REAL_DATA_DIR
    assert str(config.data_dir()) != real
    assert not str(db.working_path()).startswith(real)


def test_a_read_only_escape_is_stopped_where_the_store_is_opened(
    monkeypatch, consume_violations
):
    """The case the fingerprint could not see: nothing is written."""
    _lose_isolation(monkeypatch)
    before = len(conftest._OPEN_VIOLATIONS)

    with pytest.raises(conftest.StoreIsolationViolation, match="working.db"):
        config.chat_model()  # settings-backed: reads the settings table

    assert len(conftest._OPEN_VIOLATIONS) == before + 1


def test_the_b3_shape_undo_is_stopped(monkeypatch, consume_violations):
    """`monkeypatch.undo()` reverts the autouse isolation too, because the fixture's
    environment lives on the same monkeypatch. That is exactly the B3 near-miss."""
    monkeypatch.undo()
    config.reload()
    vectors.reset_vector_store()

    with pytest.raises(conftest.StoreIsolationViolation):
        with db.connection() as conn:
            conn.execute("SELECT MAX(version) FROM schema_version").fetchone()


def test_a_violation_caught_by_the_code_is_still_recorded(monkeypatch, consume_violations):
    """The guard derives from BaseException so `except Exception` cannot swallow it;
    code catching BaseException can. Recording it first is what lets the session
    guard still fail the run."""
    _lose_isolation(monkeypatch)
    before = len(conftest._OPEN_VIOLATIONS)

    try:
        sqlite3.connect(str(db.working_path()))
    except BaseException:  # noqa: BLE001 - the swallowing shape, on purpose
        pass

    assert len(conftest._OPEN_VIOLATIONS) == before + 1


def test_the_chroma_client_is_guarded_too(monkeypatch, consume_violations):
    """Chroma 1.x opens its SQLite from Rust, so the sqlite3 wrapper never sees it;
    the client constructor has its own guard."""
    import chromadb

    with pytest.raises(conftest.StoreIsolationViolation, match="chromadb"):
        chromadb.PersistentClient(path=conftest.REAL_CHROMA_DIR)


def test_uri_and_memory_databases(monkeypatch, consume_violations):
    """Read-only URI opens count; in-memory databases are not a store."""
    sqlite3.connect(":memory:").close()
    sqlite3.connect("file:scratch?mode=memory", uri=True).close()

    real = f"file:{conftest.REAL_DATA_DIR}/working.db?mode=ro"
    with pytest.raises(conftest.StoreIsolationViolation):
        sqlite3.connect(real, uri=True)


# --- proving the Chroma wrapper fires, without risking the real directory -------
#
# A mutation check on the Chroma wrapper cannot be run against the real path: with the
# wrapper removed, the test would construct a real `PersistentClient` on the
# production Chroma directory, and Chroma can write when it opens. So the guard is
# pointed at a DECOY. The guard's whole notion of "real" is `_REAL_ROOTS`; adding a
# throwaway directory there makes it indistinguishable from production as far as the
# guard can tell, while being disposable if the guard fails to fire.


@pytest.fixture
def decoy_root(tmp_path_factory, monkeypatch, consume_violations):
    decoy = tmp_path_factory.mktemp("decoy-real-data")
    monkeypatch.setattr(conftest, "_REAL_ROOTS", [*conftest._REAL_ROOTS, str(decoy)])
    return decoy


def test_the_chroma_wrapper_fires_on_a_decoy_real_directory(decoy_root):
    import chromadb

    target = decoy_root / "chromadb"
    with pytest.raises(conftest.StoreIsolationViolation, match="chromadb.PersistentClient"):
        chromadb.PersistentClient(path=str(target))

    assert not target.exists(), "the guard must refuse before Chroma creates anything"


def test_the_sqlite_wrapper_fires_on_a_decoy_real_directory(decoy_root):
    target = decoy_root / "working.db"
    with pytest.raises(conftest.StoreIsolationViolation, match="sqlite3.connect"):
        sqlite3.connect(str(target))

    assert not target.exists(), "refused before SQLite could create the file"


def test_the_decoy_is_not_the_temporary_store_the_test_runs_on(decoy_root):
    """Guard on the guard test: the decoy is outside this test's own isolated
    directory, so a pass cannot come from the isolation fixture instead."""
    assert not str(decoy_root).startswith(str(config.data_dir()))
    assert not str(config.data_dir()).startswith(str(decoy_root))
