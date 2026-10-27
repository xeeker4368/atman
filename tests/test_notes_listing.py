"""The listing: the entity's notes, one line each, on every turn when Notes is on (Notes v2
section 3; ``program/engine/notes_listing.py``)."""

from __future__ import annotations

import pytest

from program import config
from program.engine import loop, notes_listing, prompt, turn
from program.memory import db
from program.memory import note_admin as admin
from program.settings.permissions import Actor, Role
from program.tools import note_texts as texts

SITUATION = "The current time is 2026-09-01T16:00:00+00:00."


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "true")
    config.reload()
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    return Actor(user_id=lyle, name="Lyle", role=Role.ADMIN)


def _confirmed(note_id, stamp):
    """Set a note's confirmation time (an active note may still be edited)."""
    with db.transaction() as conn:
        conn.execute("UPDATE notes SET last_confirmed_at = ? WHERE id = ?", (stamp, note_id))


def test_the_header_is_the_designs_and_passes_the_authored_text_checks():
    assert texts.LISTING_HEADER == (
        "Your notes, one line each. They are written after a conversation closes, and Lyle "
        "approves each one, so nothing said in this conversation makes or changes a note. "
        "note_search gives a note in full.")
    for text in (texts.LISTING_HEADER, texts.LISTING_NONE, texts.listing_overflow(3),
                 texts.listing_overflow(1)):
        prompt.check_authored_text(text, "listing")
    assert texts.listing_overflow(3) == "3 more notes are not listed; note_search finds them."
    assert texts.listing_overflow(1) == "1 more note is not listed; note_search finds them."


def test_with_no_notes_it_reads_none_yet(store):
    assert notes_listing.system_listing() == f"{texts.LISTING_HEADER}\n{texts.LISTING_NONE}"


def test_with_notes_off_there_is_no_listing(store, monkeypatch):
    admin.operator_add("person", "Jodie", "Takes oat milk.", line="Oat milk in coffee.")
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "false")
    config.reload()
    assert notes_listing.system_listing() == ""


def test_person_notes_first_then_the_rest_each_newest_confirmed_first(store):
    ids = {}
    for kind, subject, stamp in [("topic", "Garden", "2026-10-03"),
                                 ("person", "Jodie", "2026-10-01"),
                                 ("project", "Shed", "2026-10-05"),
                                 ("person", "Lyle", "2026-10-04")]:
        ids[subject] = admin.operator_add(kind, subject, f"About {subject}.",
                                          line=f"{subject} line.").note_id
        _confirmed(ids[subject], f"{stamp}T00:00:00+00:00")
    retired = admin.operator_add("person", "Gone", "Old.", line="Gone line.").note_id
    admin.operator_retire(retired)
    lines = notes_listing.system_listing().split("\n")[1:]
    assert lines == ["- Lyle: Lyle line.", "- Jodie: Jodie line.", "- Shed: Shed line.",
                     "- Garden: Garden line."]


def test_a_note_without_a_line_shows_its_text_cut_to_the_line_limit(store):
    admin.operator_add("topic", "Long", "word " * 60)
    (entry,) = notes_listing.system_listing().split("\n")[1:]
    assert entry.startswith("- Long: word word") and entry.endswith("…")
    assert len(entry) - len("- Long: ") <= config.notes_line_max_chars()


def test_the_cap_keeps_whole_entries_and_counts_the_rest(store):
    for i in range(30):
        admin.operator_add("topic", f"Subject {i:02d}", "x", line="L" * 90)
    listing = notes_listing.system_listing()
    assert len(listing) <= config.notes_listing_max_chars() == 1200
    lines = listing.split("\n")
    entries = [x for x in lines if x.startswith("- ")]
    assert all(x.endswith("L" * 90) for x in entries)                  # never a cut entry
    assert lines[-1] == texts.listing_overflow(30 - len(entries))
    assert 0 < len(entries) < 30


def test_five_worst_case_entries_fit_beside_the_overflow_line_as_the_design_states():
    rows = [{"subject": "S" * config.notes_max_subject_chars(), "line": "L" * 100,
             "text": "t"} for _ in range(7)]
    listing = notes_listing.render(rows)
    assert listing.count("\n- ") == 5 and listing.endswith(texts.listing_overflow(2))
    # with no overflow line to carry, a sixth fits: 196 + 6 x 165 = 1,186
    assert notes_listing.render(rows[:6]).count("\n- ") == 6


def test_it_sits_after_the_speaker_line_and_before_the_tools_list_and_is_counted():
    listing = notes_listing.render([{"subject": "Jodie", "line": "Oat milk.", "text": "x"}])
    assembled = prompt.assemble_turn(
        [{"role": "user", "content": "hi"}], SITUATION, None, speaker="Lyle",
        notes_listing=listing, earlier_tools="EARLIER TOOLS LIST")
    system = assembled.system
    assert (system.index("You are talking with Lyle.") < system.index(texts.LISTING_HEADER)
            < system.index("EARLIER TOOLS LIST"))
    assert assembled.notes_listing_chars == len(listing)
    parts = (assembled.soul_chars + assembled.operational_chars + assembled.situation_chars
             + assembled.speaker_chars + assembled.notes_listing_chars
             + assembled.earlier_tools_chars + assembled.retrieved_chars
             + assembled.scaffolding_chars)
    assert parts == len(system)
    assert texts.LISTING_HEADER not in SITUATION


def test_a_note_naming_the_project_does_not_fail_the_turn():
    """Entries are a person's words (S9's rule for records): only the header is checked."""
    listing = notes_listing.render([{"subject": "Anam", "line": "Lyle's project, Anam.",
                                     "text": "x"}])
    assert "Anam" in prompt.build_system_prompt(SITUATION, notes_listing=listing)


def test_every_turn_sends_the_listing_and_the_gate_never_sees_it(store, monkeypatch):
    admin.operator_add("person", "Jodie", "Takes oat milk.", line="Oat milk in coffee.")
    systems, situations = [], []

    def fake(messages, **kwargs):
        if messages and messages[0].get("role") == "system":
            systems.append(messages[0]["content"])
        return {"message": {"role": "assistant", "content": "ok"}}

    real_check = turn.gate.check

    def check(answer, trace=(), situation="", *a, **k):
        situations.append(situation)
        return real_check(answer, trace, situation, *a, **k)

    monkeypatch.setattr(turn, "_retrieve", lambda query: None)
    monkeypatch.setattr(loop.ollama, "chat", fake)
    monkeypatch.setattr(turn.gate, "check", check)
    first = turn.handle_user_message(store, "hello there")
    turn.handle_user_message(store, "and again", first.conversation_id)
    assert len(systems) == 2 and all("- Jodie: Oat milk in coffee." in s for s in systems)
    assert situations and not any(texts.LISTING_HEADER in s for s in situations)
