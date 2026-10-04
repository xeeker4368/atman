"""Conversation lifecycle and vector consistency (piece 3.5).

The first three tests here are the **reproductions** from the design
(`docs/DESIGN_3.5_2026-10-04.md`, section 0.1). They were written and committed
*before* the fixes, each failing for the reason the design states:

1. a reply saved after final chunking is never indexed, and its conversation is
   not in the recovery queue either;
2. a chunk whose vector upsert failed is skipped by every later chunking run;
3. the idle sweep closes a conversation that received a message after the sweep
   took its snapshot.

Embedding is faked throughout: none of this is about embedding quality, and the
vector store is a local fake so "has a vector" is directly assertable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from program.memory import chunking, db, idle, vectors

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

LATE_REPLY = "A LATE REPLY about nine bar while pulling a shot."


class FakeStore:
    """A vector store that keeps what it is given and can be made to fail."""

    indexes_vectors = True

    def __init__(self):
        self.vectors: dict[str, list[float]] = {}
        self.fail_upserts = False
        self.upsert_calls = 0

    def upsert(self, chunk_id, vector, metadata):
        self.upsert_calls += 1
        if self.fail_upserts:
            raise RuntimeError("the vector store refused the upsert")
        self.vectors[chunk_id] = vector

    def delete(self, chunk_id):
        self.vectors.pop(chunk_id, None)

    def has(self, chunk_id):
        return chunk_id in self.vectors

    def query(self, vector, n_results=10, ids=None):
        return {"ids": [[]], "distances": [[]], "metadatas": [[]], "documents": [[]]}


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(vectors, "get_vector_store", lambda: fake)
    monkeypatch.setattr(chunking.vectors, "get_vector_store", lambda: fake)
    return fake


@pytest.fixture
def env(isolated_data_dir, monkeypatch, store):
    """Initialised store, fake embeddings, a fake vector store. Yields the user id."""
    monkeypatch.setattr(chunking.ollama, "embed", lambda text, **kw: [0.1] * 768)
    db.init_databases()
    return db.create_user("Lyle", role="admin")


def chunk_texts(conversation_id: str) -> list[str]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT text FROM chunks WHERE conversation_id = ? ORDER BY chunk_index",
            (conversation_id,),
        ).fetchall()
    return [row["text"] for row in rows]


def chunk_ids(conversation_id: str) -> list[str]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id FROM chunks WHERE conversation_id = ? ORDER BY chunk_index",
            (conversation_id,),
        ).fetchall()
    return [row["id"] for row in rows]


def save_or_refusal(*args, **kwargs) -> tuple[str, str]:
    """``("saved", message_id)`` or ``("refused", exception class name)``."""
    try:
        return "saved", db.save_message(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the class is asserted by the caller
        return "refused", type(exc).__name__


def a_conversation(user_id: str, *, minutes_ago: int = 0, turns: int = 2) -> str:
    """A conversation of complete turns, long enough that a group seals."""
    conversation_id = db.start_conversation(user_id)
    stamp = NOW - timedelta(minutes=minutes_ago)
    for n in range(turns):
        offset = timedelta(seconds=(turns - n) * 4)
        db.save_message(
            conversation_id, user_id, "user",
            f"question {n} about descaling the kettle " + "detail " * 200,
            timestamp=(stamp - offset).isoformat(),
        )
        db.save_message(
            conversation_id, user_id, "assistant",
            f"answer {n}: every two months, the water here is hard " + "more " * 200,
            timestamp=(stamp - offset + timedelta(seconds=1)).isoformat(),
        )
    return conversation_id


# ---------------------------------------------------------------------------
# Reproduction 1 — a reply that arrives after the close
# ---------------------------------------------------------------------------


def test_a_reply_saved_after_the_conversation_closed_is_indexed_or_refused(env):
    conversation_id = a_conversation(env, turns=1)
    db.save_message(conversation_id, env, "user", "and when it is hot?")
    db.end_conversation(conversation_id)
    chunking.finalise_conversation(conversation_id)
    assert db.get_conversation(conversation_id)["chunked"] == 1

    outcome, detail = save_or_refusal(conversation_id, env, "assistant", LATE_REPLY)

    if outcome == "refused":
        assert detail == "ConversationClosed", detail
        with db.connection() as conn:
            for table in ("messages", "archive.messages"):
                stored = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE content = ?", (LATE_REPLY,)
                ).fetchone()[0]
                assert stored == 0, f"the refused reply reached {table}"
        return

    # It was saved. Then it must be retrievable, which means some chunk holds it.
    chunking.checkpoint_conversation(conversation_id)
    queued = [row["id"] for row in db.get_unchunked_ended_conversations()]
    assert any(LATE_REPLY in text for text in chunk_texts(conversation_id)), (
        "the reply was saved into a closed conversation and is in no chunk; "
        f"in the recovery queue: {conversation_id in queued}"
    )


# ---------------------------------------------------------------------------
# Reproduction 2 — a chunk row whose vector never landed
# ---------------------------------------------------------------------------


def test_a_rerun_after_a_failed_upsert_writes_the_missing_vector(env, store):
    conversation_id = a_conversation(env, turns=2)
    store.fail_upserts = True
    db.end_conversation(conversation_id)
    with pytest.raises(RuntimeError):
        chunking.finalise_conversation(conversation_id)
    store.fail_upserts = False

    written = chunk_ids(conversation_id)
    assert written, "the reproduction needs at least one chunk row"
    assert not any(store.has(cid) for cid in written)

    chunking.finalise_conversation(conversation_id)

    missing = [cid for cid in chunk_ids(conversation_id) if not store.has(cid)]
    assert not missing, (
        f"{len(missing)} of {len(chunk_ids(conversation_id))} chunk rows still have "
        "no vector after a second run; chunking skipped them on their stored text"
    )


# ---------------------------------------------------------------------------
# Reproduction 3 — the sweep's snapshot race
# ---------------------------------------------------------------------------


def test_a_sweep_does_not_close_a_conversation_that_got_a_message_after_its_snapshot(
    env, monkeypatch
):
    conversation_id = a_conversation(env, minutes_ago=120, turns=1)
    assert [cid for cid, _ in _candidates()] == [conversation_id]

    real_end = db.end_conversation

    def end_after_a_new_message(cid, *args, **kwargs):
        # The person speaks between the sweep's snapshot and its close.
        db.save_message(cid, env, "user", "NEW MESSAGE, mid-sweep")
        return real_end(cid, *args, **kwargs)

    monkeypatch.setattr(db, "end_conversation", end_after_a_new_message)
    monkeypatch.setattr(idle.db, "end_conversation", end_after_a_new_message)

    idle.close_idle_conversations(now=NOW)

    row = db.get_conversation(conversation_id)
    assert row["ended_at"] is None, (
        "the sweep closed a conversation that received a message after its snapshot"
    )
    assert row["chunked"] == 0


def _candidates():
    """``(id, reason)`` pairs, whatever shape ``find_idle_conversations`` returns."""
    found = idle.find_idle_conversations(now=NOW)
    pairs = []
    for item in found:
        if isinstance(item, tuple):
            pairs.append((item[0], item[1]))
        else:
            pairs.append((item.conversation_id, item.reason))
    return pairs


# ---------------------------------------------------------------------------
# The save refuses an ended conversation
# ---------------------------------------------------------------------------


def test_save_message_refuses_an_ended_conversation(env):
    conversation_id = a_conversation(env, turns=1)
    db.end_conversation(conversation_id)

    with pytest.raises(db.ConversationClosed):
        db.save_message(conversation_id, env, "assistant", LATE_REPLY)

    with db.connection() as conn:
        for table in ("messages", "archive.messages"):
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE content = ?", (LATE_REPLY,)
            ).fetchone()[0]
            assert count == 0, f"the refused message reached {table}"
        assert conn.execute(
            "SELECT message_count FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0] == 2, "the refused write still bumped message_count"


def test_save_message_on_a_missing_conversation_still_raises_integrityerror(env):
    """A conversation that does not exist is a different failure, and stays one."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.save_message("no-such-conversation", env, "user", "hello?")


