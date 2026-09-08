"""One turn end to end: persist, retrieve, run, persist. Task 2.2.

A real store, a scripted model. The central test here is not that a message is
written — it is *when*: the fake model asserts, from inside the generation call,
that the user's message is already visible to idle-close and that the
conversation therefore reads as in-flight. That is obligation (b), checked
against the thing that actually depends on it rather than against the ordering
of two lines of code.
"""

from __future__ import annotations

import json

import pytest

from program import config
from program.engine import loop, turn
from program.engine.ollama import OllamaUnreachable
from program.memory import db, idle
from program.settings.permissions import Actor, Role
from program.tools.registry import Tool, ToolRegistry

ECHO = Tool(
    name="scaffold_echo",
    description="TEST-ONLY scaffolding. Returns its argument.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    handler=lambda text: f"echo: {text}",
)


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    """A real two-database store with the two real seed users."""
    config.reload()
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    # Retrieval is exercised by tests/test_retrieval.py against a real store;
    # here it would put a live embedding call inside every turn.
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    return {
        "lyle": Actor(user_id=lyle, name="Lyle", role=Role.ADMIN),
        "jodie": Actor(user_id=jodie, name="Jodie", role=Role.USER),
    }


@pytest.fixture
def model(monkeypatch):
    """Install a scripted model. Each response is one ``message`` dict."""

    def install(*responses, before_reply=None):
        state = {"calls": 0}

        def fake(messages, *, model=None, options=None, tools=None, timeout=None):
            state["calls"] += 1
            if before_reply is not None:
                before_reply()
            return {"message": responses[min(state["calls"] - 1, len(responses) - 1)]}

        monkeypatch.setattr(loop.ollama, "chat", fake)
        return state

    return install


def answer(text):
    return {"role": "assistant", "content": text}


def tool_call(name, args):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": args}}],
    }


# --- Obligation (b): persisted before generation ----------------------------


def test_the_user_message_is_already_persisted_when_generation_starts(store, model):
    """Checked from inside the model call, not by reading the source order."""
    seen: dict = {}

    def inspect():
        rows = db.get_conversation_messages(seen["conversation_id"])
        seen["roles"] = [row["role"] for row in rows]

    model(answer("Answered."), before_reply=inspect)
    conversation_id = db.start_conversation(store["lyle"].user_id)
    seen["conversation_id"] = conversation_id

    turn.handle_user_message(store["lyle"], "Is the fan noise normal?", conversation_id)

    assert seen["roles"] == ["user"], "generation began before the message was stored"


def test_an_in_flight_turn_takes_the_grace_window_not_the_short_one(store, model):
    """The property idle-close reads: last message from the user, mid-turn."""
    observed: dict = {}
    conversation_id = db.start_conversation(store["lyle"].user_id)

    def inspect():
        row = next(
            r
            for r in db.get_open_conversations_with_activity()
            if r["id"] == conversation_id
        )
        observed["last_role"] = row["last_role"]
        observed["window"] = idle._window_for(row["last_role"])

    model(answer("Answered."), before_reply=inspect)
    turn.handle_user_message(store["lyle"], "Still there?", conversation_id)

    assert observed["last_role"] == "user"
    assert observed["window"].total_seconds() / 60 == config.in_flight_grace_minutes()

    # And after the turn, the completed-turn window applies again.
    after = next(
        r for r in db.get_open_conversations_with_activity() if r["id"] == conversation_id
    )
    assert after["last_role"] == "assistant"


def test_a_failed_turn_leaves_the_user_message_standing(store, model, monkeypatch):
    """Not debris: an accurate record, and what the grace window covers."""

    def unreachable(*args, **kwargs):
        raise OllamaUnreachable("nothing is listening")

    monkeypatch.setattr(loop.ollama, "chat", unreachable)
    conversation_id = db.start_conversation(store["lyle"].user_id)

    with pytest.raises(OllamaUnreachable):
        turn.handle_user_message(store["lyle"], "Are you there?", conversation_id)

    rows = db.get_conversation_messages(conversation_id)
    assert [r["role"] for r in rows] == ["user"]


# --- What gets stored -------------------------------------------------------


def test_the_answer_is_persisted_and_returned(store, model):
    model(answer("Grind finer."))

    outcome = turn.handle_user_message(store["lyle"], "Sour espresso?")

    rows = db.get_conversation_messages(outcome.conversation_id)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "Grind finer." == outcome.content
    assert rows[1]["id"] == outcome.assistant_message_id


