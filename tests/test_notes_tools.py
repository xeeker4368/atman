"""`note_search` and quote resolution: Notes piece 3, as Notes v2 leaves them.

Design of record: `docs/NOTES_DESIGN.md` (N0-N7, N11) with the rulings of 2026-10-02, and
`docs/NOTES_V2_DESIGN.md` section 4: `note_propose` is removed, `note_search` stays (its rendering
unchanged, its description saying notes are written after a conversation closes), and quote
resolution takes the context message ids directly (the note pass passes the closed conversation's).
A real store and the real tool. Notes ships dark, so every test that needs it offered builds its
own registry.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from program import config
from program.engine import loop, prompt
from program.integrity import classifier
from program.memory import db, notes
from program.tools import catalog, note_quotes, note_search, registry
from program.tools import note_texts as texts
from program.tools.note_search import NOTE_SEARCH
from program.tools.registry import Tool, ToolOutcome, ToolRegistry

HEX32 = re.compile(r"\b[0-9a-f]{32}\b")


@pytest.fixture
def world(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    monkeypatch.setattr(classifier, "classify", lambda p: "CONSISTENT")
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie")
    conv = db.start_conversation(lyle)
    other = db.start_conversation(jodie)

    def say(cid, uid, role, text, ts):
        return db.save_message(cid, uid, role, text, timestamp=ts)

    ids = {
        "trigger": say(conv, lyle, "user", "Jodie takes her coffee with oat milk, never dairy.",
                       "2026-10-02T10:00:00+00:00"),
        "reply": say(conv, lyle, "assistant",
                     "I will remember that Jodie prefers a flat white in the mornings.",
                     "2026-10-02T10:00:05+00:00"),
        "later": say(conv, lyle, "user", "The backup should eventually run on a schedule.",
                     "2026-10-02T10:01:00+00:00"),
        # not in the context: another conversation, another person
        "elsewhere": say(other, jodie, "user", "My grandmother kept her starter above the stove.",
                         "2026-09-30T09:00:00+00:00"),
    }
    context = frozenset({ids["trigger"], ids["reply"], ids["later"]})
    return type("W", (), {"lyle": lyle, "jodie": jodie, "conv": conv, "other": other,
                          "ids": ids, "context": context,
                          "reg": ToolRegistry([NOTE_SEARCH])})


def rows(table):
    with db.connection() as conn:
        return conn.execute(f"SELECT * FROM {table}").fetchall()


def add_note(world, text, *, status="active", subject="Jodie", kind="person", nid=None,
             confirmed="2026-10-01T00:00:00+00:00"):
    nid = nid or db.new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO notes (id, subject_kind, subject, text, status, version, origin, "
            "created_at, last_confirmed_at) VALUES (?, ?, ?, ?, ?, 1, 'operator', ?, ?)",
            (nid, kind, subject, text, status, confirmed, confirmed))
    return nid


# --- dark by default, and what it declares ---------------------------------------------------


def test_note_search_is_in_the_catalogue_but_not_offered_while_notes_is_dark():
    registry.reset_default_registry()
    names = {t.name for t in catalog.TOOLS}
    assert "note_search" in names and "note_propose" not in names
    assert "note_search" not in registry.default_registry().names
    assert NOTE_SEARCH.enabled is config.notes_enabled


def test_notes_enabled_is_bootstrap_and_switches_note_search_on_with_a_restart(monkeypatch):
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "true")
    config.reload()
    registry.reset_default_registry()
    try:
        names = set(registry.default_registry().names)
        assert "note_search" in names and "note_propose" not in names
    finally:
        monkeypatch.setenv("ANAM_NOTES_ENABLED", "false")
        config.reload()
        registry.reset_default_registry()
    assert not config.notes_enabled()


def test_what_note_search_declares_and_that_nothing_is_model_settable():
    assert NOTE_SEARCH.takes_attribution is False
    assert not hasattr(NOTE_SEARCH, "takes_origin")
    shown = json.dumps(NOTE_SEARCH.to_ollama_schema())
    assert '"origin"' not in shown and '"attribution"' not in shown
    assert not set(inspect.signature(note_search.search_notes).parameters) & {
        "actor", "role", "user", "origin"}


def test_the_description_says_notes_are_written_after_a_conversation_closes():
    assert NOTE_SEARCH.description == (
        "Search notes about people, topics and projects in this household. "
        "Notes are written after a conversation closes. "
        "Not conversation memory (use memory_search).")


def test_the_untrusted_output_flag_is_declared_on_exactly_the_six_external_tools():
    assert registry.untrusted_tools() == (
        "moltbook_browse", "moltbook_read_agent", "moltbook_read_post", "moltbook_search",
        "web_fetch", "web_search")
    assert not NOTE_SEARCH.untrusted_output
    assert Tool(name="probe_x", description="d", parameters={"type": "object"},
                handler=lambda: "").untrusted_output is False


# --- every sentence the model reads ----------------------------------------------------------


def _all_texts():
    return [texts.NO_MATCH, texts.HEADER, texts.EMPTY_QUERY, NOTE_SEARCH.description,
            texts.LISTING_HEADER, texts.LISTING_NONE, texts.listing_overflow(1),
            texts.listing_overflow(7)]


def test_every_text_passes_the_authored_text_checks_and_names_no_identifier():
    for text in _all_texts():
        prompt.check_authored_text(text, "note text")
        assert not HEX32.search(text), text


def test_the_search_texts_are_pinned_verbatim():
    assert texts.NO_MATCH == (
        "No note matches that. Notes hold only what was written down as a note, so this says "
        "nothing about whether it was ever talked about; memory_search covers conversations.")
    assert texts.HEADER == (
        "Notes: statements about people, topics and projects in this household. "
        "They are not conversation records, and a note is only as current as its age says.")


# --- the cap, derived (N11) --------------------------------------------------------------------


def test_max_text_chars_is_the_largest_that_fits_a_worst_case_page():
    cap = config.agent_max_tool_result_chars()
    derived = note_search.derive_max_text_chars()
    assert config.notes_max_text_chars() == derived, (
        "defaults.toml's notes.max_text_chars is not what the live renderer derives")
    assert len(note_search.worst_case_render(derived)) <= cap
    assert len(note_search.worst_case_render(derived + 1)) > cap


def test_a_real_page_of_maximal_notes_renders_under_the_cap_untruncated(world):
    for i in range(config.notes_max_results() + 3):
        add_note(world, "T" * config.notes_max_text_chars(), nid=db.new_id(),
                 subject="S" * config.notes_max_subject_chars(), kind="project")
    out = world.reg.dispatch("note_search", {"query": "S" * config.notes_max_subject_chars()})

    assert out.outcome is ToolOutcome.OK
    assert out.value.count("[note ") == config.notes_max_results()
    assert len(out.value) <= config.agent_max_tool_result_chars()
    rendered = loop.render_tool_result(out)
    assert "truncated" not in rendered.lower() and rendered == out.value


def test_the_schema_stays_inside_its_budget():
    """N12: the real tokenizer measured 68 tokens for note_search's first wording. A bound on the
    JSON (not tokens) catches growth without a model."""
    assert len(json.dumps(NOTE_SEARCH.to_ollama_schema())) <= 450


# --- note_search -------------------------------------------------------------------------------


def test_search_finds_active_notes_and_shows_a_short_id_and_an_age(world):
    nid = add_note(world, "Takes her coffee with oat milk.", confirmed="2026-09-20T00:00:00+00:00")
    out = world.reg.dispatch("note_search", {"query": "oat milk coffee"})

    assert out.outcome is ToolOutcome.OK
    assert out.value.startswith(texts.HEADER)
    assert f"[note {nid[:8]} · person · Jodie · confirmed " in out.value
    assert nid not in out.value, "the full id was shown"
    assert "Takes her coffee with oat milk." in out.value


def test_age_is_derived_from_last_confirmed():
    now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    assert note_search.age_phrase("2026-10-02T01:00:00+00:00", now) == "today"
    assert note_search.age_phrase("2026-10-01T05:00:00+00:00", now) == "1 day ago"
    assert note_search.age_phrase((now - timedelta(days=12)).isoformat(), now) == "12 days ago"
    assert note_search.age_phrase("not a time", now) == "an unknown time ago"


def test_a_retired_or_superseded_note_is_never_found(world):
    add_note(world, "The oat milk note, retired.", status="retired")
    add_note(world, "The oat milk note, superseded.", status="superseded")
    assert world.reg.dispatch("note_search", {"query": "oat milk"}).value == texts.NO_MATCH


def test_a_stale_index_entry_cannot_show_a_retired_note(world):
    """N13/N17 #20: the rows are re-read and filtered by status after the index is consulted."""
    nid = add_note(world, "Originally about oat milk.")
    with db.transaction() as conn:
        conn.execute("UPDATE notes SET status = 'retired' WHERE id = ?", (nid,))
        rowid = conn.execute("SELECT rowid FROM notes WHERE id = ?", (nid,)).fetchone()[0]
        # Put the retired note back in the index behind the triggers' back: stale drift.
        conn.execute("INSERT INTO notes_fts(rowid, subject, text) VALUES (?, ?, ?)",
                     (rowid, "Jodie", "Originally about oat milk."))
        assert conn.execute("SELECT COUNT(*) FROM notes_fts WHERE notes_fts MATCH 'oat'"
                            ).fetchone()[0] == 1, "precondition: the index does hold it"

    assert world.reg.dispatch("note_search", {"query": "oat milk"}).value == texts.NO_MATCH


