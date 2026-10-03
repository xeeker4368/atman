"""CO17: an entity message that reports only empty searches is not a correction candidate.

Three layers, each with its own tests:

* **the declaration**: ``Tool.empty_result`` / ``registry.empty_result_tools()``, derived from the full
  catalogue, validated at construction, and **checked against each real tool dispatched** so a
  reworded sentence cannot silently disable the rule;
* **the detector**: ``corrections.reports_nothing_found`` (prefix match, no other successful call,
  an unparseable trace keeps the candidate and warns);
* **the pool**: ``corrections.candidates()`` drops such an entity message from both pools, before
  the per-role cap, and a turn run end to end writes no link where it used to.

Real stores and real tool handlers throughout; only the model, the embedder and the HTTP transports
are substituted. The frozen correction measurement is untouched by construction: the harness never
calls ``candidates()`` (asserted below).
"""

# ruff: noqa: E501
from __future__ import annotations

import ast
import inspect
import json
import logging

import pytest

from program import config
from program.engine import loop, turn
from program.integrity import classifier, corrections
from program.memory import db, retrieval
from program.settings.permissions import Actor, Role
from program.tools import catalog, memory_search, note_texts, registry, web_search
from program.tools.registry import Tool, ToolError, ToolRegistry

# --- the declaration --------------------------------------------------------------------------

#: Catalogue tools with NO fixed empty sentence, and why. A new tool that is in neither this table nor
#: ``empty_result_tools()`` fails the guard below: declaring one is a decision, not an oversight.
NO_FIXED_EMPTY = {
    "web_fetch": "retrieves one page; there is no 'matched nothing' result (an unreadable page is an extraction note)",
    "moltbook_read_post": "a lookup by id: its not-found sentence names the id, so it is not one fixed sentence",
    "moltbook_read_agent": "a lookup by name: its not-found sentence names the agent, so it is not one fixed sentence",
    "image_generate": "writes an artifact; not a search",
    "creative_write": "writes an artifact; not a search",
    "note_propose": "writes a proposal; not a search",
}


def test_the_declarations_are_exactly_the_five_search_like_tools():
    assert registry.empty_result_tools() == {
        "memory_search": memory_search.NO_MATCHES,
        "note_search": note_texts.NO_MATCH,
        "web_search": web_search.NO_RESULTS,
        "moltbook_browse": __import__("program.tools.moltbook", fromlist=["x"]).NO_RESULTS,
        "moltbook_search": __import__("program.tools.moltbook", fromlist=["x"]).NO_RESULTS,
    }


def test_every_catalogue_tool_either_declares_a_sentence_or_is_listed_as_having_none():
    declared = set(registry.empty_result_tools())
    names = {t.name for t in catalog.TOOLS}
    assert declared.isdisjoint(NO_FIXED_EMPTY)
    assert names == declared | set(NO_FIXED_EMPTY), (
        "a tool is neither declaring `empty_result` nor listed in NO_FIXED_EMPTY with a reason")


def test_the_map_is_derived_from_the_full_catalogue_not_the_enabled_registry():
    """A stored trace can name a tool that has since been disabled (Notes ships dark)."""
    registry.reset_default_registry()
    assert "note_search" not in registry.default_registry().names
    assert "note_search" in registry.empty_result_tools()


@pytest.mark.parametrize("bad", ["", "   ", "\n"])
def test_a_blank_declaration_is_refused_because_it_would_match_every_result(bad):
    with pytest.raises(ToolError, match="empty_result"):
        Tool(name="scaffold_blank", description="TEST-ONLY.",
             parameters={"type": "object", "properties": {}}, handler=lambda: "x",
             empty_result=bad)


def test_the_detector_holds_no_copy_of_any_sentence():
    source = inspect.getsource(corrections)
    for sentence in registry.empty_result_tools().values():
        assert sentence[:30] not in source


# --- each real tool, dispatched ----------------------------------------------------------------


