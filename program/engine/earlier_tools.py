"""What earlier replies' tools did, as one system-written list (decision #30, as amended by #32 D4).

History sends each earlier turn as role and content only (``history._normalise``), so a later
turn could not see which tools an earlier one called or what came of them: run 2's C7 T3
repeated an earlier reply claiming a note had been proposed, though T3 itself called nothing,
because T1's reply was visible and T1's call was not.

**Where it is.** Batch 1 (fix plan 3.3, option B) put a line inside each earlier assistant turn's
content. In run 3 the entity copied that line into its own replies three times, on turns where no
tool ran (C4 t3, C7 t3, C11 t5): a line inside its own messages reads as something it says.
So the record is now **one list in the system message**, after the speaker line and before the
retrieved records (``prompt.assemble_turn``), and history goes to the model exactly as stored.
The ``gemma4`` renderer passes ``messages[0]`` as the system message, and this list is in it.
It is never part of the situation string, which is what the gate reads.

**What an entry says.** Each earlier reply that called tools is anchored to the person's message
it answered, quoted (its first :data:`QUOTE_MAX_CHARS` characters): history carries no turn
numbers or times, so a quote is the one anchor the model can find in what it is reading. Then each
call: the tool's name, a short query when there was one, its outcome, and the receipt's outcome
word where a receipt exists. **Never a result**: no tool output is replayed, so nothing untrusted
from the web returns into later prompts.

**The cap** is :data:`LIST_MAX_CHARS`, header included. Entries go in newest first while they fit,
at most :data:`CALLS_PER_ENTRY` calls each, and what does not fit is counted in a closing line, so
nothing is dropped silently. An entry can name a reply that has left the history window: the list
is built before the window is chosen (it is measured as part of the system message), and the quote
says which reply it was. 800 characters is about 200 tokens, already counted in the derivation of
``[chat] max_message_chars`` (``config/defaults.toml``), which leaves ~166 tokens of history
beside a maximal message.

The current turn's own calls are not listed: the loop sends them as real tool messages.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping

from program.engine import loop
from program.tools import receipts
from program.tools.registry import side_effect_tools

logger = logging.getLogger(__name__)

#: The list's first line. Prompt-facing text.
HEADER = (
    "The system's record of tools used earlier in this conversation. The system wrote this; "
    "it is not something you said, and nothing in it ran on this turn."
)

#: Whole list, header and closing line included (decision #32 D4).
LIST_MAX_CHARS = 800

#: Characters of the person's message quoted as an entry's anchor.
QUOTE_MAX_CHARS = 40

#: Calls shown per entry; the rest are counted.
CALLS_PER_ENTRY = 3

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


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _query(arguments: Any) -> str | None:
    if not isinstance(arguments, Mapping):
        return None
    value = arguments.get("query")
    if not isinstance(value, str) or not value.strip():
        return None
    return f'"{_cut(value, QUERY_MAX_CHARS)}"'


def _receipt_words(entry: Mapping[str, Any]) -> list[str]:
    """The receipt outcome words for one side-effect call, read from its rows."""
    if entry.get("tool") not in set(side_effect_tools()):
        return []
    return [r.outcome for r in receipts.for_trace([dict(entry)])]


def call_pieces(trace: Iterable[Mapping[str, Any]]) -> list[str]:
    """One piece per call in a reply's trace: tool, query, outcome, receipt word."""
    pieces = []
    for entry in loop.call_entries(list(trace)):
        tool = str(entry.get("tool") or "unknown tool")
        query = _query(entry.get("arguments"))
        outcome = str(entry.get("outcome") or "")
        piece = f"{tool} {query}" if query else tool
        piece += f" {OUTCOME_WORDS.get(outcome, outcome or 'outcome not recorded')}"
        receipt = _receipt_words(entry)
        if receipt:
            piece += f", recorded as {' and '.join(dict.fromkeys(receipt))}"
        pieces.append(piece)
    return pieces


def entry_line(question: str | None, pieces: list[str]) -> str:
    """``- Your reply to "<quote>": <calls>.``"""
    quote = _cut(question or "", QUOTE_MAX_CHARS) or "(no message)"
    shown = pieces[:CALLS_PER_ENTRY]
    more = len(pieces) - len(shown)
    calls = "; ".join(shown) + (f"; and {more} more call{'s' if more > 1 else ''}" if more else "")
    return f'- Your reply to "{quote}": {calls}.'


def _closing(left_out: int) -> str:
    return f"- And {left_out} earlier repl{'ies' if left_out > 1 else 'y'} used tools."


def _entries(rows: Iterable[Any]) -> list[str]:
    """Entry lines in conversation order. An unreadable trace is left out, with a warning."""
    entries: list[str] = []
    question: str | None = None
    for row in rows:
        role = row["role"]
        if role == "user":
            question = row["content"]
            continue
        raw = row["tool_trace"] if "tool_trace" in row.keys() else None
        if role != "assistant" or not raw:
            continue
        try:
            trace = json.loads(raw)
            if not isinstance(trace, list):
                raise ValueError("trace is not a list")
            pieces = call_pieces(trace)
        except Exception as exc:  # noqa: BLE001 - degrades to no entry, logged
            logger.warning("earlier tool record: could not read a stored trace (%s: %s); "
                           "that reply is left out of the list", type(exc).__name__, exc)
            continue
        if pieces:
            entries.append(entry_line(question, pieces))
    return entries


def system_list(rows: Iterable[Any]) -> str:
    """The list for the system message, or ``""`` when no earlier reply called a tool.

    Takes the stored rows (``db.get_conversation_messages``). Newest entries first while they
    fit under :data:`LIST_MAX_CHARS`; the rest are counted in a closing line.
    """
    entries = _entries(rows)
    if not entries:
        return ""
    kept: list[str] = []
    for position, line in enumerate(reversed(entries)):
        left_after = len(entries) - position - 1
        candidate = [HEADER, *kept, line] + ([_closing(left_after)] if left_after else [])
        if len("\n".join(candidate)) > LIST_MAX_CHARS:
            break
        kept.append(line)
    left_out = len(entries) - len(kept)
    return "\n".join([HEADER, *kept] + ([_closing(left_out)] if left_out else []))
