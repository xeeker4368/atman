"""Tool registry and dispatch. Task 2.1.

The registry and the dispatch call. **Not** the agent loop that drives them —
that is task 2.2, which calls into here.

Data, not scattered conditionals
--------------------------------
A tool is a frozen :class:`Tool` record in a module-level tuple, looked up
through one function that raises on an unknown name. This is the same shape
``program/settings/permissions.py`` uses for capabilities and
``program/settings/store.py`` uses for settings keys, and it is the shape
``config.py``'s ``_ENV_MAP`` gives its reason: *"named explicitly rather than
derived, so the full set of overrides is greppable from one place."*

**The list of tools lives in** ``program/tools/catalog.py``, not here. A tool
module imports :class:`Tool` from this module to declare itself, so this module
importing tool modules would be a cycle — and a bottom-of-file import only hides
that until something imports a tool module first. ``catalog.py`` holds the
contents, this module holds the mechanism, and ``default_registry()`` reaches for
the catalogue at call time.

Central registration, not self-registration
-------------------------------------------
Tools are listed explicitly in ``catalog.TOOLS`` rather than registering
themselves via an import-time decorator.

The tradeoff is real. Self-registration keeps a tool's declaration next to its
implementation and means adding one touches a single file. But it makes the
live tool set depend on **which modules happened to be imported** — a tool
silently missing because nothing imported its module is a failure with no error
message, and the full set stops being greppable from one place. For a registry
whose contents determine what the entity can *do*, and which the fabrication
gate later reasons over, "what is registered" must be answerable by reading one
tuple rather than by tracing imports.

The cost is that adding a tool touches two files. That is the intended cost.

The dispatch contract
---------------------
``dispatch()`` **always returns a** :class:`ToolResult` **and never raises** for
the three failure modes task 2.2 has to handle, because all three are things the
agent loop must report back to the model rather than crash on:

===================  ==================================================
``ToolOutcome``      Meaning
===================  ==================================================
``OK``               the tool ran and returned
``UNKNOWN_TOOL``     the model named a tool that does not exist
``INVALID_ARGUMENTS`` the call was malformed before the tool ever ran
``TOOL_ERROR``       the tool ran and raised
===================  ==================================================

The distinction task 2.2 needs is between ``INVALID_ARGUMENTS`` — the *call* was
wrong, the model may usefully retry with different arguments — and
``TOOL_ERROR`` — the call was well-formed and the *execution* failed, where
retrying the same call will likely fail the same way. Collapsing those into one
"it didn't work" would leave the loop unable to tell a hallucinated argument
from an unreachable network.

Programmer errors still raise: registering a duplicate name, or an invalid tool
definition, is a bug in this repo rather than a model mistake, and it fails
loudly at construction.

``except Exception``, deliberately not ``BaseException``
--------------------------------------------------------
A raising tool is caught and converted to ``TOOL_ERROR``. ``KeyboardInterrupt``,
``SystemExit`` and the test suite's ``StoreIsolationViolation`` — which derives
from ``BaseException`` precisely so that ``except Exception`` cannot swallow it —
propagate untouched.

Per-tool timeouts, enforced
---------------------------
Task 2.1 left this field out deliberately, on the grounds that *"an unenforced
timeout field would read as protection that exists."* Task 2.2 adds it **with**
enforcement: every :class:`Tool` carries a ``timeout_seconds``, and the handler
runs in a daemon thread that :meth:`dispatch` waits on for exactly that long.

**What the timeout bounds is how long the turn waits, not how long the tool
runs.** Python has no safe way to kill a running thread, so a handler that
overruns keeps going in the background until it returns on its own; the daemon
flag keeps it from holding up interpreter exit. Subprocess isolation would make
the kill real, and is not worth its cost for tools that need in-process access
to the database and the vector store.

That is why a timeout is its own outcome rather than a flavour of
``TOOL_ERROR``. ``TIMEOUT`` means *the handler was entered and its outcome is
unknown* — a ``web_fetch`` that timed out may well have fetched, and a posting
tool may well have posted. ``ToolResult.ran`` is therefore **True** for a
timeout: something happened that a later claim could legitimately refer to, and
task 3.1 must be able to see that it cannot be checked.

``SKIPPED`` is the opposite state and is produced by the agent loop rather than
here: the turn's aggregate tool budget was spent, so the call was never started.
It exists so that every tool message fed back to the model has a matching trace
entry — a tool result with no trace entry is exactly the asymmetry task 3.1
cannot reason over.

Feeding the fabrication gate
----------------------------
BUILD_PLAN requires the turn's tool-call trace to be *"a first-class return
value the fabrication gate reasons over structurally — not debug/log output"*.
:meth:`ToolResult.to_trace_entry` is that value's per-call unit. Every dispatch
gets a ``call_id``, which is what makes task 3.1's structural check — *"invalid
IDs, no matching tool_result in trace"* — a lookup rather than a guess.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

from program import config
from program.attribution import AttributionContext

logger = logging.getLogger(__name__)

#: Tool names as the model will emit them. Matches the function-name shape
#: Ollama and the OpenAI-style schema accept.
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: The keyword an attribution-taking handler receives. Reserved: a tool may not
#: declare it in ``parameters``, so it can never be model-settable.
ATTRIBUTION_ARGUMENT = "attribution"

#: JSON Schema primitives this validator understands. Deliberately small — see
#: ``_validate_arguments``.
_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


class ToolError(RuntimeError):
    """A problem with the registry itself. Not a tool failing at runtime."""


class UnknownToolError(KeyError):
    """A tool name that is not registered was looked up directly."""


class DuplicateToolError(ToolError):
    """Two tools were registered under one name."""


@dataclass(frozen=True)
class Tool:
    """One callable the entity can invoke.

    ``parameters`` is a JSON Schema *object* schema — the same structure Ollama
    expects inside a function definition — carried as data rather than derived
    from the handler's signature. Deriving it from Python annotations would make
    the schema the model sees an accident of the implementation; declaring it
    means the contract is the thing that was written down.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    #: Whether this tool's handler is passed an ``AttributionContext`` alongside
    #: the model's arguments (Phase 4 P0). Declared rather than inferred from the
    #: handler's signature, for the same reason ``parameters`` is declared: the
    #: contract should be the thing that was written down.
    #:
    #: **It is never part of ``parameters``**, and ``__post_init__`` refuses a
    #: tool that puts it there. If the model could set it, the model could
    #: attribute a write to the other household member — so the value arrives
    #: from the caller and the model is not asked.
    takes_attribution: bool = False
    #: Whether this capability exists right now (decision #12's first axis). A
    #: **call-time predicate**, not a boolean, so the answer comes from config when
    #: the registry is built rather than from whatever it was at import. ``None``
    #: means unconditional, which is every tool built before Phase 4.
    #:
    #: A disabled tool is omitted from ``default_registry()`` entirely — the model is
    #: never shown a schema for something it cannot use. Offering it and refusing the
    #: call would burn a turn on the discovery.
    enabled: Callable[[], bool] | None = None
    #: How long a turn will wait for this handler. ``None`` means *use
    #: ``tools.default_timeout_seconds``* — it does **not** mean "no limit".
    #: There is deliberately no way to declare an unbounded tool: an unbounded
    #: handler makes the turn unbounded, and the idle-close floor is derived
    #: from a bounded turn.
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.takes_attribution and ATTRIBUTION_ARGUMENT in (
            self.parameters.get("properties") or {}
        ):
            raise ToolError(
                f"tool {self.name!r} declares {ATTRIBUTION_ARGUMENT!r} in its "
                f"parameters. Attribution comes from the caller, never from the "
                f"model — a model that could set it could attribute a write to "
                f"the other household member."
            )
        if not _NAME.match(self.name):
            raise ToolError(
                f"tool name {self.name!r} must be lowercase alphanumeric with "
                f"underscores, starting with a letter — it is emitted by the "
                f"model as a function name."
            )
        if not self.description.strip():
            raise ToolError(
                f"tool {self.name!r} has no description. The description is how "
                f"the model decides whether to call it; an empty one makes the "
                f"tool undiscoverable rather than merely undocumented."
            )
        if self.parameters.get("type") != "object":
            raise ToolError(
                f"tool {self.name!r} parameters must be a JSON Schema object "
                f"schema (\"type\": \"object\"), got {self.parameters.get('type')!r}."
            )
        if not callable(self.handler):
            raise ToolError(f"tool {self.name!r} handler is not callable.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ToolError(
                f"tool {self.name!r} has timeout_seconds="
                f"{self.timeout_seconds!r}. It must be positive, or None to "
                f"take tools.default_timeout_seconds."
            )

    @property
    def properties(self) -> dict[str, Any]:
        return self.parameters.get("properties", {}) or {}

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(self.parameters.get("required", ()) or ())

    def resolved_timeout(self) -> float:
        """This tool's timeout, falling back to the configured default."""
        if self.timeout_seconds is not None:
            return float(self.timeout_seconds)
        return config.tool_default_timeout_seconds()

    def to_ollama_schema(self) -> dict[str, Any]:
        """This tool in the shape ``ollama.chat(tools=...)`` accepts."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolOutcome(str, Enum):
    """What happened. See the module docstring for why these are distinct."""

    OK = "ok"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    TOOL_ERROR = "tool_error"
    #: Entered, then abandoned. Whether it finished is unknown; side effects
    #: are possible. See the module docstring.
    TIMEOUT = "timeout"
    #: Never started — the turn's aggregate tool budget was spent. Produced by
    #: the agent loop, not by dispatch.
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ToolResult:
    """The outcome of one dispatch, and the trace's per-call unit."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    outcome: ToolOutcome
    value: Any = None
    error: str | None = None
    duration_seconds: float = 0.0
    #: The bound that was in force for this call, in seconds. Recorded so the
    #: trace answers "how long was it given" as well as "how long did it take".
    timeout_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is ToolOutcome.OK

    @property
    def ran(self) -> bool:
        """Whether the handler was actually entered.

        False for ``UNKNOWN_TOOL``, ``INVALID_ARGUMENTS`` and ``SKIPPED`` —
        nothing executed, so nothing happened that a later claim could
        legitimately refer to.

        **True for ``TIMEOUT``.** The handler was entered and may have completed
        after the wait was abandoned, so its side effects are unknown rather
        than absent. Task 3.1 needs that distinction: "this did not happen" and
        "whether this happened cannot be determined" are different claims.
        """
        return self.outcome in (
            ToolOutcome.OK,
            ToolOutcome.TOOL_ERROR,
            ToolOutcome.TIMEOUT,
        )

    def to_trace_entry(self) -> dict[str, Any]:
        """Structured record for the turn's tool-call trace.

        Consumed by task 3.1's fabrication gate, which checks a claimed result
        against what actually happened. It is a return value, not a log line.
        """
        return {
            "call_id": self.call_id,
            "tool": self.tool_name,
            "arguments": self.arguments,
            "outcome": self.outcome.value,
            "ran": self.ran,
            "value": self.value,
            "error": self.error,
            "duration_seconds": round(self.duration_seconds, 6),
            "timeout_seconds": self.timeout_seconds,
        }


