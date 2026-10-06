"""What earlier turns' tools did, as one system-written line in history (fix plan 3.3, option B).

History sends each earlier turn as role and content only (``history._normalise``), so a later
turn could not see which tools an earlier one called or what came of them: run 2's C7 T3
repeated an earlier reply claiming a note had been proposed, though T3 itself called nothing,
because T1's reply was visible and T1's call was not.

Each earlier assistant turn that called tools gets **one line**, built from its stored
``tool_trace``: each tool's name, a short query when there was one, its outcome, and the
receipt's outcome word where a receipt exists. **Never a result**: no tool output is replayed,
so nothing untrusted from the web returns into later prompts and the size stays small.

**Where the line goes, and why.** ``gemma4:26b`` is served through Ollama's built-in
``gemma4`` renderer (``ollama show --modelfile``: ``RENDERER gemma4``). Its source treats only
``messages[0]`` as the system message; a later ``system`` message is not an expected turn, and a
``tool`` message is rendered only after an assistant message with real ``tool_calls``. Assistant
content is the one place the renderer always passes through unchanged, so the line is prepended
to the earlier assistant turn's content, marked as written by the system. Being content, it is
priced by the history budget like the rest of that turn.

The current turn's own calls are not touched: the loop already sends them as real tool messages.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping

from program.engine import loop
from program.tools import receipts
from program.tools.registry import side_effect_tools

logger = logging.getLogger(__name__)

#: The line's opening words. Prompt-facing text.
PREFIX = "[system record of the tools this turn used, written by the system: "

#: Characters of a query shown; the rest is cut, never the tool name or the outcome.
QUERY_MAX_CHARS = 60

#: Plain words for each dispatch outcome (``ToolOutcome``).
OUTCOME_WORDS = {
    "ok": "succeeded",
    "tool_error": "failed",
    "timeout": "timed out, result unknown",
    "skipped": "was not run",
    "invalid_arguments": "was refused, invalid arguments",
    "unknown_tool": "was refused, no such tool",
}


def _query(arguments: Any) -> str | None:
    if not isinstance(arguments, Mapping):
        return None
    value = arguments.get("query")
    if not isinstance(value, str) or not value.strip():
        return None
    text = " ".join(value.split())
    if len(text) > QUERY_MAX_CHARS:
        text = text[:QUERY_MAX_CHARS - 1] + "…"
    return f'"{text}"'


def _receipt_words(entry: Mapping[str, Any]) -> list[str]:
    """The receipt outcome words for one side-effect call, read from its rows."""
    if entry.get("tool") not in set(side_effect_tools()):
        return []
    return [r.outcome for r in receipts.for_trace([dict(entry)])]


def record_line(trace: Iterable[Mapping[str, Any]]) -> str | None:
    """The one line for a turn's trace, or None when it called no tool."""
    parts = []
    for entry in loop.call_entries(list(trace)):
        tool = str(entry.get("tool") or "unknown tool")
        query = _query(entry.get("arguments"))
        outcome = str(entry.get("outcome") or "")
        words = OUTCOME_WORDS.get(outcome, outcome or "outcome not recorded")
        receipt = _receipt_words(entry)
        piece = f"{tool} {query}" if query else tool
        piece += f" {words}"
        if receipt:
            piece += f", recorded as {' and '.join(dict.fromkeys(receipt))}"
        parts.append(piece)
    if not parts:
        return None
    return PREFIX + "; ".join(parts) + "]"


def with_tool_records(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """History for the model: earlier assistant turns carry their tool record line.

    Takes the stored rows (``db.get_conversation_messages``). A row whose trace cannot be
    read is sent unchanged, with a warning: a missing line is a smaller harm than a failed
    turn, and it is logged rather than silent.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        role, content = row["role"], row["content"]
        message = {"role": role, "content": content}
        raw = row["tool_trace"] if role == "assistant" and "tool_trace" in row.keys() else None
        if raw:
            try:
                trace = json.loads(raw)
                if not isinstance(trace, list):
                    raise ValueError("trace is not a list")
                line = record_line(trace)
            except Exception as exc:  # noqa: BLE001 - degrades to no line, logged
                logger.warning("earlier tool record: could not read a stored trace (%s: %s); "
                               "that turn is sent without its line", type(exc).__name__, exc)
                line = None
            if line:
                message["content"] = f"{line}\n{content}"
        out.append(message)
    return out
