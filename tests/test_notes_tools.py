"""`note_search` and `note_propose`: Notes piece 3 (`docs/NOTES_BUILD_PLAN.md`).

Design of record: `docs/NOTES_DESIGN.md` (N0-N7, N11) with the rulings of 2026-10-02 (quote evidence
only against a person's words; `notes.enabled` is bootstrap; handler-side validation with
`TOOL_ERROR`). A real store and the real tools, with only the identity classifier scripted. Both
tools ship dark, so every test that needs them offered builds its own registry.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from program import config
from program.attribution import AttributionContext
from program.engine import loop, prompt, turn
from program.integrity import classifier, gate
from program.memory import db, notes
from program.origin import OriginContext
from program.settings.permissions import Actor, Role
from program.tools import catalog, note_quotes, note_search, registry
from program.tools import note_texts as texts
from program.tools.note_propose import NOTE_PROPOSE
from program.tools.note_search import NOTE_SEARCH
from program.tools.registry import Tool, ToolOutcome, ToolRegistry

HEX32 = re.compile(r"\b[0-9a-f]{32}\b")


@pytest.fixture
def world(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    verdicts = []
    monkeypatch.setattr(classifier, "classify",
                        lambda p: verdicts.append(p) or "CONSISTENT")
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
        # not in this turn's context: another conversation, another person
        "elsewhere": say(other, jodie, "user", "My grandmother kept her starter above the stove.",
                         "2026-09-30T09:00:00+00:00"),
    }
    origin = OriginContext(
        conversation_id=conv, user_message_id=ids["later"],
        context_message_ids=frozenset({ids["trigger"], ids["reply"], ids["later"]}))
    return type("W", (), {"lyle": lyle, "jodie": jodie, "conv": conv, "other": other,
                          "ids": ids, "origin": origin, "verdicts": verdicts,
                          "attribution": AttributionContext(user_id=lyle),
                          "reg": ToolRegistry([NOTE_SEARCH, NOTE_PROPOSE])})


def propose(world, *, origin=None, **arguments):
    base = dict(action="add", subject_kind="person", subject="Jodie",
                text="Takes her coffee with oat milk.",
                quotes=["Jodie takes her coffee with oat milk"])
    base.update(arguments)
    base = {k: v for k, v in base.items() if v is not None}
    return world.reg.dispatch("note_propose", base, attribution=world.attribution,
                              origin=origin or world.origin)


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


# --- dark by default, and what they declare -------------------------------------------------


def test_both_tools_are_in_the_catalogue_but_not_offered_while_notes_is_dark():
    registry.reset_default_registry()
    assert {"note_search", "note_propose"} <= {t.name for t in catalog.TOOLS}
    assert not {"note_search", "note_propose"} & set(registry.default_registry().names)
    assert NOTE_SEARCH.enabled is config.notes_enabled
    assert NOTE_PROPOSE.enabled is config.notes_enabled


def test_notes_enabled_is_bootstrap_and_switches_both_tools_on_with_a_restart(monkeypatch):
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "true")
    config.reload()
    registry.reset_default_registry()
    try:
        assert {"note_search", "note_propose"} <= set(registry.default_registry().names)
    finally:
        monkeypatch.setenv("ANAM_NOTES_ENABLED", "false")
        config.reload()
        registry.reset_default_registry()
    assert not config.notes_enabled()


def test_what_each_tool_declares_and_that_neither_is_model_settable():
    assert (NOTE_PROPOSE.takes_attribution, NOTE_PROPOSE.takes_origin) == (True, True)
    assert (NOTE_SEARCH.takes_attribution, NOTE_SEARCH.takes_origin) == (False, False)
    shown = json.dumps([NOTE_SEARCH.to_ollama_schema(), NOTE_PROPOSE.to_ollama_schema()])
    assert '"origin"' not in shown and '"attribution"' not in shown
    assert not set(NOTE_PROPOSE.parameters["properties"]) & {
        "origin", "attribution", "user_id", "actor", "role", "status", "conversation_id"}
    assert not set(inspect.signature(note_search.search_notes).parameters) & {
        "actor", "role", "user", "origin"}


def test_there_is_no_self_kind_and_the_enums_are_the_designs():
    props = NOTE_PROPOSE.parameters["properties"]
    assert props["action"]["enum"] == ["add", "revise", "retire"]
    assert props["subject_kind"]["enum"] == ["person", "topic", "project"]
    assert NOTE_PROPOSE.parameters["required"] == ["action", "subject_kind", "subject", "quotes"]


def test_the_untrusted_output_flag_is_declared_on_exactly_the_six_external_tools():
    assert registry.untrusted_tools() == (
        "moltbook_browse", "moltbook_read_agent", "moltbook_read_post", "moltbook_search",
        "web_fetch", "web_search")
    assert not any(t.untrusted_output for t in (NOTE_SEARCH, NOTE_PROPOSE))
    assert Tool(name="probe_x", description="d", parameters={"type": "object"},
                handler=lambda: "").untrusted_output is False


# --- every sentence the model reads ----------------------------------------------------------


def _all_texts():
    simple = [texts.NO_MATCH, texts.HEADER, texts.EMPTY_QUERY, texts.PENDING_ADD,
              texts.PENDING_CHANGE, texts.SUBJECT_EMPTY, texts.TEXT_REQUIRED,
              texts.RETIRE_TAKES_NO_TEXT, texts.NOTE_ID_REQUIRED, texts.ADD_TAKES_NO_NOTE_ID,
              texts.NOTE_ID_AMBIGUOUS, texts.QUOTES_REQUIRED, texts.QUOTE_NOT_A_PERSONS_WORDS,
              texts.QUOTE_NO_MATCH, NOTE_SEARCH.description, NOTE_PROPOSE.description]
    built = [texts.bad_action("x"), texts.bad_subject_kind("x"), texts.subject_too_long(99, 60),
             texts.text_too_long(999, 650), texts.note_not_found("abc"), texts.quote_not_text(2),
             texts.quote_too_short(24, 4), texts.quote_ambiguous(3)]
    return simple + built


def test_every_result_text_passes_the_authored_text_checks_and_names_no_identifier():
    for text in _all_texts():
        prompt.check_authored_text(text, "note tool text")
        assert not HEX32.search(text), text


def test_the_result_texts_are_pinned_verbatim():
    assert texts.PENDING_ADD == ("Proposed. A person will review it before anything changes. "
                                 "No note exists yet because of this.")
    assert texts.PENDING_CHANGE == ("Proposed. A person will review it before anything changes. "
                                    "The note stays as it is until then.")
    assert texts.NO_MATCH == (
        "No note matches that. Notes hold only what was proposed and approved, so this says "
        "nothing about whether it was ever talked about; memory_search covers conversations.")
    for claim in ("saved", "noted", "remember", "recorded", "stored"):
        assert claim not in (texts.PENDING_ADD + texts.PENDING_CHANGE).lower()


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


def test_the_schema_description_states_the_derived_limit():
    assert f"max {config.notes_max_text_chars()} characters" in json.dumps(
        NOTE_PROPOSE.parameters)


def test_the_two_schemas_stay_inside_their_budget():
    """N12: the real tokenizer measured 68 and 212 tokens for these wordings. A bound on the
    JSON (not tokens) catches growth without a model: 391 and 1,092 characters today."""
    assert len(json.dumps(NOTE_SEARCH.to_ollama_schema())) <= 400
    assert len(json.dumps(NOTE_PROPOSE.to_ollama_schema())) <= 1_100


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


# --- note_propose: every refusal is a TOOL_ERROR with its message, and writes nothing ----------


def assert_refused(world, message_part, **arguments):
    gate_calls = len(world.verdicts)
    before = {t: [tuple(r) for r in rows(t)] for t in ("notes", "note_proposals", "approval_log")}
    out = propose(world, **arguments)
    assert out.outcome is ToolOutcome.TOOL_ERROR, out
    assert message_part in out.error, out.error
    after = {t: [tuple(r) for r in rows(t)] for t in before}
    assert after == before, "a refused proposal changed a table"
    assert len(world.verdicts) == gate_calls, "the gate ran on a refused proposal"
    return out


def test_refusal_bad_action(world):
    assert_refused(world, texts.bad_action("delete"), action="delete")


def test_refusal_bad_subject_kind_and_there_is_no_self_kind(world):
    assert_refused(world, texts.bad_subject_kind("self"), subject_kind="self")


def test_refusal_empty_subject(world):
    assert_refused(world, texts.SUBJECT_EMPTY, subject="   ")


def test_refusal_subject_too_long(world):
    limit = config.notes_max_subject_chars()
    assert_refused(world, texts.subject_too_long(limit + 1, limit), subject="S" * (limit + 1))


def test_refusal_text_required_for_an_add_and_a_revise(world):
    nid = add_note(world, "old")
    assert_refused(world, texts.TEXT_REQUIRED, text=None)
    assert_refused(world, texts.TEXT_REQUIRED, action="revise", note_id=nid[:8], text=None)


def test_refusal_text_too_long_names_the_limit(world):
    limit = config.notes_max_text_chars()
    out = assert_refused(world, texts.text_too_long(limit + 1, limit), text="x" * (limit + 1))
    assert str(limit) in out.error


def test_refusal_a_retire_takes_no_text(world):
    nid = add_note(world, "old")
    assert_refused(world, texts.RETIRE_TAKES_NO_TEXT, action="retire", note_id=nid[:8],
                   text="why")


def test_refusal_note_id_required_for_a_revise_and_a_retire(world):
    for action in ("revise", "retire"):
        assert_refused(world, texts.NOTE_ID_REQUIRED, action=action,
                       text=None if action == "retire" else "new text")


def test_refusal_an_add_takes_no_note_id(world):
    nid = add_note(world, "old")
    assert_refused(world, texts.ADD_TAKES_NO_NOTE_ID, note_id=nid[:8])


def test_refusal_the_note_must_exist_and_be_active(world):
    gone = add_note(world, "gone", status="retired")
    assert_refused(world, texts.note_not_found("ffffffff"), action="revise", note_id="ffffffff")
    assert_refused(world, texts.note_not_found(gone[:8]), action="retire", note_id=gone[:8],
                   text=None)


def test_refusal_an_ambiguous_note_id_prefix(world):
    add_note(world, "one", nid="abcdef01-0000-4000-8000-000000000001")
    add_note(world, "two", nid="abcdef01-0000-4000-8000-000000000002")
    assert_refused(world, texts.NOTE_ID_AMBIGUOUS, action="revise", note_id="abcdef01")


def test_refusal_quotes_are_required(world):
    assert_refused(world, texts.QUOTES_REQUIRED, quotes=[])


def test_refusal_each_quote_must_be_text(world):
    good = "Jodie takes her coffee with oat milk"
    assert_refused(world, texts.quote_not_text(2), quotes=[good, 7])
    assert_refused(world, texts.quote_not_text(1), quotes=[None])
    assert_refused(world, texts.quote_not_text(1), quotes=["   "])


def test_refusal_a_quote_too_short_in_characters_or_in_words(world):
    chars, words = config.notes_min_quote_chars(), config.notes_min_quote_words()
    assert_refused(world, texts.quote_too_short(chars, words), quotes=["oat milk, never dairy"])
    assert_refused(world, texts.quote_too_short(chars, words),
                   quotes=["supercalifragilisticexpialidocious oat"])   # long, but two words


def test_refusal_a_quote_of_the_entitys_own_reply_is_not_a_persons_words(world):
    assert_refused(world, texts.QUOTE_NOT_A_PERSONS_WORDS,
                   quotes=["Jodie prefers a flat white in the mornings"])


def test_refusal_a_quote_that_appears_in_no_message(world):
    assert_refused(world, texts.QUOTE_NO_MATCH,
                   quotes=["Jodie hates mushrooms with a passion"])


def test_refusal_a_paraphrase_is_not_a_quote(world):
    """Nothing looser than whitespace, quote marks and case."""
    assert_refused(world, texts.QUOTE_NO_MATCH, quotes=["Jodie drinks her coffee with oat milk"])
    assert_refused(world, texts.QUOTE_NO_MATCH, quotes=["Jodie take her coffee with oat milks"])
    # every word present, in another order: still not what anyone said
    assert_refused(world, texts.QUOTE_NO_MATCH, quotes=["oat milk with her coffee Jodie takes"])


def test_refusal_a_quote_matching_two_different_messages(world):
    def say(t, ts):
        return db.save_message(world.conv, world.lyle, "user", t, timestamp=ts)

    a = say("Lyle wants the backups to live on the NAS in the hall.", "2026-10-02T09:00:00+00:00")
    b = say("Lyle wants the backups to live on the NAS in the cellar.", "2026-10-02T09:01:00+00:00")
    origin = OriginContext(world.conv, world.ids["later"], frozenset({a, b}))
    assert_refused(world, texts.quote_ambiguous(2), origin=origin,
                   quotes=["Lyle wants the backups to live on the NAS"])


def test_refusal_identical_words_from_two_different_people_are_ambiguous(world):
    same = "We need more of the good coffee for Saturday."
    ts = "2026-10-02T09:0{}:00+00:00"
    a = db.save_message(world.conv, world.lyle, "user", same, timestamp=ts.format(0))
    b = db.save_message(world.other, world.jodie, "user", same, timestamp=ts.format(1))
    origin = OriginContext(world.conv, world.ids["later"], frozenset({a, b}))
    assert_refused(world, texts.quote_ambiguous(2), origin=origin,
                   quotes=["We need more of the good coffee for Saturday"])


# --- resolving a quote: what is accepted, and from where ----------------------------------------


def resolve(world, quote, origin=None):
    return note_quotes.resolve_quote(quote, origin or world.origin)


def test_normalisation_folds_whitespace_quote_marks_and_case_and_nothing_else(world):
    msg = db.save_message(world.conv, world.lyle, "user",
                          'Lyle said it’s "fine" for the grinder   to stay\nwhere it is.',
                          timestamp="2026-10-02T09:30:00+00:00")
    origin = OriginContext(world.conv, world.ids["later"], frozenset({msg}))
    got = resolve(world, "LYLE SAID IT'S “fine” for the grinder to stay where it is",
                  origin)
    assert got.message_id == msg and got.tier == "context" and got.identical_count == 1
    assert note_quotes.normalise("  A’b \t C ") == "a'b c"


def test_a_quote_from_the_triggering_message_resolves_in_the_context_tier(world):
    got = resolve(world, "The backup should eventually run on a schedule")
    assert (got.message_id, got.tier) == (world.ids["later"], "context")


def test_the_context_tier_is_preferred_over_the_store(world):
    """The same words said by a person inside this turn's context and in another conversation:
    the context one is taken, and not as an ambiguity."""
    same = "The market opens at nine unless it rains in the morning."
    inside = db.save_message(world.conv, world.lyle, "user", same,
                             timestamp="2026-10-02T09:00:00+00:00")
    db.save_message(world.other, world.jodie, "user", same, timestamp="2026-09-01T09:00:00+00:00")
    origin = OriginContext(world.conv, world.ids["later"], frozenset({inside}))
    got = resolve(world, "The market opens at nine unless it rains", origin)
    assert (got.message_id, got.tier) == (inside, "context")


def test_a_quote_outside_the_context_resolves_in_the_store_tier_and_says_so(world):
    got = resolve(world, "My grandmother kept her starter above the stove")
    assert (got.message_id, got.tier) == (world.ids["elsewhere"], "store")


def test_the_open_trailing_group_is_searchable_because_the_store_tier_is_not_chunk_based(world):
    """No chunk exists for any message here (nothing has been chunked), which is the normal state
    of the message being answered; the store tier still finds it."""
    assert rows("chunks") == []
    assert resolve(world, "My grandmother kept her starter above the stove").tier == "store"


def test_identical_text_from_the_same_person_is_one_piece_of_evidence_with_its_count(world):
    text = "Make me an image of a copper kettle on a slate worktop."
    first = db.save_message(world.conv, world.lyle, "user", text,
                            timestamp="2026-10-02T08:00:00+00:00")
    newest = db.save_message(world.conv, world.lyle, "user", text,
                             timestamp="2026-10-02T08:30:00+00:00")
    origin = OriginContext(world.conv, world.ids["later"], frozenset({first, newest}))
    got = resolve(world, "image of a copper kettle on a slate worktop", origin)
    assert (got.message_id, got.identical_count) == (newest, 2)


def test_a_quote_said_by_a_person_and_repeated_by_the_entity_resolves_to_the_person(world):
    said = db.save_message(world.conv, world.lyle, "user", "Grind the beans finer for the shot.",
                           timestamp="2026-10-02T09:00:00+00:00")
    db.save_message(world.conv, world.lyle, "assistant", "Grind the beans finer for the shot.",
                    timestamp="2026-10-02T09:00:05+00:00")
    origin = OriginContext(world.conv, world.ids["later"], frozenset())
    got = resolve(world, "Grind the beans finer for the shot", origin)
    assert got.message_id == said and got.tier == "store"


def test_a_resolved_quote_serialises_as_the_stored_evidence_shape(world):
    got = resolve(world, "The backup should eventually run on a schedule")
    assert got.to_dict() == {"quote": "The backup should eventually run on a schedule",
                             "message_id": world.ids["later"], "tier": "context",
                             "identical_count": 1}


# --- what a successful proposal writes ---------------------------------------------------------


def test_an_add_is_inserted_pending_with_its_evidence_origin_and_attribution(world):
    out = propose(world)

    assert out.outcome is ToolOutcome.OK and out.value == texts.PENDING_ADD
    [row] = rows("note_proposals")
    assert row["status"] == "pending" and row["decided_at"] is None
    assert row["resulting_note_id"] is None and row["target_note_id"] is None
    assert (row["action"], row["subject_kind"], row["subject"]) == ("add", "person", "Jodie")
    assert row["text"] == "Takes her coffee with oat milk."
    assert json.loads(row["evidence"]) == [{
        "quote": "Jodie takes her coffee with oat milk", "message_id": world.ids["trigger"],
        "tier": "context", "identical_count": 1}]
    assert row["conversation_id"] == world.conv
    assert row["user_message_id"] == world.ids["later"], "the triggering message is always recorded"
    assert row["call_id"] == out.call_id and row["call_id"]
    assert row["user_id"] == world.lyle
    assert row["untrusted_context"] is None, "not recorded until the turn fills it"
    assert rows("notes") == [] and rows("approval_log") == []


def test_a_proposal_never_creates_or_changes_a_note_and_has_no_path_to_a_decided_status(world):
    nid = add_note(world, "old text")
    before = [tuple(r) for r in rows("notes")]
    for kwargs in (dict(), dict(action="revise", note_id=nid[:8]),
                   dict(action="retire", note_id=nid[:8], text=None)):
        assert propose(world, **kwargs).outcome is ToolOutcome.OK
    assert [tuple(r) for r in rows("notes")] == before
    assert {r["status"] for r in rows("note_proposals")} == {"pending"}
    assert rows("approval_log") == []
    source = inspect.getsource(notes)
    assert "approved" not in source and "applied" not in source and "UPDATE notes" not in source
    assert "INSERT INTO notes " not in source


def test_a_revise_and_a_retire_target_the_active_note_by_its_short_id(world):
    nid = add_note(world, "Takes her coffee with milk.")

    revise = propose(world, action="revise", note_id=nid[:8], text="Takes oat milk now.")
    retire = propose(world, action="retire", note_id=nid[:8], text=None)

    assert revise.value == texts.PENDING_CHANGE and retire.value == texts.PENDING_CHANGE
    got = {r["action"]: r for r in rows("note_proposals")}
    assert got["revise"]["target_note_id"] == nid and got["retire"]["target_note_id"] == nid
    assert got["retire"]["text"] is None


def test_the_result_text_names_no_note_or_proposal_id(world):
    nid = add_note(world, "old")
    for kwargs in (dict(), dict(action="revise", note_id=nid[:8]),
                   dict(action="retire", note_id=nid[:8], text=None)):
        value = propose(world, **kwargs).value
        assert not HEX32.search(value) and nid[:8] not in value
        assert [r["id"] for r in rows("note_proposals")][-1] not in value


def test_the_identity_gate_runs_on_the_text_and_its_verdict_is_stored(world):
    propose(world)
    [row] = rows("note_proposals")
    verdict = json.loads(row["integrity_check"])
    assert verdict["status"] == "clean" and verdict["scope"] == "identity_only"
    assert len(world.verdicts) == 1 and "Takes her coffee with oat milk." in world.verdicts[0]


def test_a_flag_is_recorded_and_changes_nothing_about_the_proposal(world, monkeypatch):
    monkeypatch.setattr(classifier, "classify", lambda p: (
        "CONTRADICTS-SELF\n- Takes her coffee with oat milk. | nothing runs between replies"))
    out = propose(world)
    [row] = rows("note_proposals")
    assert out.outcome is ToolOutcome.OK and row["status"] == "pending"
    assert json.loads(row["integrity_check"])["status"] == "flagged"


def test_an_unavailable_classifier_is_stored_as_unavailable_never_clean(world, monkeypatch):
    def down(prompt_text):
        raise RuntimeError("classifier down")

    monkeypatch.setattr(classifier, "classify", down)
    assert propose(world).outcome is ToolOutcome.OK
    assert json.loads(rows("note_proposals")[0]["integrity_check"])["status"] == "unavailable"


def test_a_retire_has_no_text_to_judge_so_no_verdict_is_recorded(world):
    nid = add_note(world, "old")
    propose(world, action="retire", note_id=nid[:8], text=None)
    [row] = rows("note_proposals")
    assert row["integrity_check"] is None, "NULL is 'no verdict recorded', never clean"
    assert world.verdicts == []


def test_the_proposal_is_attributed_to_the_person_present_not_the_model(world):
    other = AttributionContext(user_id=world.jodie)
    world.reg.dispatch("note_propose", dict(
        action="add", subject_kind="topic", subject="the backup",
        text="Should eventually run on a schedule.",
        quotes=["The backup should eventually run on a schedule"]),
        attribution=other, origin=world.origin)
    assert rows("note_proposals")[0]["user_id"] == world.jodie


def test_a_model_cannot_pass_origin_or_attribution_or_status(world):
    for forbidden in ("origin", "attribution", "status", "user_id", "conversation_id"):
        out = world.reg.dispatch(
            "note_propose", dict(action="add", subject_kind="person", subject="J", text="t",
                                 quotes=["x"], **{forbidden: "forged"}),
            attribution=world.attribution, origin=world.origin)
        assert out.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert rows("note_proposals") == []


def test_the_trace_entry_carries_no_origin_and_the_call_id_matches_the_row(world):
    out = propose(world)
    entry = out.to_trace_entry()
    assert "origin" not in json.dumps(entry) and world.conv not in json.dumps(entry)
    assert rows("note_proposals")[0]["call_id"] == entry["call_id"]


# --- the gate is unaffected by note_propose joining the catalogue (review, 2026-10-02) ----------


def test_note_propose_is_a_side_effect_tool_and_the_gates_verdicts_are_byte_identical():
    """`side_effect_tools()` reads the FULL catalogue, so adding a tool that declares
    `takes_attribution` changes its output even while Notes is dark. The proof the gate does not
    care: the digest over every frozen case under four scripted replies (verdict, advisory, every
    classifier prompt), taken from HEAD's gate before `check_identity` existed, is unchanged."""
    from tests import test_gate_identity as pinned

    assert "note_propose" in registry.side_effect_tools()
    mp = pytest.MonkeyPatch()
    try:
        pinned.test_check_is_byte_identical_to_before_check_identity_existed(mp)
    finally:
        mp.undo()