def _validate_arguments(tool: Tool, arguments: Mapping[str, Any]) -> str | None:
    """Return an error message, or None when the arguments are acceptable.

    **Deliberately a small subset of JSON Schema**: required-key presence,
    unexpected keys, and top-level primitive types. It is not a JSON Schema
    implementation and does not pretend to be — no ``$ref``, no nested object
    validation, no ``enum``, no numeric bounds.

    That is enough to make the ``INVALID_ARGUMENTS`` / ``TOOL_ERROR``
    distinction real, which is the contract task 2.2 depends on. Going further
    means either a large hand-rolled validator or an external dependency, and
    neither is justified before a single real tool exists to show which
    constructs are actually used. A tool needing stricter checks validates
    inside its own handler and raises, which surfaces as ``TOOL_ERROR``.
    """
    missing = [key for key in tool.required if key not in arguments]
    if missing:
        return (
            f"missing required argument(s): {', '.join(sorted(missing))}. "
            f"Expected: {', '.join(sorted(tool.properties)) or '(none)'}"
        )

    unexpected = [key for key in arguments if key not in tool.properties]
    if unexpected:
        return (
            f"unexpected argument(s): {', '.join(sorted(unexpected))}. "
            f"Accepted: {', '.join(sorted(tool.properties)) or '(none)'}"
        )

    for key, value in arguments.items():
        declared = tool.properties.get(key, {}).get("type")
        if declared is None:
            continue
        expected = _JSON_TYPES.get(declared)
        if expected is None:
            continue
        # bool is a subclass of int in Python; the schema types are distinct.
        if declared in ("integer", "number") and isinstance(value, bool):
            return f"argument {key!r} must be {declared}, got boolean"
        if not isinstance(value, expected):
            return (
                f"argument {key!r} must be {declared}, got "
                f"{type(value).__name__}"
            )
    return None