def _entry(result) -> dict:
    return result.to_trace_entry()


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    monkeypatch.setattr(retrieval.ollama, "embed", lambda text, **k: [0.1] * 768)
    lyle = db.create_user("Lyle", role="admin")
    return Actor(user_id=lyle, name="Lyle", role=Role.ADMIN)


class _Resp:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _dispatch_empty(name, monkeypatch, *, degraded=False):
    """Run the REAL tool so that it matches nothing, and return its ToolResult."""
    tool = next(t for t in catalog.TOOLS if t.name == name)
    bench = ToolRegistry([tool])
    if name == "note_search":
        return bench.dispatch(name, {"query": "zzzxqv"})
    if name == "memory_search":
        if degraded:
            def down(text, **k):
                raise RuntimeError("embedder down")
            monkeypatch.setattr(retrieval.ollama, "embed", down)
        return bench.dispatch(name, {"query": "zzzxqv"})
    if name == "web_search":
        engines = [["duckduckgo", "CAPTCHA"]] if degraded else []
        monkeypatch.setattr(web_search.requests, "get", lambda *a, **k: _Resp(
            {"query": "x", "results": [], "unresponsive_engines": engines}))
        return bench.dispatch(name, {"query": "zzzxqv"})
    from program.tools import moltbook
    from tests.test_moltbook import FakeResponse, FakeSession
    FakeSession.calls, FakeSession.sessions = [], []
    body = ({"success": True, "posts": [], "has_more": False} if name == "moltbook_browse"
            else {"success": True, "results": [], "has_more": False})
    FakeSession.answers = [FakeResponse(payload=body)]
    monkeypatch.setattr(moltbook.requests, "Session", FakeSession)
    args = {} if name == "moltbook_browse" else {"query": "zzzxqv"}
    return bench.dispatch(name, args)


@pytest.mark.parametrize("name", sorted(registry.empty_result_tools()))
def test_each_real_tool_returns_its_declared_sentence_and_the_detector_sees_it(
        name, store, monkeypatch):
    result = _dispatch_empty(name, monkeypatch)
    assert result.outcome.value == "ok", result.error
    assert result.value.startswith(registry.empty_result_tools()[name]), (
        f"{name} no longer returns the sentence it declares")
    assert corrections.reports_nothing_found(json.dumps([_entry(result)])) is True


@pytest.mark.parametrize("name", ["memory_search", "web_search"])
def test_the_empty_result_with_a_degradation_note_is_still_detected(name, store, monkeypatch):
    """The tools append a note when a leg or an engine was down: the prefix still matches."""
    result = _dispatch_empty(name, monkeypatch, degraded=True)
    assert result.value != registry.empty_result_tools()[name], "the note was not appended"
    assert corrections.reports_nothing_found(json.dumps([_entry(result)])) is True


# --- the detector -------------------------------------------------------------------------------

NOTE_EMPTY = {"tool": "note_search", "outcome": "ok", "value": note_texts.NO_MATCH}
MEM_EMPTY = {"tool": "memory_search", "outcome": "ok", "value": memory_search.NO_MATCHES}


def detect(entries) -> bool:
    return corrections.reports_nothing_found(json.dumps(entries))


def test_one_empty_search_or_several_is_a_claim_of_nothing_found():
    assert detect([NOTE_EMPTY]) is True
    assert detect([NOTE_EMPTY, MEM_EMPTY]) is True


def test_a_search_that_found_something_keeps_the_message():
    hit = {"tool": "memory_search", "outcome": "ok", "value": "The following are records…"}
    assert detect([hit]) is False
    assert detect([NOTE_EMPTY, hit]) is False


@pytest.mark.parametrize("other", [
    {"tool": "web_search", "outcome": "ok", "value": "Results: …"},
    {"tool": "web_fetch", "outcome": "ok", "value": "A page about kettles"},
    {"tool": "note_propose", "outcome": "ok", "value": "Proposed."},
    {"tool": "creative_write", "outcome": "ok", "value": "Saved."},
])
def test_any_other_successful_call_keeps_the_message(other):
    assert detect([NOTE_EMPTY, other]) is False


