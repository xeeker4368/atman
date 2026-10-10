"""Earlier replies' tool calls reach later turns as one system-written list (#30, #32 D4).

Never a result: tool name, a short query, the outcome, and the receipt's word. The list is in the
system message, after the speaker line and before the retrieved records, anchored to the person's
quoted message, capped at 800 characters; history goes to the model exactly as stored.
"""

from __future__ import annotations

import json
import logging

import pytest

from program import config
from program.engine import earlier_tools, loop, prompt, turn
from program.memory import db
from program.settings.permissions import Actor, Role
from program.tools import receipts
from program.tools.registry import Tool, ToolRegistry

SEARCH = {"call_id": "c1", "tool": "memory_search", "arguments": {"query": "the walnut table"},
          "outcome": "ok", "ran": True, "value": "SECRET RESULT TEXT", "error": None,
          "records": []}


class _Row(dict):
    def keys(self):
        return list(super().keys())


def rows(*pairs):
    """(user text, assistant trace or None) pairs as stored rows."""
    out = []
    for question, trace in pairs:
        out.append(_Row(role="user", content=question, tool_trace=None))
        out.append(_Row(role="assistant", content="a reply",
                        tool_trace=json.dumps(trace) if trace is not None else None))
    return out


# --- one entry --------------------------------------------------------------------------------


def test_a_piece_names_the_tool_its_query_and_outcome_and_never_its_result():
    assert earlier_tools.call_pieces([SEARCH]) == ['memory_search "the walnut table" succeeded']
    listing = earlier_tools.system_list(rows(("find the table", [SEARCH])))
    assert listing == (earlier_tools.HEADER + "\n"
                       '- Your reply to "find the table": '
                       'memory_search "the walnut table" succeeded.')
    assert "SECRET RESULT TEXT" not in listing


def test_a_side_effect_call_carries_its_receipt_word(monkeypatch):
    monkeypatch.setattr(earlier_tools, "side_effect_tools", lambda: ["note_propose"])
    monkeypatch.setattr(receipts, "for_trace",
                        lambda trace: [receipts.Receipt("note_propose", receipts.PROPOSED)])
    entry = {**SEARCH, "tool": "note_propose", "arguments": {"text": "medium roast"}}
    [piece] = earlier_tools.call_pieces([entry])
    assert piece == "note_propose succeeded, recorded as proposed"
    assert "medium roast" not in piece


def test_failures_and_timeouts_are_said_plainly_and_window_events_are_not_calls():
    trace = [{**SEARCH, "outcome": "tool_error"},
             {"iteration": 2, "window_event": "rounds_dropped", "rounds": 1, "of_rounds": 1},
             {**SEARCH, "call_id": "c2", "tool": "web_search", "outcome": "timeout",
              "arguments": {"query": "x " * 80}}]
    pieces = earlier_tools.call_pieces(trace)
    assert pieces[0] == 'memory_search "the walnut table" failed'
    assert pieces[1].startswith("web_search") and "timed out, result unknown" in pieces[1]
    assert len(pieces) == 2 and "…" in pieces[1], "a window event was read as a call"


def test_the_anchor_is_the_question_the_reply_answered_quoted_and_cut():
    question = "can   you look\nup when the library opens on the weekend please"
    listing = earlier_tools.system_list(rows(("hello", None), (question, [SEARCH])))
    [entry] = listing.splitlines()[1:]
    quote = "can you look up when the library opens …"
    assert entry.startswith(f'- Your reply to "{quote}": ')
    assert len(quote) == earlier_tools.QUOTE_MAX_CHARS


def test_more_than_three_calls_are_counted_not_listed():
    trace = [{**SEARCH, "call_id": f"c{i}", "arguments": {"query": f"q{i}"}} for i in range(5)]
    [entry] = earlier_tools.system_list(rows(("look", trace))).splitlines()[1:]
    assert entry.count("memory_search") == earlier_tools.CALLS_PER_ENTRY
    assert entry.endswith("; and 2 more calls.")


def test_no_tools_means_no_list():
    assert earlier_tools.system_list(rows(("hi", None), ("again", []))) == ""
    assert earlier_tools.system_list([]) == ""


# --- the cap ------------------------------------------------------------------------------------


def test_the_list_never_exceeds_its_cap_and_counts_what_it_leaves_out():
    long = "y" * 200
    trace = [{**SEARCH, "call_id": f"c{i}", "tool": "moltbook_search",
              "arguments": {"query": long}, "outcome": "invalid_arguments"} for i in range(4)]
    history = rows(*[(f"question number {n} " + long, trace) for n in range(12)])
    listing = earlier_tools.system_list(history)
    lines = listing.splitlines()

    assert len(listing) <= earlier_tools.LIST_MAX_CHARS == 800
    assert lines[0] == earlier_tools.HEADER
    kept = [line for line in lines if line.startswith("- Your reply to")]
    assert kept and "question number 11" in kept[0], "newest first"
    assert lines[-1] == f"- And {12 - len(kept)} earlier replies used tools."


def test_one_left_out_reply_is_said_in_the_singular():
    assert earlier_tools._closing(1) == "- And 1 earlier reply used tools."
    assert earlier_tools._closing(2) == "- And 2 earlier replies used tools."


