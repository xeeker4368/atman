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
import logging
import sqlite3

import pytest

from program import config
from program.engine import loop, turn
from program.engine.ollama import OllamaUnreachable
from program.integrity import corrections
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


# --- Nothing after the answer is durable may fail the turn -------------------
#
# Three writes run after `save_message` has put the assistant's answer in both
# stores: the integrity verdict, the advisory note, and the correction links.
# None of them is the answer. Before this guard, any of them raising reached
# FastAPI as an unhandled 500 — the route catches only ConversationAccessError,
# EmptyMessageError and OllamaError — so the person got a server error for a turn
# that had succeeded and never saw a reply that was already in the database.
#
# Reachable, not theoretical: `db.create_supersedes_link` carries
# `@retry_on_locked` and raises `OperationalError` past its deadline, and a writer
# racing a backup snapshot was measured hitting `database is locked` 2 of 5 times.


def _locked():
    """The exception `retry_on_locked` really raises once its deadline passes."""
    return sqlite3.OperationalError("database is locked")


def test_a_failing_integrity_write_leaves_the_turn_standing(store, model, monkeypatch):
    model(answer("the boiler is at 1.4 bar"))
    monkeypatch.setattr(db, "set_message_integrity_check",
                        lambda *a, **k: (_ for _ in ()).throw(_locked()))

    outcome = turn.handle_user_message(store["lyle"], "what pressure?")

    assert outcome.content == "the boiler is at 1.4 bar"
    rows = db.get_conversation_messages(outcome.conversation_id)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "the boiler is at 1.4 bar"


def test_a_failed_verdict_write_records_NULL_rather_than_something_false(
    store, model, monkeypatch
):
    """`gate.py` defines NULL as *no verdict recorded*, never as clean.

    So losing this write leaves the record honest — which is the whole reason it
    is safe to swallow. Asserted rather than assumed.
    """
    model(answer("ok"))
    monkeypatch.setattr(db, "set_message_integrity_check",
                        lambda *a, **k: (_ for _ in ()).throw(_locked()))

    outcome = turn.handle_user_message(store["lyle"], "hello")

    with db.connection() as conn:
        row = conn.execute(
            "SELECT integrity_check FROM messages WHERE id = ?",
            (outcome.assistant_message_id,),
        ).fetchone()
    assert row["integrity_check"] is None


def test_a_failing_correction_link_does_not_fail_the_turn(store, model, monkeypatch):
    """The real path: classify returns a correction, and the WRITE loses the race.

    `corrections.record()` catches only `sqlite3.IntegrityError` — an expected
    schema refusal — so an `OperationalError` from the retry deadline goes
    straight through it.
    """
    model(answer("actually it is 1.8 bar"))
    monkeypatch.setattr(turn.corrections, "candidates", lambda *a, **k: [object()])
    monkeypatch.setattr(
        turn.corrections, "classify",
        lambda *a, **k: corrections.Correction(
            superseding_message_id="new", superseded_message_id="old",
            rationale="r", replacement="replaced"),
    )
    monkeypatch.setattr(turn.corrections, "record",
                        lambda *a, **k: (_ for _ in ()).throw(_locked()))

    outcome = turn.handle_user_message(store["lyle"], "no, 1.8")

    assert outcome.content == "actually it is 1.8 bar"
    assert outcome.assistant_message_id


def test_every_post_durability_write_failing_still_returns_the_answer(
    store, model, monkeypatch
):
    model(answer("still answered"))
    for name in ("set_message_integrity_check", "set_message_integrity_advisory"):
        monkeypatch.setattr(db, name, lambda *a, **k: (_ for _ in ()).throw(_locked()))
    monkeypatch.setattr(turn, "_record_corrections",
                        lambda *a, **k: (_ for _ in ()).throw(_locked()))

    outcome = turn.handle_user_message(store["lyle"], "anything")

    assert outcome.content == "still answered"


def test_the_guard_swallows_Exception_but_never_BaseException(
    store, model, monkeypatch
):
    """`StoreIsolationViolation` derives from BaseException precisely so a broad
    `except` cannot hide a test writing into the real store. Same line
    `registry.dispatch` draws.
    """

    class Interrupt(BaseException):
        pass

    model(answer("x"))
    monkeypatch.setattr(db, "set_message_integrity_check",
                        lambda *a, **k: (_ for _ in ()).throw(Interrupt()))

    with pytest.raises(Interrupt):
        turn.handle_user_message(store["lyle"], "hello")


def test_the_failure_is_logged_with_which_step_was_lost(store, model, monkeypatch, caplog):
    """A swallowed write must not be a silent one: the operator needs to know
    which piece of bookkeeping was lost, since each has a different consequence."""
    model(answer("x"))
    monkeypatch.setattr(db, "set_message_integrity_check",
                        lambda *a, **k: (_ for _ in ()).throw(_locked()))

    with caplog.at_level(logging.WARNING, logger="program.engine.turn"):
        turn.handle_user_message(store["lyle"], "hello")

    messages = [r.getMessage() for r in caplog.records]
    assert any("the integrity verdict could not be recorded" in m for m in messages)
    assert any("database is locked" in m for m in messages)


# --- Message length cap (merged-queue item 16, plan B6a) ----------------------


def test_an_over_long_message_is_refused_before_anything_is_written(
    store, model, monkeypatch
):
    """The archive is append-only: an over-long message stored first could never
    be taken back out. So the refusal comes before the conversation or the message
    exists anywhere, and before the model is called."""
    monkeypatch.setenv("ANAM_CHAT_MAX_MESSAGE_CHARS", "100")
    config.reload()
    calls = model(answer("should never be produced"))
    before = db.count_messages()

    with pytest.raises(turn.MessageTooLongError, match="limit is 100"):
        turn.handle_user_message(store["lyle"], "x" * 101)

    assert db.count_messages() == before
    assert calls["calls"] == 0
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0


