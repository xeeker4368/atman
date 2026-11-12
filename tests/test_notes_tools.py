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
# The world's context is one conversation: the trigger and "later" (Lyle) and one reply (the
# entity). "elsewhere" is Jodie's, in another conversation.


def resolve(world, quote, context=None):
    return note_quotes.resolve_quote(quote, world.context if context is None else context)


def refused(world, reason, quote, context=None):
    with pytest.raises(texts.QuoteRefused) as caught:
        resolve(world, quote, context)
    assert str(caught.value) == reason


def say(world, text, ts, role="user"):
    return db.save_message(world.conv, world.lyle, role, text, timestamp=ts)


def test_resolve_quote_takes_the_context_ids_directly():
    params = list(inspect.signature(note_quotes.resolve_quote).parameters)
    assert params == ["quote", "context_message_ids"]


def test_a_quote_of_one_two_word_fragment_is_too_short(world):
    reason = texts.quote_too_short(config.notes_min_fragment_words())
    assert config.notes_min_fragment_words() == 3
    refused(world, reason, "oat milk")
    refused(world, reason, "never dairy.")
    refused(world, reason, '"  "')                                     # nothing left
    refused(world, reason, '"...!"')


def test_a_quote_of_only_short_matching_fragments_is_refused_and_named(world):
    reason = texts.quote_too_short(config.notes_min_fragment_words())
    refused(world, f"all 3 fragments ('oat milk', 'never dairy', 'a schedule'): {reason}",
            "oat milk... never dairy. a schedule")


def test_a_three_word_fragment_is_enough(world):
    got = resolve(world, "oat milk, never")
    assert got.message_id == world.ids["trigger"]


def test_a_short_matching_sentence_beside_a_long_one_is_accepted(world):
    got = resolve(world, "Never dairy. The backup should eventually run on a schedule.")
    assert [f.text for f in got.fragments] == ["Never dairy",
                                               "The backup should eventually run on a schedule"]
    assert [f.message_id for f in got.fragments] == [world.ids["trigger"], world.ids["later"]]


def test_a_short_fragment_must_still_match(world):
    refused(world, "fragment 2 of 2 ('never tea'): " + texts.QUOTE_NO_MATCH,
            "Jodie takes her coffee with oat milk... never tea")


def test_a_quote_of_the_entitys_own_reply_is_not_a_persons_words(world):
    refused(world, texts.QUOTE_NOT_A_PERSONS_WORDS, "Jodie prefers a flat white in the mornings")


def test_a_fragment_taken_from_the_entitys_reply_is_refused_and_named(world):
    refused(world, "fragment 2 of 2 ('prefers a flat white'): " + texts.QUOTE_NOT_A_PERSONS_WORDS,
            "Jodie takes her coffee with oat milk... prefers a flat white")


def test_a_quote_that_appears_in_no_message(world):
    refused(world, texts.QUOTE_NO_MATCH, "Jodie hates mushrooms with a passion")


def test_an_exact_quote_with_one_invented_fragment_is_refused_and_named(world):
    refused(world, "fragment 2 of 3 ('and she hates mushrooms'): " + texts.QUOTE_NO_MATCH,
            "Jodie takes her coffee with oat milk... and she hates mushrooms... "
            "The backup should eventually run on a schedule")


def test_a_paraphrase_is_not_a_quote(world):
    """Nothing looser than whitespace, quote marks and case."""
    refused(world, texts.QUOTE_NO_MATCH, "Jodie drinks her coffee with oat milk")
    refused(world, texts.QUOTE_NO_MATCH, "Jodie take her coffee with oat milks")
    # every word present, in another order: still not what anyone said
    refused(world, texts.QUOTE_NO_MATCH, "oat milk with her coffee Jodie takes")


@pytest.mark.parametrize("joiner", [". ", "! ", "? ", ".  ", ".\" ", "!\u201d "])
def test_messages_joined_as_sentences_are_split_and_accepted(world, joiner):
    """Run 5's commonest shape: two messages run together as sentences, no ellipsis."""
    got = resolve(world, f"Jodie takes her coffee with oat milk, never dairy{joiner}"
                         "The backup should eventually run on a schedule")
    assert [f.message_id for f in got.fragments] == [world.ids["trigger"], world.ids["later"]]


