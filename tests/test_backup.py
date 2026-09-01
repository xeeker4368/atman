"""Backup: consistent across both stores, verified, and never destructive."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from program.memory import db
from program.ops import backup


@pytest.fixture
def populated(isolated_data_dir):
    db.init_databases()
    uid = db.create_user("Lyle", role="admin")
    cid = db.start_conversation(uid)
    for i in range(6):
        db.save_message(cid, uid, "user", f"question {i}")
        db.save_message(cid, uid, "assistant", f"answer {i}")
    return isolated_data_dir


# --- What it captures -------------------------------------------------------


def test_it_captures_both_databases_and_a_manifest(populated):
    result = backup.create_backup(include_vectors=False)

    assert (result.directory / "working.db").exists()
    assert (result.directory / "archive.db").exists()
    assert (result.directory / backup.MANIFEST_NAME).exists()
    assert result.verified is True


def test_the_backup_is_a_real_readable_database_not_a_byte_blob(populated):
    """The point of the backup API over a file copy: it opens and queries."""
    result = backup.create_backup(include_vectors=False)

    conn = sqlite3.connect(str(result.directory / "working.db"))
    try:
        rows = conn.execute("SELECT content FROM messages ORDER BY timestamp, id").fetchall()
    finally:
        conn.close()

    assert len(rows) == 12
    assert rows[0][0] == "question 0"


def test_every_database_artifact_passes_integrity_check(populated):
    result = backup.create_backup(include_vectors=False)
    checked = [a for a in result.artifacts if a.integrity_check is not None]
    assert checked
    for artifact in checked:
        assert artifact.integrity_check == "ok"


def test_both_stores_hold_the_same_message_count_in_the_backup(populated):
    """The dual-write invariant has to survive into the copy, not just live."""
    result = backup.create_backup(include_vectors=False)

    counts = {}
    for name in ("working.db", "archive.db"):
        conn = sqlite3.connect(str(result.directory / name))
        try:
            counts[name] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            conn.close()

    assert counts["working.db"] == counts["archive.db"] == 12


def test_a_write_during_the_snapshot_cannot_land_in_one_store_only(populated):
    """The reason both databases are captured under one held lock.

    A writer racing the snapshot must be either wholly in the backup or wholly
    absent from it — never in archive but not working, or the reverse. Two
    independent backups would permit exactly that skew.
    """
    barrier = threading.Barrier(2, timeout=30)
    uid = db.list_users()[0]["id"]
    cid = db.get_open_conversations_with_activity()[0]["id"]
    errors: list[Exception] = []

    def writer():
        try:
            barrier.wait()
            for i in range(20):
                db.save_message(cid, uid, "user", f"racing {i}")
                # Paced deliberately. A zero-pause loop starves the snapshot's
                # own connection out of its lock — a real property of db.py's
                # 10-second busy timeout under sustained contention, flagged
                # separately rather than worked around here. What this test is
                # for is cross-store consistency, which does not need a
                # pathological writer to exercise.
                time.sleep(0.005)
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    barrier.wait()
    result = backup.create_backup(include_vectors=False)
    thread.join(timeout=30)

    assert not errors, f"writer failed: {errors}"

    counts = {}
    for name in ("working.db", "archive.db"):
        conn = sqlite3.connect(str(result.directory / name))
        try:
            counts[name] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            conn.close()

    assert counts["working.db"] == counts["archive.db"], (
        f"cross-store skew captured: {counts}. The snapshot did not hold a "
        f"consistent point-in-time across both databases."
    )


def test_it_captures_the_vector_store_when_one_exists(populated):
    from program.memory import vectors

    store = vectors.get_vector_store()
    store.upsert(
        chunk_id="c1", vector=[0.01] * 768, metadata={"conversation_id": "x"}
    )

    result = backup.create_backup(include_vectors=True)

    chroma = [a for a in result.artifacts if a.name == "chroma"]
    assert chroma, "vector store was not captured"
    assert (result.directory / "chroma").is_dir()


def test_the_vector_store_is_recorded_as_best_effort_not_transactional(populated):
    """The manifest must not overstate what a directory copy guarantees."""
    from program.memory import vectors

    store = vectors.get_vector_store()
    store.upsert(chunk_id="c1", vector=[0.02] * 768, metadata={})

    result = backup.create_backup(include_vectors=True)
    by_name = {a.name: a for a in result.artifacts}

    assert by_name["working.db"].consistency == backup.TRANSACTIONAL
    assert by_name["archive.db"].consistency == backup.TRANSACTIONAL
    assert by_name["chroma"].consistency == backup.BEST_EFFORT
    assert "reconcile_vectors" in by_name["chroma"].note


def test_missing_vector_store_is_a_warning_not_a_failure(populated):
    result = backup.create_backup(include_vectors=True)
    assert result.verified is True
    assert any("no vector store" in w for w in result.warnings)


# --- The manifest -----------------------------------------------------------


def test_the_manifest_records_counts_hashes_and_guarantees(populated):
    result = backup.create_backup(include_vectors=False)
    manifest = backup.read_manifest(result.directory)

    assert manifest["row_counts"]["working.messages"] == 12
    assert manifest["row_counts"]["archive.messages"] == 12
    assert manifest["schema_version"] == 1
    assert manifest["source"]["working_db"] == str(db.working_path())

    artifacts = {a["name"]: a for a in manifest["artifacts"]}
    assert len(artifacts["working.db"]["sha256"]) == 64
    assert backup.TRANSACTIONAL in manifest["consistency"]
    assert backup.BEST_EFFORT in manifest["consistency"]


def test_the_manifest_hash_matches_the_file_on_disk(populated):
    result = backup.create_backup(include_vectors=False)
    manifest = backup.read_manifest(result.directory)
    recorded = {a["name"]: a["sha256"] for a in manifest["artifacts"]}

    assert recorded["working.db"] == backup._sha256(result.directory / "working.db")
    assert recorded["archive.db"] == backup._sha256(result.directory / "archive.db")


def test_the_manifest_says_restore_does_not_exist(populated):
    """Restore is Tier 3 and undesigned; the manifest must not imply otherwise."""
    manifest = backup.read_manifest(backup.create_backup(include_vectors=False).directory)
    assert "Tier 3" in manifest["restore"]


# --- Never destructive ------------------------------------------------------


def test_it_refuses_to_overwrite_an_existing_destination(populated, tmp_path):
    destination = tmp_path / "already-here"
    destination.mkdir()
    (destination / "precious.txt").write_text("do not lose me")

    with pytest.raises(backup.BackupError, match="already exists"):
        backup.create_backup(destination=destination)

    assert (destination / "precious.txt").read_text() == "do not lose me"


def test_two_backups_coexist_rather_than_replacing_each_other(populated, tmp_path):
    first = backup.create_backup(destination=tmp_path / "one", include_vectors=False)
    second = backup.create_backup(destination=tmp_path / "two", include_vectors=False)

    assert first.directory.exists() and second.directory.exists()
    assert first.directory != second.directory


def test_the_source_stores_are_untouched(populated):
    before = db.count_messages()
    before_users = len(db.list_users())

    backup.create_backup(include_vectors=False)

    assert db.count_messages() == before
    assert len(db.list_users()) == before_users


def test_there_is_no_prune_or_rotate_surface(populated):
    """Rotation is the one part of a backup tool that can destroy data.

    Deliberately absent. This pins that rather than leaving it to be noticed.
    """
    surface = {n for n in dir(backup) if not n.startswith("_")}
    for forbidden in ("prune", "rotate", "cleanup", "delete_backup", "restore"):
        assert forbidden not in surface


# --- Failure paths ----------------------------------------------------------


def test_backing_up_a_nonexistent_store_fails_clearly(isolated_data_dir):
    with pytest.raises(backup.BackupError, match="no databases to back up"):
        backup.create_backup()


def test_writers_are_only_blocked_for_the_snapshot_not_afterwards(populated):
    """The read lock must actually be released, or the app deadlocks after one backup."""
    backup.create_backup(include_vectors=False)

    uid = db.list_users()[0]["id"]
    cid = db.get_open_conversations_with_activity()[0]["id"]
    db.save_message(cid, uid, "user", "after the backup")

    assert db.count_messages() == (13, 13)
