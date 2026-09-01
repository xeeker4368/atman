"""Idle-close.

These prove the *outcome* — a conversation actually closed, actually chunked,
and its trailing turns actually retrievable — not that a check function exists.

Embedding is faked: these tests are about which conversations close, not about
embedding. Time is injected rather than slept.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from anam import config
from anam.engine import ollama
from anam.memory import chunking, db, idle, vectors

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(isolated_data_dir, monkeypatch):
    """Initialised store, fake embeddings, null vector store."""

    def embed(text, **kwargs):
        return [0.1] * 768

    monkeypatch.setattr(chunking.ollama, "embed", embed)
    vectors.set_vector_store(vectors.NullVectorStore())
    db.init_databases()
    yield db.create_user("Lyle", role="admin")
    vectors.set_vector_store(None)


def conversation_at(user_id, *, minutes_ago, last_role="assistant", turns=1):
    """A conversation whose last message is ``minutes_ago`` before NOW."""
    conversation_id = db.start_conversation(user_id)
    stamp = NOW - timedelta(minutes=minutes_ago)
    for n in range(turns):
        offset = timedelta(seconds=(turns - n) * 2)
        db.save_message(
            conversation_id, user_id, "user",
            f"question {n} about the harbour " + "detail " * 200,
            timestamp=(stamp - offset - timedelta(seconds=1)).isoformat(),
        )
        if last_role == "assistant" or n < turns - 1:
            db.save_message(
                conversation_id, user_id, "assistant",
                f"answer {n} about small boats " + "more " * 200,
                timestamp=(stamp - offset).isoformat(),
            )
    if last_role == "user":
        db.save_message(
            conversation_id, user_id, "user", "an unanswered question",
            timestamp=stamp.isoformat(),
        )
    else:
        # Ensure the final assistant message carries exactly `stamp`.
        db.save_message(
            conversation_id, user_id, "assistant", "final reply",
            timestamp=stamp.isoformat(),
        )
    return conversation_id


# ---------------------------------------------------------------------------
# Closure and the chunked flag — the actual outcome
# ---------------------------------------------------------------------------


def test_idle_conversation_is_closed_and_marked_chunked(env):
    conversation_id = conversation_at(env, minutes_ago=20)

    result = idle.close_idle_conversations(now=NOW)

    assert result.closed == 1
    assert result.chunked == 1
    row = db.get_conversation(conversation_id)
    assert row["ended_at"] is not None
    assert row["chunked"] == 1


def test_closing_makes_the_trailing_turns_retrievable(env):
    """The entire point: the open trailing group is unindexed until close."""
    conversation_id = conversation_at(env, minutes_ago=20, turns=2)
    assert db.get_conversation_chunks(conversation_id) == []

    idle.close_idle_conversations(now=NOW)

    chunks = db.get_conversation_chunks(conversation_id)
    assert chunks
    text = " ".join(c["text"] for c in chunks)
    assert "final reply" in text
    with db.connection() as conn:
        hits = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'harbour'"
        ).fetchall()
    assert hits


def test_conversation_inside_the_window_is_untouched(env):
    conversation_id = conversation_at(env, minutes_ago=5)

    result = idle.close_idle_conversations(now=NOW)

    assert result.closed == 0
    row = db.get_conversation(conversation_id)
    assert row["ended_at"] is None
    assert row["chunked"] == 0


def test_exactly_at_the_window_closes(env):
    conversation_at(env, minutes_ago=config.idle_close_minutes())
    assert idle.close_idle_conversations(now=NOW).closed == 1


# ---------------------------------------------------------------------------
# The two windows
# ---------------------------------------------------------------------------


def test_unanswered_message_is_not_closed_at_the_short_window(env):
    """A user message with no reply may be a turn still generating."""
    conversation_id = conversation_at(env, minutes_ago=20, last_role="user")

    result = idle.close_idle_conversations(now=NOW)

    assert result.closed == 0
    assert db.get_conversation(conversation_id)["ended_at"] is None


def test_unanswered_message_closes_at_the_grace_window(env):
    conversation_id = conversation_at(env, minutes_ago=35, last_role="user")

    result = idle.close_idle_conversations(now=NOW)

    assert result.closed == 1
    assert db.get_conversation(conversation_id)["chunked"] == 1


def test_completed_turn_uses_the_shorter_window(env):
    """Same age, different last-message role, different outcome."""
    answered = conversation_at(env, minutes_ago=20, last_role="assistant")
    unanswered = conversation_at(env, minutes_ago=20, last_role="user")

    idle.close_idle_conversations(now=NOW)

    assert db.get_conversation(answered)["ended_at"] is not None
    assert db.get_conversation(unanswered)["ended_at"] is None


# ---------------------------------------------------------------------------
# What idle is measured from
# ---------------------------------------------------------------------------


def test_idle_is_measured_from_the_last_message_not_started_at(env):
    """A long-open conversation with a recent message is not idle."""
    conversation_id = db.start_conversation(env)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE conversations SET started_at = ? WHERE id = ?",
            ((NOW - timedelta(days=3)).isoformat(), conversation_id),
        )
    db.save_message(
        conversation_id, env, "user", "recent",
        timestamp=(NOW - timedelta(minutes=2)).isoformat(),
    )
    db.save_message(
        conversation_id, env, "assistant", "also recent",
        timestamp=(NOW - timedelta(minutes=1)).isoformat(),
    )

    assert idle.close_idle_conversations(now=NOW).closed == 0
    assert db.get_conversation(conversation_id)["ended_at"] is None


def test_conversation_with_no_messages_closes_on_started_at(env):
    conversation_id = db.start_conversation(env)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE conversations SET started_at = ? WHERE id = ?",
            ((NOW - timedelta(minutes=30)).isoformat(), conversation_id),
        )

    result = idle.close_idle_conversations(now=NOW)

    assert result.closed == 1
    assert db.get_conversation(conversation_id)["ended_at"] is not None


# ---------------------------------------------------------------------------
# Exclusion and idempotence
# ---------------------------------------------------------------------------


def test_excluded_conversation_is_never_closed(env):
    """Task 2.2 passes the active conversation; a sweep must not close it."""
    active = conversation_at(env, minutes_ago=90)
    other = conversation_at(env, minutes_ago=90)

    result = idle.close_idle_conversations(now=NOW, exclude_conversation_id=active)

    assert db.get_conversation(active)["ended_at"] is None
    assert db.get_conversation(other)["ended_at"] is not None
    assert result.skipped_active == 1


def test_already_closed_conversation_is_not_reclosed(env):
    conversation_id = conversation_at(env, minutes_ago=20)
    idle.close_idle_conversations(now=NOW)
    first_ended = db.get_conversation(conversation_id)["ended_at"]

    later = NOW + timedelta(hours=2)
    result = idle.close_idle_conversations(now=later)

    assert result.closed == 0
    assert db.get_conversation(conversation_id)["ended_at"] == first_ended


def test_dry_run_changes_nothing(env):
    conversation_id = conversation_at(env, minutes_ago=20)

    result = idle.close_idle_conversations(now=NOW, dry_run=True)

    assert result.examined == 1
    assert result.closed == 0
    assert db.get_conversation(conversation_id)["ended_at"] is None


# ---------------------------------------------------------------------------
# Failure behaviour
# ---------------------------------------------------------------------------


def test_chunking_failure_leaves_it_closed_unchunked_and_recoverable(env, monkeypatch):
    """ended_at first, chunking second — so a chunking failure is recoverable."""
    conversation_id = conversation_at(env, minutes_ago=20)

    def boom(text, **kwargs):
        raise ollama.OllamaUnreachable("model down")

    monkeypatch.setattr(chunking.ollama, "embed", boom)

    with pytest.raises(idle.IdleCloseError):
        idle.close_idle_conversations(now=NOW)

    row = db.get_conversation(conversation_id)
    assert row["ended_at"] is not None
    assert row["chunked"] == 0
    recoverable = [r["id"] for r in db.get_unchunked_ended_conversations()]
    assert conversation_id in recoverable


def test_one_failure_does_not_stop_the_sweep(env, monkeypatch):
    """Deliberate deviation from the chunking pipeline's abort-immediately rule.

    One unreachable model must not prevent every other idle conversation from
    closing — but the failure is still raised, not swallowed.
    """
    first = conversation_at(env, minutes_ago=30)
    second = conversation_at(env, minutes_ago=30)

    calls = {"n": 0}
    real_finalise = chunking.finalise_conversation

    def flaky(conversation_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ollama.OllamaUnreachable("down")
        return real_finalise(conversation_id)

    monkeypatch.setattr(idle.chunking, "finalise_conversation", flaky)

    with pytest.raises(idle.IdleCloseError) as exc:
        idle.close_idle_conversations(now=NOW)

    # Both closed; the second still chunked despite the first failing.
    assert db.get_conversation(first)["ended_at"] is not None
    assert db.get_conversation(second)["ended_at"] is not None
    assert calls["n"] == 2
    assert "1 of 2" in str(exc.value)


def test_failures_are_reported_not_swallowed(env, monkeypatch):
    conversation_at(env, minutes_ago=20)

    def boom(conversation_id):
        raise RuntimeError("something specific")

    monkeypatch.setattr(idle.chunking, "finalise_conversation", boom)

    with pytest.raises(idle.IdleCloseError, match="something specific"):
        idle.close_idle_conversations(now=NOW)


# ---------------------------------------------------------------------------
# The floor
# ---------------------------------------------------------------------------


def test_grace_below_the_floor_raises(monkeypatch):
    """A silently clamped value would hide that the operator asked for
    something that closes conversations mid-generation."""
    monkeypatch.setenv("ANAM_IN_FLIGHT_GRACE_MINUTES", "5")
    config.reload()
    try:
        with pytest.raises(config.ConfigError, match="floor"):
            config.in_flight_grace_minutes()
    finally:
        monkeypatch.delenv("ANAM_IN_FLIGHT_GRACE_MINUTES", raising=False)
        config.reload()


def test_grace_at_the_floor_is_accepted(monkeypatch):
    monkeypatch.setenv(
        "ANAM_IN_FLIGHT_GRACE_MINUTES", str(config.IN_FLIGHT_GRACE_FLOOR_MINUTES)
    )
    config.reload()
    try:
        assert config.in_flight_grace_minutes() == config.IN_FLIGHT_GRACE_FLOOR_MINUTES
    finally:
        monkeypatch.delenv("ANAM_IN_FLIGHT_GRACE_MINUTES", raising=False)
        config.reload()


def test_short_window_has_no_floor(monkeypatch):
    """Closing a completed turn early only fragments a conversation."""
    monkeypatch.setenv("ANAM_IDLE_CLOSE_MINUTES", "1")
    config.reload()
    try:
        assert config.idle_close_minutes() == 1
    finally:
        monkeypatch.delenv("ANAM_IDLE_CLOSE_MINUTES", raising=False)
        config.reload()
