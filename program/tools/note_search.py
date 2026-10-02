"""``note_search``: recall reviewed notes on demand. Notes piece 3.

Design of record: ``docs/NOTES_DESIGN.md`` N0, N1, N5, N6, N11. **The only way notes reach the
entity**: there is no passive retrieval of notes, and nothing here touches ``memory_search`` or
conversation memory. Search is **lexical only** (FTS5/BM25), which is stated rather than hidden: a
paraphrase can miss (N18). Not filtered by who is asking (decision #20): a note about Jodie can
come up for Lyle, and what to say about it is the entity's discretion.

What it returns is bounded by construction: at most ``notes.max_results`` notes, each at most
``notes.max_text_chars`` (derived, N11), so a full page renders under
``agent.max_tool_result_chars`` and the loop never has to cut a note mid-sentence.

Each note is shown with its age (*"confirmed 12 days ago"*), so an old note reads as old (N5), and
by the first 8 characters of its id, which the entity passes back to ``note_propose`` to revise or
retire it. **A note no person reviewed is never shown as confirmed**: one applied while approval was
off reads *"added without review 12 days ago"* (or *"changed without review"*), found by a join to
the proposal row, not a column.
"""

from __future__ import annotations

from datetime import datetime, timezone

from program import config
from program.memory import notes
from program.tools import note_texts as texts
from program.tools.registry import Tool

#: Judgment value: a note's id is shown by this many leading characters.
ID_PREFIX_CHARS = 8
#: The longest age phrase the renderer can emit, for the worst-case size (N11).
_WORST_AGE = "9999 days ago"
_SEPARATOR = "\n\n"


def age_phrase(confirmed_at: str, now: datetime | None = None) -> str:
    """*"today"*, *"1 day ago"*, *"12 days ago"*, from ``last_confirmed_at``."""
    now = now or datetime.now(timezone.utc)
    try:
        days = (now - datetime.fromisoformat(confirmed_at)).days
    except ValueError:
        return "an unknown time ago"
    if days <= 0:
        return "today"
    return "1 day ago" if days == 1 else f"{days} days ago"


#: What precedes the age, by how the note came to be: a person's decision, or none.
_CONFIRMED = "confirmed"
_APPLIED_WORDS = {"add": "added without review", "revise": "changed without review"}
#: The longest lead-in, for the worst-case size (N11).
_WORST_LEAD = max([_CONFIRMED, *_APPLIED_WORDS.values()], key=len)


def framing(note_id: str, kind: str, subject: str, age: str, applied: str | None = None) -> str:
    """``applied`` is the proposal action of a note applied without review, else ``None``."""
    lead = _APPLIED_WORDS.get(applied, _CONFIRMED) if applied else _CONFIRMED
    return f"[note {note_id[:ID_PREFIX_CHARS]} · {kind} · {subject} · {lead} {age}]"


def render_notes(rows, now: datetime | None = None) -> str:
    entries = []
    for r in rows:
        age = age_phrase(r["last_confirmed_at"], now)
        applied = r["applied_action"] if "applied_action" in r.keys() else None
        entries.append(
            f"{framing(r['id'], r['subject_kind'], r['subject'], age, applied)}\n{r['text']}")
    return texts.HEADER + _SEPARATOR + _SEPARATOR.join(entries)


def worst_case_render(text_chars: int, results: int | None = None) -> str:
    """A full page of the largest notes the tool can render, for the derivation (N11)."""
    n = results if results is not None else config.notes_max_results()
    subject = "S" * config.notes_max_subject_chars()
    lead = framing('0' * 36, 'project', subject, _WORST_AGE, 'revise')
    assert _WORST_LEAD in lead, "the worst-case framing must use the longest lead-in"
    entry = f"{lead}\n{'T' * text_chars}"
    return texts.HEADER + _SEPARATOR + _SEPARATOR.join([entry] * n)


def derive_max_text_chars() -> int:
    """The largest note text for which a worst-case full page fits ``agent.max_tool_result_chars``.

    Computed from the live renderer, so it cannot drift from it: a reworded header or framing
    moves this number, and ``tests/test_notes_tools.py`` fails until ``defaults.toml`` follows."""
    cap = config.agent_max_tool_result_chars()
    low, high = 0, cap
    while low < high:
        mid = (low + high + 1) // 2
        if len(worst_case_render(mid)) <= cap:
            low = mid
        else:
            high = mid - 1
    return low


def search_notes(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise texts.NoteRefused(texts.EMPTY_QUERY)
    rows = notes.search_active(query, config.notes_max_results())
    if not rows:
        return texts.NO_MATCH
    return render_notes(rows)


NOTE_SEARCH = Tool(
    name="note_search",
    description=(
        "Search notes about people, topics and projects in this household. "
        "Not conversation memory (use memory_search)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words to look for"},
        },
        "required": ["query"],
    },
    handler=search_notes,
    #: Its own switch, off by default: Notes ships dark (decision #12's first axis).
    enabled=config.notes_enabled,
    #: A local FTS read; well under a second.
    timeout_seconds=15.0,
)
