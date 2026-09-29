"""B11 stage 3: a person may correct the entity. Design: CORRECTION_DESIGN CO16.

A real store and a scripted model throughout. Three things are pinned here, each
with a break test recorded in the stage 3 changelog:

* **the three-call structure** (D1): the person against their own statements, the
  person against the entity's, the entity against its own, with the third gated on
  there being entity candidates;
* **D5**: when the person and the entity both supersede the same entity message in
  one turn, only the person's link is written, and the dropped one is logged;
* **CO4 still holds**: the entity never supersedes a person. It is refused at
  ``classify()`` before any model call and at ``record()`` before any write.

Plus D9's cost claim, checked from inside the classifier call rather than argued
from the order of lines: by the time a correction call runs, the answer is saved
and the conversation reads as a completed turn, so the in-flight grace floor does
not cover it.
"""

from __future__ import annotations

import logging

import pytest

from program import config
from program.engine import loop, turn
from program.integrity import corrections
from program.memory import db
from program.settings.permissions import Actor, Role


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    # The third call is a ship gate that defaults off (D6). These tests are about the
    # mechanism, so they switch it on through config, the path an operator would use.
    monkeypatch.setenv("ANAM_CORRECTIONS_PERSON_CORRECTS_ENTITY", "true")
    config.reload()
    yield _users()
    monkeypatch.delenv("ANAM_CORRECTIONS_PERSON_CORRECTS_ENTITY", raising=False)
    config.reload()


def _users():
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    return {
        "lyle": Actor(user_id=lyle, name="Lyle", role=Role.ADMIN),
        "jodie": Actor(user_id=jodie, name="Jodie", role=Role.USER),
    }


@pytest.fixture(autouse=True)
def _no_retrieval(monkeypatch):
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)


@pytest.fixture
def replies(monkeypatch):
    """Script the chat model's answers, in order; the gate's classifier says clean."""

    def install(*texts):
        queue = list(texts)
        monkeypatch.setattr(
            loop.ollama, "chat",
            lambda *a, **k: {"message": {"role": "assistant", "content": queue.pop(0)}})
        return queue

    monkeypatch.setattr(turn.gate.classifier, "classify", lambda *a, **k: "CONSISTENT")
    return install


def is_correction_prompt(prompt: str) -> bool:
    return "EARLIER STATEMENTS:" in prompt


@pytest.fixture
def correction_calls(monkeypatch):
    """Record every `corrections.classify` call's (speaker, candidate role) and every
    prompt that reached the model, answering NONE unless told otherwise."""
    state = {"pairs": [], "prompts": [], "reply": lambda prompt: "NONE"}
    real = corrections.classify

    def spy(new_message, new_id, pool, speaker_role, speaker_label="the person",
            candidate_role=None):
        state["pairs"].append((speaker_role, candidate_role))
        return real(new_message, new_id, pool, speaker_role, speaker_label,
                    candidate_role=candidate_role)

    def model(prompt, **kwargs):
        if not is_correction_prompt(prompt):
            return "CONSISTENT"
        state["prompts"].append(prompt)
        return state["reply"](prompt)

    monkeypatch.setattr(turn.corrections, "classify", spy)
    monkeypatch.setattr(corrections.classifier, "classify", model)
    return state


def links():
    with db.connection() as conn:
        return {(r["superseding_message_id"], r["superseded_message_id"])
                for r in conn.execute("SELECT * FROM supersedes")}


# --- D1: three calls ---------------------------------------------------------


def test_a_turn_makes_three_correction_calls_one_per_pair(store, replies, correction_calls):
    replies("Noted: the dentist on Tuesday.", "Noted: Wednesday.")
    first = turn.handle_user_message(store["lyle"], "The dentist is on Tuesday.")
    correction_calls["pairs"].clear()
    correction_calls["prompts"].clear()

    turn.handle_user_message(store["lyle"], "The dentist is Wednesday.",
                             first.conversation_id)

    assert correction_calls["pairs"] == [
        ("user", None), ("user", "assistant"), ("assistant", None)]
    assert len(correction_calls["prompts"]) == 3, "all three had candidates to judge"


def test_switched_off_the_third_call_never_happens(
    store, replies, correction_calls, monkeypatch
):
    """D6's ship gate: off, a turn is exactly the two same-speaker calls it was."""
    monkeypatch.setenv("ANAM_CORRECTIONS_PERSON_CORRECTS_ENTITY", "false")
    config.reload()
    assert config.corrections_person_corrects_entity() is False
    replies("Noted: the dentist on Tuesday.", "Noted: Wednesday.")
    first = turn.handle_user_message(store["lyle"], "The dentist is on Tuesday.")
    correction_calls["pairs"].clear()

    turn.handle_user_message(store["lyle"], "The dentist is Wednesday.",
                             first.conversation_id)

    assert correction_calls["pairs"] == [("user", None), ("assistant", None)]


