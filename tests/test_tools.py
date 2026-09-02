"""Tool registry and dispatch.

**Every `Tool` in this file is test-only scaffolding, not a real tool.** They
exist to give dispatch something to dispatch to. None is registered into
`registry.TOOLS` or the default registry, and
`test_the_default_registry_is_empty_because_no_tools_exist_yet` fails if one
ever is — a placeholder tool would read as built while being nothing, the same
category of problem as a permission gate mounted on no route.
"""

from __future__ import annotations

import pytest

from program.tools import registry
from program.tools.registry import (
    DuplicateToolError,
    Tool,
    ToolError,
    ToolOutcome,
    ToolRegistry,
    UnknownToolError,
)

# --- test-only scaffolding ---------------------------------------------------

SCAFFOLD_ECHO = Tool(
    name="scaffold_echo",
    description="TEST-ONLY scaffolding. Returns its argument.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    handler=lambda text: f"echo: {text}",
)

SCAFFOLD_ADD = Tool(
    name="scaffold_add",
    description="TEST-ONLY scaffolding. Adds two numbers.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
    handler=lambda a, b: a + b,
)


def _boom(**_kwargs):
    raise ValueError("scaffolding failure, on purpose")


SCAFFOLD_RAISES = Tool(
    name="scaffold_raises",
    description="TEST-ONLY scaffolding. Always raises.",
    parameters={"type": "object", "properties": {}},
    handler=_boom,
)

SCAFFOLD_OPTIONAL = Tool(
    name="scaffold_optional",
    description="TEST-ONLY scaffolding. One optional argument.",
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer"}, "flag": {"type": "boolean"}},
    },
    handler=lambda limit=10, flag=False: {"limit": limit, "flag": flag},
)


@pytest.fixture
def bench() -> ToolRegistry:
    """An isolated registry. Never the default one."""
    return ToolRegistry([SCAFFOLD_ECHO, SCAFFOLD_ADD, SCAFFOLD_RAISES, SCAFFOLD_OPTIONAL])


# --- No real tools exist -----------------------------------------------------


def test_the_default_registry_is_empty_because_no_tools_exist_yet():
    """memory_search, web_search, web_fetch and ingestion are later tasks.

    If this fails, either a real tool landed (update it) or scaffolding leaked
    out of a test file (fix that) — a placeholder would read as built.
    """
    registry.reset_default_registry()
    assert registry.TOOLS == ()
    assert len(registry.default_registry()) == 0
    assert registry.default_registry().names == ()


def test_no_scaffolding_tool_is_reachable_from_the_default_registry():
    for name in (
        "scaffold_echo", "scaffold_add", "scaffold_raises", "scaffold_optional",
    ):
        assert not registry.default_registry().has(name)
        result = registry.dispatch(name, {})
        assert result.outcome is ToolOutcome.UNKNOWN_TOOL


# --- Registration ------------------------------------------------------------


def test_a_registered_tool_is_findable(bench):
    assert bench.has("scaffold_echo")
    assert bench.get("scaffold_echo") is SCAFFOLD_ECHO
    assert bench.names == (
        "scaffold_add", "scaffold_echo", "scaffold_optional", "scaffold_raises",
    )
    assert len(bench) == 4


def test_registering_a_duplicate_name_raises(bench):
    """A repo bug, not a model mistake — so it raises rather than returning."""
    with pytest.raises(DuplicateToolError, match="already registered"):
        bench.register(SCAFFOLD_ECHO)


def test_get_raises_for_an_unknown_name_and_lists_what_exists(bench):
    with pytest.raises(UnknownToolError, match="scaffold_echo"):
        bench.get("no_such_tool")


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"name": "Bad Name"}, "lowercase"),
        ({"name": "9leading"}, "lowercase"),
        ({"description": "   "}, "no description"),
        ({"parameters": {"type": "string"}}, "object schema"),
        ({"handler": "not callable"}, "not callable"),
    ],
)
def test_an_invalid_tool_definition_raises_at_construction(kwargs, match):
    base = {
        "name": "valid_name",
        "description": "A description.",
        "parameters": {"type": "object", "properties": {}},
        "handler": lambda: None,
    }
    with pytest.raises(ToolError, match=match):
        Tool(**{**base, **kwargs})


def test_an_empty_registry_is_valid():
    empty = ToolRegistry()
    assert len(empty) == 0
    assert empty.ollama_schema() == []


# --- Successful dispatch -----------------------------------------------------


def test_successful_dispatch_returns_the_value(bench):
    result = bench.dispatch("scaffold_echo", {"text": "hello"})

    assert result.outcome is ToolOutcome.OK
    assert result.ok is True
    assert result.ran is True
    assert result.value == "echo: hello"
    assert result.error is None
    assert result.tool_name == "scaffold_echo"
    assert result.arguments == {"text": "hello"}


def test_optional_arguments_may_be_omitted(bench):
    result = bench.dispatch("scaffold_optional", {})
    assert result.ok
    assert result.value == {"limit": 10, "flag": False}


def test_every_dispatch_gets_a_unique_call_id(bench):
    ids = {bench.dispatch("scaffold_echo", {"text": "x"}).call_id for _ in range(5)}
    assert len(ids) == 5


def test_duration_is_recorded(bench):
    result = bench.dispatch("scaffold_add", {"a": 1, "b": 2})
    assert result.value == 3
    assert result.duration_seconds >= 0.0


# --- Unknown tool ------------------------------------------------------------


