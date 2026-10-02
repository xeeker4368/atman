"""The reflection journal, step 3: kind, storage, run, command, label, late indexing.

Design of record: `docs/REFLECTION_JOURNAL_DESIGN.md` J1–J8, J11. The model and the
classifier are scripted; what these check is the mechanism. What the entity writes and
how the gate reads it is the live run's and J8's to report, not this file's.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from program import config
from program.artifacts import indexing, kinds, writing
from program.artifacts import journal as storage
from program.engine import ollama, prompt
from program.integrity import classifier, gate
from program.memory import db, retrieval
from program.reflection import journal

NOW = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)  # 11:00 local, 23 September
DAY = date(2026, 9, 21)  # EDT, UTC-4: local 09:00 is 13:00 UTC
TZ = ZoneInfo("America/New_York")
ENTRY = "The records show Jodie moved a dentist appointment from Tuesday to Wednesday."


def deterministic(text, *_a, **_k):
    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[i % len(digest)] / 255.0) for i in range(768)]


class Model:
    """Scripted `ollama.chat`: records every call and replies with `text`."""

    def __init__(self, text=ENTRY, done_reason="stop"):
        self.text, self.done_reason, self.calls = text, done_reason, []

    def __call__(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return {"message": {"content": self.text}, "done_reason": self.done_reason,
                "eval_count": 7}


@pytest.fixture
def world(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(indexing.ollama, "embed", deterministic)
    monkeypatch.setattr(retrieval.ollama, "embed", deterministic)
    monkeypatch.setattr(classifier, "classify", lambda prompt_text: "CONSISTENT")
    model = Model()
    monkeypatch.setattr(journal.ollama, "chat", model)
    db.init_databases()
    return type("World", (), {
        "model": model,
        "lyle": db.create_user("Lyle", role="admin"),
        "jodie": db.create_user("Jodie"),
    })


def say(user_id, role, text, when, conversation, trace=None):
    return db.save_message(conversation, user_id, role, text,
                           tool_trace=json.dumps(trace) if trace else None,
                           timestamp=when)


def seed_day(world):
    """Two people's conversations on DAY, with a saved piece and a web search."""
    piece = writing.store("The kettle again.", world.jodie, title="Kettle")
    a = db.start_conversation(world.lyle)
    say(world.lyle, "user", "What is a good retrieval floor?", "2026-09-21T13:14:00+00:00", a)
    say(world.lyle, "assistant", "It depends on the corpus.", "2026-09-21T13:15:00+00:00", a,
        [{"iteration": 1, "tool": "web_search", "outcome": "ok", "ran": True,
          "arguments": {"query": "SECRET-ARGUMENT"}, "artifact_ids": []}])
    b = db.start_conversation(world.jodie)
    say(world.jodie, "user", "Write something short and keep it.", "2026-09-21T14:00:00+00:00", b)
    say(world.jodie, "assistant", "It is saved.", "2026-09-21T14:01:00+00:00", b,
        [{"iteration": 1, "tool": "creative_write", "outcome": "tool_error", "ran": True,
          "arguments": {"text": "SECRET-PIECE"}, "artifact_ids": [piece.artifact_id]}])
    return piece


# --- J1: the covered day ------------------------------------------------------


def test_the_default_day_is_yesterday_in_local_time():
    # 03:00 UTC on the 23rd is 23:00 on the 22nd locally, so "yesterday" is the 21st.
    late = datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc)
    assert journal.default_covered_date(late, TZ) == date(2026, 9, 21)
    assert journal.default_covered_date(NOW, TZ) == date(2026, 9, 22)


def test_the_window_is_the_real_length_of_the_local_day_across_clock_changes():
    def hours(day):
        start, end = journal.covered_window(day, TZ)
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 3600

    assert hours(date(2026, 3, 8)) == 23   # clocks go forward
    assert hours(date(2026, 11, 1)) == 25  # clocks go back
    assert hours(DAY) == 24
    assert journal.covered_window(DAY, TZ)[0] == "2026-09-21T04:00:00+00:00"