def test_it_ships_switched_off(monkeypatch):
    monkeypatch.delenv("ANAM_CORRECTIONS_PERSON_CORRECTS_ENTITY", raising=False)
    config.reload()
    assert config.corrections_person_corrects_entity() is False


def test_the_person_against_entity_call_is_skipped_without_entity_candidates(
    store, replies, correction_calls, monkeypatch
):
    """D9's gating: `classify` makes no model call when nothing is eligible, so on a
    turn whose pool holds no entity statements the third call costs nothing."""
    replies("Hello.")
    only_people = [corrections.Candidate("u-old", "user", "It was Tuesday.",
                                         "2026-09-18T10:00:00")]
    monkeypatch.setattr(turn.corrections, "candidates", lambda *a, **k: only_people)

    turn.handle_user_message(store["lyle"], "It was Wednesday.")

    assert correction_calls["pairs"] == [
        ("user", None), ("user", "assistant"), ("assistant", None)]
    assert len(correction_calls["prompts"]) == 1, (
        "only the person-against-their-own call had anything to judge")
    assert "EARLIER STATEMENTS:\n1. (Lyle," in correction_calls["prompts"][0]


def test_the_third_call_judges_only_the_entitys_statements(
    store, replies, correction_calls
):
    replies("Noted: the dentist on Tuesday.", "Right.")
    first = turn.handle_user_message(store["lyle"], "The dentist is on Tuesday.")
    correction_calls["prompts"].clear()

    turn.handle_user_message(store["lyle"], "No, Wednesday.", first.conversation_id)

    person_vs_entity = correction_calls["prompts"][1]
    listed = person_vs_entity.split("EARLIER STATEMENTS:")[1].split("NEW MESSAGE")[0]
    assert "(the system," in listed and "(Lyle," not in listed
    assert "NEW MESSAGE (from Lyle)" in person_vs_entity


# --- D5: the person's link wins ----------------------------------------------


def _paired_turn(store, replies, correction_calls, *, entity_targets_echo: bool):
    """Turn 1 puts a claim and its echo on record; turn 2's three verdicts are
    scripted by which list the prompt shows."""
    replies("Noted: the dentist on Tuesday.", "Sorry, I had Tuesday. It is Wednesday.")
    first = turn.handle_user_message(store["lyle"], "The dentist is on Tuesday.")
    rows = db.get_conversation_messages(first.conversation_id)
    claim, echo = rows[0]["id"], rows[1]["id"]

    def reply(prompt):
        speaker_is_entity = "NEW MESSAGE (from the system)" in prompt
        listed = prompt.split("EARLIER STATEMENTS:")[1].split("NEW MESSAGE")[0]
        entity_list = "(the system," in listed
        if not speaker_is_entity and not entity_list:
            return "CORRECTS 1 REPLACED\n- Tuesday to Wednesday | replaces the day"
        if not speaker_is_entity and entity_list:
            return "CORRECTS 1 REPLACED\n- Tuesday to Wednesday | replaces the echo"
        if entity_targets_echo:
            return "CORRECTS 1 REPLACED\n- self-correction of the echo | replaces it"
        return "NONE"

    correction_calls["reply"] = reply
    second = turn.handle_user_message(store["lyle"], "The dentist is Wednesday.",
                                      first.conversation_id)
    return claim, echo, second


def test_when_both_supersede_the_same_echo_only_the_persons_link_is_written(
    store, replies, correction_calls, caplog
):
    caplog.set_level(logging.INFO, logger="program.engine.turn")
    claim, echo, second = _paired_turn(store, replies, correction_calls,
                                       entity_targets_echo=True)

    assert links() == {(second.user_message_id, claim), (second.user_message_id, echo)}
    dropped = [r.getMessage() for r in caplog.records if "D5" in r.getMessage()]
    assert len(dropped) == 1, "the discarded self-correction must be logged, not lost"
    assert echo in dropped[0] and second.assistant_message_id in dropped[0]
    assert second.user_message_id in dropped[0]
    assert "self-correction of the echo" in dropped[0], "both verdicts are in the log"


def test_different_targets_are_both_written(store, replies, correction_calls, monkeypatch):
    """D5 applies only to the same target: an entity self-correction of a different
    message is written beside the person's links."""
    replies("Noted: the dentist on Tuesday. The market is at ten.",
            "Wednesday, noted. And the market is at nine, I had that wrong.")
    first = turn.handle_user_message(store["lyle"], "The dentist is on Tuesday.")
    rows = db.get_conversation_messages(first.conversation_id)
    claim, echo = rows[0]["id"], rows[1]["id"]
    older_entity = db.save_message(first.conversation_id, store["lyle"].user_id,
                                   "assistant", "The market opens at ten.")

    def reply(prompt):
        listed = prompt.split("EARLIER STATEMENTS:")[1].split("NEW MESSAGE")[0]
        if "NEW MESSAGE (from the system)" in prompt:
            number = next(n for n, line in enumerate(
                [line for line in listed.strip().splitlines() if line[:1].isdigit()],
                start=1) if "market" in line)
            return f"CORRECTS {number} REPLACED\n- ten to nine | replaces it"
        if "(the system," in listed:
            number = next(n for n, line in enumerate(
                [line for line in listed.strip().splitlines() if line[:1].isdigit()],
                start=1) if "dentist" in line)
            return f"CORRECTS {number} REPLACED\n- Tuesday to Wednesday | echo"
        return "CORRECTS 1 REPLACED\n- Tuesday to Wednesday | replaces the day"

    correction_calls["reply"] = reply
    second = turn.handle_user_message(store["lyle"], "The dentist is Wednesday.",
                                      first.conversation_id)

    assert links() == {(second.user_message_id, claim),
                       (second.user_message_id, echo),
                       (second.assistant_message_id, older_entity)}