def test_no_search_at_all_is_not_a_search_claim():
    assert detect([]) is False
    assert corrections.reports_nothing_found(None) is False
    assert corrections.reports_nothing_found("") is False


def test_a_failed_or_skipped_call_counts_neither_way():
    failed = {"tool": "note_propose", "outcome": "tool_error", "value": None, "error": "refused"}
    assert detect([NOTE_EMPTY, failed]) is True          # the refused call wrote nothing
    assert detect([failed]) is False                     # and a refused call alone claims no search
    skipped = {"tool": "web_search", "outcome": "skipped", "value": None}
    assert detect([NOTE_EMPTY, skipped]) is True


def test_only_a_successful_call_counts_as_an_empty_search():
    """A timed-out or errored call whose text happened to be the sentence is not a report."""
    for outcome in ("tool_error", "timeout", "skipped"):
        assert detect([{**NOTE_EMPTY, "outcome": outcome}]) is False


def test_a_window_marker_and_a_nameless_entry_are_ignored():
    assert detect([{"window_event": "records_trimmed", "iteration": 2}, NOTE_EMPTY, {"x": 1}]) is True
    # an entry that says ok and carries a value but names no tool is not evidence of anything
    assert detect([NOTE_EMPTY, {"outcome": "ok", "value": "text"}]) is True


def test_the_sentence_must_come_first_a_result_that_merely_contains_it_is_not_empty():
    assert detect([{**NOTE_EMPTY, "value": "Found 1 note. " + note_texts.NO_MATCH}]) is False


def test_a_result_that_is_not_a_string_is_not_empty():
    assert detect([{**NOTE_EMPTY, "value": None}]) is False
    assert detect([{**NOTE_EMPTY, "value": ["x"]}]) is False


@pytest.mark.parametrize("bad", ["not json {", "{}", '"text"', "7"])
def test_an_unparseable_or_non_list_trace_keeps_the_candidate_and_warns(bad, caplog):
    with caplog.at_level(logging.WARNING):
        assert corrections.reports_nothing_found(bad, message_id="m-123") is False
    assert "m-123" in caplog.text and "stays a correction candidate" in caplog.text


def test_a_trace_that_parses_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING):
        detect([NOTE_EMPTY])
        corrections.reports_nothing_found(None)
    assert caplog.text == ""


# --- the pool -----------------------------------------------------------------------------------


def seed(store, entity_trace=None, *, user_trace=None, extra=0):
    """A conversation: a person's message, the entity's reply carrying ``entity_trace``, and the
    current turn's two messages (excluded, as in production)."""
    conv = db.start_conversation(store.user_id)
    u1 = db.save_message(conv, store.user_id, "user", "Is there a note on descaling the kettle?",
                         tool_trace=json.dumps(user_trace) if user_trace else None)
    a1 = db.save_message(conv, store.user_id, "assistant", "No, there is no note on descaling.",
                         tool_trace=json.dumps(entity_trace) if entity_trace else None)
    u2 = db.save_message(conv, store.user_id, "user", "OK, how do I descale it?")
    a2 = db.save_message(conv, store.user_id, "assistant", "Use vinegar.")
    return conv, u1, a1, (u2, a2)


def pool(store, conv, exclude, chunk_ids=()):
    return corrections.candidates(store.user_id, conv, list(chunk_ids), exclude_message_ids=exclude,
                                  user_name="Lyle")


def test_an_entity_message_reporting_an_empty_search_is_not_offered(store):
    conv, u1, a1, exclude = seed(store, [NOTE_EMPTY])
    assert {c.message_id for c in pool(store, conv, exclude)} == {u1}


def test_the_same_message_is_offered_when_its_search_found_something(store):
    hit = {"tool": "note_search", "outcome": "ok", "value": "Notes: …\n[note 1 · …]\nText."}
    conv, u1, a1, exclude = seed(store, [hit])
    assert {c.message_id for c in pool(store, conv, exclude)} == {u1, a1}