def test_the_window_boundary_is_inclusive_at_the_start_and_exclusive_at_the_end(world):
    """A whole-second timestamp omits its fraction, so a plain string compare is only
    right if '+' orders before '.'. Pinned at both edges, with and without a fraction."""
    c = db.start_conversation(world.lyle)
    start, end = journal.covered_window(DAY, TZ)
    inside = [start, "2026-09-21T04:00:00.000001+00:00", "2026-09-22T03:59:59.999999+00:00"]
    outside = ["2026-09-21T03:59:59.999999+00:00", end, "2026-09-22T04:00:00.000001+00:00"]
    for i, stamp in enumerate(inside + outside):
        say(world.lyle, "user", f"m{i}", stamp, c)

    got = {row["content"] for row in db.get_messages_between(start, end)}

    assert got == {"m0", "m1", "m2"}


@pytest.mark.parametrize("covered", [date(2026, 9, 23), date(2026, 9, 24)])
def test_today_and_future_days_are_refused(world, covered):
    with pytest.raises(journal.DateRefused):
        journal.write_entry(covered, now=NOW)
    assert world.model.calls == []


def test_a_day_with_no_messages_writes_nothing_and_makes_no_model_call(world):
    result = journal.write_entry(DAY, now=NOW)

    assert result.status == "empty"
    assert world.model.calls == []
    assert db.list_artifacts() == []


def test_a_second_run_for_the_same_date_reports_and_does_nothing(world):
    seed_day(world)
    first = journal.write_entry(DAY, now=NOW)
    calls = len(world.model.calls)

    second = journal.write_entry(DAY, now=NOW)

    assert first.status == "written" and second.status == "exists"
    assert second.existing_id == first.entry.artifact_id
    assert len(world.model.calls) == calls
    assert len([a for a in db.list_artifacts() if a["artifact_type"] == "reflection_journal"]) == 1


def test_a_dry_run_reports_and_makes_no_model_call_and_writes_nothing(world):
    seed_day(world)
    before = len(db.list_artifacts())

    result = journal.write_entry(DAY, dry_run=True, now=NOW)

    assert result.status == "dry_run"
    assert (result.prepared.messages_in, result.prepared.conversations) == (4, 2)
    assert result.prepared.messages_omitted == 0 and result.prepared.estimated_prompt_tokens > 0
    assert world.model.calls == []
    assert len(db.list_artifacts()) == before
    assert db.get_user_by_name(db.ENTITY_USER_NAME) is None  # not even the entity row


# --- J3: what the run reads ---------------------------------------------------


def test_all_users_are_included_replies_are_you_and_tool_use_is_a_system_record(world):
    seed_day(world)
    prepared = journal.prepare(DAY, now=NOW)

    records = prepared.system
    assert "Conversation with Lyle, 09:14–09:15" in records
    assert "Conversation with Jodie, 10:00–10:01" in records
    assert "[09:14] Lyle: What is a good retrieval floor?" in records
    assert "[09:15] You: It depends on the corpus." in records
    assert "[09:15] (system record: web_search ran — ok)" in records
    # The row, not the trace's label: this call is `tool_error` in the trace but its
    # artifact row exists, which is what a receipt reads.
    assert "[10:01] (system record: creative_write ran — saved)" in records


def test_tool_arguments_are_never_rendered(world):
    seed_day(world)
    prepared = journal.prepare(DAY, now=NOW)

    assert "SECRET-ARGUMENT" not in prepared.system + prepared.user
    assert "SECRET-PIECE" not in prepared.system + prepared.user


def test_a_long_message_is_clipped_and_marked(world, monkeypatch):
    monkeypatch.setattr(config, "journal_max_message_chars", lambda: 50)
    c = db.start_conversation(world.lyle)
    say(world.lyle, "user", "x" * 200, "2026-09-21T13:00:00+00:00", c)

    records = journal.prepare(DAY, now=NOW).system

    assert "x" * 50 + " … [clipped: 150 more characters]" in records
    assert "x" * 51 not in records


