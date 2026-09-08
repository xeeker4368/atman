"""The agent loop: iterate, dispatch, feed back, stop. Task 2.2.

No store and no live model. The model is a scripted fake so that "what did the
loop send on the second call" is answerable exactly, and the tools are the same
kind of TEST-ONLY scaffolding `tests/test_tools.py` uses — none is registered
into `registry.TOOLS`.

Assertions are about outcomes: that the last call carries no tools, that a
timed-out tool is reported as *entered, outcome unknown* rather than as a
failure, that every tool message the model receives has a matching trace entry.
"""

from __future__ import annotations

import time

import pytest

from program import config
from program.engine import loop
from program.engine.loop import ANSWERED, ITERATION_LIMIT
from program.engine.ollama import OllamaUnreachable
from program.tools.registry import Tool, ToolOutcome, ToolRegistry

# --- test-only scaffolding ---------------------------------------------------

ECHO = Tool(
    name="scaffold_echo",
    description="TEST-ONLY scaffolding. Returns its argument.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    handler=lambda text: f"echo: {text}",
)

SLOW = Tool(
    name="scaffold_slow",
    description="TEST-ONLY scaffolding. Sleeps past its own timeout.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: time.sleep(0.3) or "finished after all",
    timeout_seconds=0.05,
)

PATIENT = Tool(
    name="scaffold_patient",
    description="TEST-ONLY scaffolding. Sleeps, but declares a long timeout.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: time.sleep(0.2) or "done",
    timeout_seconds=5.0,
)

BIG = Tool(
    name="scaffold_big",
    description="TEST-ONLY scaffolding. Returns more than fits.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: "x" * 500,
)


def answer(text: str) -> dict:
    return {"role": "assistant", "content": text}


def calls(*specs) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"function": {"name": name, "arguments": args}} for name, args in specs
        ],
    }


