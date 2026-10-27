"""The listing: the entity's notes, one line each, in every conversation (Notes v2, #36(d)).

Design of record: ``docs/NOTES_V2_DESIGN.md`` section 3. A system section after the speaker line
and before the earlier-tools list (``prompt.assemble_turn``), present on every turn whenever Notes
is on, and reading "None yet." when there is no active note. Never part of the situation string,
which is what the gate reads (section 2: so a reply citing a note is unchecked, like one citing a
retrieved record).

**What it lists:** active notes only, person notes first, then the rest, each newest-confirmed
first, as ``- {subject}: {line}``. A note made before the ``line`` column existed shows its text,
cut to the line limit.

**The cap** is ``notes.listing_max_chars`` (1,200), header and overflow line included, whole
entries only. What does not fit is counted in the last line, so nothing is dropped silently. Its
tokens are in the budget chain beside the speaker line (``tests/test_turn.py``).
"""

from __future__ import annotations

from typing import Any, Iterable

from program import config
from program.memory import notes as notes_db
from program.tools import note_texts as texts


def entry(note: Any) -> str:
    limit = config.notes_line_max_chars()
    line = note["line"] or note["text"].replace("\n", " ")
    if len(line) > limit:
        line = line[:limit - 1].rstrip() + "…"
    return f"- {note['subject']}: {line}"


def render(rows: Iterable[Any]) -> str:
    """The listing for these notes, already in listing order."""
    entries = [entry(r) for r in rows]
    if not entries:
        return f"{texts.LISTING_HEADER}\n{texts.LISTING_NONE}"
    cap = config.notes_listing_max_chars()
    kept: list[str] = []
    for position, line in enumerate(entries):
        left_after = len(entries) - position - 1
        candidate = [texts.LISTING_HEADER, *kept, line]
        if left_after:
            candidate.append(texts.listing_overflow(left_after))
        if len("\n".join(candidate)) > cap:
            break
        kept.append(line)
    left_out = len(entries) - len(kept)
    return "\n".join([texts.LISTING_HEADER, *kept]
                     + ([texts.listing_overflow(left_out)] if left_out else []))


def system_listing() -> str:
    """The listing for this turn, or ``""`` when Notes is off."""
    if not config.notes_enabled():
        return ""
    return render(notes_db.active_notes_for_listing())