def test_the_earliest_messages_are_dropped_whole_and_counted(world, monkeypatch):
    """J3: when the day does not fit, drop the earliest messages, whole, and say so in the
    prompt and in the row. Nothing is deleted."""
    monkeypatch.setattr(config, "model_options", lambda: {"num_ctx": 7000})
    monkeypatch.setattr(config, "journal_max_message_chars", lambda: 100000)
    c = db.start_conversation(world.lyle)
    for i in range(40):
        say(world.lyle, "user", f"message-{i:02d} " + "w" * 800,
            f"2026-09-21T13:{i:02d}:00+00:00", c)

    prepared = journal.prepare(DAY, now=NOW)

    assert 0 < prepared.messages_omitted < 40 and prepared.messages_in == 40
    kept = 40 - prepared.messages_omitted
    assert f"The {prepared.messages_omitted} earliest messages of the day are not shown" \
        in prepared.system
    assert f"message-{prepared.messages_omitted:02d} " in prepared.system   # first kept
    assert f"message-{prepared.messages_omitted - 1:02d} " not in prepared.system  # last dropped
    assert prepared.system.count("message-") == kept
    assert db.count_messages()[1] == 40  # nothing deleted
    assert prepared.note()["messages_omitted"] == prepared.messages_omitted


def test_clipped_messages_are_counted_beside_omitted_and_stored(world, monkeypatch):
    """Only messages shown to the model count: a long message that was also omitted
    is omitted, not clipped."""
    monkeypatch.setattr(config, "journal_max_message_chars", lambda: 50)
    c = db.start_conversation(world.lyle)
    say(world.lyle, "user", "a" * 60, "2026-09-21T13:00:00+00:00", c)
    say(world.lyle, "user", "b" * 50, "2026-09-21T13:01:00+00:00", c)   # exactly the limit
    say(world.lyle, "user", "c" * 500, "2026-09-21T13:02:00+00:00", c)

    prepared = journal.prepare(DAY, now=NOW)
    assert (prepared.messages_in, prepared.messages_omitted, prepared.messages_clipped) == (3, 0, 2)
    assert prepared.note()["messages_clipped"] == 2

    result = journal.write_entry(DAY, now=NOW)
    assert json.loads(db.get_artifact(result.entry.artifact_id)["extraction_note"])[
        "messages_clipped"] == 2


def test_a_day_that_fits_omits_nothing_and_says_nothing_about_omission(world):
    seed_day(world)
    prepared = journal.prepare(DAY, now=NOW)

    assert prepared.messages_omitted == 0
    assert "earliest messages" not in prepared.system


# --- J4: the prompt -----------------------------------------------------------


def test_the_block_is_exactly_the_text_j8_measured():
    """What shipped is what was measured: both arms, character for character."""
    from scripts import journal_gate_dev_j8 as dev

    assert journal.journal_block(dev.NOW, dev.DAY) == dev.REVISED_BLOCK
    assert journal.journal_block(dev.NOW, dev.DAY, clause=True) == dev.REVISED_WITH_CLAUSE


@pytest.mark.parametrize("clause", [False, True])
def test_the_block_passes_the_prompt_checks_and_states_no_elapsed_time(clause):
    block = journal.journal_block("Tuesday 22 September 2026, 11:00",
                                  "Monday 21 September 2026", clause=clause)

    prompt.check_authored_text(block, "journal block")
    assert not prompt.states_elapsed_time(block)   # nothing for the pairing rule to pair
    assert "Lyle" not in block and "Jodie" not in block  # names come only from the records


def test_the_system_prompt_order_is_soul_then_block_then_records(world):
    seed_day(world)
    p = journal.prepare(DAY, now=NOW)

    soul = prompt.load_soul()
    assert p.system.startswith(soul)
    assert p.system.index(soul) < p.system.index(p.block) < p.system.index("RECORDS OF")
    assert p.system.index("RECORDS OF") < p.system.index("Conversation with")
    assert p.user.startswith("(Written by the system, not by a person.)")


def test_the_gate_is_given_the_same_block_the_entity_was(world, monkeypatch):
    seed_day(world)
    seen = {}
    real = gate.check_identity
    monkeypatch.setattr(
        journal.gate, "check_identity",
        lambda text, situation="": seen.update(situation=situation) or real(text, situation))
    result = journal.write_entry(DAY, clause=True, now=NOW)

    assert seen["situation"] == result.prepared.block
    assert journal.CLAUSE in seen["situation"]
    assert journal.CLAUSE.strip(", ") in world.model.calls[0][0][0]["content"]


