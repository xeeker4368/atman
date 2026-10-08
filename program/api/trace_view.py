"""What an API response shows of a turn's trace: the stored trace, minus the entity's own writing.

The entity may decline to show something it wrote for itself (``docs/DECISIONS.md`` #10, and
``soul.md``'s declining paragraph). A trace entry carries a tool's arguments and its result
(``value``), so without this a person reading the API response, or the trace panel built on it,
would see the piece whatever the entity chose. The stored ``tool_trace`` is not changed: this is
applied only on the way out, by ``POST /api/chat`` and ``GET /api/conversations/{id}/messages``,
and receipts are built from the stored trace before it, so they are unaffected.

What is hidden, each replaced by ``[N characters, not shown here]``:

* ``creative_write``: the ``text`` and ``title`` arguments, and the result, which repeats the title
  and a file name made from it.
* ``memory_search``: the whole result, when it holds a retrieved record of the entity's own
  creative writing or reflection journal. Recognised by the record header's source label, taken
  from ``prompt._SOURCE_LABELS`` itself so the two cannot drift. A message that merely contains
  such a header line is hidden too: that errs toward hiding, never toward showing.

Not hidden, on purpose: ``image_generate``'s prompt (the picture is made for the person asking),
``note_propose`` and ``note_search`` (what people said about themselves, decision #27), and the
web and Moltbook tools (outside content).
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from program.engine import prompt

#: The entity's own writing, by the source label a retrieved record's header carries.
PRIVATE_SOURCE_TYPES = ("creative_writing", "reflection_journal")

_PRIVATE_RECORD = re.compile(
    r"^\[[^\]\n]* · (?:"
    + "|".join(re.escape(prompt._SOURCE_LABELS[t]) for t in PRIVATE_SOURCE_TYPES)
    + r"), ",
    re.MULTILINE,
)


def hidden(text: str) -> str:
    return f"[{len(text)} characters, not shown here]"


def _view(entry: dict[str, Any]) -> dict[str, Any]:
    tool = entry.get("tool")
    if tool == "creative_write":
        shown = dict(entry)
        if isinstance(shown.get("arguments"), dict):
            shown["arguments"] = {
                key: hidden(value) if key in ("text", "title") and isinstance(value, str)
                else value
                for key, value in shown["arguments"].items()
            }
        if isinstance(shown.get("value"), str):
            shown["value"] = hidden(shown["value"])
        return shown
    if tool == "memory_search":
        value = entry.get("value")
        if isinstance(value, str) and _PRIVATE_RECORD.search(value):
            return {**entry, "value": hidden(value)}
    return entry


def for_response(trace: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The trace as an API response shows it. A new list; the input is not modified."""
    return [_view(entry) if isinstance(entry, dict) else entry for entry in trace]
