"""B21 regression: this turn's user message is never windowed out.

Before the fix, windowing walked newest-first and kept only the newest message
unconditionally; after a tool round that is a tool result, so a long question could
be dropped from the prompt with nothing logged. Reproduced by
``scripts/history_window_diagnosis_b21.py``, whose scenarios are run here.

The order of giving up (NOW.md B21, approved at review 2026-10-01): older history,
then the retrieved records (continuation pieces first, then hits from the lowest
rank), then the oldest whole tool rounds, then, as the last resort, a final call
without tools. Every step past older history is logged and marked in the trace.
"""

from __future__ import annotations

import logging

import pytest

from program import config
from program.engine import loop, turn
from program.memory.retrieval import RetrievalResult
from program.tools import receipts
from program.tools.registry import Tool, ToolRegistry
from scripts import history_window_diagnosis_b21 as diag
from tests.test_retrieved_cap import chunk, pathological

MARK = diag.MARK
RECORDS_CAP_STANDIN = 57_000


def events(result) -> list[dict]:
    return [e for e in result.trace if "window_event" in e]


# --- the reproduction's own scenarios ---------------------------------------


@pytest.mark.parametrize("message, records, rounds, calls, dropped_before", [
    (50_000, RECORDS_CAP_STANDIN, 1, 3, "call 2"),
    (50_000, RECORDS_CAP_STANDIN, 2, 2, "call 3"),
    (9_500, RECORDS_CAP_STANDIN, 4, 3, "within 4 rounds"),
    (42_800, RECORDS_CAP_STANDIN, 4, 1, "within 4 rounds"),
    (41_700, 25_000, 4, 3, "within 4 rounds"),
])
def test_the_users_message_is_on_every_call(message, records, rounds, calls, dropped_before):
    """Each case dropped the question before the fix (`dropped_before`, measured)."""
    present, result = diag.scenario("", message, records, rounds, calls)
    assert all(present), f"the user's message was missing from a call: {present}"


def test_a_dropped_round_is_marked_in_the_trace_and_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="program.engine.loop"):
        present, result = diag.scenario("", 50_000, RECORDS_CAP_STANDIN, 1, 3)

    assert all(present)
    assert [e["window_event"] for e in events(result)] == ["rounds_dropped"]
    assert events(result)[0]["iteration"] == 2
    assert any("rounds_dropped" in r.getMessage() for r in caplog.records)
    # the calls themselves are still traced, apart from the markers
    assert len(loop.call_entries(result.trace)) == 3


def test_an_ordinary_turn_is_untouched_and_unmarked():
    present, result = diag.scenario("", 2_000, 2_000, 2, 2)
    assert all(present)
    assert events(result) == []


# --- the order of giving up, on real retrieval results ------------------------


BIG = Tool(name="big_result", description="TEST-ONLY: returns a large result.",
           parameters={"type": "object", "properties": {}, "required": []},
           handler=lambda: "R" * 6000)