def test_a_miss_says_exactly_the_shared_sentence(world):
    out = world.reg.dispatch("note_search", {"query": "pottery kilns"})
    assert out.outcome is ToolOutcome.OK and out.value == texts.NO_MATCH


@pytest.mark.parametrize("query", ['"unbalanced', "AND OR NOT", "a-b:c*", "'; DROP TABLE notes;--",
                                   "???", "NEAR(", ")("])
def test_hostile_or_empty_of_terms_queries_never_reach_fts_as_syntax(world, query):
    add_note(world, "oat milk")
    out = world.reg.dispatch("note_search", {"query": query})
    assert out.outcome is ToolOutcome.OK
    assert rows("notes"), "the table was damaged"


def test_an_empty_query_is_a_tool_error_with_the_stated_reason(world):
    for query in ("", "   "):
        out = world.reg.dispatch("note_search", {"query": query})
        assert out.outcome is ToolOutcome.TOOL_ERROR and texts.EMPTY_QUERY in out.error


def test_at_most_max_results_notes_come_back(world):
    for i in range(config.notes_max_results() + 4):
        add_note(world, f"coffee fact {i}", subject=f"S{i}")
    out = world.reg.dispatch("note_search", {"query": "coffee"})
    assert out.value.count("[note ") == config.notes_max_results()