def test_the_tool_trace_is_stored_as_structured_json_on_the_assistant_row(
    store, model
):
    """Task 3.1 reads this back; it must be data, not a rendered log line."""
    model(tool_call("scaffold_echo", {"text": "hi"}), answer("It said echo: hi"))

    outcome = turn.handle_user_message(
        store["lyle"], "Try the echo tool", registry=ToolRegistry([ECHO])
    )

    row = db.get_conversation_messages(outcome.conversation_id)[1]
    stored = json.loads(row["tool_trace"])
    assert stored == outcome.trace
    assert stored[0]["tool"] == "scaffold_echo"
    assert stored[0]["outcome"] == "ok"
    assert stored[0]["call_id"]


def test_a_turn_with_no_tool_calls_stores_no_trace(store, model):
    """NULL, not an empty list: nothing happened, rather than something empty."""
    model(answer("No tools needed."))

    outcome = turn.handle_user_message(store["lyle"], "Hello")

    row = db.get_conversation_messages(outcome.conversation_id)[1]
    assert row["tool_trace"] is None
    assert outcome.trace == []


def test_both_stores_receive_the_turn(store, model):
    """The dual write is not bypassed by going through the loop."""
    model(answer("Answered."))

    turn.handle_user_message(store["lyle"], "Anything")

    archive_count, working_count = db.count_messages()
    assert archive_count == working_count == 2


# --- Conversations and ownership --------------------------------------------


def test_omitting_a_conversation_id_starts_one(store, model):
    model(answer("Answered."))

    outcome = turn.handle_user_message(store["lyle"], "First thing I've said")

    assert outcome.new_conversation is True
    assert db.get_conversation(outcome.conversation_id)["user_id"] == (
        store["lyle"].user_id
    )


def test_one_user_cannot_speak_into_another_users_conversation(store, model):
    model(answer("Should never be reached."))
    lyles = db.start_conversation(store["lyle"].user_id)

    with pytest.raises(turn.ConversationAccessError):
        turn.handle_user_message(store["jodie"], "Let me in", lyles)

    assert db.get_conversation_messages(lyles) == []


def test_an_unknown_conversation_is_refused_the_same_way_as_an_unowned_one(
    store, model
):
    model(answer("Should never be reached."))

    with pytest.raises(turn.ConversationAccessError):
        turn.handle_user_message(store["lyle"], "Hello", "no-such-conversation")


def test_a_closed_conversation_starts_a_new_one_rather_than_reopening(store, model):
    """Reopening would append turns to a conversation already chunked in full."""
    model(answer("Answered."))
    closed = db.start_conversation(store["lyle"].user_id)
    db.end_conversation(closed)

    outcome = turn.handle_user_message(store["lyle"], "Are you still there?", closed)

    assert outcome.new_conversation is True
    assert outcome.conversation_id != closed
    assert db.get_conversation_messages(closed) == []
    assert db.get_conversation(closed)["ended_at"] is not None


def test_an_empty_message_is_refused_before_anything_is_written(store, model):
    model(answer("Should never be reached."))
    conversation_id = db.start_conversation(store["lyle"].user_id)

    with pytest.raises(turn.EmptyMessageError):
        turn.handle_user_message(store["lyle"], "   ", conversation_id)

    assert db.get_conversation_messages(conversation_id) == []


# --- Degradation ------------------------------------------------------------


def test_a_failing_retrieval_degrades_rather_than_taking_the_turn(
    store, model, monkeypatch
):
    """A person is waiting and nothing can be corrupted by answering without it."""

    def broken(query):
        raise RuntimeError("chroma is unavailable")

    monkeypatch.setattr(turn.retrieval, "search", broken)
    model(answer("Answered from history alone."))

    outcome = turn.handle_user_message(store["lyle"], "What did we say?")

    assert outcome.content == "Answered from history alone."


def test_the_actor_is_recorded_on_both_messages(store, model):
    """Attribution comes from the authenticated actor, not from the payload."""
    model(answer("Answered."))

    outcome = turn.handle_user_message(store["jodie"], "Hello")

    rows = db.get_conversation_messages(outcome.conversation_id)
    assert {r["user_id"] for r in rows} == {store["jodie"].user_id}
