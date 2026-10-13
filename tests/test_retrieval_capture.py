"""The per-turn retrieval capture (decision #33, migration 9).

What each assistant turn's prompt held from retrieval, written after the answer is durable,
read by nothing at runtime. The prompt itself is unchanged: the pinned digests
(``BEFORE_B20_B21``, ``BEFORE_PIECE_2``, ``BEFORE_RENDER_SETTING``) still hold.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from program import config
from program.engine import loop, prompt, turn
from program.memory import db, migrations, retrieval_capture
from program.memory.retrieval import RetrievalResult, RetrievedChunk
from program.ops import backup
from program.settings.permissions import Actor, Role

PROGRAM = Path(__file__).resolve().parent.parent / "program"
TABLES = ("turn_retrieval", "turn_retrieved_records", "turn_capture_status")


def _records(n, size=600, sib=0):
    def chunk(key, text, siblings=(), lexical=None, vector=None):
        return RetrievedChunk(chunk_id=f"c{key}", text=text, created_at="2026-09-30T10:00:00",
                              source_type="conversation", rrf_score=1.0 / (60 + len(key)),
                              siblings=list(siblings), bm25_rank=lexical, vector_rank=vector)
    # Hit i is lexical rank i + 1; every other hit was also found by the vector search.
    return RetrievalResult(query="q", results=[
        chunk(f"{i}", f"R{i}: " + "record text " * (size // 12),
              [chunk(f"{i}s{s}", f"R{i}S{s}: " + "piece text " * (size // 11))
               for s in range(sib)],
              lexical=i + 1, vector=None if i % 2 else n - i)
        for i in range(n)])


def _status(message_id):
    with db.connection() as conn:
        row = conn.execute("SELECT status FROM turn_capture_status WHERE message_id = ?",
                           (message_id,)).fetchone()
    return row["status"] if row else None


# --- The schema -------------------------------------------------------------


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    return Actor(user_id=lyle, name="Lyle", role=Role.ADMIN)


def _columns(table):
    with db.connection() as conn:
        return {r["name"]: r for r in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_nine_creates_both_tables(store):
    assert migrations.current_version() >= 9
    turn_cols, record_cols = _columns("turn_retrieval"), _columns("turn_retrieved_records")
    assert {"message_id", "outcome", "lexical_ran", "lexical_skip_reason", "vector_ran",
            "vector_skip_reason", "records", "block_chars", "continuations_withheld",
            "hits_left_out", "truncated", "captured_at"} == set(turn_cols)
    assert {"message_id", "rank", "chunk_id", "source_type", "label", "score", "lexical_rank",
            "vector_rank", "rendered_chars", "continuations_sent",
            "continuations_withheld"} == set(record_cols)
    assert {"message_id", "conversation_id", "timestamp", "status"} == set(
        _columns("turn_capture_status"))


@pytest.mark.parametrize("columns, values", [
    # Not run, yet a search is said to have run.
    ("outcome, lexical_ran, vector_ran", "'not_run', 1, 1"),
    # Ran, yet neither search's state is recorded.
    ("outcome, lexical_ran, vector_ran", "'empty', NULL, NULL"),
    # One search's state recorded and the other's missing.
    ("outcome, lexical_ran, vector_ran", "'empty', 1, NULL"),
    # Empty, yet records counted.
    ("outcome, lexical_ran, vector_ran, records", "'empty', 1, 1, 2"),
    # An outcome that is not one of the three.
    ("outcome, lexical_ran, vector_ran", "'unknown', 1, 1"),
])
def test_an_inconsistent_capture_row_is_refused(store, columns, values):
    conversation = db.start_conversation(store.user_id)
    message = db.save_message(conversation, store.user_id, "assistant", "Hi.")
    defaults = {"records": "0", "block_chars": "0", "continuations_withheld": "0",
                "hits_left_out": "0", "truncated": "0", "captured_at": "'x'"}
    named = [c.strip() for c in columns.split(",")]
    rest = {k: v for k, v in defaults.items() if k not in named}
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            f"INSERT INTO turn_retrieval (message_id, {columns}, {', '.join(rest)}) "
            f"VALUES (?, {values}, {', '.join(rest.values())})", (message,))


def test_the_tables_are_not_in_working_sql():
    working = (db.SCHEMA_DIR / "working.sql").read_text(encoding="utf-8")
    assert not any(table in working for table in TABLES)


def test_a_capture_must_name_a_real_message(store):
    with pytest.raises(sqlite3.IntegrityError):
        retrieval_capture.record("no-such-message", None, None)


def test_migration_nine_failing_rolls_back_completely(isolated_data_dir, monkeypatch):
    full = list(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", [m for m in full if m.version <= 8])
    db.init_databases()
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    ninth = next(m for m in full if m.version == 9)

    def apply_then_fail(conn):
        ninth.apply(conn)
        conn.execute("INSERT INTO injected_mid_migration_failure VALUES (1)")

    monkeypatch.setattr(migrations, "MIGRATIONS", [
        m if m.version != 9 else migrations.Migration(9, ninth.name, apply_then_fail)
        for m in full])
    with pytest.raises(sqlite3.OperationalError, match="injected_mid_migration_failure"):
        migrations.run_working_migrations()
    assert migrations.current_version() == 8
    assert not _columns("turn_retrieval"), "the CREATE survived a rollback"

    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    migrations.run_working_migrations()
    assert migrations.current_version() == 9


# --- The renderer's account -------------------------------------------------


def test_the_account_describes_exactly_the_text_sent(isolated_data_dir):
    config.reload()
    result = _records(3, sib=2)
    rendered = prompt.render_records(result)

    assert rendered.text == prompt.render_retrieved(result)
    assert [r.rank for r in rendered.records] == [1, 2, 3]
    assert [r.chunk_id for r in rendered.records] == ["c0", "c1", "c2"]
    for record in rendered.records:
        assert record.label.startswith(f"[record {record.rank} · ")
        assert record.label in rendered.text.split("\n")
        assert record.continuations_sent == 2 and record.continuations_withheld == 0
        assert record.score == result.results[record.rank - 1].rrf_score
        assert record.lexical_rank == result.results[record.rank - 1].bm25_rank
        assert record.vector_rank == result.results[record.rank - 1].vector_rank
    # Every record's characters are in the text, plus the header and the separators.
    assert sum(r.rendered_chars for r in rendered.records) < len(rendered.text)
    assert not rendered.truncated


def test_a_smaller_cap_is_recorded_as_truncation(isolated_data_dir):
    config.reload()
    rendered = prompt.render_records(_records(3, sib=2), cap=0)
    assert rendered.continuations_withheld == 6
    assert rendered.truncated
    assert all(r.continuations_sent == 0 and r.continuations_withheld == 2
               for r in rendered.records)


def test_hits_left_out_for_room_are_listed_with_no_label(isolated_data_dir):
    config.reload()
    rendered = prompt.render_records(_records(4), cap=0, keep_hits=2)
    assert rendered.hits_left_out == 2 and rendered.truncated
    assert [r.label is None for r in rendered.records] == [False, False, True, True]
    assert [r.rendered_chars == 0 for r in rendered.records] == [False, False, True, True]

    none_kept = prompt.render_records(_records(2), cap=0, keep_hits=0)
    assert none_kept.hits_left_out == 2 and len(none_kept.records) == 2
    assert none_kept.text == prompt.render_retrieved(_records(2), cap=0, keep_hits=0)


def test_no_retrieval_renders_nothing_and_records_nothing():
    for empty in (None, RetrievalResult(query="q")):
        rendered = prompt.render_records(empty)
        assert rendered.text == "" and rendered.records == () and not rendered.truncated


# --- The turn writes it -----------------------------------------------------


def _scripted(monkeypatch, seen):
    def fake(messages, *, model=None, options=None, tools=None, timeout=None):
        seen.append(messages[0]["content"])
        return {"message": {"role": "assistant", "content": "Answered."}}
    monkeypatch.setattr(loop.ollama, "chat", fake)


def test_a_turn_records_what_its_prompt_held(store, monkeypatch):
    result = _records(3, sib=1)
    monkeypatch.setattr(turn.retrieval, "search", lambda query: result)
    seen: list[str] = []
    _scripted(monkeypatch, seen)

    outcome = turn.handle_user_message(store, "What do you remember?", situation="")
    captured = retrieval_capture.read(outcome.assistant_message_id)

    assert captured["turn"]["outcome"] == "records" and _status(
        outcome.assistant_message_id) == "records"
    assert (captured["turn"]["lexical_ran"], captured["turn"]["vector_ran"]) == (1, 1)
    assert captured["turn"]["records"] == 3
    assert captured["turn"]["truncated"] == 0
    system = seen[0]  # the turn's call; the gate's classifier call comes after it
    assert system.endswith(prompt.render_retrieved(result))
    assert captured["turn"]["block_chars"] == len(prompt.render_retrieved(result))
    for row in captured["records"]:
        assert row["label"] in system
        assert row["source_type"] == "conversation"
    assert [r["chunk_id"] for r in captured["records"]] == ["c0", "c1", "c2"]
    assert [r["lexical_rank"] for r in captured["records"]] == [1, 2, 3]
    assert [r["vector_rank"] for r in captured["records"]] == [3, None, 1]
    # The user's message gets no capture: it is the assistant turn that was answered from it.
    assert retrieval_capture.read(outcome.user_message_id) is None


def test_a_turn_whose_retrieval_failed_says_so(store, monkeypatch):
    monkeypatch.setattr(turn.retrieval, "search",
                        lambda query: (_ for _ in ()).throw(RuntimeError("down")))
    _scripted(monkeypatch, [])
    outcome = turn.handle_user_message(store, "Hello.", situation="")
    captured = retrieval_capture.read(outcome.assistant_message_id)
    assert captured["turn"]["outcome"] == "not_run"
    assert captured["turn"]["lexical_ran"] is None and captured["turn"]["vector_ran"] is None
    assert captured["turn"]["records"] == 0 and captured["records"] == []
    assert _status(outcome.assistant_message_id) == "not_run"


def test_retrieval_that_found_nothing_is_not_retrieval_that_did_not_run(store, monkeypatch):
    monkeypatch.setattr(turn.retrieval, "search", lambda query: RetrievalResult(query=query))
    _scripted(monkeypatch, [])
    outcome = turn.handle_user_message(store, "Hello.", situation="")
    captured = retrieval_capture.read(outcome.assistant_message_id)
    assert captured["turn"]["outcome"] == "empty" and captured["turn"]["records"] == 0
    assert (captured["turn"]["lexical_ran"], captured["turn"]["vector_ran"]) == (1, 1)
    assert _status(outcome.assistant_message_id) == "empty"


def test_a_search_that_did_not_run_is_recorded_with_its_reason(store, monkeypatch):
    result = _records(2)
    result.vector.ran = False
    result.vector.skip_reason = "the query could not be embedded: refused"
    monkeypatch.setattr(turn.retrieval, "search", lambda query: result)
    _scripted(monkeypatch, [])
    outcome = turn.handle_user_message(store, "Hello.", situation="")
    row = retrieval_capture.read(outcome.assistant_message_id)["turn"]
    assert row["outcome"] == "records"
    assert (row["lexical_ran"], row["lexical_skip_reason"]) == (1, None)
    assert (row["vector_ran"], row["vector_skip_reason"]) == (
        0, "the query could not be embedded: refused")


def test_a_failing_capture_does_not_fail_the_turn(store, monkeypatch):
    monkeypatch.setattr(turn.retrieval, "search", lambda query: _records(1))
    _scripted(monkeypatch, [])

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(turn.retrieval_capture, "record", broken)

    outcome = turn.handle_user_message(store, "Hello.", situation="")
    assert outcome.content == "Answered."
    assert retrieval_capture.read(outcome.assistant_message_id) is None
    # Visibly uncaptured, not empty.
    assert _status(outcome.assistant_message_id) == "not_captured"


def test_a_capture_that_fails_mid_write_leaves_no_partial_row(store):
    """The second table's insert failing rolls the first back: not_captured, never empty."""
    conversation = db.start_conversation(store.user_id)
    message = db.save_message(conversation, store.user_id, "assistant", "Hi.")
    twice = prompt.RenderedRecord(rank=1, chunk_id="c0", source_type=None, score=0.1,
                                  label="[record 1]", rendered_chars=10,
                                  continuations_sent=0, continuations_withheld=0)
    rendered = prompt.RenderedRecords(text="x" * 10, records=(twice, twice))
    with pytest.raises(sqlite3.IntegrityError):
        retrieval_capture.record(message, rendered, _records(1))
    assert retrieval_capture.read(message) is None
    assert _status(message) == "not_captured"