def run_with_records(monkeypatch, retrieval: RetrievalResult, message_chars: int,
                     rounds: int, calls: int, tools=(BIG,)):
    sent = []

    def fake_chat(messages, model=None, options=None, tools=None):
        sent.append({"messages": [dict(m) for m in messages], "tools": tools})
        if tools is not None and len(sent) <= rounds:
            return {"message": {"role": "assistant", "content": "", "tool_calls":
                    [{"function": {"name": "big_result", "arguments": {}}}] * calls},
                    "done_reason": "stop"}
        return {"message": {"role": "assistant", "content": "Answered."},
                "done_reason": "stop"}

    monkeypatch.setattr(loop.ollama, "chat", fake_chat)
    user = MARK + " " + "word " * (message_chars // 5)
    result = loop.run_turn([{"role": "user", "content": user[:message_chars]}],
                           retrieval=retrieval, registry=ToolRegistry(list(tools)))
    present = [any(m["role"] == "user" and str(m["content"]).startswith(MARK)
                   for m in call["messages"]) for call in sent]
    return sent, present, result


def short_hits_long_siblings() -> RetrievalResult:
    """Ranked hits of 1,000 chars, each with long continuation pieces, so B17's cap is
    mostly spent on pieces: the first thing to give up under pressure."""
    limit = config.retrieval_max_siblings_per_hit()
    return RetrievalResult(query="q", results=[
        chunk(f"h{h}", f"H{h}:" + "x" * 996,
              [chunk(f"h{h}s{s}", f"H{h}S{s}:" + "y" * 4994) for s in range(1, limit + 1)])
        for h in range(1, config.retrieval_top_k() + 1)])


def test_continuation_pieces_go_before_any_ranked_hit(monkeypatch):
    """B6a's derivation fits a maximal message beside maximal records, so the records
    give way only once this turn's tool rounds need room. Moderate pressure: pieces
    are withheld and every ranked hit is kept."""
    sent, present, result = run_with_records(
        monkeypatch, short_hits_long_siblings(), 50_000, 1, 5)

    assert all(present)
    [event] = events(result)
    assert event["window_event"] == "records_shrunk" and event["iteration"] == 2
    assert event["hits_kept"] == event["hits"] == config.retrieval_top_k()
    assert event["records_chars_after"] < event["records_chars_before"]
    second_call_messages = sent[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert roles[-7:] == ["user", "assistant", *["tool"] * 5], "the round was kept whole"


def test_hits_go_from_the_lowest_rank_and_are_counted(monkeypatch):
    """Heavy pressure: fewer hits, the top ones kept, and the prompt says how many
    were left out."""
    sent, present, result = run_with_records(monkeypatch, pathological(), 50_000, 1, 5)

    assert all(present)
    [event] = events(result)
    assert event["window_event"] == "records_shrunk"
    assert 0 < event["hits_kept"] < event["hits"]
    system = sent[1]["messages"][0]["content"]
    assert "[record 1 ·" in system or "[record 1]" in system
    assert f"[record {event['hits']} " not in system
    left_out = event["hits"] - event["hits_kept"]
    assert f"{left_out} lower-ranked retrieved record" in system


def test_rounds_are_dropped_only_once_the_records_are_gone(monkeypatch):
    """Measured progression at this load: records shrink 8 -> 4 -> 0 hits over the
    iterations, and only then do rounds go. Asserted, not just iterated: a version of
    this test that never produced a dropped round passed by vacuity."""
    sent, present, result = run_with_records(monkeypatch, pathological(), 50_000, 4, 5)

    assert all(present)
    assert any(e["window_event"] == "rounds_dropped" for e in events(result)), (
        "this load must reach the rounds step, or the test proves nothing")
    for event in events(result):
        if event["window_event"] == "rounds_dropped":
            same_call = [e for e in events(result) if e["iteration"] == event["iteration"]
                         and e["window_event"] == "records_shrunk"]
            assert same_call and same_call[0]["hits_kept"] == 0, (
                "a tool round was dropped while records could still have shrunk")


def test_the_last_resort_is_a_call_without_tools(monkeypatch):
    """The question fits only once the tool schemas are gone: no tools are offered
    on that call, and the trace says why."""
    monkeypatch.setenv("ANAM_MODEL_NUM_CTX", "4096")
    config.reload()
    fat = Tool(name="big_result", description="TEST-ONLY. " + "padding " * 500,
               parameters={"type": "object", "properties": {}, "required": []},
               handler=lambda: "R")
    sent, present, result = run_with_records(monkeypatch, None, 1_000, 1, 1, tools=(fat,))

    assert all(present)
    assert sent[0]["tools"] is None, "tools were offered on a call that could not hold them"
    assert [e["window_event"] for e in events(result)] == ["final_call_forced"]
    assert result.stop_reason == loop.ANSWERED


def test_a_question_too_big_for_the_window_is_still_sent_and_marked(monkeypatch):
    monkeypatch.setenv("ANAM_MODEL_NUM_CTX", "4096")
    config.reload()
    sent, present, result = run_with_records(monkeypatch, None, 8_000, 0, 1, tools=())

    assert all(present)
    assert [e["window_event"] for e in events(result)] == ["overflow"]
    assert result.overflowed


# --- markers never reach what reasons over calls -----------------------------


def test_the_gate_is_given_calls_only(monkeypatch):
    marker = {"iteration": 2, "window_event": "rounds_dropped", "rounds": 1, "of_rounds": 1}
    call = {"iteration": 1, "tool": "web_search", "call_id": "c" * 32, "outcome": "ok",
            "ran": True, "arguments": {}, "artifact_ids": []}
    seen = {}

    def spy(answer, trace=(), situation="", ground_truth=None):
        seen["trace"] = list(trace)
        from program.integrity.gate import GateVerdict
        return GateVerdict()

    monkeypatch.setattr(turn.gate, "check", spy)
    monkeypatch.setattr(turn.loop, "run_turn", lambda *a, **k: loop.TurnResult(
        text="Answered.", trace=[call, marker]))
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    from program.integrity import classifier
    monkeypatch.setattr(classifier, "classify", lambda prompt: "NONE")

    from program.memory import db
    db.init_databases()
    uid = db.create_user("Lyle", role="admin")
    from program.settings.permissions import Actor, Role
    turn.handle_user_message(Actor(user_id=uid, name="Lyle", role=Role.ADMIN), "hi")

    assert seen["trace"] == [call]


def test_receipts_ignore_markers():
    marker = {"iteration": 2, "window_event": "records_shrunk"}
    assert receipts.for_trace([marker]) == []


def test_a_turn_with_only_markers_did_not_call_tools():
    assert not loop.TurnResult(text="x", trace=[{"window_event": "overflow"}]).called_tools
