"""The speaker is named on every turn: "You are talking with <name>." (2026-10-07 brief).

Before this line the only place a turn named its speaker was the situation block, which does so
only on a first message or after a gap of 15 minutes or more; under that the block is the time
alone and is not resent with history, so most turns carried no name while soul.md says each turn
tells the entity who is speaking. The line is its own system-prompt part, after the situation
block and before the retrieved records, and never inside the situation string: the gate reads
that string, and its input must not move.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from program.engine import loop, prompt, situation, turn
from program.memory import db
from program.memory.retrieval import RetrievalResult, RetrievedChunk
from program.settings.permissions import Actor, Role

NOW = datetime(2026, 10, 7, 17, 57, 15, tzinfo=timezone.utc)
SITUATION_NO_ELAPSED = "The current time is 2026-09-01T16:00:00+00:00."


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(turn, "_retrieve", lambda query: None)
    monkeypatch.setattr(turn, "datetime", _Frozen)
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    return {
        "Lyle": Actor(user_id=lyle, name="Lyle", role=Role.ADMIN),
        "Jodie": Actor(user_id=jodie, name="Jodie", role=Role.USER),
    }


@pytest.fixture
def seen(monkeypatch):
    """Each turn's system prompt (the loop's call only) and each situation the gate was given."""
    record: dict = {"system": [], "gate": [], "classifier": []}

    def fake(messages, **kwargs):
        if messages and messages[0].get("role") == "system":
            record["system"].append(messages[0]["content"])
        else:
            record["classifier"].append(repr(messages))
        return {"message": {"role": "assistant", "content": "ok"}}

    real_check = turn.gate.check

    def check(answer, trace=(), situation="", *args, **kwargs):
        record["gate"].append(situation)
        return real_check(answer, trace, situation, *args, **kwargs)

    monkeypatch.setattr(loop.ollama, "chat", fake)
    monkeypatch.setattr(turn.gate, "check", check)
    return record


def _age_every_message(by: timedelta) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE messages SET timestamp = ?", ((NOW - by).isoformat(),))


# --- The line ---------------------------------------------------------------------------------


def test_the_line_is_the_brief_wording_and_passes_the_authored_text_checks():
    assert prompt.speaker_line("Lyle") == "You are talking with Lyle."
    assert prompt.speaker_line("Jodie") == "You are talking with Jodie."
    prompt.check_authored_text(prompt.SPEAKER_LINE, "speaker line template")


def test_no_person_states_nothing():
    assert prompt.speaker_line(None) == ""
    assert prompt.speaker_line("  ") == ""
    assert "You are talking with" not in prompt.build_system_prompt(SITUATION_NO_ELAPSED)


def test_it_sits_after_the_situation_and_before_the_records_and_is_not_in_the_situation():
    retrieval = RetrievalResult(query="q", results=[RetrievedChunk(
        chunk_id="c1", text="Lyle: a remembered thing", created_at="2026-09-01T10:00:00+00:00",
    )])
    assembled = prompt.assemble_turn(
        [{"role": "user", "content": "hi"}], SITUATION_NO_ELAPSED, retrieval, speaker="Jodie")
    system = assembled.system
    assert system.count("You are talking with Jodie.") == 1
    assert (system.index(SITUATION_NO_ELAPSED) < system.index("You are talking with Jodie.")
            < system.index(prompt._RETRIEVED_HEADER[:40]))
    assert f"{SITUATION_NO_ELAPSED}\n\nYou are talking with Jodie.\n\n" in system


def test_the_parts_still_sum_to_the_system_string_and_the_budget_counts_the_line(monkeypatch):
    captured = {}
    real = prompt.history.plan_budget

    def spy(system_prompt_chars=0, retrieved_chars=0, context_tokens=None, tool_schema_chars=0):
        captured["system_prompt_chars"] = system_prompt_chars
        return real(system_prompt_chars, retrieved_chars, context_tokens, tool_schema_chars)

    monkeypatch.setattr(prompt.history, "plan_budget", spy)
    without = prompt.assemble_turn([], SITUATION_NO_ELAPSED)
    without_chars = captured["system_prompt_chars"]
    named = prompt.assemble_turn([], SITUATION_NO_ELAPSED, speaker="Lyle")

    line = "You are talking with Lyle."
    assert named.speaker_chars == len(line) and without.speaker_chars == 0
    assert (named.soul_chars + named.operational_chars + named.situation_chars
            + named.speaker_chars + named.retrieved_chars + named.scaffolding_chars
            ) == len(named.system)
    assert captured["system_prompt_chars"] - without_chars == len(line) + len(prompt._SECTION_SEP)


# --- Every turn, whatever the situation block says ------------------------------------------


def test_present_on_a_first_message(store, seen):
    turn.handle_user_message(store["Jodie"], "a first message")

    system = seen["system"][-1]
    assert "first message from Jodie" in system, "the block names her too, here"
    assert "You are talking with Jodie." in system


def test_present_under_15_minutes_when_the_block_is_the_time_alone(store, seen):
    first = turn.handle_user_message(store["Jodie"], "a first message")
    _age_every_message(timedelta(seconds=24))

    turn.handle_user_message(store["Jodie"], "a second message", first.conversation_id)

    block = seen["gate"][-1]
    assert block.startswith("The current time is") and "\n" not in block, block
    assert "Jodie" not in block, "the case the line exists for: the block does not name her"
    assert "You are talking with Jodie." in seen["system"][-1]


def test_present_after_a_gap_of_15_minutes_or_more_when_the_block_also_names_them(store, seen):
    first = turn.handle_user_message(store["Lyle"], "a first message")
    _age_every_message(timedelta(days=16))

    turn.handle_user_message(store["Lyle"], "back again", first.conversation_id)

    assert "since the last message from Lyle" in seen["gate"][-1]
    assert "You are talking with Lyle." in seen["system"][-1]


def test_overlapping_sessions_in_separate_conversations_each_name_their_own_speaker(store, seen):
    order = ["Jodie", "Lyle", "Jodie", "Lyle", "Lyle", "Jodie"]
    conversations: dict = {}
    for name in order:
        outcome = turn.handle_user_message(store[name], f"{name} here", conversations.get(name))
        conversations[name] = outcome.conversation_id
    assert conversations["Lyle"] != conversations["Jodie"]

    for name, system in zip(order, seen["system"], strict=True):
        other = "Lyle" if name == "Jodie" else "Jodie"
        assert f"You are talking with {name}." in system
        assert f"You are talking with {other}." not in system


# --- The gate's input does not move ---------------------------------------------------------


def _turn_pair(store, seen, monkeypatch, age: timedelta | None):
    """One turn's gate situation and classifier calls with the line, then the same turn without.

    "The same turn": the same speaker, the same frozen clock and the same previous-message time,
    restored by re-ageing the store before each run.
    """
    runs = []
    for with_line in (True, False):
        if not with_line:
            monkeypatch.setattr(prompt, "speaker_line", lambda speaker: "")
        if age is not None:
            _age_every_message(age)
        seen["classifier"].clear()
        turn.handle_user_message(store["Lyle"], "a second message")
        runs.append((seen["gate"][-1], list(seen["classifier"]), seen["system"][-1]))
    return runs


@pytest.mark.parametrize("age", [timedelta(seconds=72), timedelta(days=16)],
                         ids=["under-15-minutes", "16-days"])
def test_the_gate_input_is_byte_identical_with_and_without_the_line(
        store, seen, monkeypatch, age):
    turn.handle_user_message(store["Lyle"], "a first message")
    (gate_with, classifier_with, system_with), (gate_without, classifier_without, system_without) \
        = _turn_pair(store, seen, monkeypatch, age)

    assert gate_with == gate_without
    assert classifier_with, "the classifier ran, so its calls are compared, not two empty lists"
    assert classifier_with == classifier_without
    assert "You are talking with" not in gate_with
    assert "You are talking with Lyle." in system_with
    assert "You are talking with" not in system_without


def test_the_gate_input_on_a_first_message_is_what_the_unchanged_builder_makes(store, seen):
    turn.handle_user_message(store["Lyle"], "hello")

    assert seen["gate"][-1] == situation.build_situation(NOW, None, "Lyle")
    assert "You are talking with" not in seen["gate"][-1]