def test_dispatching_an_unknown_tool_returns_rather_than_raising(bench):
    """The model naming a tool that does not exist is expected, not exceptional.

    Task 2.2 has to feed this back so the model can correct itself; raising
    would take the whole turn down.
    """
    result = bench.dispatch("hallucinated_tool", {"anything": 1})

    assert result.outcome is ToolOutcome.UNKNOWN_TOOL
    assert result.ok is False
    assert result.ran is False
    assert "hallucinated_tool" in result.error
    assert "scaffold_echo" in result.error  # tells the model what does exist


# --- Malformed arguments -----------------------------------------------------


def test_a_missing_required_argument_is_invalid_arguments_not_tool_error(bench):
    """The distinction task 2.2 depends on: the call was wrong, not the tool."""
    result = bench.dispatch("scaffold_echo", {})

    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert result.ran is False, "the handler must not have been entered"
    assert "missing required" in result.error
    assert "text" in result.error


def test_an_unexpected_argument_is_rejected_before_the_tool_runs(bench):
    result = bench.dispatch("scaffold_echo", {"text": "hi", "bogus": 1})
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert result.ran is False
    assert "bogus" in result.error


def test_a_wrongly_typed_argument_is_rejected(bench):
    result = bench.dispatch("scaffold_add", {"a": 1, "b": "two"})
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert "must be integer" in result.error


def test_a_boolean_is_not_accepted_as_an_integer(bench):
    """True == 1 in Python; the JSON Schema types are distinct."""
    result = bench.dispatch("scaffold_add", {"a": True, "b": 2})
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert "boolean" in result.error


def test_non_object_arguments_are_invalid_arguments(bench):
    result = bench.dispatch("scaffold_echo", ["not", "an", "object"])
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert "must be an object" in result.error


def test_omitted_arguments_are_treated_as_empty(bench):
    result = bench.dispatch("scaffold_echo")
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert "missing required" in result.error


# --- A tool that raises ------------------------------------------------------


def test_a_raising_tool_is_tool_error_not_a_crash(bench):
    result = bench.dispatch("scaffold_raises", {})

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert result.ok is False
    assert result.ran is True, "the handler was entered, unlike a malformed call"
    assert "ValueError" in result.error
    assert "on purpose" in result.error


def test_tool_error_and_invalid_arguments_are_distinguishable(bench):
    """Collapsing these would leave 2.2 unable to tell a bad argument from a
    broken tool, and so unable to decide whether a retry could help."""
    malformed = bench.dispatch("scaffold_echo", {})
    failed = bench.dispatch("scaffold_raises", {})

    assert malformed.outcome is not failed.outcome
    assert malformed.ran is False and failed.ran is True


def test_base_exceptions_are_not_swallowed():
    """KeyboardInterrupt, SystemExit and the suite's own isolation guard.

    StoreIsolationViolation derives from BaseException precisely so that an
    `except Exception` cannot hide it; dispatch must not be the place it does.
    """
    def interrupt(**_kwargs):
        raise KeyboardInterrupt("ctrl-c during a tool")

    bench = ToolRegistry([
        Tool(
            name="scaffold_interrupt",
            description="TEST-ONLY scaffolding.",
            parameters={"type": "object", "properties": {}},
            handler=interrupt,
        )
    ])
    with pytest.raises(KeyboardInterrupt):
        bench.dispatch("scaffold_interrupt", {})


# --- The trace entry (feeds task 3.1) ---------------------------------------


def test_the_trace_entry_is_structured_not_a_string(bench):
    """BUILD_PLAN: the trace is a first-class return value the fabrication gate
    reasons over structurally, not debug output."""
    entry = bench.dispatch("scaffold_echo", {"text": "hi"}).to_trace_entry()

    assert isinstance(entry, dict)
    assert set(entry) == {
        "call_id", "tool", "arguments", "outcome", "ran", "value", "error",
        "duration_seconds",
    }
    assert entry["tool"] == "scaffold_echo"
    assert entry["outcome"] == "ok"
    assert entry["ran"] is True
    assert entry["value"] == "echo: hi"


def test_a_call_id_can_be_matched_back_to_a_real_call(bench):
    """Task 3.1 checks a claimed result against what actually happened."""
    results = [bench.dispatch("scaffold_echo", {"text": str(i)}) for i in range(3)]
    trace = [r.to_trace_entry() for r in results]
    by_id = {entry["call_id"]: entry for entry in trace}

    assert by_id[results[1].call_id]["value"] == "echo: 1"
    assert "invented-call-id" not in by_id


def test_failed_calls_appear_in_the_trace_too(bench):
    """A tool that failed must be visible, or a claim about it is uncheckable."""
    for name, args in (
        ("scaffold_raises", {}), ("no_such", {}), ("scaffold_echo", {}),
    ):
        entry = bench.dispatch(name, args).to_trace_entry()
        assert entry["outcome"] != "ok"
        assert entry["error"]


# --- The Ollama schema -------------------------------------------------------


def test_a_tool_renders_in_the_shape_ollama_accepts(bench):
    schema = SCAFFOLD_ECHO.to_ollama_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "scaffold_echo"
    assert schema["function"]["parameters"]["required"] == ["text"]


def test_the_registry_renders_every_tool(bench):
    schema = bench.ollama_schema()
    assert len(schema) == 4
    assert {entry["function"]["name"] for entry in schema} == set(bench.names)


def test_registries_are_isolated_from_each_other():
    """A test registry must not leak into another registry or the default."""
    one = ToolRegistry([SCAFFOLD_ECHO])
    two = ToolRegistry()

    assert one.has("scaffold_echo")
    assert not two.has("scaffold_echo")
    assert not registry.default_registry().has("scaffold_echo")