class FakeModel:
    """A scripted ``ollama.chat``. Records every call it was handed."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, messages, *, model=None, options=None, tools=None, timeout=None):
        self.calls.append({"messages": messages, "tools": tools})
        if not self.responses:
            raise AssertionError("the loop called the model more times than scripted")
        return {"message": self.responses.pop(0)}

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def model(monkeypatch):
    """Install a scripted model; the test fills in the script."""

    def install(*responses) -> FakeModel:
        fake = FakeModel(*responses)
        monkeypatch.setattr(loop.ollama, "chat", fake)
        return fake

    return install


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry([ECHO, SLOW, PATIENT, BIG])


HISTORY = [{"role": "user", "content": "what did we say about espresso?"}]


# --- Terminating ------------------------------------------------------------


def test_a_response_with_no_tool_calls_ends_the_turn(model, registry):
    fake = model(answer("Grind finer."))

    result = loop.run_turn(HISTORY, registry=registry)

    assert result.text == "Grind finer."
    assert result.iterations == 1
    assert result.stop_reason == ANSWERED
    assert result.trace == []
    assert fake.call_count == 1


def test_a_tool_call_is_dispatched_and_its_result_fed_back(model, registry):
    fake = model(calls(("scaffold_echo", {"text": "hi"})), answer("It said: echo: hi"))

    result = loop.run_turn(HISTORY, registry=registry)

    assert result.text == "It said: echo: hi"
    assert result.iterations == 2
    assert len(result.trace) == 1
    assert result.trace[0]["outcome"] == ToolOutcome.OK.value
    assert result.trace[0]["value"] == "echo: hi"

    # The second model call saw the assistant's tool call and the result.
    second = fake.calls[1]["messages"]
    assert any(m.get("tool_calls") for m in second), "the tool call itself was dropped"
    tool_messages = [m for m in second if m["role"] == "tool"]
    assert [m["content"] for m in tool_messages] == ["echo: hi"]


# --- The iteration limit ----------------------------------------------------


def test_the_last_iteration_is_sent_with_no_tools_so_the_turn_cannot_hang(
    model, registry
):
    """L bounds model calls, and the Lth is offered no tools — so it must answer."""
    fake = model(
        calls(("scaffold_echo", {"text": "a"})),
        calls(("scaffold_echo", {"text": "b"})),
        answer("Enough looking; here is the answer."),
    )

    result = loop.run_turn(HISTORY, registry=registry, max_iterations=3)

    assert fake.call_count == 3
    assert [bool(c["tools"]) for c in fake.calls] == [True, True, False]
    assert result.stop_reason == ITERATION_LIMIT
    assert result.text == "Enough looking; here is the answer."


def test_tool_calls_on_the_final_iteration_are_recorded_as_skipped_not_dropped(
    model, registry
):
    """The model asked, nothing ran. A trace that omitted it could not say so."""
    fake = model(
        calls(("scaffold_echo", {"text": "a"})),
        calls(("scaffold_echo", {"text": "b"})),
    )

    result = loop.run_turn(HISTORY, registry=registry, max_iterations=2)

    assert fake.call_count == 2
    assert result.stop_reason == ITERATION_LIMIT
    assert [e["outcome"] for e in result.trace] == [
        ToolOutcome.OK.value,
        ToolOutcome.SKIPPED.value,
    ]
    assert result.trace[-1]["ran"] is False


def test_a_single_iteration_turn_is_never_offered_tools(model, registry):
    fake = model(answer("Just answering."))

    loop.run_turn(HISTORY, registry=registry, max_iterations=1)

    assert fake.calls[0]["tools"] is None


# --- Timeouts and the turn's tool budget ------------------------------------


def test_a_tool_that_overruns_is_a_timeout_not_a_tool_error(model, registry):
    """`timeout` and `tool_error` are different claims about what happened."""
    fake = model(calls(("scaffold_slow", {})), answer("It never came back."))

    result = loop.run_turn(HISTORY, registry=registry)

    entry = result.trace[0]
    assert entry["outcome"] == ToolOutcome.TIMEOUT.value
    assert entry["ran"] is True, "a timed-out handler was entered; side effects possible"
    assert entry["value"] is None
    assert entry["timeout_seconds"] == 0.05

    fed_back = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert fed_back[0]["content"].startswith("[timeout]")


def test_the_turn_budget_bounds_the_aggregate_not_just_each_call(model, registry):
    """Two calls, each inside its own timeout, together past the turn's budget."""
    fake = model(
        calls(("scaffold_patient", {}), ("scaffold_patient", {})),
        answer("Answered without the second one."),
    )

    result = loop.run_turn(
        HISTORY, registry=registry, max_iterations=2, tool_budget_seconds=0.1
    )

    first, second = result.trace
    # The first is cut to what remained of the budget, not its own 5s timeout.
    assert first["outcome"] == ToolOutcome.TIMEOUT.value
    assert first["timeout_seconds"] == pytest.approx(0.1)
    # The second never started: the budget was already spent.
    assert second["outcome"] == ToolOutcome.SKIPPED.value
    assert second["ran"] is False
    assert result.tool_budget_exhausted

    fed_back = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert fed_back[1]["content"].startswith("[skipped]")


def test_a_declared_timeout_beats_the_configured_default():
    assert SLOW.resolved_timeout() == 0.05
    assert ECHO.resolved_timeout() == config.tool_default_timeout_seconds()


# --- Failures the model is told about ---------------------------------------


def test_an_unknown_tool_is_reported_back_rather_than_ending_the_turn(model, registry):
    fake = model(
        calls(("scaffold_nonexistent", {})), answer("That tool does not exist.")
    )

    result = loop.run_turn(HISTORY, registry=registry)

    assert result.trace[0]["outcome"] == ToolOutcome.UNKNOWN_TOOL.value
    assert result.trace[0]["ran"] is False
    fed_back = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert "[unknown_tool]" in fed_back[0]["content"]


def test_unparseable_arguments_are_invalid_arguments_not_an_empty_call(model, registry):
    """A malformed call must not quietly run the tool with no arguments."""
    fake = model(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "scaffold_echo", "arguments": "{not json"}}
            ],
        },
        answer("I malformed that."),
    )

    result = loop.run_turn(HISTORY, registry=registry)

    assert result.trace[0]["outcome"] == ToolOutcome.INVALID_ARGUMENTS.value
    assert result.trace[0]["ran"] is False
    assert fake.call_count == 2