def test_sentence_end_punctuation_the_person_did_not_type_is_ignored(world):
    """Run 5: the quote ends each sentence with a period the message does not have."""
    plain = say(world, "taking the dog out now", "2026-10-02T11:00:00+00:00")
    other = say(world, "back in ten minutes or so", "2026-10-02T11:01:00+00:00")
    for quote in ("taking the dog out now.", "taking the dog out now!", "taking the dog out now?",
                  "taking the dog out now,", "taking the dog out now. back in ten minutes.",
                  '"taking the dog out now."'):
        assert resolve(world, quote, frozenset({plain, other})).message_id == plain


def test_a_joined_quote_with_a_changed_word_in_one_sentence_is_refused_and_named(world):
    """Run 5's row 8: one sentence's word differs from what the person typed."""
    a = say(world, "the fence gets its first coat today", "2026-10-02T11:00:00+00:00")
    b = say(world, "supposed to stay dry all weekend", "2026-10-02T11:01:00+00:00")
    refused(world, "fragment 2 of 2 ('suppose to stay dry all weekend'): " + texts.QUOTE_NO_MATCH,
            "the fence gets its first coat today. suppose to stay dry all weekend.",
            frozenset({a, b}))


def test_a_sentence_end_inside_a_message_is_still_one_message_s_words(world):
    """Splitting inside one message still requires each sentence in a person's message."""
    got = resolve(world, "Jodie takes her coffee with oat milk, never dairy.")
    assert got.message_id == world.ids["trigger"] and len(got.fragments) == 1


@pytest.mark.parametrize("joiner", ["...", "....", " ... ", "\u2026", " \u2026 ", "\n", "\r\n",
                                    ".. ", "...\n"])
def test_fragments_joined_from_two_person_messages_are_accepted(world, joiner):
    """Run 5's shape (a): the parts of what a person said in several messages, joined with an
    ellipsis (with or without spaces, three dots or four) or a line break."""
    quote = f"Jodie takes her coffee with oat milk{joiner}The backup should eventually run"
    got = resolve(world, quote)
    assert [f.message_id for f in got.fragments] == [world.ids["trigger"], world.ids["later"]]
    assert got.message_id == world.ids["trigger"]


@pytest.mark.parametrize("quote", [
    '"Jodie takes her coffee with oat milk"',
    "'Jodie takes her coffee with oat milk'",
    "\u201cJodie takes her coffee with oat milk\u201d",
    "  \u2018Jodie takes her coffee with oat milk\u2019  ",
    "  \u2018Jodie takes her coffee with oat milk.\u2019  ",
    '"Jodie takes her coffee with oat milk... The backup should eventually run"',
    '"Jodie takes her coffee with oat milk"... "The backup should eventually run"',
])
def test_wrapping_quote_marks_are_stripped(world, quote):
    """Run 5's shape (b): the quote, or each fragment, wrapped in quote marks."""
    assert resolve(world, quote).message_id == world.ids["trigger"]


def test_normalisation_folds_whitespace_quote_marks_and_case_and_nothing_else(world):
    """Shape (c): curly against straight, case and whitespace runs, on either side."""
    msg = say(world, 'Lyle said it\u2019s "fine" for the grinder   to stay\nwhere it is.',
              "2026-10-02T09:30:00+00:00")
    got = resolve(world, "LYLE SAID IT'S \u201cfine\u201d for the grinder to stay where it is",
                  frozenset({msg}))
    assert got.message_id == msg and got.fragments[0].identical_count == 1
    assert note_quotes.normalise("  A\u2019b \t C ") == "a'b c"


def test_a_quote_from_another_conversation_is_refused(world):
    """Only this conversation's person messages count: no other conversation is searched."""
    refused(world, texts.QUOTE_NO_MATCH, "My grandmother kept her starter above the stove")


def test_a_fragment_in_two_different_messages_resolves_to_the_newest(world):
    a = say(world, "Lyle wants the backups to live on the NAS in the hall.",
            "2026-10-02T09:00:00+00:00")
    b = say(world, "Lyle wants the backups to live on the NAS in the cellar.",
            "2026-10-02T09:01:00+00:00")
    got = resolve(world, "Lyle wants the backups to live on the NAS", frozenset({a, b}))
    assert (got.message_id, got.fragments[0].identical_count) == (b, 1)


def test_identical_text_from_the_same_person_is_one_piece_of_evidence_with_its_count(world):
    text = "Make me an image of a copper kettle on a slate worktop."
    first = say(world, text, "2026-10-02T08:00:00+00:00")
    newest = say(world, text, "2026-10-02T08:30:00+00:00")
    got = resolve(world, "image of a copper kettle on a slate worktop", frozenset({first, newest}))
    assert (got.message_id, got.fragments[0].identical_count) == (newest, 2)


