"""B21 diagnosis: is the user's own message dropped from the prompt after tool rounds?

    python -m scripts.history_window_diagnosis_b21

Runs the REAL agent loop, prompt assembly and history windowing on a throwaway data
directory. Only ``ollama.chat`` is replaced, by a fake that records what it is sent and
answers with a TEST-ONLY tool call for a set number of rounds. No model and no network.

The retrieved-records block is stood in for by a situation block of the same size: the
window's budget reads only the system prompt's size, so this changes nothing about the
windowing while avoiding building a synthetic retrieval result.

**Before the B21 fix** (2026-09-30, see NOW.md B21): a 50,000-char message beside
maximal records was dropped after one round of 3 calls, and at the records cap three
calls per round dropped a ~9,400-char message within 4 rounds, with no warning logged.

**After the fix** the user's message is present on every call, and whatever was given
up instead is a ``window_event`` in the trace. ``tests/test_history_window_b21.py``
runs these same scenarios as the regression test.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from contextlib import redirect_stdout

from program import config
from program.engine import loop
from program.tools.registry import Tool, ToolRegistry

MARK = "USER-MESSAGE-MARKER"

BIG = Tool(
    name="big_result",
    description="TEST-ONLY: returns a large result.",
    parameters={"type": "object", "properties": {}, "required": []},
    handler=lambda: "R" * 6000,
)


def scenario(label: str, message_chars: int, records_chars: int, rounds: int,
             calls_per_round: int) -> tuple[list[bool], loop.TurnResult]:
    """Run one turn. Prints, and returns, per model call, whether the user message was
    sent, together with the turn's result (its trace carries any window events).

    ``ollama.chat`` is restored afterwards, so a test can call this safely.
    """
    sent: list[list[dict]] = []

    def fake_chat(messages, model=None, options=None, tools=None):
        sent.append([dict(m) for m in messages])
        if tools is not None and len(sent) <= rounds:
            calls = [{"function": {"name": "big_result", "arguments": {}}}] * calls_per_round
            return {"message": {"role": "assistant", "content": "", "tool_calls": calls},
                    "done_reason": "stop"}
        return {"message": {"role": "assistant", "content": "Answered."},
                "done_reason": "stop"}

    real_chat, loop.ollama.chat = loop.ollama.chat, fake_chat
    user = MARK + " " + ("word " * (message_chars // 5))[: message_chars - len(MARK) - 1]
    situation = ("Record filler text. " * (records_chars // 20 + 1))[:records_chars]
    try:
        result = loop.run_turn([{"role": "user", "content": user}], situation=situation,
                               registry=ToolRegistry([BIG]))
    finally:
        loop.ollama.chat = real_chat

    print(f"\n== {label}: message {len(user):,} chars, records stand-in "
          f"{records_chars:,}, {rounds} round(s) x {calls_per_round} call(s)")
    present = []
    for i, msgs in enumerate(sent, 1):
        has_user = any(m["role"] == "user" and str(m.get("content", "")).startswith(MARK)
                       for m in msgs)
        present.append(has_user)
        chars = sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or []))
                    for m in msgs)
        print(f"  call {i}: {len(msgs)} messages, "
              f"{[m['role'] for m in msgs].count('tool')} tool results, "
              f"user message present: {has_user}, ~{chars:,} chars")
    for event in (e for e in result.trace if "window_event" in e):
        print(f"  window event: {event}")
    return present, result


def smallest_dropped(records_chars: int, rounds: int, calls: int) -> int | None:
    """Binary search for the smallest message that is dropped, or None up to the cap."""
    def dropped(n: int) -> bool:
        with redirect_stdout(io.StringIO()):
            return not all(scenario("", n, records_chars, rounds, calls)[0])

    lo, hi = 1_000, config.chat_max_message_chars()
    if not dropped(hi):
        return None
    while hi - lo > 500:
        mid = (lo + hi) // 2
        lo, hi = (lo, mid) if dropped(mid) else (mid, hi)
    return hi


def main() -> None:
    os.environ["ANAM_DATA_DIR"] = tempfile.mkdtemp(prefix="b21-")
    config.reload()
    records = 57_000  # B17's 51,000-char records cap + the 6,000-char annotation bound
    scenario("maximal message + maximal records, 1 round x 2 calls", 50_000, records, 1, 2)
    scenario("maximal message + maximal records, 1 round x 3 calls", 50_000, records, 1, 3)
    scenario("maximal message + maximal records, 2 rounds x 2 calls", 50_000, records, 2, 2)
    print("\nSmallest message dropped within 4 tool rounds:")
    for rc in (0, 25_000, records):
        for calls in (1, 2, 3):
            found = smallest_dropped(rc, 4, calls)
            where = "never, up to the cap" if found is None else f"from ~{found:,} chars"
            print(f"  records {rc:>6,}, {calls} call(s)/round: {where}")


if __name__ == "__main__":
    main()