def test_a_message_exactly_at_the_limit_is_answered(store, model, monkeypatch):
    monkeypatch.setenv("ANAM_CHAT_MAX_MESSAGE_CHARS", "100")
    config.reload()
    model(answer("Answered."))

    outcome = turn.handle_user_message(store["lyle"], "x" * 100)

    assert outcome.content == "Answered."


def test_the_limit_is_measured_after_stripping_like_the_stored_message(
    store, model, monkeypatch
):
    """What is stored is the stripped text, so that is what is measured."""
    monkeypatch.setenv("ANAM_CHAT_MAX_MESSAGE_CHARS", "10")
    config.reload()
    model(answer("Answered."))

    assert turn.handle_user_message(store["lyle"], "   " + "x" * 10 + "\n\n").content


def _full_catalogue_schema_tokens() -> int:
    """B20: every tool's schema, enabled or not, priced as the window prices it.

    The **full catalogue**, so switching a tool on can never break the derivation
    silently: a tool's schema counts from the moment it exists.
    """
    import json

    from program.engine import history
    from program.tools import catalog, registry

    schemas = registry.ToolRegistry(list(catalog.TOOLS)).ollama_schema()
    return history.estimate_tokens_from_chars(len(json.dumps(schemas)))


def _authored_reserve_tokens() -> int:
    """Both authored files at their enforced ceilings (decision #25)."""
    from program.engine import history, prompt

    return history.estimate_tokens_from_chars(
        prompt.SOUL_MAX_CHARS + prompt.OPERATIONAL_MAX_CHARS)


def _reserved_beside_a_maximal_message() -> int:
    """B6a's chain from LIVE config, now with B20's schema term."""
    from program.engine import earlier_tools, history, prompt

    authored = _authored_reserve_tokens()
    situation = history.estimate_tokens_from_chars(600)  # judgment allowance; 206 measured
    # The speaker line (a name allowance of 64 characters, a judgment) and the earlier-tools
    # list at its cap (decision #32 D4), each with its separator.
    speaker_and_tools = history.estimate_tokens_from_chars(
        len(prompt.SPEAKER_LINE) + 64 + earlier_tools.LIST_MAX_CHARS + 4)
    retrieved_chars = (
        # The records cap itself (B17), not a copy of its arithmetic: top_k x
        # max_input_chars + 1,000 of headers. Before B17 this line assumed that bound
        # and nothing enforced it; siblings could add ~150,000 more characters.
        prompt.retrieved_records_max_chars()
        + 6000  # annotation bound asserted by test_the_worst_case_render_is_bounded
    )
    return (
        config.history_output_reserve_tokens()
        + config.history_safety_margin_tokens()
        + config.history_message_overhead_tokens()
        + authored + situation + speaker_and_tools
        + history.estimate_tokens_from_chars(retrieved_chars)
        + _full_catalogue_schema_tokens()
    )


def test_the_configured_limit_fits_the_context_window_by_its_own_derivation():
    """`defaults.toml` derives the cap; this recomputes that chain from LIVE config,
    so a smaller num_ctx, a larger output reserve, a larger top_k or **another tool's
    schema** (B20) fails here rather than quietly letting a maximal message overflow
    the window."""
    context = int(config.model_options()["num_ctx"])
    reserved = _reserved_beside_a_maximal_message()
    fits_chars = (context - reserved) * config.history_chars_per_token()

    assert config.chat_max_message_chars() <= fits_chars, (
        f"chat.max_message_chars {config.chat_max_message_chars()} exceeds the "
        f"{fits_chars:.0f} characters that fit beside a maximal turn"
    )


def test_the_derivation_reserves_both_authored_files_at_their_ceilings():
    """Trap (a), soul v2: lowering SOUL_MAX_CHARS left the cap test passing with the
    operational ceiling missing from the reserve, because a smaller reserve only makes
    that test easier. So the authored term is pinned directly: it covers soul.md AND
    operational.md at their enforced ceilings, and it is at least what the two cost
    when each is filled to its ceiling and joined as assembly joins them."""
    from program.engine import history, prompt

    reserve = _authored_reserve_tokens()
    assert reserve == history.estimate_tokens_from_chars(
        prompt.SOUL_MAX_CHARS + prompt.OPERATIONAL_MAX_CHARS)
    filled = "x" * prompt.SOUL_MAX_CHARS + "\n\n" + "y" * prompt.OPERATIONAL_MAX_CHARS
    # The separator between them is scaffolding, priced with the other separators.
    assert reserve >= history.estimate_tokens(filled.replace("\n\n", ""))
    assert reserve > history.estimate_tokens_from_chars(prompt.SOUL_MAX_CHARS), (
        "the operational ceiling is missing from the reserve")


def test_tool_schemas_fit_the_headroom_beside_a_maximal_message():
    """B20's own check, stated in the schemas' terms: when the catalogue grows past
    what the chat cap leaves free, this fails and names both numbers, so the choice
    (a lower cap, or shorter schemas) is made on purpose."""
    from program.engine import history

    context = int(config.model_options()["num_ctx"])
    schemas = _full_catalogue_schema_tokens()
    headroom = (context - (_reserved_beside_a_maximal_message() - schemas)
                - history.estimate_tokens_from_chars(config.chat_max_message_chars()))

    assert schemas <= headroom, (
        f"the full tool catalogue's schemas need ~{schemas} tokens, but only "
        f"{headroom} are free beside a maximal {config.chat_max_message_chars():,}-char "
        f"message. Lower chat.max_message_chars or shorten the schemas (B20)."
    )