def _run_in_thread(
    tool: Tool, kwargs: dict[str, Any], timeout: float
) -> tuple[Any, BaseException | None, bool]:
    """Run ``tool.handler`` and wait at most ``timeout`` seconds for it.

    Returns ``(value, exception, timed_out)``. The thread is a daemon, so a
    handler that never returns cannot keep the interpreter alive at exit.

    ``BaseException`` is captured rather than only ``Exception`` because the
    calling thread must re-raise it to preserve dispatch's contract: a
    ``KeyboardInterrupt``, ``SystemExit`` or the suite's
    ``StoreIsolationViolation`` raised inside the worker would otherwise vanish
    with the thread and read here as a timeout.
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = tool.handler(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised or converted below
            box["exception"] = exc

    thread = threading.Thread(target=target, name=f"tool-{tool.name}", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, None, True
    return box.get("value"), box.get("exception"), False


def skipped_result(
    name: str, arguments: Mapping[str, Any] | None, reason: str
) -> ToolResult:
    """A call the caller refused to start, as a first-class trace entry.

    The agent loop uses this when the turn's aggregate tool budget is spent.
    It is a real :class:`ToolResult` rather than a bare message so that every
    tool result fed back to the model has a matching entry in the trace.
    """
    return ToolResult(
        call_id=uuid.uuid4().hex,
        tool_name=name,
        arguments=dict(arguments or {}),
        outcome=ToolOutcome.SKIPPED,
        error=reason,
    )


class ToolRegistry:
    """A set of tools, looked up by name.

    Instantiable rather than a module-level dict so a test can build its own
    registry without mutating global state — the same reason
    ``program/memory/vectors.py`` resolves its store per path instead of binding
    one at import.
    """

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> Tool:
        """Add a tool. Raises on a duplicate name — that is a repo bug."""
        if tool.name in self._tools:
            raise DuplicateToolError(
                f"a tool named {tool.name!r} is already registered. Names are "
                f"how the model addresses a tool, so two would make dispatch "
                f"ambiguous and silently pick one."
            )
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        """Look up a tool. Raises for an unknown name.

        Note ``dispatch()`` does **not** go through this — a model naming a
        tool that does not exist is an expected runtime event, not an error,
        and comes back as ``UNKNOWN_TOOL``.
        """
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(
                f"no tool named {name!r}. Registered: "
                f"{', '.join(sorted(self._tools)) or '(none)'}"
            ) from None

    def has(self, name: str) -> bool:
        return name in self._tools

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def ollama_schema(self) -> list[dict[str, Any]]:
        """Every tool in the shape ``ollama.chat(tools=...)`` accepts."""
        return [tool.to_ollama_schema() for tool in self]

    def dispatch(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
        attribution: AttributionContext | None = None,
    ) -> ToolResult:
        """Invoke a tool by name. Always returns; never raises for the
        model-facing failure modes. See the module docstring for the contract.

        ``attribution`` is passed on **only** to a tool that declares
        ``takes_attribution``, and only ever as a handler keyword — it never joins
        the model's arguments, never appears in the trace, and never reaches a
        tool that did not ask for it. So the three Phase 2 tools are dispatched
        exactly as they were before it existed.

        ``timeout_seconds`` overrides the tool's own declared timeout for this
        one call. The agent loop passes the turn's *remaining* tool budget, so
        a tool declaring 30 seconds gets 8 when 8 are left — the aggregate
        bound the idle-close floor is derived from holds regardless of how many
        calls the model makes.
        """
        call_id = uuid.uuid4().hex

        # Shape-check before coercing. `dict(some_list)` raises, and a raise
        # here would break the never-raises contract for exactly the case the
        # contract exists to cover — a model emitting the wrong JSON shape.
        if arguments is not None and not isinstance(arguments, Mapping):
            return ToolResult(
                call_id=call_id,
                tool_name=name,
                arguments={},
                outcome=ToolOutcome.INVALID_ARGUMENTS,
                error=(
                    f"arguments must be an object, got "
                    f"{type(arguments).__name__}"
                ),
            )

        supplied: dict[str, Any] = dict(arguments) if arguments else {}

        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                call_id=call_id,
                tool_name=name,
                arguments=supplied,
                outcome=ToolOutcome.UNKNOWN_TOOL,
                error=(
                    f"no tool named {name!r}. Available: "
                    f"{', '.join(self.names) or '(none)'}"
                ),
            )

        problem = _validate_arguments(tool, supplied)
        if problem is not None:
            return ToolResult(
                call_id=call_id,
                tool_name=name,
                arguments=supplied,
                outcome=ToolOutcome.INVALID_ARGUMENTS,
                error=problem,
            )

        # Attribution is kept OUT of `supplied` on purpose. `supplied` is what
        # `_validate_arguments` checks (it rejects unexpected keys), what reaches
        # `ToolResult.arguments`, and therefore what reaches the tool trace the
        # fabrication gate reasons over. Mixing it in would change the recorded
        # shape of every call for a value the model never sent.
        call_kwargs = dict(supplied)
        if tool.takes_attribution:
            if attribution is None:
                raise ToolError(
                    f"tool {name!r} takes attribution and none was supplied. "
                    f"This is a wiring bug, not a model error: the caller knows "
                    f"whose record the write belongs to and the model does not, "
                    f"so there is no honest value to substitute here."
                )
            call_kwargs[ATTRIBUTION_ARGUMENT] = attribution

        limit = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else tool.resolved_timeout()
        )
        if limit <= 0:
            return skipped_result(
                name,
                supplied,
                f"no time remained in the turn's tool budget (limit {limit}s)",
            )

        started = time.monotonic()
        value, exc, timed_out = _run_in_thread(tool, call_kwargs, limit)
        elapsed = time.monotonic() - started

        if timed_out:
            logger.warning(
                "tool %s exceeded its %.1fs timeout; the turn stopped waiting. "
                "The handler is still running and may still complete.",
                name,
                limit,
            )
            return ToolResult(
                call_id=call_id,
                tool_name=name,
                arguments=supplied,
                outcome=ToolOutcome.TIMEOUT,
                error=(
                    f"timed out after {limit:g}s; the call was abandoned and "
                    f"whether it completed is unknown"
                ),
                duration_seconds=elapsed,
                timeout_seconds=limit,
            )

        if exc is not None:
            if not isinstance(exc, Exception):
                # KeyboardInterrupt, SystemExit, StoreIsolationViolation.
                raise exc
            logger.warning("tool %s raised: %s: %s", name, type(exc).__name__, exc)
            return ToolResult(
                call_id=call_id,
                tool_name=name,
                arguments=supplied,
                outcome=ToolOutcome.TOOL_ERROR,
                error=f"{type(exc).__name__}: {exc}",
                duration_seconds=elapsed,
                timeout_seconds=limit,
            )

        return ToolResult(
            call_id=call_id,
            tool_name=name,
            arguments=supplied,
            outcome=ToolOutcome.OK,
            value=value,
            duration_seconds=elapsed,
            timeout_seconds=limit,
        )


_default: ToolRegistry | None = None


def default_registry() -> ToolRegistry:
    """The registry built from ``catalog.TOOLS``, constructed once.

    The import is deferred into the function body rather than placed at module
    scope, because every tool module imports :class:`Tool` from here: importing
    the catalogue at the top would be a cycle, and importing it at the bottom
    would be one whenever a tool module is imported first. By the time this runs,
    this module is fully initialised and the direction is unambiguous.
    """
    global _default
    if _default is None:
        from program.tools.catalog import TOOLS

        # A disabled capability is not registered at all, so its schema never
        # reaches the model. The predicate is evaluated here, once, which is why
        # flipping a flag needs `reset_default_registry()` — recorded in
        # config/defaults.toml beside the flag.
        _default = ToolRegistry(
            [tool for tool in TOOLS if tool.enabled is None or tool.enabled()]
        )
    return _default


def reset_default_registry() -> None:
    """Drop the cached default registry. For tests."""
    global _default
    _default = None


def dispatch(
    name: str,
    arguments: Mapping[str, Any] | None = None,
    timeout_seconds: float | None = None,
    attribution: AttributionContext | None = None,
) -> ToolResult:
    """Dispatch against the default registry."""
    return default_registry().dispatch(
        name, arguments, timeout_seconds, attribution=attribution
    )