def test_the_same_message_is_offered_when_it_has_no_trace_or_a_web_search_with_hits(store):
    for trace in (None, [NOTE_EMPTY, {"tool": "web_search", "outcome": "ok", "value": "1. A page"}]):
        conv, u1, a1, exclude = seed(store, trace)
        assert a1 in {c.message_id for c in pool(store, conv, exclude)}


def test_a_message_with_an_unparseable_trace_is_offered_and_logged(store, caplog):
    conv = db.start_conversation(store.user_id)
    db.save_message(conv, store.user_id, "user", "Is there a note?")
    a1 = db.save_message(conv, store.user_id, "assistant", "No note.", tool_trace="not json {")
    u2 = db.save_message(conv, store.user_id, "user", "Ok")
    a2 = db.save_message(conv, store.user_id, "assistant", "Fine.")
    with caplog.at_level(logging.WARNING):
        got = {c.message_id for c in pool(store, conv, (u2, a2))}
    assert a1 in got and "stays a correction candidate" in caplog.text


def test_a_persons_message_is_never_dropped_by_this_rule(store):
    """Only the entity's own trace is read: a user row carrying the same trace stays."""
    conv, u1, a1, exclude = seed(store, None, user_trace=[NOTE_EMPTY])
    assert u1 in {c.message_id for c in pool(store, conv, exclude)}


def test_messages_reached_through_retrieved_chunks_are_filtered_too(store, monkeypatch):
    conv, u1, a1, _ = seed(store, [NOTE_EMPTY])
    rows = [r for r in db.get_conversation_messages(conv)]
    monkeypatch.setattr(db, "get_messages_in_chunks", lambda ids: rows)
    other = db.start_conversation(store.user_id)       # nothing in its open group
    got = {c.message_id for c in corrections.candidates(
        store.user_id, other, ["any-chunk"], exclude_message_ids=(), user_name="Lyle")}
    assert a1 not in got and u1 in got


def test_the_filter_runs_before_the_per_role_cap(store, monkeypatch):
    """With a cap of 2: two older normal entity messages and a newest one reporting an empty search.
    If the empty one were counted against the cap, one normal message would be pushed out."""
    monkeypatch.setattr(corrections, "MAX_CANDIDATES", 2)
    conv = db.start_conversation(store.user_id)
    normal = []
    for i in range(2):
        db.save_message(conv, store.user_id, "user", f"question {i}")
        normal.append(db.save_message(conv, store.user_id, "assistant", f"answer {i}"))
    db.save_message(conv, store.user_id, "user", "Is there a note on kettles?")
    db.save_message(conv, store.user_id, "assistant", "No note.", tool_trace=json.dumps([NOTE_EMPTY]))
    u, a = (db.save_message(conv, store.user_id, "user", "Thanks"),
            db.save_message(conv, store.user_id, "assistant", "Welcome"))
    got = [c for c in pool(store, conv, (u, a)) if c.role == "assistant"]
    assert {c.message_id for c in got} == set(normal)


def test_the_other_household_member_is_still_never_a_candidate(store):
    jodie = db.create_user("Jodie")
    conv = db.start_conversation(jodie)
    db.save_message(conv, jodie, "user", "Pour-over at 10.")
    got = corrections.candidates(store.user_id, conv, [], user_name="Lyle")
    assert got == []


# --- end to end: a turn that used to link, with a classifier that always would -------------------


def _fake_chat(script):
    calls = {"n": 0}

    def fake(messages, *, model=None, options=None, tools=None, timeout=None):
        calls["n"] += 1
        step = script[min(calls["n"] - 1, len(script) - 1)]
        return {"message": {"role": "assistant", "content": step.get("content", ""),
                            **({"tool_calls": step["tool_calls"]} if step.get("tool_calls") else {})},
                "done_reason": "stop"}

    return fake