def test_with_note_propose_in_the_trace_a_claim_to_have_noted_is_cleared_by_the_action_rule():
    """The known gap, pinned (N7, N18): ACTION clears any claim once a side-effect tool ran, so
    *"I've saved that note"* passes even though the proposal is only pending. The result text is
    what counters it; the gate does not."""
    trace = [{"tool": "note_propose", "outcome": "ok", "ran": True, "arguments": {},
              "call_id": "c1", "artifact_ids": []}]
    assert gate.a_side_effect_tool_ran(trace) is True


# --- the writers carry the retry --------------------------------------------------------------


def test_every_write_in_the_notes_module_carries_the_retry():
    undecorated = []
    for name, fn in inspect.getmembers(notes, inspect.isfunction):
        if fn.__module__ != notes.__name__ or name.startswith("_"):
            continue
        if "with db.transaction()" in inspect.getsource(fn) and not hasattr(fn, "__wrapped__"):
            undecorated.append(name)
    assert undecorated == [], f"writers missing @retry_on_locked: {undecorated}"


# --- the untrusted context, filled by the turn ----------------------------------------------


@pytest.fixture
def turn_world(world, monkeypatch):
    import types

    actor = Actor(user_id=world.lyle, name="Lyle", role=Role.ADMIN)
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    state = types.SimpleNamespace(web_calls=0)

    def web(**kw):
        state.web_calls += 1
        return "A page written by a stranger."

    def broken_web(**kw):
        raise RuntimeError("the fetch failed")

    probe_web = Tool(name="probe_web", description="TEST-ONLY external text.",
                     parameters={"type": "object", "properties": {}, "required": []},
                     handler=web, untrusted_output=True)
    probe_broken = Tool(name="probe_broken", description="TEST-ONLY failing external tool.",
                        parameters={"type": "object", "properties": {}, "required": []},
                        handler=broken_web, untrusted_output=True)

    def run(rounds, tools=None):
        sent = []

        def fake(messages, model=None, options=None, tools=None, timeout=None):
            sent.append(1)
            if tools is not None and len(sent) <= len(rounds):
                return {"message": {"role": "assistant", "content": "", "tool_calls": rounds[
                    len(sent) - 1]}, "done_reason": "stop"}
            return {"message": {"role": "assistant", "content": "Done."}, "done_reason": "stop"}

        monkeypatch.setattr(loop.ollama, "chat", fake)
        conv = db.start_conversation(world.lyle)
        db.save_message(conv, world.lyle, "user", "Earlier: Jodie takes her coffee with oat milk.",
                        timestamp="2026-10-02T11:00:00+00:00")
        return turn.handle_user_message(
            actor, "Please note that down.", conv, situation="",
            registry=ToolRegistry(tools or [NOTE_PROPOSE, probe_web, probe_broken]))

    return types.SimpleNamespace(run=run, world=world, state=state)