def test_an_open_conversation_is_unaffected(env):
    conversation_id = a_conversation(env, turns=1)
    message_id = db.save_message(conversation_id, env, "assistant", "still open")
    assert message_id
    assert [row["content"] for row in db.get_conversation_messages(conversation_id)][-1] \
        == "still open"


# ---------------------------------------------------------------------------
# A turn whose conversation closes under it
# ---------------------------------------------------------------------------


@pytest.fixture
def turn_env(env, monkeypatch):
    """The turn path over the lifecycle fixture: no retrieval, a scripted model."""
    from program.engine import loop, turn
    from program.settings.permissions import Actor, Role

    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    return turn, loop, Actor(user_id=env, name="Lyle", role=Role.ADMIN)


def test_a_turn_whose_conversation_closes_mid_turn_answers_in_a_new_conversation(
    turn_env, monkeypatch
):
    turn, loop, actor = turn_env
    conversation_id = db.start_conversation(actor.user_id)

    def close_it_mid_generation(*args, **kwargs):
        # What another process's sweep does while the model is answering.
        db.end_conversation(conversation_id)
        chunking.finalise_conversation(conversation_id)
        return {"message": {"role": "assistant", "content": "Here is the answer."}}

    monkeypatch.setattr(loop.ollama, "chat", close_it_mid_generation)

    outcome = turn.handle_user_message(actor, "what pressure?", conversation_id,
                                       situation="")

    assert outcome.content == "Here is the answer."
    assert outcome.conversation_id != conversation_id
    assert outcome.new_conversation is True

    old = [row["content"] for row in db.get_conversation_messages(conversation_id)]
    assert old == ["what pressure?"], "the question stays where it was asked"
    new = [row["content"] for row in db.get_conversation_messages(outcome.conversation_id)]
    assert new == ["Here is the answer."], "the reply goes to the new conversation alone"

    with db.connection() as conn:
        copies = conn.execute(
            "SELECT COUNT(*) FROM archive.messages WHERE content = ?",
            ("what pressure?",),
        ).fetchone()[0]
    assert copies == 1, "the person's message must not be recorded twice"

    # And the reply is chunkable where it landed.
    db.end_conversation(outcome.conversation_id)
    chunking.finalise_conversation(outcome.conversation_id)
    assert any(
        "Here is the answer." in text for text in chunk_texts(outcome.conversation_id)
    )


