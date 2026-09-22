"""The agent loop: call the model, dispatch its tool calls, feed the results
back, repeat until an answer or the iteration limit. Task 2.2.

This module is the iterate-and-dispatch turn and nothing else. It makes no
database writes, resolves no conversation and authenticates nobody —
``program/engine/turn.py`` does that around it, and the HTTP shape lives in
``program/api/routes/chat.py``. Keeping the loop free of persistence is what
lets it be tested against a fake registry with no store at all.

The loop always ends in an answer
---------------------------------
Iteration ``L`` — ``agent.max_iterations`` — is sent with **no tools attached**.
The model cannot ask for another round because it is not offered one, so the
turn cannot terminate on an unanswered tool call. ``L`` therefore counts model
calls, and the number of tool rounds is ``L - 1``.

The alternative, stopping after ``L`` tool-bearing calls and returning whatever
text happened to accompany the last one, produces a turn that ends mid-thought
with no answer and no explanation. This costs one model call in the worst case
and is the difference between a bounded loop and a truncated one.

Two bounds, not one
-------------------
``L`` bounds model calls. ``agent.tool_budget_seconds`` bounds the wall clock
spent waiting on tools, **in aggregate across the whole turn** — because a
single iteration may emit several tool calls, so a per-tool timeout alone leaves
the turn unbounded. Each dispatch is given ``min(the tool's own timeout, what
remains of the budget)``; once the budget is spent, further calls are recorded
as ``SKIPPED`` and the model is told so rather than being left waiting.

Together those two make the maximum turn duration a finite number, which is
what ``conversations.in_flight_grace_minutes``' floor is derived from. Neither
bound is decoration: remove either and the floor stops being a bound.

The window is re-planned every iteration
----------------------------------------
Tool results are appended to the conversation and then the whole prompt is
re-assembled, so they are priced against the same token budget as everything
else and old history is evicted to make room for them. The obvious shortcut —
budget once, then append freely — spends the turn's slack silently and hands
the overflow to the model server, which drops the oldest content without
reporting it. That is the exact failure ``history.py`` exists to prevent, and it
would have been reintroduced here.

Degrade or abort
----------------
This module **degrades** on tool failure and **propagates** on model failure, on
the criterion ``prompt.py`` and ``retrieval.py`` already record: *abort when a
failure could corrupt something or when retrying is free; degrade when nothing
can be corrupted and a person is waiting.* A failed tool is information the
model can use and answer around, so it is fed back. An unreachable model leaves
nothing to answer with, so it raises and the route reports it honestly.

The trace
---------
``TurnResult.trace`` is a list of structured entries built from
``ToolResult.to_trace_entry()`` — a return value, not a log line, as
BUILD_PLAN requires, so task 3.1's fabrication gate can check a claim against
what actually happened by lookup rather than by reading prose. Every call the
model made appears in it: successful, failed, malformed, unknown, timed out and
skipped alike. A claim about a tool that failed is only checkable if the failure
was recorded.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from program import config
from program.attribution import AttributionContext
from program.engine import ollama, prompt
from program.memory.retrieval import RetrievalResult
from program.tools import registry as tools
from program.tools.registry import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

#: Why the loop stopped. Recorded rather than inferred from the shape of the
#: result, because "the model chose to answer" and "the model ran out of
#: iterations and was made to answer" look identical from the text alone.
ANSWERED = "answered"
ITERATION_LIMIT = "iteration_limit"


@dataclass(frozen=True)
class TurnResult:
    """One completed turn: the answer, and an account of how it was reached."""

    text: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    #: In-turn messages — the assistant's tool-call messages and the tool
    #: results that answered them. Not persisted as message rows: the schema's
    #: role is ``user`` or ``assistant``, and the record of what the tools did
    #: is the trace, stored on the assistant row's ``tool_trace``.
    messages: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    stop_reason: str = ANSWERED
    tool_seconds_used: float = 0.0
    tool_budget_seconds: float = 0.0
    overflowed: bool = False
    prompt: prompt.AssembledPrompt | None = None

    @property
    def called_tools(self) -> bool:
        return bool(self.trace)

    @property
    def tool_budget_exhausted(self) -> bool:
        return self.tool_seconds_used >= self.tool_budget_seconds


def _call_name(call: Mapping[str, Any]) -> str:
    """The tool name out of Ollama's tool-call shape.

    Returns ``""`` rather than raising when the shape is wrong: an empty name
    dispatches to ``UNKNOWN_TOOL``, which is fed back to the model, and that is
    a better outcome than a crashed turn over a malformed field.
    """
    function = call.get("function") or {}
    return str(function.get("name") or call.get("name") or "")


def _call_arguments(call: Mapping[str, Any]) -> Any:
    """The arguments out of Ollama's tool-call shape.

    Ollama returns these as an object, but has returned a JSON *string* in
    other versions and other models emit one. A string is parsed; anything that
    does not parse is passed through unchanged so dispatch reports
    ``INVALID_ARGUMENTS`` — which tells the model its call was malformed —
    rather than this function inventing empty arguments and letting a call run
    with defaults nobody asked for.
    """
    function = call.get("function") or {}
    arguments = function.get("arguments", call.get("arguments"))
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except ValueError:
            return arguments
    return arguments


def render_tool_result(result: ToolResult, max_chars: int | None = None) -> str:
    """The text of a tool result as the model sees it.

    Truncation is **stated in the text**, never silent: a model that cannot tell
    a complete result from a cut one will answer confidently from a fragment.
    The untruncated value stays in the trace, so what the tool actually returned
    is still available to task 3.1 even when the model only saw part of it.
    """
    limit = max_chars if max_chars is not None else config.agent_max_tool_result_chars()

    if result.ok:
        value = result.value
        if isinstance(value, str):
            body = value
        else:
            try:
                body = json.dumps(value, default=str)
            except (TypeError, ValueError):
                body = repr(value)
    else:
        body = f"[{result.outcome.value}] {result.error or 'no detail given'}"

    if len(body) > limit:
        omitted = len(body) - limit
        body = f"{body[:limit]}\n[truncated: {omitted} more characters]"
    return body


def _dispatch_call(
    registry: ToolRegistry,
    call: Mapping[str, Any],
    seconds_left: float,
    attribution: AttributionContext | None = None,
) -> ToolResult:
    """One tool call, bounded by whatever is left of the turn's budget."""
    name = _call_name(call)
    arguments = _call_arguments(call)

    if seconds_left <= 0:
        return tools.skipped_result(
            name,
            arguments if isinstance(arguments, Mapping) else None,
            "the turn's tool budget was spent before this call could start",
        )

    tool = registry.get(name) if registry.has(name) else None
    limit = min(tool.resolved_timeout(), seconds_left) if tool else seconds_left
    return registry.dispatch(
        name, arguments, timeout_seconds=limit, attribution=attribution
    )