def test_search_does_not_touch_conversation_memory_and_is_not_filtered_by_who_asks(world):
    """Decision #20 applied to notes, and N0: notes and conversation memory are separate.
    A conversation chunk containing the word is not returned; a note about Jodie is returned
    to a turn that is Lyle's, because nothing in the path takes an asker."""
    db.insert_chunk(chunk_id="c1", conversation_id=world.conv, user_id=world.lyle,
                    text="oat milk conversation chunk", source_type="conversation",
                    source_trust="firsthand", text_sha256="x", chunk_index=0,
                    first_message_id=world.ids["trigger"], last_message_id=world.ids["later"])
    assert world.reg.dispatch("note_search", {"query": "oat milk"}).value == texts.NO_MATCH
    add_note(world, "Takes oat milk.", subject="Jodie")
    assert "Jodie" in world.reg.dispatch("note_search", {"query": "oat milk"}).value
    assert "chunk" not in inspect.getsource(note_search).replace("chunked", "")


# --- resolving a quote: what is accepted, and from where ----------------------------------------


def resolve(world, quote, context=None):
    return note_quotes.resolve_quote(quote, world.context if context is None else context)


def refused(world, reason, quote, context=None):
    with pytest.raises(texts.QuoteRefused) as caught:
        resolve(world, quote, context)
    assert str(caught.value) == reason


def test_resolve_quote_takes_the_context_ids_directly():
    params = list(inspect.signature(note_quotes.resolve_quote).parameters)
    assert params == ["quote", "context_message_ids"]


def test_a_quote_too_short_in_characters_or_in_words(world):
    reason = texts.quote_too_short(config.notes_min_quote_chars(), config.notes_min_quote_words())
    refused(world, reason, "oat milk, never dairy")
    refused(world, reason, "supercalifragilisticexpialidocious oat")   # long, but two words


def test_a_quote_of_the_entitys_own_reply_is_not_a_persons_words(world):
    refused(world, texts.QUOTE_NOT_A_PERSONS_WORDS, "Jodie prefers a flat white in the mornings")


def test_a_quote_that_appears_in_no_message(world):
    refused(world, texts.QUOTE_NO_MATCH, "Jodie hates mushrooms with a passion")


def test_a_paraphrase_is_not_a_quote(world):
    """Nothing looser than whitespace, quote marks and case."""
    refused(world, texts.QUOTE_NO_MATCH, "Jodie drinks her coffee with oat milk")
    refused(world, texts.QUOTE_NO_MATCH, "Jodie take her coffee with oat milks")
    # every word present, in another order: still not what anyone said
    refused(world, texts.QUOTE_NO_MATCH, "oat milk with her coffee Jodie takes")