def test_a_turn_older_than_the_capture_is_not_counted_as_missed(store):
    conversation = db.start_conversation(store.user_id)
    old = db.save_message(conversation, store.user_id, "assistant", "Hi.",
                          timestamp="2026-01-01T00:00:00+00:00")
    assert _status(old) == "before_capture"
    # The user's messages are not turns the capture covers.
    asked = db.save_message(conversation, store.user_id, "user", "Hello.")
    assert _status(asked) is None


def test_the_capture_is_in_the_backup(store, monkeypatch):
    """working.db is copied whole by SQLite's backup API, so the new tables come with it."""
    monkeypatch.setattr(turn.retrieval, "search", lambda query: _records(2))
    _scripted(monkeypatch, [])
    outcome = turn.handle_user_message(store, "Hello.", situation="")

    made = backup.create_backup(include_vectors=False)
    conn = sqlite3.connect(f"file:{made.directory / 'working.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT chunk_id FROM turn_retrieved_records WHERE message_id = ? "
                            "ORDER BY rank", (outcome.assistant_message_id,)).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["c0", "c1"]


# --- Nothing reads it at runtime --------------------------------------------


def test_nothing_in_program_reads_the_capture():
    """Only the migration and the writer name the tables; nothing calls ``read``."""
    allowed = {PROGRAM / "memory" / "migrations.py", PROGRAM / "memory" / "retrieval_capture.py"}
    naming, reading = [], []
    for path in PROGRAM.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path not in allowed and any(table in text for table in TABLES):
            naming.append(path)
        if path != PROGRAM / "memory" / "retrieval_capture.py" and re.search(
                r"retrieval_capture\.read\b", text):
            reading.append(path)
    assert naming == [] and reading == []