def run_turn(
    messages: Sequence[Mapping[str, Any]],
    situation: str = "",
    retrieval: RetrievalResult | None = None,
    *,
    registry: ToolRegistry | None = None,
    max_iterations: int | None = None,
    tool_budget_seconds: float | None = None,
    model: str | None = None,
    options: dict[str, Any] | None = None,
    soul_text: str | None = None,
    attribution: AttributionContext | None = None,
) -> TurnResult:
    """Run one turn to a terminal answer.

    ``attribution`` says whose record a *writing* tool's output belongs to
    (Phase 4 P0). It is passed straight through to dispatch and reaches only the
    tools that declare they take it — no tool built before Phase 4 sees it, and
    nothing in this module reads it. It is **not** an authorization object: see
    ``program/attribution.py``.

    ``messages`` is the conversation so far, **including the user message being
    answered** — which the caller has already persisted (task 2.2's obligation
    (b)), so an interrupted turn is distinguishable from a completed one.

    Raises whatever ``ollama`` raises. A turn with no model behind it has no
    honest degraded form, and the caller needs to know which failure it was.
    """
    registry = registry if registry is not None else tools.default_registry()
    limit = max_iterations if max_iterations is not None else config.agent_max_iterations()
    budget = (
        tool_budget_seconds
        if tool_budget_seconds is not None
        else config.agent_tool_budget_seconds()
    )

    extras: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    spent = 0.0
    assembled: prompt.AssembledPrompt | None = None

    for iteration in range(1, limit + 1):
        final_call = iteration == limit
        assembled = prompt.assemble_turn(
            [*messages, *extras], situation, retrieval, soul_text=soul_text
        )
        # No tools on the last call: see the module docstring. Also no tools
        # when none are registered — an empty `tools: []` is not the same
        # request as one without the field, and Phase 2 registers its tools one
        # task at a time.
        payload = registry.ollama_schema() if (not final_call and len(registry)) else None

        response = ollama.chat(
            assembled.to_messages(), model=model, options=options, tools=payload
        )
        message = response.get("message") or {}
        content = message.get("content") or ""
        calls = message.get("tool_calls") or []

        if not calls:
            return TurnResult(
                text=content,
                trace=trace,
                messages=extras,
                iterations=iteration,
                stop_reason=ITERATION_LIMIT if final_call and iteration > 1 else ANSWERED,
                tool_seconds_used=spent,
                tool_budget_seconds=budget,
                overflowed=assembled.overflowed,
                prompt=assembled,
            )

        if final_call:
            # Tool calls with no tools offered. Recorded as skipped rather than
            # dropped: the model asked for something, nothing ran, and a trace
            # that omitted the request would make that unanswerable later.
            logger.warning(
                "model emitted %d tool call(s) on the final tool-free "
                "iteration; none were dispatched.",
                len(calls),
            )
            for call in calls:
                skipped = tools.skipped_result(
                    _call_name(call),
                    None,
                    "no tools were offered on the final iteration of the turn",
                )
                trace.append({"iteration": iteration, **skipped.to_trace_entry()})
            return TurnResult(
                text=content,
                trace=trace,
                messages=extras,
                iterations=iteration,
                stop_reason=ITERATION_LIMIT,
                tool_seconds_used=spent,
                tool_budget_seconds=budget,
                overflowed=assembled.overflowed,
                prompt=assembled,
            )

        extras.append({"role": "assistant", "content": content, "tool_calls": calls})

        for call in calls:
            result = _dispatch_call(
                registry, call, budget - spent, attribution=attribution
            )
            spent += result.duration_seconds
            trace.append({"iteration": iteration, **result.to_trace_entry()})
            extras.append(
                {
                    "role": "tool",
                    "tool_name": result.tool_name,
                    "content": render_tool_result(result),
                }
            )

    # Unreachable: the final iteration returns from inside the loop either way.
    raise AssertionError("agent loop ended without a terminal response")