def test_the_users_message_moves_to_a_new_conversation_if_the_close_beats_it(
    turn_env, monkeypatch
):
    turn, loop, actor = turn_env
    conversation_id = db.start_conversation(actor.user_id)
    monkeypatch.setattr(
        loop.ollama, "chat",
        lambda *a, **k: {"message": {"role": "assistant", "content": "Answered."}},
    )
    real_resolve = turn._resolve_conversation

    def close_after_resolving(actor_, cid):
        resolved = real_resolve(actor_, cid)
        db.end_conversation(resolved[0])
        return resolved

    monkeypatch.setattr(turn, "_resolve_conversation", close_after_resolving)

    outcome = turn.handle_user_message(actor, "hello?", conversation_id, situation="")

    assert outcome.conversation_id != conversation_id
    assert outcome.new_conversation is True
    assert [row["content"] for row in db.get_conversation_messages(conversation_id)] == []
    assert [
        row["content"] for row in db.get_conversation_messages(outcome.conversation_id)
    ] == ["hello?", "Answered."]


# ---------------------------------------------------------------------------
# The sweep: what it does when it does not close
# ---------------------------------------------------------------------------


def test_a_sweep_does_not_chunk_a_conversation_it_did_not_close(env, monkeypatch):
    conversation_id = a_conversation(env, minutes_ago=120, turns=1)
    real_end = db.end_conversation

    def end_after_a_new_message(cid, *args, **kwargs):
        db.save_message(cid, env, "user", "NEW MESSAGE, mid-sweep")
        return real_end(cid, *args, **kwargs)

    monkeypatch.setattr(idle.db, "end_conversation", end_after_a_new_message)

    result = idle.close_idle_conversations(now=NOW)

    assert result.closed == 0
    assert result.chunked == 0
    assert result.skipped_recently_active == 1
    assert chunk_texts(conversation_id) == [], "nothing was chunked"
    assert db.get_conversation(conversation_id)["chunked"] == 0