def test_a_quote_said_by_a_person_and_repeated_by_the_entity_resolves_to_the_person(world):
    said = say(world, "Grind the beans finer for the shot.", "2026-10-02T09:00:00+00:00")
    echo = say(world, "Grind the beans finer for the shot.", "2026-10-02T09:00:05+00:00",
               role="assistant")
    got = resolve(world, "Grind the beans finer for the shot", frozenset({said, echo}))
    assert got.message_id == said


@pytest.mark.parametrize("quote", [
    '"Jodie takes her coffee with oat milk" "The backup should eventually run"',
    "\u201cJodie takes her coffee with oat milk\u201d  "
    "\u201cThe backup should eventually run\u201d",
    "'Jodie takes her coffee with oat milk' 'The backup should eventually run'",
])
def test_a_closing_mark_space_and_opening_mark_splits_fragments(world, quote):
    """Run 5's repass, C5: two messages quoted as two quoted strings on one line."""
    got = resolve(world, quote)
    assert [f.message_id for f in got.fragments] == [world.ids["trigger"], world.ids["later"]]


def test_an_apostrophe_inside_words_does_not_split_and_a_mark_pair_still_resolves(world):
    msg = say(world, "the kids' 'fort' is pure rock 'n' roll", "2026-10-02T11:00:00+00:00")
    got = resolve(world, "pure rock 'n' roll", frozenset({msg}))
    assert [f.text for f in got.fragments] == ["pure rock 'n' roll"]
    # a closing apostrophe, a space and an opening mark do split, and both parts must match
    got = resolve(world, "the kids' 'fort' is pure rock", frozenset({msg}))
    assert [f.text for f in got.fragments] == ["the kids", "fort' is pure rock"]


def resolve_all(world, quotes, context=None):
    return note_quotes.resolve_quotes(quotes, world.context if context is None else context)


def test_several_quote_lines_each_resolve_and_all_are_evidence(world):
    got = resolve_all(world, ["Jodie takes her coffee with oat milk",
                              "The backup should eventually run"])
    assert [(e["quote"], e["message_id"]) for e in got.evidence()] == [
        ("Jodie takes her coffee with oat milk", world.ids["trigger"]),
        ("The backup should eventually run", world.ids["later"])]


def test_the_minimum_is_across_the_block_not_per_quote_line(world):
    """A short line beside a long one is accepted; short lines alone are refused, all named."""
    got = resolve_all(world, ["never dairy", "The backup should eventually run"])
    assert len(got.fragments) == 2
    reason = texts.quote_too_short(config.notes_min_fragment_words())
    with pytest.raises(texts.QuoteRefused) as caught:
        resolve_all(world, ["never dairy", "a schedule"])
    assert str(caught.value) == f"all 2 fragments ('never dairy', 'a schedule'): {reason}"


def test_every_quote_line_must_match_and_the_failing_one_is_named(world):
    with pytest.raises(texts.QuoteRefused) as caught:
        resolve_all(world, ["Jodie takes her coffee with oat milk",
                            "Jodie prefers a flat white"])
    assert str(caught.value) == "quote 2 of 2: " + texts.QUOTE_NOT_A_PERSONS_WORDS
    with pytest.raises(texts.QuoteRefused) as caught:
        resolve_all(world, ["Jodie takes her coffee with oat milk", '""'])
    assert str(caught.value).startswith("quote 2 of 2: that quote is too short")


def test_the_evidence_is_one_entry_per_fragment_with_the_quote_as_given(world):
    quote = ' "Jodie takes her coffee with oat milk....the backup should EVENTUALLY run" '
    assert resolve(world, quote).evidence() == [
        {"quote": quote, "fragment": "Jodie takes her coffee with oat milk",
         "message_id": world.ids["trigger"], "tier": "context", "identical_count": 1},
        {"quote": quote, "fragment": "the backup should EVENTUALLY run",
         "message_id": world.ids["later"], "tier": "context", "identical_count": 1},
    ]


# --- the writers carry the retry --------------------------------------------------------------


def test_every_write_in_the_notes_module_carries_the_retry():
    undecorated = []
    for name, fn in inspect.getmembers(notes, inspect.isfunction):
        if fn.__module__ != notes.__name__ or name.startswith("_"):
            continue
        if "with db.transaction()" in inspect.getsource(fn) and not hasattr(fn, "__wrapped__"):
            undecorated.append(name)
    assert undecorated == [], f"writers missing @retry_on_locked: {undecorated}"