def test_arguments_sent_as_a_json_string_are_parsed(model, registry):
    fake = model(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "scaffold_echo",
                        "arguments": '{"text": "from a string"}',
                    }
                }
            ],
        },
        answer("ok"),
    )

    result = loop.run_turn(HISTORY, registry=registry)

    assert result.trace[0]["outcome"] == ToolOutcome.OK.value
    assert result.trace[0]["value"] == "echo: from a string"
    assert fake.call_count == 2


def test_a_model_failure_propagates_rather_than_degrading(model, registry, monkeypatch):
    """A turn with no model has no honest degraded form."""

    def unreachable(*args, **kwargs):
        raise OllamaUnreachable("nothing is listening")

    monkeypatch.setattr(loop.ollama, "chat", unreachable)

    with pytest.raises(OllamaUnreachable):
        loop.run_turn(HISTORY, registry=registry)


# --- The trace (task 3.1's input) -------------------------------------------


def test_every_tool_message_the_model_saw_has_a_matching_trace_entry(model, registry):
    """The symmetry the fabrication gate depends on: no result without a record."""
    fake = model(
        calls(("scaffold_echo", {"text": "a"}), ("scaffold_nonexistent", {})),
        calls(("scaffold_slow", {})),
        answer("done"),
    )

    result = loop.run_turn(HISTORY, registry=registry, max_iterations=3)

    sent = [m for m in fake.calls[-1]["messages"] if m["role"] == "tool"]
    assert len(sent) == len(result.trace) == 3
    assert all(entry["call_id"] for entry in result.trace)
    assert len({entry["call_id"] for entry in result.trace}) == 3
    assert [entry["iteration"] for entry in result.trace] == [1, 1, 2]


def test_the_trace_is_structured_data_not_prose(model, registry):
    model(calls(("scaffold_echo", {"text": "hi"})), answer("done"))

    result = loop.run_turn(HISTORY, registry=registry)

    entry = result.trace[0]
    assert isinstance(entry, dict)
    assert set(entry) >= {
        "iteration",
        "call_id",
        "tool",
        "arguments",
        "outcome",
        "ran",
        "value",
        "error",
        "duration_seconds",
        "timeout_seconds",
    }
    assert entry["arguments"] == {"text": "hi"}


def test_a_truncated_result_says_so_and_the_trace_keeps_the_whole_thing(
    model, registry, monkeypatch
):
    monkeypatch.setenv("ANAM_AGENT_MAX_TOOL_RESULT_CHARS", "100")
    config.reload()
    fake = model(calls(("scaffold_big", {})), answer("done"))

    result = loop.run_turn(HISTORY, registry=registry)

    fed_back = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "[truncated: " in fed_back["content"]
    assert len(result.trace[0]["value"]) == 500, "the trace kept the full value"


def test_truncation_is_the_only_thing_that_shortens_a_result():
    """Rendering is verbatim below the ceiling — no reformatting, no summary."""
    from program.tools.registry import ToolResult

    result = ToolResult(
        call_id="c", tool_name="t", arguments={}, outcome=ToolOutcome.OK, value="short"
    )
    assert loop.render_tool_result(result, max_chars=100) == "short"


# --- The window is re-planned each iteration --------------------------------


def test_tool_results_are_priced_against_the_window_not_appended_freely(
    model, registry, monkeypatch
):
    """A long turn evicts old history rather than silently overflowing num_ctx."""
    monkeypatch.setenv("ANAM_MODEL_NUM_CTX", "4096")
    config.reload()

    history = [
        {"role": "user", "content": f"turn {i}: " + "filler words here. " * 20}
        for i in range(12)
    ]
    fake = model(calls(("scaffold_big", {})), answer("done"))

    loop.run_turn(history, registry=registry)

    # System message + whatever history fits. The second call carries the tool
    # exchange, so it must have dropped older turns to stay inside the window.
    first_history = [m for m in fake.calls[0]["messages"] if m["role"] == "user"]
    second = fake.calls[1]["messages"]
    assert len(first_history) < len(history), "history was not windowed at all"
    assert second[-1]["role"] == "tool", "the newest content must survive"
    assert any(m.get("tool_calls") for m in second)
