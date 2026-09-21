"""The correction/supersession classifier. Design: `docs/CORRECTION_DESIGN.md`.

The classifier is scripted throughout — this file checks the mechanism, not its
accuracy. **Accuracy is task 3.4's frozen case set**, which does not exist yet,
and nothing here should be read as establishing that the detection is good
enough to trust. The same split the fabrication gate keeps between
`test_gate.py` and `test_gate_eval.py`.
"""

from __future__ import annotations

import sqlite3

import pytest

from program.engine import ollama
from program.integrity import corrections
from program.memory import chunking, db
from program.settings.permissions import Actor, Role


@pytest.fixture
def store(isolated_data_dir):
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    return {
        "lyle": Actor(user_id=lyle, name="Lyle", role=Role.ADMIN),
        "jodie": Actor(user_id=jodie, name="Jodie", role=Role.USER),
    }


def script(monkeypatch, reply):
    monkeypatch.setattr(corrections.classifier, "classify", lambda *a, **k: reply)


def candidate(message_id="m1", role="user", content="It was Tuesday.", ts="2026-09-18T10:00:00"):
    return corrections.Candidate(
        message_id=message_id, role=role, content=content, timestamp=ts)


# --- the grammar -------------------------------------------------------------


def test_none_means_no_link():
    assert corrections._parse("NONE") == (None, "", "")


def test_a_correction_carries_its_number_rationale_and_state():
    number, rationale, state = corrections._parse(
        "CORRECTS 2 REPLACED\n- Wednesday, not Tuesday | replaces the day rather than adding one")

    assert number == 2
    assert "Wednesday" in rationale
    assert state == "replaced"


@pytest.mark.parametrize("word, expected", [
    ("REPLACED", "replaced"),
    ("CONTRADICTED", "contradicted"),
    ("replaced", "replaced"),
    ("Contradicted", "contradicted"),
])
def test_both_states_parse_and_case_does_not_matter(word, expected):
    """CO8/migration 6. `contradicted` means the message said the earlier claim was
    wrong without giving the correct value, which task 3.5 renders differently."""
    assert corrections._parse(f"CORRECTS 1 {word}\n- a | b") == (1, "a | b", expected)


@pytest.mark.parametrize("reply", [
    "CORRECTS 1\n- a | b",
    "CORRECTS 1 MAYBE\n- a | b",
    "CORRECTS 1 WRONG\n- a | b",
])
def test_a_candidate_without_a_state_is_unusable_not_defaulted(reply):
    """R4. Picking a state on the model's behalf would manufacture whichever
    annotation is cheaper to render, and would make "the classifier did not say"
    indistinguishable from "the classifier said contradicted" — the same
    None-versus-sentinel mistake `Actor.operator()` and the unset retrieval floors
    exist to avoid.

    **The cost is real and is the point of the test:** a correction the classifier
    did identify becomes a miss. That is the safe direction (no wrong link, no
    wrong annotation) and it is a deliberate trade, not an oversight.
    """
    with pytest.raises(ollama.OllamaResponseError, match="replaced or only contradicted"):
        corrections._parse(reply)


def test_the_state_reaches_the_correction_and_the_row(monkeypatch):
    script(monkeypatch, "CORRECTS 1 CONTRADICTED\n- not Tuesday | says the claim is wrong")

    result = corrections.classify("The dentist isn't Tuesday.", "new", [candidate()], "user")

    assert result is not None and result.replacement == "contradicted"


@pytest.mark.parametrize("reply", ["", "   ", "maybe?", "I think so", "42"])
def test_an_unusable_reply_raises(reply):
    """The shared framework's principle, pointed the other way: a model that
    answered neither form has not said "no correction"."""
    with pytest.raises(ollama.OllamaResponseError):
        corrections._parse(reply)


def test_a_reply_naming_several_candidates_writes_nothing(monkeypatch):
    """CO5. A reply that did not obey the grammar is not trustworthy enough to
    write, and guessing which candidate was meant is how a wrong link happens."""
    script(monkeypatch, "CORRECTS 1\n- a | b\nCORRECTS 3\n- c | d")

    assert corrections.classify("x", "new", [candidate(), candidate("m2")], "user") is None


@pytest.mark.parametrize("reply", [
    "CORRECTS 1, 2\n- a | b",
    "CORRECTS 1 and 2\n- a | b",
    "CORRECTS 2,1\n- a | b",
])
def test_several_candidates_on_one_line_also_write_nothing(monkeypatch, reply):
    """The form the model actually uses, and the one the first parser missed.

    `CORRECTS 1, 2` is one line and one `CORRECTS` token, so counting matches saw
    a single confident verdict and linked candidate 1. Measured through the frozen
    harness at 20/20 false links on `G2-ambiguous-two-claims` before this was
    fixed — CO5 was enforced against a reply shape the model does not produce.
    """
    script(monkeypatch, reply)

    assert corrections.classify("x", "new", [candidate(), candidate("m2")], "user") is None