def test_every_listed_reply_fits_when_there_is_room():
    history = rows(*[(f"q{n}", [SEARCH]) for n in range(3)])
    lines = earlier_tools.system_list(history).splitlines()
    assert [line.split('"')[1] for line in lines[1:]] == ["q2", "q1", "q0"]
    assert not lines[-1].startswith("- And")


def test_an_unreadable_trace_is_left_out_and_says_so(caplog):
    history = [_Row(role="user", content="hi", tool_trace=None),
               _Row(role="assistant", content="hello", tool_trace="{not json")]
    with caplog.at_level(logging.WARNING):
        assert earlier_tools.system_list(history) == ""
    assert "could not read a stored trace" in caplog.text


def test_the_header_passes_the_authored_text_checks():
    prompt.check_authored_text(earlier_tools.HEADER, "earlier-tools header")


# --- end to end -------------------------------------------------------------------------------


@pytest.fixture
def lyle(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    user = db.create_user("Lyle", role="admin")
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    return Actor(user_id=user, name="Lyle", role=Role.ADMIN)


def _probe_registry():
    probe = Tool(name="probe_search", description="TEST-ONLY search.",
                 parameters={"type": "object", "properties": {"query": {"type": "string"}},
                             "required": ["query"]},
                 handler=lambda query: "RESULT THE MODEL SAW ONCE")
    return ToolRegistry([probe])


def _chat_that_searches_once(sent):
    def fake_chat(messages, model=None, options=None, tools=None):
        if messages and messages[0].get("role") == "system":
            sent.append(messages)
        if len(sent) == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "probe_search", "arguments": {"query": "fern"}}}]},
                "done_reason": "stop"}
        return {"message": {"role": "assistant", "content": "I looked it up."},
                "done_reason": "stop"}
    return fake_chat


def test_the_next_turn_sees_the_list_in_the_system_message_and_history_as_stored(
        lyle, monkeypatch):
    registry = _probe_registry()
    sent = []
    monkeypatch.setattr(loop.ollama, "chat", _chat_that_searches_once(sent))
    first = turn.handle_user_message(lyle, "look up the fern", situation="", registry=registry)
    turn.handle_user_message(lyle, "and what did you do?", first.conversation_id,
                             situation="", registry=registry)

    system, *history = sent[-1]
    expected = (earlier_tools.HEADER + "\n"
                '- Your reply to "look up the fern": probe_search "fern" succeeded.')
    assert expected in system["content"]
    assert (system["content"].index("You are talking with Lyle.")
            < system["content"].index(earlier_tools.HEADER))
    earlier = [m for m in history if m["role"] == "assistant"]
    assert earlier[-1]["content"] == "I looked it up.", "history goes as stored"
    assert "system record" not in json.dumps(history)
    assert "RESULT THE MODEL SAW ONCE" not in json.dumps(sent[-1])
    stored = [m for m in db.get_conversation_messages(first.conversation_id)
              if m["role"] == "assistant"]
    assert stored[0]["content"] == "I looked it up.", "the stored reply must not change"


def test_the_list_is_never_in_the_situation_the_gate_reads(lyle, monkeypatch):
    registry = _probe_registry()
    sent, situations = [], []
    real_check = turn.gate.check

    def check(answer, trace=(), situation="", *a, **k):
        situations.append(situation)
        return real_check(answer, trace, situation, *a, **k)

    monkeypatch.setattr(loop.ollama, "chat", _chat_that_searches_once(sent))
    monkeypatch.setattr(turn.gate, "check", check)
    first = turn.handle_user_message(lyle, "look up the fern", registry=registry)
    turn.handle_user_message(lyle, "and then?", first.conversation_id, registry=registry)

    assert earlier_tools.HEADER in sent[-1][0]["content"]
    assert len(situations) == 2
    assert all(earlier_tools.HEADER not in s and "probe_search" not in s for s in situations)


def test_the_parts_sum_to_the_system_string_and_the_budget_counts_the_list(monkeypatch):
    captured = {}
    real = prompt.history.plan_budget

    def spy(system_prompt_chars=0, retrieved_chars=0, context_tokens=None, tool_schema_chars=0):
        captured["system_prompt_chars"] = system_prompt_chars
        return real(system_prompt_chars, retrieved_chars, context_tokens, tool_schema_chars)

    monkeypatch.setattr(prompt.history, "plan_budget", spy)
    situation = "The current time is 2026-09-01T16:00:00+00:00."
    listing = earlier_tools.system_list(rows(("look", [SEARCH])))
    without = prompt.assemble_turn([], situation, speaker="Lyle")
    before = captured["system_prompt_chars"]
    assembled = prompt.assemble_turn([], situation, speaker="Lyle", earlier_tools=listing)

    assert assembled.earlier_tools_chars == len(listing) and without.earlier_tools_chars == 0
    assert (assembled.soul_chars + assembled.operational_chars + assembled.situation_chars
            + assembled.speaker_chars + assembled.earlier_tools_chars
            + assembled.retrieved_chars + assembled.scaffolding_chars) == len(assembled.system)
    assert captured["system_prompt_chars"] - before == len(listing) + len(prompt._SECTION_SEP)
    assert f"You are talking with Lyle.\n\n{listing}" in assembled.system