def _links():
    """The links written ON THE ENTITY'S MESSAGES. (The scripted classifier also links the
    person's own earlier message when offered it, which is outside this rule.)"""
    with db.connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT s.* FROM supersedes s JOIN messages m ON m.id = s.superseded_message_id "
            "WHERE m.role = 'assistant'")]


def _two_turn_conversation(store, monkeypatch, first_turn_script, always_link=True):
    """Turn 1 through the real loop with the given scripted model; turn 2 an ordinary answer.
    The correction classifier is scripted to link whatever it is offered, the adversarial case."""
    monkeypatch.setattr(turn.retrieval, "search", lambda q: None)
    monkeypatch.setattr(classifier, "classify", lambda *a, **k: (
        "CORRECTS 1 REPLACED\n- the answer | contradicts the earlier claim" if always_link
        else "NONE"))
    registry.reset_default_registry()
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "true")
    config.reload()
    registry.reset_default_registry()
    try:
        monkeypatch.setattr(loop.ollama, "chat", _fake_chat(first_turn_script))
        t1 = turn.handle_user_message(store, "Is there a note on descaling the kettle?", None,
                                      situation="")
        monkeypatch.setattr(loop.ollama, "chat", _fake_chat(
            [{"content": "Use equal parts vinegar and water, boil it, and rinse."}]))
        t2 = turn.handle_user_message(store, "OK, how do I descale it?", t1.conversation_id,
                                      situation="")
    finally:
        registry.reset_default_registry()
    return t1, t2


SEARCH_CALL = {"tool_calls": [{"function": {"name": "note_search",
                                              "arguments": {"query": "descaling the kettle"}}}]}


def test_a_no_note_claim_after_an_empty_note_search_is_not_linked_to_the_next_answer(
        store, monkeypatch):
    t1, t2 = _two_turn_conversation(
        store, monkeypatch, [SEARCH_CALL, {"content": "No, there is no note on descaling."}])
    assert t1.trace and t1.trace[0]["tool"] == "note_search"
    assert _links() == [], "the entity's 'no note' claim was linked as superseded"


def test_the_same_turn_without_a_search_still_links_with_that_classifier(store, monkeypatch):
    """The control: the exclusion is the reason there is no link above, not a dead classifier."""
    t1, t2 = _two_turn_conversation(
        store, monkeypatch, [{"content": "No, there is no note on descaling."}])
    assert t1.trace == []
    links = _links()
    assert len(links) == 1 and links[0]["superseded_message_id"] == t1.assistant_message_id


def test_a_genuine_correction_in_a_conversation_with_no_search_still_links(store, monkeypatch):
    t1, t2 = _two_turn_conversation(
        store, monkeypatch, [{"content": "The kettle should be descaled every six months."}])
    assert [row["superseded_message_id"] for row in _links()] == [t1.assistant_message_id]


def test_a_claim_whose_message_also_has_a_web_search_with_hits_is_still_a_candidate(
        store, monkeypatch):
    from program.tools import web_search as ws
    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: _Resp({
        "query": "x", "unresponsive_engines": [],
        "results": [{"title": "Descaling", "url": "https://example.org/d", "content": "Use vinegar.",
                     "score": 1.0}]}))
    web = {"tool_calls": [{"function": {"name": "web_search",
                                          "arguments": {"query": "descale kettle"}}}]}
    t1, t2 = _two_turn_conversation(
        store, monkeypatch, [SEARCH_CALL, web, {"content": "There is no note, but the web says vinegar."}])
    tools = [e["tool"] for e in t1.trace if e.get("tool")]
    assert tools == ["note_search", "web_search"]
    assert [row["superseded_message_id"] for row in _links()] == [t1.assistant_message_id]


# --- the frozen measurement cannot see this -----------------------------------------------------


def test_the_correction_eval_harness_never_calls_candidates():
    """The harness builds its own pools (`Case.pool()` through `production_order`), so this change
    cannot move the frozen 44-case record. Pinned on the code, not on the fingerprint alone."""
    from program.integrity import correction_eval

    tree = ast.parse(inspect.getsource(correction_eval))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "candidates" not in called
    assert "reports_nothing_found" not in called