def test_the_arm_is_recorded_in_the_row(world):
    seed_day(world)
    plain = journal.write_entry(DAY, now=NOW)
    note = json.loads(db.get_artifact(plain.entry.artifact_id)["extraction_note"])
    assert note["prompt_revision"] == "J4r2"

    other = date(2026, 9, 20)
    c = db.start_conversation(world.lyle)
    say(world.lyle, "user", "hello", "2026-09-20T13:00:00+00:00", c)
    with_clause = journal.write_entry(other, clause=True, now=NOW)
    note = json.loads(db.get_artifact(with_clause.entry.artifact_id)["extraction_note"])
    assert note["prompt_revision"] == "J4r2+clause"


# --- J2, J5, J7: storage ------------------------------------------------------


def test_the_kind_is_registered_under_journals_with_the_new_trust_value():
    kind = kinds.kind("reflection_journal")

    assert (kind.source_type, kind.source_trust) == ("reflection_journal", "interpretive")
    assert kind.subdirectory == "journals" and kind.root is config.workspace_dir
    assert kind.note.strip()


def test_an_entry_is_stored_attributed_to_the_entity_with_its_verdict_and_not_indexed(world):
    seed_day(world)
    result = journal.write_entry(DAY, now=NOW)
    row = db.get_artifact(result.entry.artifact_id)

    assert row["artifact_type"] == "reflection_journal"
    assert row["user_id"] == db.entity_user_id()
    assert row["user_id"] not in (world.lyle, world.jodie)
    assert row["extraction_status"] == "extracted" and row["extracted_text"] == ENTRY
    assert result.entry.absolute_path.read_text(encoding="utf-8") == ENTRY
    assert row["filename"] == f"journal-2026-09-21-{row['id'][:8]}.md"
    assert "journals" in str(result.entry.absolute_path)
    note = json.loads(row["extraction_note"])
    assert note["covered_date"] == "2026-09-21" and note["messages_in"] == 4
    assert note["conversations"] == 2 and note["model"] == config.chat_model()
    # The verdict is the one the gate returned, written in the same insert.
    assert json.loads(row["integrity_check"]) == result.verdict.to_dict()
    assert db.get_artifact_chunks(row["id"]) == []   # stored, not indexed


def test_an_unavailable_classifier_is_stored_as_unavailable_never_clean(world, monkeypatch):
    def down(_prompt):
        raise ollama.OllamaUnreachable("down")

    monkeypatch.setattr(classifier, "classify", down)
    seed_day(world)
    result = journal.write_entry(DAY, now=NOW)

    assert json.loads(db.get_artifact(result.entry.artifact_id)["integrity_check"])["status"] \
        == "unavailable"


def test_a_truncated_reply_stores_nothing(world):
    seed_day(world)
    world.model.done_reason = "length"
    before = len(db.list_artifacts())

    with pytest.raises(ollama.OllamaOutputTruncated):
        journal.write_entry(DAY, now=NOW)

    assert len(db.list_artifacts()) == before
    assert storage.find_entry(DAY) is None
    world.model.done_reason = "stop"
    assert journal.write_entry(DAY, now=NOW).status == "written"   # a re-run is safe


def test_an_empty_reply_stores_nothing(world):
    seed_day(world)
    world.model.text = "   "
    with pytest.raises(journal.JournalError):
        journal.write_entry(DAY, now=NOW)
    assert storage.find_entry(DAY) is None


def test_the_reply_is_capped_by_the_same_reservation_a_turn_uses(world):
    seed_day(world)
    journal.write_entry(DAY, now=NOW)

    cap = config.history_output_reserve_tokens()
    assert world.model.calls[0][1]["options"]["num_predict"] == cap
    assert "tools" not in world.model.calls[0][1]   # the run has no tools


def test_the_path_derives_from_the_id_and_nothing_else(world):
    stored = storage.store("x", DAY, {}, None, world.lyle)
    assert stored.storage_path == f"{stored.artifact_id[:2]}/{stored.artifact_id}"


def test_a_storage_call_with_no_verdict_records_none_not_clean(world):
    stored = storage.store("an entry", DAY, {}, None, world.lyle)
    assert db.get_artifact(stored.artifact_id)["integrity_check"] is None


# --- J5: the label ------------------------------------------------------------


