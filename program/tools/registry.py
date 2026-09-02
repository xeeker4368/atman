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

**There are no tools.** ``TOOLS`` is empty, deliberately. ``memory_search``,
``web_search``, ``web_fetch`` and file ingestion are each their own later task
in this phase, and each registers itself here when it is built. A placeholder
tool would be the same category of problem as an unmounted permission gate:
something that reads as built and is not.

Central registration, not self-registration
-------------------------------------------
Tools are listed explicitly in ``TOOLS`` rather than registering themselves via
an import-time decorator.

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
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

#: Tool names as the model will emit them. Matches the function-name shape
#: Ollama and the OpenAI-style schema accept.
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

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

    def __post_init__(self) -> None:
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

    @property
    def properties(self) -> dict[str, Any]:
        return self.parameters.get("properties", {}) or {}

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(self.parameters.get("required", ()) or ())

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

    @property
    def ok(self) -> bool:
        return self.outcome is ToolOutcome.OK

    @property
    def ran(self) -> bool:
        """Whether the handler was actually entered.

        False for ``UNKNOWN_TOOL`` and ``INVALID_ARGUMENTS`` — nothing executed,
        so nothing happened that a later claim could legitimately refer to.
        """
        return self.outcome in (ToolOutcome.OK, ToolOutcome.TOOL_ERROR)

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
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> ToolResult:
        """Invoke a tool by name. Always returns; never raises for the three
        model-facing failure modes. See the module docstring for the contract.
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

        started = time.monotonic()
        try:
            value = tool.handler(**supplied)
        except Exception as exc:  # noqa: BLE001 - converted to TOOL_ERROR
            elapsed = time.monotonic() - started
            logger.warning("tool %s raised: %s: %s", name, type(exc).__name__, exc)
            return ToolResult(
                call_id=call_id,
                tool_name=name,
                arguments=supplied,
                outcome=ToolOutcome.TOOL_ERROR,
                error=f"{type(exc).__name__}: {exc}",
                duration_seconds=elapsed,
            )

        return ToolResult(
            call_id=call_id,
            tool_name=name,
            arguments=supplied,
            outcome=ToolOutcome.OK,
            value=value,
            duration_seconds=time.monotonic() - started,
        )


#: **The central declaration. Empty on purpose — no tools are built yet.**
#:
#: ``memory_search``, ``web_search``, ``web_fetch`` and file ingestion are each
#: their own task in this phase; each appends its :class:`Tool` here when it is
#: built. Nothing is placed here to have something to register.
TOOLS: tuple[Tool, ...] = ()

_default: ToolRegistry | None = None


def default_registry() -> ToolRegistry:
    """The registry built from :data:`TOOLS`, constructed once."""
    global _default
    if _default is None:
        _default = ToolRegistry(TOOLS)
    return _default


def reset_default_registry() -> None:
    """Drop the cached default registry. For tests."""
    global _default
    _default = None


def dispatch(name: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
    """Dispatch against the default registry."""
    return default_registry().dispatch(name, arguments)