QUOTE = "Jodie takes her coffee with oat milk"


def _call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


def _proposal_call():
    return _call("note_propose", action="add", subject_kind="person", subject="Jodie",
                 text="Takes oat milk.", quotes=[QUOTE])


def contexts():
    return [r["untrusted_context"] for r in rows("note_proposals")]


def test_a_proposal_after_an_untrusted_tool_is_flagged_with_that_tool(turn_world):
    turn_world.run([[_call("probe_web")], [_proposal_call()]])
    assert contexts() == ['["probe_web"]']


def test_a_proposal_with_no_untrusted_tool_records_none_not_null(turn_world):
    turn_world.run([[_proposal_call()]])
    assert contexts() == ["[]"], "[] means recorded-none; NULL would mean not recorded"


def test_a_failed_untrusted_tool_gave_the_entity_nothing_to_read(turn_world):
    turn_world.run([[_call("probe_broken")], [_proposal_call()]])
    assert contexts() == ["[]"]


def test_only_tools_that_ran_before_the_proposal_count(turn_world):
    turn_world.run([[_proposal_call(), _call("probe_web")]])
    assert contexts() == ["[]"]
    assert turn_world.state.web_calls == 1


def test_each_tool_is_named_once_in_the_order_first_seen(turn_world):
    turn_world.run([[_call("probe_web"), _call("probe_web")], [_proposal_call()]])
    assert contexts() == ['["probe_web"]']


