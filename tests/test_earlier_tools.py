"""Earlier turns' tool calls reach later turns as one system-written line (fix plan 3.3, B).

Never a result: tool name, a short query, the outcome, and the receipt's word.
"""

from __future__ import annotations

import json
import logging

import pytest

from program import config
from program.engine import earlier_tools, loop, turn
from program.memory import db
from program.settings.permissions import Actor, Role
from program.tools import receipts
from program.tools.registry import Tool, ToolRegistry

SEARCH = {"call_id": "c1", "tool": "memory_search", "arguments": {"query": "the walnut table"},
          "outcome": "ok", "ran": True, "value": "SECRET RESULT TEXT", "error": None,
          "records": []}


def test_a_line_names_the_tool_its_query_and_outcome_and_never_its_result():
    line = earlier_tools.record_line([SEARCH])
    assert line == (earlier_tools.PREFIX + 'memory_search "the walnut table" succeeded]')
    assert "SECRET RESULT TEXT" not in line


def test_a_side_effect_call_carries_its_receipt_word(monkeypatch):
    monkeypatch.setattr(earlier_tools, "side_effect_tools", lambda: ["note_propose"])
    monkeypatch.setattr(receipts, "for_trace",
                        lambda trace: [receipts.Receipt("note_propose", receipts.PROPOSED)])
    entry = {**SEARCH, "tool": "note_propose", "arguments": {"text": "medium roast"}}
    line = earlier_tools.record_line([entry])
    assert line.endswith("note_propose succeeded, recorded as proposed]")
    assert "medium roast" not in line


def test_failures_and_timeouts_are_said_plainly_and_window_events_are_not_calls():
    trace = [{**SEARCH, "outcome": "tool_error"},
             {"iteration": 2, "window_event": "rounds_dropped", "rounds": 1, "of_rounds": 1},
             {**SEARCH, "call_id": "c2", "tool": "web_search", "outcome": "timeout",
              "arguments": {"query": "x " * 80}}]
    line = earlier_tools.record_line(trace)
    assert "memory_search \"the walnut table\" failed" in line
    assert "web_search" in line and "timed out, result unknown" in line
    assert "rounds_dropped" not in line and "…" in line
    assert line.count("; ") == 1 and "unknown tool" not in line, "a window event was read as a call"


def test_a_turn_with_no_tools_has_no_line():
    assert earlier_tools.record_line([]) is None


def test_an_unreadable_trace_sends_the_turn_unchanged_and_says_so(caplog):
    rows = [{"role": "assistant", "content": "hello", "tool_trace": "{not json"}]
    with caplog.at_level(logging.WARNING):
        out = earlier_tools.with_tool_records([_Row(r) for r in rows])
    assert out == [{"role": "assistant", "content": "hello"}]
    assert "could not read a stored trace" in caplog.text


class _Row(dict):
    def keys(self):
        return list(super().keys())


@pytest.fixture
def lyle(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    user = db.create_user("Lyle", role="admin")
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    return Actor(user_id=user, name="Lyle", role=Role.ADMIN)


def test_the_next_turn_sees_the_earlier_call_and_the_record_is_not_edited(lyle, monkeypatch):
    probe = Tool(name="probe_search", description="TEST-ONLY search.",
                 parameters={"type": "object", "properties": {"query": {"type": "string"}},
                             "required": ["query"]},
                 handler=lambda query: "RESULT THE MODEL SAW ONCE")
    registry = ToolRegistry([probe])
    sent = []

    def fake_chat(messages, model=None, options=None, tools=None):
        sent.append(messages)
        if len(sent) == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "probe_search", "arguments": {"query": "fern"}}}]},
                "done_reason": "stop"}
        return {"message": {"role": "assistant", "content": "I looked it up."},
                "done_reason": "stop"}

    monkeypatch.setattr(loop.ollama, "chat", fake_chat)
    first = turn.handle_user_message(lyle, "look up the fern", situation="", registry=registry)
    turn.handle_user_message(lyle, "and what did you do?", first.conversation_id,
                             situation="", registry=registry)

    earlier = [m for m in sent[-1] if m["role"] == "assistant"]
    assert earlier[-1]["content"] == (
        earlier_tools.PREFIX + 'probe_search "fern" succeeded]\nI looked it up.')
    assert "RESULT THE MODEL SAW ONCE" not in json.dumps(sent[-1])
    stored = [m for m in db.get_conversation_messages(first.conversation_id)
              if m["role"] == "assistant"]
    assert stored[0]["content"] == "I looked it up.", "the stored reply must not change"