def test_the_label_is_pinned_verbatim():
    assert prompt._SOURCE_LABELS["reflection_journal"] == (
        "reflection journal — a later interpretation, not a record of what was said")


def test_an_indexed_entry_renders_with_its_label_and_a_conversation_chunk_does_not(world):
    seed_day(world)
    entry = journal.write_entry(DAY, now=NOW).entry
    indexing.index_existing(entry.artifact_id)

    result = retrieval.search("dentist appointment Tuesday Wednesday")
    rendered = prompt.render_retrieved(result)

    assert "reflection journal — a later interpretation, not a record of what was said" in rendered
    assert any(r.source_type == "reflection_journal" and r.source_trust == "interpretive"
               for r in result.results)
    assert prompt._SOURCE_LABELS.get("conversation") is None


# --- J7: index after reading --------------------------------------------------


def stored_entry(world):
    seed_day(world)
    return journal.write_entry(DAY, now=NOW).entry.artifact_id


def test_an_entry_is_not_retrievable_before_index_and_is_after(world):
    entry = stored_entry(world)
    query = "dentist appointment Tuesday Wednesday"

    before = retrieval.search(query)
    assert not [r for r in before.results if r.source_type == "reflection_journal"]

    count, ids = indexing.index_existing(entry)
    after = retrieval.search(query)

    assert count >= 1
    assert {r.chunk_id for r in after.results if r.source_type == "reflection_journal"} & set(ids)


def test_insert_chunks_without_the_keyword_does_not_check_for_existing_chunks(world):
    """The default path is what every other writer uses: with the keyword absent, a second
    batch for an artifact that already has chunks is written, exactly as before."""
    entry = stored_entry(world)

    def row(i):
        return {"chunk_id": f"c{i}", "conversation_id": None, "user_id": world.lyle,
                "text": f"t{i}", "source_type": "reflection_journal",
                "source_trust": "interpretive", "text_sha256": str(i), "chunk_index": i,
                "artifact_id": entry}

    db.insert_chunks([row(0)])
    db.insert_chunks([row(1)])          # no keyword: no refusal
    assert len(db.get_artifact_chunks(entry)) == 2
    with pytest.raises(db.ArtifactAlreadyIndexed):
        db.insert_chunks([row(2)], only_if_unindexed=entry)
    assert len(db.get_artifact_chunks(entry)) == 2


def test_a_double_index_is_refused_and_writes_nothing_more(world):
    entry = stored_entry(world)
    indexing.index_existing(entry)
    chunks = len(db.get_artifact_chunks(entry))

    with pytest.raises(indexing.AlreadyIndexed):
        indexing.index_existing(entry)

    assert len(db.get_artifact_chunks(entry)) == chunks


def test_the_already_indexed_check_is_inside_the_inserting_transaction(world):
    """Two runs that both pass a separate check must not both write: the insert itself
    refuses, and rolls back."""
    entry = stored_entry(world)
    indexing.index_existing(entry)
    row = {"chunk_id": "c2", "conversation_id": None, "user_id": world.lyle, "text": "t",
           "source_type": "reflection_journal", "source_trust": "interpretive",
           "text_sha256": "x", "chunk_index": 9, "artifact_id": entry}
    before = len(db.get_artifact_chunks(entry))

    with pytest.raises(db.ArtifactAlreadyIndexed):
        db.insert_chunks([row], only_if_unindexed=entry)

    assert len(db.get_artifact_chunks(entry)) == before


def test_another_kind_and_an_unknown_id_are_refused(world):
    piece = writing.store("A piece.", world.lyle, title="P")
    with pytest.raises(indexing.IndexRefused, match="creative_writing"):
        indexing.index_existing(piece.artifact_id)
    with pytest.raises(indexing.IndexRefused, match="no artifact"):
        indexing.index_existing("0" * 32)


def test_a_failed_embedding_leaves_no_chunks_and_a_retry_succeeds(world, monkeypatch):
    entry = stored_entry(world)

    def down(*_a, **_k):
        raise ollama.OllamaUnreachable("embedder down")

    monkeypatch.setattr(indexing.ollama, "embed", down)
    with pytest.raises(ollama.OllamaUnreachable):
        indexing.index_existing(entry)
    assert db.get_artifact_chunks(entry) == []

    monkeypatch.setattr(indexing.ollama, "embed", deterministic)
    assert indexing.index_existing(entry)[0] >= 1