def test_the_only_production_caller_of_candidates_is_the_turn():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "program"
    callers = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "candidates"
                    and getattr(node.func.value, "id", "") == "corrections"):
                callers.append(path.name)
    assert callers == ["turn.py"]


# --- the symmetric skip: a NEW message that reports only empty searches is not classified -------


def test_the_parsed_form_agrees_with_the_json_form():
    cases = [[NOTE_EMPTY], [NOTE_EMPTY, MEM_EMPTY], [], [NOTE_EMPTY, {"tool": "web_search", "outcome": "ok", "value": "Results"}],
             [{**NOTE_EMPTY, "outcome": "tool_error"}], [{"window_event": "x"}, NOTE_EMPTY]]
    for entries in cases:
        assert corrections.entries_report_nothing_found(entries) == detect(entries)
    assert corrections.entries_report_nothing_found(None) is False


def _turns_with_an_earlier_genuine_claim(store, monkeypatch, second_answer_script):
    """Turn 1: the entity makes a genuine claim (no search). Turn 2: the scripted second answer.
    The correction classifier is scripted to link whatever it is offered."""
    monkeypatch.setattr(turn.retrieval, "search", lambda q: None)
    monkeypatch.setattr(classifier, "classify", lambda *a, **k: (
        "CORRECTS 1 REPLACED\n- the claim | the new message contradicts it"))
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "true")
    config.reload()
    registry.reset_default_registry()
    try:
        monkeypatch.setattr(loop.ollama, "chat", _fake_chat(
            [{"content": "The kettle should be descaled once a month with white vinegar."}]))
        t1 = turn.handle_user_message(store, "How often should the kettle be descaled?", None, situation="")
        monkeypatch.setattr(loop.ollama, "chat", _fake_chat(second_answer_script))
        t2 = turn.handle_user_message(store, "Is there a note on that?", t1.conversation_id, situation="")
    finally:
        registry.reset_default_registry()
    return t1, t2


def test_an_answer_reporting_only_an_empty_search_is_not_judged_against_the_earlier_claim(
        store, monkeypatch):
    t1, t2 = _turns_with_an_earlier_genuine_claim(
        store, monkeypatch, [SEARCH_CALL, {"content": "There is no note about descaling."}])
    assert [e["tool"] for e in t2.trace if e.get("tool")] == ["note_search"]
    assert _links() == [], "an empty-search report was judged to supersede the entity's earlier claim"


def test_the_same_answer_with_no_search_is_still_judged(store, monkeypatch):
    t1, t2 = _turns_with_an_earlier_genuine_claim(
        store, monkeypatch, [{"content": "There is no note about descaling."}])
    assert [row["superseded_message_id"] for row in _links()] == [t1.assistant_message_id]


def test_an_answer_with_an_empty_search_and_a_successful_web_search_is_still_judged(
        store, monkeypatch):
    from program.tools import web_search as ws
    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: _Resp({
        "query": "x", "unresponsive_engines": [],
        "results": [{"title": "Descaling", "url": "https://example.org/d", "content": "Every two weeks.",
                     "score": 1.0}]}))
    web = {"tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "descale"}}}]}
    t1, t2 = _turns_with_an_earlier_genuine_claim(
        store, monkeypatch, [SEARCH_CALL, web, {"content": "No note, but the web says every two weeks."}])
    assert [row["superseded_message_id"] for row in _links()] == [t1.assistant_message_id]


def test_a_window_marker_in_the_turns_trace_does_not_hide_an_empty_search(store, monkeypatch):
    """`loop.call_entries` strips B21's markers before the trace is read."""
    t1, t2 = _turns_with_an_earlier_genuine_claim(
        store, monkeypatch, [SEARCH_CALL, {"content": "There is no note about descaling."}])
    assert corrections.entries_report_nothing_found(
        loop.call_entries([{"window_event": "records_trimmed", "iteration": 2}, *t2.trace])) is True