def test_a_quote_matching_two_different_messages_is_ambiguous(world):
    def say(t, ts):
        return db.save_message(world.conv, world.lyle, "user", t, timestamp=ts)

    a = say("Lyle wants the backups to live on the NAS in the hall.", "2026-10-02T09:00:00+00:00")
    b = say("Lyle wants the backups to live on the NAS in the cellar.", "2026-10-02T09:01:00+00:00")
    refused(world, texts.quote_ambiguous(2), "Lyle wants the backups to live on the NAS",
            frozenset({a, b}))


def test_identical_words_from_two_different_people_are_ambiguous(world):
    same = "We need more of the good coffee for Saturday."
    ts = "2026-10-02T09:0{}:00+00:00"
    a = db.save_message(world.conv, world.lyle, "user", same, timestamp=ts.format(0))
    b = db.save_message(world.other, world.jodie, "user", same, timestamp=ts.format(1))
    refused(world, texts.quote_ambiguous(2), "We need more of the good coffee for Saturday",
            frozenset({a, b}))


def test_normalisation_folds_whitespace_quote_marks_and_case_and_nothing_else(world):
    msg = db.save_message(world.conv, world.lyle, "user",
                          'Lyle said it’s "fine" for the grinder   to stay\nwhere it is.',
                          timestamp="2026-10-02T09:30:00+00:00")
    got = resolve(world, "LYLE SAID IT'S “fine” for the grinder to stay where it is",
                  frozenset({msg}))
    assert got.message_id == msg and got.tier == "context" and got.identical_count == 1
    assert note_quotes.normalise("  A’b \t C ") == "a'b c"


def test_a_quote_from_a_message_in_the_context_resolves_in_the_context_tier(world):
    got = resolve(world, "The backup should eventually run on a schedule")
    assert (got.message_id, got.tier) == (world.ids["later"], "context")


def test_the_context_tier_is_preferred_over_the_store(world):
    """The same words said by a person inside the context and in another conversation: the
    context one is taken, and not as an ambiguity."""
    same = "The market opens at nine unless it rains in the morning."
    inside = db.save_message(world.conv, world.lyle, "user", same,
                             timestamp="2026-10-02T09:00:00+00:00")
    db.save_message(world.other, world.jodie, "user", same, timestamp="2026-09-01T09:00:00+00:00")
    got = resolve(world, "The market opens at nine unless it rains", frozenset({inside}))
    assert (got.message_id, got.tier) == (inside, "context")


def test_a_quote_outside_the_context_resolves_in_the_store_tier_and_says_so(world):
    got = resolve(world, "My grandmother kept her starter above the stove")
    assert (got.message_id, got.tier) == (world.ids["elsewhere"], "store")


def test_the_store_tier_is_not_chunk_based(world):
    """No chunk exists for any message here; the store tier still finds it."""
    assert rows("chunks") == []
    assert resolve(world, "My grandmother kept her starter above the stove").tier == "store"


def test_identical_text_from_the_same_person_is_one_piece_of_evidence_with_its_count(world):
    text = "Make me an image of a copper kettle on a slate worktop."
    first = db.save_message(world.conv, world.lyle, "user", text,
                            timestamp="2026-10-02T08:00:00+00:00")
    newest = db.save_message(world.conv, world.lyle, "user", text,
                             timestamp="2026-10-02T08:30:00+00:00")
    got = resolve(world, "image of a copper kettle on a slate worktop", frozenset({first, newest}))
    assert (got.message_id, got.identical_count) == (newest, 2)


def test_a_quote_said_by_a_person_and_repeated_by_the_entity_resolves_to_the_person(world):
    said = db.save_message(world.conv, world.lyle, "user", "Grind the beans finer for the shot.",
                           timestamp="2026-10-02T09:00:00+00:00")
    db.save_message(world.conv, world.lyle, "assistant", "Grind the beans finer for the shot.",
                    timestamp="2026-10-02T09:00:05+00:00")
    got = resolve(world, "Grind the beans finer for the shot", frozenset())
    assert got.message_id == said and got.tier == "store"


def test_a_resolved_quote_serialises_as_the_stored_evidence_shape(world):
    got = resolve(world, "The backup should eventually run on a schedule")
    assert got.to_dict() == {"quote": "The backup should eventually run on a schedule",
                             "message_id": world.ids["later"], "tier": "context",
                             "identical_count": 1}


# --- the writers carry the retry --------------------------------------------------------------


def test_every_write_in_the_notes_module_carries_the_retry():
    undecorated = []
    for name, fn in inspect.getmembers(notes, inspect.isfunction):
        if fn.__module__ != notes.__name__ or name.startswith("_"):
            continue
        if "with db.transaction()" in inspect.getsource(fn) and not hasattr(fn, "__wrapped__"):
            undecorated.append(name)
    assert undecorated == [], f"writers missing @retry_on_locked: {undecorated}"