def test_the_d5_rule_passes_the_entity_link_through_when_there_is_no_conflict():
    person = corrections.Correction("u2", "e1", "r", "replaced")
    entity = corrections.Correction("a2", "e9", "r", "replaced")
    assert turn._unless_person_took_it(person, entity, "c" * 8) is entity
    assert turn._unless_person_took_it(None, entity, "c" * 8) is entity
    assert turn._unless_person_took_it(person, None, "c" * 8) is None
    same = corrections.Correction("a2", "e1", "r", "replaced")
    assert turn._unless_person_took_it(person, same, "c" * 8) is None


# --- CO4: the entity never supersedes a person --------------------------------


def test_classify_refuses_an_entity_speaker_against_person_statements(monkeypatch):
    monkeypatch.setattr(corrections.classifier, "classify",
                        lambda *a, **k: pytest.fail("a model call was made"))
    pool = [corrections.Candidate("u1", "user", "It was Tuesday.", "2026-09-18T10:00:00")]

    with pytest.raises(ValueError, match="CO4"):
        corrections.classify("It was Wednesday.", "a1", pool, "assistant", "the system",
                             candidate_role="user")


def test_record_refuses_a_link_from_the_entity_to_a_person(store):
    lyle = store["lyle"].user_id
    conversation = db.start_conversation(lyle)
    said = db.save_message(conversation, lyle, "user", "It was Tuesday.")
    answer = db.save_message(conversation, lyle, "assistant", "It was Wednesday.")

    with pytest.raises(corrections.CorrectionScopeError, match="CO4"):
        corrections.record(corrections.Correction(answer, said, "r", "replaced"))
    assert links() == set()


def test_record_refuses_a_link_across_users(store):
    lyle, jodie = store["lyle"].user_id, store["jodie"].user_id
    theirs = db.save_message(db.start_conversation(jodie), jodie, "assistant", "Tuesday.")
    mine = db.save_message(db.start_conversation(lyle), lyle, "user", "Wednesday.")

    with pytest.raises(corrections.CorrectionScopeError, match="#21"):
        corrections.record(corrections.Correction(mine, theirs, "r", "replaced"))
    assert links() == set()


def test_record_allows_a_person_to_supersede_the_entity(store):
    lyle = store["lyle"].user_id
    conversation = db.start_conversation(lyle)
    claim = db.save_message(conversation, lyle, "assistant", "It boils at 90.")
    person = db.save_message(conversation, lyle, "user", "It boils at 100.")

    assert corrections.record(corrections.Correction(person, claim, "r", "replaced"))
    assert links() == {(person, claim)}


def test_the_allowed_pairs_are_exactly_co4s_three():
    assert corrections.ALLOWED_PAIRS == {
        ("user", "user"), ("assistant", "assistant"), ("user", "assistant")}


# --- D9: correction calls run after the answer is saved -----------------------


def test_correction_calls_run_after_the_answer_is_durable(store, replies, monkeypatch):
    """From inside each correction call: the answer is already in the store and the
    conversation's last message is the assistant's. So the turn reads as completed,
    `idle.py` applies `idle_close_minutes` rather than `in_flight_grace_minutes`, and
    the correction calls are outside what `IN_FLIGHT_GRACE_FLOOR_MINUTES` has to cover.
    That is why `tests/test_idle.py::test_the_floor_is_recomputed_from_the_loops_own_
    limits` counts the gate's classifier (before the save) and no correction call."""
    replies("Noted: Tuesday.", "Noted: Wednesday.")
    first = turn.handle_user_message(store["lyle"], "Tuesday.")
    seen = []

    def model(prompt, **kwargs):
        if not is_correction_prompt(prompt):
            return "CONSISTENT"
        [row] = [r for r in db.get_open_conversations_with_activity()
                 if r["id"] == first.conversation_id]
        messages = db.get_conversation_messages(first.conversation_id)
        seen.append((row["last_role"], messages[-1]["role"], messages[-1]["content"]))
        return "NONE"

    monkeypatch.setattr(corrections.classifier, "classify", model)
    turn.handle_user_message(store["lyle"], "Wednesday.", first.conversation_id)

    assert seen == [("assistant", "assistant", "Noted: Wednesday.")] * 3
