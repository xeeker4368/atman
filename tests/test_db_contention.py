"""Write contention under a held lock, and the retry that survives it.

Design: `docs/DB_CONTENTION_DESIGN.md`.

The trigger is **a lock held longer than `busy_timeout`**, not writer-vs-writer
contention — measured at 0/320 for the latter. `ops/backup.py` creates exactly
that shape by holding a read transaction across both stores for a snapshot.

These tests shrink `busy_timeout` so a ~1s hold reproduces what an 11s hold does
against the shipped 10s timeout, in a fiftieth of the wall time.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from program import config
from program.memory import db

#: Long enough to exceed the shrunken busy_timeout by a wide margin.
HOLD_SECONDS = 1.0
WRITERS = 4
WRITES_EACH = 6


@pytest.fixture
def fast_locks(isolated_data_dir, monkeypatch):
    """A store whose lock timeout is short enough to lose a race quickly."""
    monkeypatch.setenv("ANAM_DB_BUSY_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("ANAM_DB_WRITE_RETRY_BASE_DELAY_SECONDS", "0.01")
    config.reload()
    db.init_databases()
    user_id = db.create_user("Lyle", role="admin")
    conversation_id = db.start_conversation(user_id)
    yield {"user_id": user_id, "conversation_id": conversation_id}
    config.reload()


def _run_contention(context) -> tuple[list[Exception], int]:
    """Hold a snapshot-shaped lock while writers hammer. Returns (errors, ok)."""
    errors: list[Exception] = []
    written = 0
    lock = threading.Lock()
    holding = threading.Event()
    release = threading.Event()

    def holder():
        # Exactly what ops/backup.py does: BEGIN, read both databases to take
        # SHARED on each, then hold while the snapshot runs.
        with db.connection() as conn:
            conn.execute("BEGIN")
            conn.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone()
            conn.execute("SELECT COUNT(*) FROM archive.sqlite_schema").fetchone()
            holding.set()
            release.wait(timeout=30)
            conn.execute("COMMIT")

    def writer(n: int):
        nonlocal written
        holding.wait(timeout=10)
        for i in range(WRITES_EACH):
            try:
                db.save_message(
                    context["conversation_id"], context["user_id"], "user",
                    f"writer {n} message {i}",
                )
                with lock:
                    written += 1
            except Exception as exc:  # noqa: BLE001 - collected for assertion
                with lock:
                    errors.append(exc)

    holder_thread = threading.Thread(target=holder)
    holder_thread.start()
    writers = [threading.Thread(target=writer, args=(n,)) for n in range(WRITERS)]
    for thread in writers:
        thread.start()
    time.sleep(HOLD_SECONDS)
    release.set()
    for thread in writers:
        thread.join(timeout=60)
    holder_thread.join(timeout=60)
    return errors, written


# --- The fix works -----------------------------------------------------------


def test_writes_survive_a_lock_held_longer_than_the_busy_timeout(fast_locks):
    """The bug, fixed: no writer raises, even losing the race repeatedly."""
    monkey_deadline = config.db_write_retry_deadline_seconds()
    assert monkey_deadline > 0, "retry must be enabled for this test to mean anything"

    errors, written = _run_contention(fast_locks)

    assert errors == [], f"{len(errors)} write(s) still failed: {errors[:3]}"
    assert written == WRITERS * WRITES_EACH


def test_cross_store_consistency_holds_through_contention(fast_locks):
    """The guarantee the fix must not weaken, asserted under the failure mode."""
    _run_contention(fast_locks)

    archive_count, working_count = db.count_messages()
    assert archive_count == working_count, (
        f"cross-store skew: archive={archive_count} working={working_count}"
    )


def test_every_write_that_returned_is_actually_present(fast_locks):
    """A retry must not report success without landing the row."""
    _, written = _run_contention(fast_locks)

    rows = db.get_conversation_messages(fast_locks["conversation_id"])
    assert len(rows) == written


def test_no_write_is_duplicated_by_a_retry(fast_locks):
    """The COMMIT-BUSY ambiguity, closed.

    A `COMMIT` that raises SQLITE_BUSY did not commit — it failed to take the
    exclusive lock, the transaction stays open, and `transaction()` rolls it
    back. So a retry cannot land a second copy of a write that already
    succeeded. Asserted rather than argued.
    """
    _run_contention(fast_locks)

    rows = db.get_conversation_messages(fast_locks["conversation_id"])
    contents = [row["content"] for row in rows]
    assert len(contents) == len(set(contents)), "a retry duplicated a write"


# --- The fix reverted: the bug is still there underneath ---------------------


def test_without_retry_contention_actually_fails(fast_locks, monkeypatch):
    """Proof the test reproduces the original bug rather than passing vacuously.

    With the deadline at 0 — retry disabled, which is the pre-fix behaviour —
    the same scenario must produce `database is locked`. A test that only ever
    passes after a fix cannot distinguish "fixed" from "never reproduced".
    """
    monkeypatch.setenv("ANAM_DB_WRITE_RETRY_DEADLINE_SECONDS", "0")
    config.reload()
    assert config.db_write_retry_deadline_seconds() == 0

    errors, _ = _run_contention(fast_locks)

    assert errors, "contention did not reproduce — the test proves nothing"
    assert all(isinstance(exc, sqlite3.OperationalError) for exc in errors)
    assert any("locked" in str(exc).lower() for exc in errors)


def test_even_unretried_failures_leave_the_stores_consistent(fast_locks, monkeypatch):
    """Losing the race was never an integrity problem, only an availability one."""
    monkeypatch.setenv("ANAM_DB_WRITE_RETRY_DEADLINE_SECONDS", "0")
    config.reload()

    errors, written = _run_contention(fast_locks)
    assert errors

    archive_count, working_count = db.count_messages()
    assert archive_count == working_count == written


# --- What is and is not retried ---------------------------------------------


def test_a_non_lock_operational_error_is_not_retried(isolated_data_dir):
    """`no such table` is permanent; retrying it would just delay the error."""
    db.init_databases()
    calls = {"n": 0}

    @db.retry_on_locked
    def boom():
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: nonexistent")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        boom()
    assert calls["n"] == 1


def test_integrity_errors_are_never_retried(isolated_data_dir):
    """`insert_chunk` uses a duplicate-index violation as the arbiter between
    two concurrent writers. Retrying it would spin on a genuine conflict."""
    db.init_databases()
    calls = {"n": 0}

    @db.retry_on_locked
    def conflict():
        calls["n"] += 1
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    with pytest.raises(sqlite3.IntegrityError):
        conflict()
    assert calls["n"] == 1


def test_a_lock_error_is_retried_then_succeeds(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_DB_WRITE_RETRY_BASE_DELAY_SECONDS", "0.001")
    config.reload()
    calls = {"n": 0}

    @db.retry_on_locked
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "landed"

    assert flaky() == "landed"
    assert calls["n"] == 3
    config.reload()


def test_retry_gives_up_at_the_deadline_and_reraises(isolated_data_dir, monkeypatch):
    """A genuinely stuck lock still fails — correctly, and with the same error."""
    monkeypatch.setenv("ANAM_DB_WRITE_RETRY_DEADLINE_SECONDS", "0.2")
    monkeypatch.setenv("ANAM_DB_WRITE_RETRY_BASE_DELAY_SECONDS", "0.01")
    config.reload()

    @db.retry_on_locked
    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        always_locked()
    elapsed = time.monotonic() - started

    assert 0.2 <= elapsed < 3.0, f"gave up after {elapsed:.2f}s"
    config.reload()


def test_each_retry_is_logged(isolated_data_dir, monkeypatch, caplog):
    """A retry that silently succeeds hides that contention is happening."""
    monkeypatch.setenv("ANAM_DB_WRITE_RETRY_BASE_DELAY_SECONDS", "0.001")
    config.reload()
    calls = {"n": 0}

    @db.retry_on_locked
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise sqlite3.OperationalError("database is locked")
        return None

    with caplog.at_level("WARNING"):
        flaky()

    assert "lock contention" in caplog.text
    assert "retrying" in caplog.text
    config.reload()


def test_the_decorator_preserves_the_function_identity():
    """functools.wraps — a decorated write must still be introspectable."""
    assert db.save_message.__name__ == "save_message"
    assert db.save_message.__doc__ and "both stores" in db.save_message.__doc__


def test_migrations_are_deliberately_not_retried():
    """Its transaction body calls arbitrary `migration.apply(conn)` and mutates
    Python state a rollback cannot undo. It also runs once, at startup."""
    from program.memory import migrations

    assert not hasattr(migrations.run_working_migrations, "__wrapped__")