# --- The review script ------------------------------------------------------


def test_the_review_script_prints_a_turns_records_read_only(store, monkeypatch, capsys):
    from scripts import show_retrieval

    monkeypatch.setattr(turn.retrieval, "search", lambda query: _records(2, sib=1))
    _scripted(monkeypatch, [])
    outcome = turn.handle_user_message(store, "Where were we?", situation="")
    path = db.working_path()
    before = (path.stat().st_size, path.stat().st_mtime_ns)

    assert show_retrieval.main([outcome.assistant_message_id[:12], "--db", str(path),
                                "--text"]) == 0
    printed = capsys.readouterr().out
    captured = retrieval_capture.read(outcome.assistant_message_id)
    assert "asked        Where were we?" in printed
    assert "retrieval ran and returned records" in printed
    assert "searches     lexical ran; vector ran" in printed
    assert "2 record(s)" in printed and "not truncated" in printed
    assert "lexical rank 1  vector rank 2" in printed and "lexical rank 2  vector rank -" in printed
    for row in captured["records"]:
        assert row["label"] in printed and row["chunk_id"] in printed
    assert "(no longer in chunks)" in printed  # the probe chunks were never stored
    assert (path.stat().st_size, path.stat().st_mtime_ns) == before


def test_the_review_script_says_when_a_turn_was_not_captured(store, monkeypatch, capsys):
    from scripts import show_retrieval

    conversation = db.start_conversation(store.user_id)
    db.save_message(conversation, store.user_id, "user", "Hello.")
    old = db.save_message(conversation, store.user_id, "assistant", "Hi.")
    assert show_retrieval.main([old, "--db", str(db.working_path())]) == 0
    assert "not captured" in capsys.readouterr().out