def test_a_sweep_skips_a_conversation_with_a_running_turn(env):
    from program.engine import turn_locks

    conversation_id = a_conversation(env, minutes_ago=120, turns=1)

    with turn_locks.turn(conversation_id):
        result = idle.close_idle_conversations(now=NOW, is_busy=turn_locks.held)

    assert result.closed == 0
    assert result.skipped_recently_active == 1
    assert db.get_conversation(conversation_id)["ended_at"] is None


def test_an_idle_conversation_with_no_message_since_the_snapshot_still_closes(env):
    """The conditional close must not refuse the ordinary case."""
    conversation_id = a_conversation(env, minutes_ago=120, turns=1)

    result = idle.close_idle_conversations(now=NOW)

    assert (result.closed, result.chunked, result.skipped_recently_active) == (1, 1, 0)
    assert db.get_conversation(conversation_id)["ended_at"] is not None


# ---------------------------------------------------------------------------
# The vector repair, and what it must not do
# ---------------------------------------------------------------------------


def test_an_ordinary_checkpoint_repairs_nothing(env, store, monkeypatch):
    """The repair is for a defect. On an untroubled store it must never fire."""
    embedded: list[str] = []
    monkeypatch.setattr(
        chunking.ollama, "embed",
        lambda text, **kw: (embedded.append(text), [0.1] * 768)[1],
    )
    conversation_id = a_conversation(env, turns=3)
    first = chunking.checkpoint_conversation(conversation_id)
    assert first.chunks_written >= 1
    before = len(embedded)

    again = chunking.checkpoint_conversation(conversation_id)

    assert again.vectors_repaired == 0
    assert again.chunks_written == 0
    assert len(embedded) == before, "a repeat checkpoint embedded something"


def test_the_repair_does_not_run_against_a_store_that_holds_no_vectors(env, monkeypatch):
    """``NullVectorStore.has`` is always False, which must not mean "repair it"."""
    null = vectors.NullVectorStore()
    monkeypatch.setattr(vectors, "get_vector_store", lambda: null)
    monkeypatch.setattr(chunking.vectors, "get_vector_store", lambda: null)
    embedded: list[str] = []
    monkeypatch.setattr(
        chunking.ollama, "embed",
        lambda text, **kw: (embedded.append(text), [0.1] * 768)[1],
    )
    conversation_id = a_conversation(env, turns=3)
    chunking.checkpoint_conversation(conversation_id)
    before = len(embedded)

    result = chunking.checkpoint_conversation(conversation_id)

    assert result.vectors_repaired == 0
    assert len(embedded) == before


def test_a_repaired_chunk_keeps_its_row_and_its_text(env, store):
    """The text check still runs first: a disagreeing row raises, it is not repaired."""
    conversation_id = a_conversation(env, turns=2)
    chunking.finalise_conversation(conversation_id)
    [first_id, *_] = chunk_ids(conversation_id)
    store.vectors.pop(first_id)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE chunks SET text_sha256 = ? WHERE id = ?",
            ("0" * 64, first_id),
        )

    with pytest.raises(chunking.ChunkIntegrityError):
        chunking.checkpoint_conversation(conversation_id)

    assert not store.has(first_id), "a disagreeing row must not be re-embedded"


def test_a_repair_reports_itself(env, store):
    conversation_id = a_conversation(env, turns=2)
    chunking.finalise_conversation(conversation_id)
    written = chunk_ids(conversation_id)
    store.vectors.clear()

    result = chunking.finalise_conversation(conversation_id)

    assert result.vectors_repaired == len(written)
    assert all(store.has(cid) for cid in written)


# ---------------------------------------------------------------------------
# A checkpoint on an ended conversation
# ---------------------------------------------------------------------------