def test_a_failed_update_leaves_null_and_never_fails_the_turn(turn_world, monkeypatch):
    def lock(call_id, tools):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(turn.notes_db, "set_untrusted_context", lock)
    outcome = turn_world.run([[_call("probe_web")], [_proposal_call()]])

    assert outcome.content == "Done."
    assert contexts() == [None], "a failed update must read as not recorded, never as none"


def test_a_pending_proposal_is_not_frozen_so_the_update_is_allowed(world):
    propose(world)
    call_id = rows("note_proposals")[0]["call_id"]
    assert notes.set_untrusted_context(call_id, ["web_fetch"]) == 1
    assert rows("note_proposals")[0]["untrusted_context"] == '["web_fetch"]'


def test_a_decided_proposal_is_never_updated(world):
    propose(world)
    row = rows("note_proposals")[0]
    with db.transaction() as conn:
        conn.execute("UPDATE note_proposals SET status = 'rejected', decided_at = 'now' "
                     "WHERE id = ?", (row["id"],))
    assert notes.set_untrusted_context(row["call_id"], ["web_fetch"]) == 0
    assert rows("note_proposals")[0]["untrusted_context"] is None


def test_a_turn_that_proposed_nothing_does_no_untrusted_context_work(turn_world, monkeypatch):
    calls = []
    monkeypatch.setattr(turn.notes_db, "set_untrusted_context", lambda *a: calls.append(a))
    monkeypatch.setattr(turn, "untrusted_context_by_call",
                        lambda *a, **k: calls.append("looked") or {})
    turn_world.run([[_call("probe_web")]])
    assert calls == []


def test_the_origin_for_a_real_turn_lets_the_quote_resolve_in_the_context_tier(turn_world):
    turn_world.run([[_proposal_call()]])
    evidence = json.loads(rows("note_proposals")[0]["evidence"])
    assert evidence[0]["tier"] == "context"
    assert rows("note_proposals")[0]["conversation_id"]