def test_list_unindexed_shows_held_entries_and_drops_indexed_ones(world):
    entry = stored_entry(world)
    assert [r["id"] for r in storage.list_unindexed()] == [entry]
    indexing.index_existing(entry)
    assert storage.list_unindexed() == []


# --- J7: what the operator sees ----------------------------------------------


def run_cli(monkeypatch, capsys, *argv):
    import sys

    from scripts import write_journal

    monkeypatch.setattr(sys, "argv", ["write_journal", *argv])
    code = write_journal.main()
    return code, capsys.readouterr()


def test_the_command_prints_the_entry_path_status_findings_and_what_you_can_do(
        world, monkeypatch, capsys):
    seed_day(world)
    monkeypatch.setattr(journal, "datetime", type("D", (), {
        "now": staticmethod(lambda tz=None: NOW), "combine": datetime.combine,
        "fromisoformat": datetime.fromisoformat}))
    monkeypatch.setattr(
        classifier, "classify",
        lambda _p: ("CONTRADICTS-SELF\n- I kept thinking about it overnight "
                    "| nothing runs between replies"))
    world.model.text = "I kept thinking about it overnight."

    code, out = run_cli(monkeypatch, capsys, "--date", "2026-09-21")
    text = out.out

    assert code == 0
    assert "I kept thinking about it overnight." in text
    assert "stored at:" in text and "journals" in text
    assert "NOT indexed" in text
    assert "integrity verdict: flagged" in text
    assert "cited: 'I kept thinking about it overnight.'" in text and "reason:" in text
    assert "--index" in text and "cannot edit, delete or regenerate" in text
    assert "Your reading is the control" in text


def test_the_command_prints_unavailable_as_such_never_as_clean(world, monkeypatch, capsys):
    seed_day(world)
    monkeypatch.setattr(journal, "datetime", type("D", (), {
        "now": staticmethod(lambda tz=None: NOW), "combine": datetime.combine,
        "fromisoformat": datetime.fromisoformat}))

    def down(_p):
        raise ollama.OllamaUnreachable("down")

    monkeypatch.setattr(classifier, "classify", down)
    code, out = run_cli(monkeypatch, capsys, "--date", "2026-09-21")

    assert code == 0
    assert "integrity verdict: unavailable" in out.out and "UNCHECKED, not clean" in out.out
    assert "integrity verdict: clean" not in out.out


def test_the_command_index_and_list_flags(world, monkeypatch, capsys):
    entry = stored_entry(world)

    code, out = run_cli(monkeypatch, capsys, "--list-unindexed")
    assert code == 0 and entry in out.out

    code, out = run_cli(monkeypatch, capsys, "--index", entry)
    assert code == 0 and "indexed" in out.out

    code, out = run_cli(monkeypatch, capsys, "--index", entry)
    assert code == 0 and "already indexed" in out.out

    code, out = run_cli(monkeypatch, capsys, "--index", "0" * 32)
    assert code == 1 and "refused" in out.err


def test_the_command_refuses_a_day_that_is_not_over(world, monkeypatch, capsys):
    monkeypatch.setattr(journal, "datetime", type("D", (), {
        "now": staticmethod(lambda tz=None: NOW), "combine": datetime.combine,
        "fromisoformat": datetime.fromisoformat}))
    code, out = run_cli(monkeypatch, capsys, "--date", "2026-09-23")
    assert code == 2 and "refused" in out.err


# --- the rules the design states about its own shape -------------------------


def test_nothing_in_the_run_takes_an_actor_or_a_role():
    import inspect

    for fn in (journal.write_entry, journal.prepare, journal.generate, storage.store,
               indexing.index_existing):
        assert not set(inspect.signature(fn).parameters) & {"actor", "role", "user", "permissions"}


def test_the_run_reads_the_store_directly_and_offers_no_tools():
    """J3: raw messages, not retrieval (which would pull earlier journal chunks in and
    build interpretations on interpretations), and no tools (J9)."""
    import inspect

    assert not hasattr(journal, "retrieval") and not hasattr(journal, "memory_search")
    assert "tools=" not in inspect.getsource(journal)