def test_a_checkpoint_on_an_ended_conversation_indexes_the_tail(env):
    conversation_id = a_conversation(env, turns=1)
    db.save_message(conversation_id, env, "assistant", "the trailing answer")
    db.end_conversation(conversation_id)
    assert chunk_texts(conversation_id) == []

    result = chunking.checkpoint_conversation(conversation_id)

    assert any("the trailing answer" in text for text in chunk_texts(conversation_id))
    assert result.marked_chunked is False, "only finalise marks a conversation chunked"
    assert db.get_conversation(conversation_id)["chunked"] == 0


def test_a_checkpoint_on_an_open_conversation_still_leaves_the_tail_open(env):
    conversation_id = a_conversation(env, turns=1)
    db.save_message(conversation_id, env, "user", "a question with no reply yet")

    chunking.checkpoint_conversation(conversation_id)

    assert not any(
        "a question with no reply yet" in text for text in chunk_texts(conversation_id)
    ), "the open trailing group is never indexed"


def test_a_finalise_after_a_checkpoint_of_an_ended_conversation_is_idempotent(env):
    conversation_id = a_conversation(env, turns=2)
    db.end_conversation(conversation_id)
    chunking.checkpoint_conversation(conversation_id)
    before = chunk_ids(conversation_id)

    result = chunking.finalise_conversation(conversation_id)

    assert chunk_ids(conversation_id) == before
    assert result.chunks_written == 0
    assert result.marked_chunked is True


# ---------------------------------------------------------------------------
# The recovery queue
# ---------------------------------------------------------------------------


def a_queued_conversation(user_id: str) -> str:
    """A conversation that was closed and whose final chunking failed."""
    conversation_id = a_conversation(user_id, turns=1)
    db.end_conversation(conversation_id)
    return conversation_id


def test_the_recovery_queue_is_drained(env):
    conversation_id = a_queued_conversation(env)
    assert [row["id"] for row in db.get_unchunked_ended_conversations()] == [
        conversation_id
    ]

    result = idle.drain_recovery_queue(limit=5)

    assert (result.queued, result.chunked) == (1, 1)
    assert db.get_unchunked_ended_conversations() == []
    assert chunk_texts(conversation_id), "the trailing turns are indexed now"


def test_the_drain_is_bounded(env):
    first = a_queued_conversation(env)
    second = a_queued_conversation(env)

    result = idle.drain_recovery_queue(limit=1)

    assert (result.attempted, result.chunked) == (1, 1)
    remaining = [row["id"] for row in db.get_unchunked_ended_conversations()]
    assert len(remaining) == 1 and remaining[0] in {first, second}


def test_a_conversation_that_fails_again_stays_queued(env, monkeypatch):
    conversation_id = a_queued_conversation(env)
    monkeypatch.setattr(
        chunking.ollama, "embed",
        lambda text, **kw: (_ for _ in ()).throw(RuntimeError("model is down")),
    )

    result = idle.drain_recovery_queue(limit=5)

    assert result.chunked == 0
    assert [cid for cid, _ in result.failures] == [conversation_id]
    assert [row["id"] for row in db.get_unchunked_ended_conversations()] == [
        conversation_id
    ]


@pytest.mark.parametrize("order", [("user", "assistant"), ("assistant", "user")])
def test_two_messages_sharing_a_timestamp_pick_the_last_deterministically(env, order):
    """The window the sweep applies must not depend on which of two rows SQLite picks.

    Both insertion orders are driven, so the answer cannot be right by accident:
    whichever message was written second is the one that names the role.
    """
    conversation_id = db.start_conversation(env)
    stamp = (NOW - timedelta(minutes=20)).isoformat()
    for role in order:
        db.save_message(conversation_id, env, role, f"a {role} message", timestamp=stamp)

    for _ in range(3):
        [row] = [
            r for r in db.get_open_conversations_with_activity()
            if r["id"] == conversation_id
        ]
        assert row["last_role"] == order[-1], (
            "with a tie, the row written last decides the role"
        )

    # The consequence: an assistant-last tie takes the completed-turn window and is
    # idle at 20 minutes; a user-last tie takes the in-flight grace and is not.
    expected = [conversation_id] if order[-1] == "assistant" else []
    assert [cid for cid, _ in _candidates()] == expected