def test_a_rationale_on_the_same_line_does_not_look_like_a_second_candidate(monkeypatch):
    """The opposite failure the widened pattern could cause: sweeping digits out
    of trailing prose would turn a valid single verdict into no link."""
    script(monkeypatch, "CORRECTS 1 REPLACED - the time 3 became 4")

    result = corrections.classify("x", "new", [candidate(), candidate("m2")], "user")

    assert result is not None and result.superseded_message_id == "m1"


@pytest.mark.parametrize("number", ["0", "7", "99"])
def test_a_number_outside_the_list_writes_nothing(monkeypatch, number):
    script(monkeypatch, f"CORRECTS {number} REPLACED\n- a | b")

    assert corrections.classify("x", "new", [candidate()], "user") is None


# --- never linked by default -------------------------------------------------


def test_no_candidates_means_no_call(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("the classifier ran with nothing to correct")

    monkeypatch.setattr(corrections.classifier, "classify", must_not_run)

    assert corrections.classify("x", "new", [], "user") is None


def test_a_none_verdict_produces_no_correction(monkeypatch):
    script(monkeypatch, "NONE")

    assert corrections.classify("x", "new", [candidate()], "user") is None


def test_a_correction_names_both_sides(monkeypatch):
    script(monkeypatch, "CORRECTS 1 REPLACED\n- Wednesday | replaces the day")

    result = corrections.classify("Actually it was Wednesday.", "new", [candidate()], "user")

    assert result.superseding_message_id == "new"
    assert result.superseded_message_id == "m1"
    assert result.confidence is None, "no calibrated number exists to write (C7)"


# --- who may correct whom, by construction -----------------------------------


def test_the_entity_is_never_offered_a_persons_statement(monkeypatch):
    """CO4. An automated classifier's inference must not override a human's
    explicit self-report about their own words."""
    seen = {}

    def capture(prompt, *a, **k):
        seen["prompt"] = prompt
        return "NONE"

    monkeypatch.setattr(corrections.classifier, "classify", capture)

    result = corrections.classify(
        "I said Tuesday earlier.", "new",
        [candidate(role="user", content="It was Tuesday.")],
        "assistant", "the system",
    )

    assert result is None, "a user statement was offered to the entity"
    assert "prompt" not in seen, "the call was made with nothing eligible"


def test_a_person_is_never_offered_the_entitys_claim(monkeypatch):
    script(monkeypatch, "CORRECTS 1 REPLACED\n- a | b")

    assert corrections.classify(
        "Actually it was Wednesday.", "new",
        [candidate(role="assistant", content="The meeting was Tuesday.")],
        "user", "Lyle",
    ) is None


def test_the_other_household_member_is_never_a_candidate(store):
    """Decision #21/Q16, enforced where the candidates are built rather than in
    the prompt: a cross-user target is never offered, so it cannot be picked."""
    lyle_conversation = db.start_conversation(store["lyle"].user_id)
    db.save_message(lyle_conversation, store["lyle"].user_id, "user", "Espresso at 9.")
    jodie_conversation = db.start_conversation(store["jodie"].user_id)
    db.save_message(jodie_conversation, store["jodie"].user_id, "user", "Pour-over at 10.")

    pool = corrections.candidates(
        store["jodie"].user_id, jodie_conversation, [], user_name="Jodie")

    assert {c.content for c in pool} == {"Pour-over at 10."}


# --- candidate assembly ------------------------------------------------------


def test_the_open_trailing_group_is_a_candidate_source(store):
    """The case that matters most and that retrieval cannot serve: chunking never
    indexes the trailing group, so without this a correction of something said a
    minute ago is invisible."""
    conversation = db.start_conversation(store["lyle"].user_id)
    first = db.save_message(conversation, store["lyle"].user_id, "user", "It was Tuesday.")
    db.save_message(conversation, store["lyle"].user_id, "assistant", "Noted.")

    pool = corrections.candidates(
        store["lyle"].user_id, conversation, [], user_name="Lyle")

    assert first in {c.message_id for c in pool}


def test_the_turns_own_messages_are_excluded(store):
    """A message cannot correct itself, and the self-link CHECK would refuse it
    anyway — but offering it as a candidate would waste a call and invite the
    classifier to pick it."""
    conversation = db.start_conversation(store["lyle"].user_id)
    earlier = db.save_message(conversation, store["lyle"].user_id, "user", "It was Tuesday.")
    current = db.save_message(
        conversation, store["lyle"].user_id, "user", "Actually Wednesday.")

    pool = corrections.candidates(
        store["lyle"].user_id, conversation, [], exclude_message_ids=(current,),
        user_name="Lyle")

    ids = {c.message_id for c in pool}
    assert earlier in ids and current not in ids


def test_the_candidate_list_is_bounded(store):
    conversation = db.start_conversation(store["lyle"].user_id)
    for i in range(corrections.MAX_CANDIDATES + 6):
        db.save_message(conversation, store["lyle"].user_id, "user", f"claim {i}")

    pool = corrections.candidates(
        store["lyle"].user_id, conversation, [], user_name="Lyle")

    assert len(pool) <= corrections.MAX_CANDIDATES


def test_recent_means_chunkings_own_boundary(store):
    """CO3: tied to the existing trailing-group boundary rather than a new
    constant, so one definition governs what "not yet sealed" means."""
    conversation = db.start_conversation(store["lyle"].user_id)
    for i in range(4):
        db.save_message(conversation, store["lyle"].user_id, "user", f"q{i}")
        db.save_message(conversation, store["lyle"].user_id, "assistant", f"a{i}")

    messages = db.get_conversation_messages(conversation)
    expected = {row["id"] for row in chunking.open_group_messages(messages, "Lyle")}
    pool = corrections.candidates(
        store["lyle"].user_id, conversation, [], user_name="Lyle")

    assert {c.message_id for c in pool} <= expected | set()


# --- the record --------------------------------------------------------------


def test_a_link_is_written_and_edits_nothing(store):
    conversation = db.start_conversation(store["lyle"].user_id)
    old = db.save_message(conversation, store["lyle"].user_id, "user", "It was Tuesday.")
    new = db.save_message(conversation, store["lyle"].user_id, "user", "Actually Wednesday.")

    link_id = corrections.record(corrections.Correction(new, old, "the day changed", "replaced"))

    assert link_id
    [link] = db.get_supersedes_links(old)
    assert link["superseding_message_id"] == new
    assert link["superseded_message_id"] == old
    assert link["confidence"] is None
    messages = {row["id"]: row["content"] for row in db.get_conversation_messages(conversation)}
    assert messages[old] == "It was Tuesday.", "raw experience was edited"


def test_the_schema_refuses_a_cycle_and_the_module_survives_it(store):
    """The guard lives in the schema because the writer is not always this
    module. A refusal is expected behaviour, not an error to crash on."""
    conversation = db.start_conversation(store["lyle"].user_id)
    a = db.save_message(conversation, store["lyle"].user_id, "user", "a")
    b = db.save_message(conversation, store["lyle"].user_id, "user", "b")

    assert corrections.record(corrections.Correction(b, a, "first", "replaced")) is not None
    loop = corrections.Correction(a, b, "would close a loop", "replaced")
    assert corrections.record(loop) is None
    assert len(db.get_supersedes_links()) == 1


def test_a_duplicate_link_is_refused_once_not_raised(store):
    conversation = db.start_conversation(store["lyle"].user_id)
    a = db.save_message(conversation, store["lyle"].user_id, "user", "a")
    b = db.save_message(conversation, store["lyle"].user_id, "user", "b")

    corrections.record(corrections.Correction(b, a, "first", "replaced"))

    assert corrections.record(corrections.Correction(b, a, "again", "replaced")) is None
    assert len(db.get_supersedes_links()) == 1


def test_a_message_cannot_supersede_itself(store):
    conversation = db.start_conversation(store["lyle"].user_id)
    only = db.save_message(conversation, store["lyle"].user_id, "user", "a")

    self_link = corrections.Correction(only, only, "nonsense", "contradicted")
    assert corrections.record(self_link) is None


# --- the link table's granularity -------------------------------------------


def test_the_link_table_is_message_granular(store):
    db.init_databases()
    with db.connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(supersedes)")}

    assert {"superseding_message_id", "superseded_message_id"} <= columns
    assert "superseding_chunk_id" not in columns


def test_working_sql_still_holds_the_version_one_definition():
    """Migration 5 brings the live schema forward; `working.sql` stays version 1,
    so a fresh store and an existing one reach the same shape by the same path."""
    text = (db.SCHEMA_DIR / "working.sql").read_text(encoding="utf-8")

    assert "superseding_chunk_id" in text
    assert "superseding_message_id" not in text


def test_a_chunks_messages_resolve_by_timestamp_window(store):
    """The mapping 3.5 will need, and the reason message granularity costs
    something: there is no ordinal, so the range is resolved by timestamp."""
    from program.memory import chunking as chunking_module

    conversation = db.start_conversation(store["lyle"].user_id)
    ids = [
        db.save_message(conversation, store["lyle"].user_id, role, text)
        for role, text in (("user", "It was Tuesday."), ("assistant", "Noted."),
                           ("user", "And the venue?"), ("assistant", "The hall."))
    ]
    db.end_conversation(conversation)
    chunking_module.finalise_conversation(conversation)

    with db.connection() as conn:
        chunk_ids = [row["id"] for row in conn.execute(
            "SELECT id FROM chunks WHERE conversation_id = ?", (conversation,))]
    assert chunk_ids, "the conversation produced no chunk to resolve"

    resolved = {row["id"] for cid in chunk_ids for row in db.get_messages_in_chunk(cid)}
    assert set(ids) <= resolved


def test_a_link_survives_the_chunk_it_predates(store):
    """The timing gap C3 dissolves: the correction is written against messages,
    so it does not wait for a chunk and does not break when one arrives."""
    from program.memory import chunking as chunking_module

    conversation = db.start_conversation(store["lyle"].user_id)
    old = db.save_message(conversation, store["lyle"].user_id, "user", "It was Tuesday.")
    db.save_message(conversation, store["lyle"].user_id, "assistant", "Noted.")
    new = db.save_message(conversation, store["lyle"].user_id, "user", "Actually Wednesday.")
    db.save_message(conversation, store["lyle"].user_id, "assistant", "Corrected.")

    corrections.record(corrections.Correction(new, old, "the day changed", "replaced"))
    db.end_conversation(conversation)
    chunking_module.finalise_conversation(conversation)

    [link] = db.get_supersedes_links(old)
    assert link["superseded_message_id"] == old


# --- no actor reaches the classifier ----------------------------------------


def test_nothing_in_the_call_path_takes_an_actor():
    """F13 holds: candidate *assembly* queries the database with a user id, the
    same way the gate assembles its trace, but neither the classifier nor the
    framework is handed an actor."""
    import inspect

    for fn in (corrections.classify, corrections._parse, corrections.record):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"actor", "role", "user", "user_id"}