def test_the_review_script_counts_every_turns_status(store, monkeypatch, capsys):
    from scripts import show_retrieval

    results = iter([_records(1), RetrievalResult(query="q"), None])

    def search(query):
        result = next(results)
        if result is None:
            raise RuntimeError("down")
        return result
    monkeypatch.setattr(turn.retrieval, "search", search)
    _scripted(monkeypatch, [])
    for text in ("One.", "Two.", "Three."):
        turn.handle_user_message(store, text, situation="")
    monkeypatch.setattr(turn.retrieval, "search", lambda query: _records(1))
    monkeypatch.setattr(turn.retrieval_capture, "record",
                        lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    missed = turn.handle_user_message(store, "Four.", situation="").assistant_message_id

    assert show_retrieval.main(["--status", "--db", str(db.working_path())]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert [line.split() for line in printed[:5]] == [
        ["records", "1"], ["empty", "1"], ["not_run", "1"], ["not_captured", "1"],
        ["before_capture", "0"]]
    assert printed[5].startswith(f"  not captured: {missed}")


def test_the_review_script_does_not_import_program():
    source = (PROGRAM.parent / "scripts" / "show_retrieval.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import) program\b", source, re.M)
    assert "mode=ro" in source


def test_the_writer_is_retried_on_a_lock_error_like_notes_writers(store, monkeypatch):
    conversation = db.start_conversation(store.user_id)
    message = db.save_message(conversation, store.user_id, "assistant", "Hi.")
    real, calls = db.transaction, []

    def locked_once():
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real()
    monkeypatch.setattr(db, "transaction", locked_once)
    monkeypatch.setattr(config, "db_write_retry_base_delay_seconds", lambda: 0.0)

    retrieval_capture.record(message, None, RetrievalResult(query="q"))
    assert len(calls) == 2
    assert retrieval_capture.read(message)["turn"]["outcome"] == "empty"