def test_a_failed_classifier_does_not_take_the_turn_down(store, monkeypatch):
    """A missed link leaves the record accurate; a failed turn loses an answer
    that was already generated."""
    from program.engine import loop, turn

    monkeypatch.setattr(turn, "_retrieve", lambda query: None)
    monkeypatch.setattr(
        loop.ollama, "chat",
        lambda messages, **kw: {"message": {"role": "assistant", "content": "Grind finer."}},
    )
    def route(prompt, *a, **k):
        if "EARLIER STATEMENTS" in prompt:
            raise ollama.OllamaUnreachable("down")
        return "CONSISTENT"

    monkeypatch.setattr(corrections.classifier, "classify", route)

    outcome = turn.handle_user_message(store["lyle"], "sour espresso?")

    assert outcome.content == "Grind finer."
    assert db.get_supersedes_links() == []


def test_a_real_turn_records_a_correction(store, monkeypatch):
    """End to end through `turn.py`: the wiring, not the judgment."""
    from program.engine import loop, turn

    monkeypatch.setattr(turn, "_retrieve", lambda query: None)
    monkeypatch.setattr(
        loop.ollama, "chat",
        lambda messages, **kw: {"message": {"role": "assistant", "content": "Understood."}},
    )
    # `corrections.classifier` and `gate.classifier` are the SAME module object,
    # so patching the attribute cannot script them separately. Route on the
    # prompt instead — which is also what a real turn does: one classifier,
    # two prompts.
    def route(prompt, *a, **k):
        return ("CORRECTS 1 REPLACED\n- Wednesday | replaces the day"
                if "EARLIER STATEMENTS" in prompt else "CONSISTENT")

    monkeypatch.setattr(corrections.classifier, "classify", route)

    first = turn.handle_user_message(store["lyle"], "The meeting is Tuesday.")
    # The same conversation, deliberately: a fresh one has nothing to correct,
    # which is the designed behaviour and not what this test is about.
    turn.handle_user_message(
        store["lyle"], "Actually the meeting is Wednesday.",
        conversation_id=first.conversation_id)

    links = db.get_supersedes_links()
    assert len(links) >= 1
    assert all(link["classifier_model"] for link in links), "the judging model is recorded"
    with db.connection() as conn:
        row = conn.execute(
            "SELECT content FROM messages WHERE id = ?",
            (links[0]["superseded_message_id"],)).fetchone()
    assert row is not None, "the link points at a real message"


def test_integrity_error_is_caught_not_leaked(store, monkeypatch):
    def boom(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(db, "create_supersedes_link", boom)

    assert corrections.record(corrections.Correction("a", "b", "x", "replaced")) is None
