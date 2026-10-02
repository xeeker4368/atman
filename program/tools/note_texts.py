"""Every sentence the two note tools say to the model, in one place.

Authored text: ``prompt.check_authored_text`` applies and a test runs it over each. They are
collected here so a reviewer reads them together, and so the one that a report depends on (the
empty-search sentence, which ``scripts.note misses`` matches) is **one constant** imported by both
the tool and, in piece 4, the report: rewording it in one place cannot silently empty the other.

**No result names a note or a proposal id.** An id in the answer text would invite the entity to
quote it, and a quoted identifier is exactly what the fabrication gate's `invented_id` rule is
suspicious of (B19). Notes are shown by a short prefix the entity passes back in a tool call.
"""

from __future__ import annotations


class NoteRefused(Exception):
    """A note tool refuses a call. Raised from a handler, so dispatch returns ``TOOL_ERROR`` and
    the model reads the message (prefixed by this class's name) and can try differently. Never a
    crash and never a silent drop."""


# --- note_search --------------------------------------------------------------------------

#: What an empty search says. N6: it separates "no note" from "never discussed", because
#: conflating them is a false claim about the record.
NO_MATCH = (
    "No note matches that. Notes hold only what was written down as a note, so this says "
    "nothing about whether it was ever talked about; memory_search covers conversations."
)

HEADER = (
    "Notes: statements about people, topics and projects in this household. "
    "They are not conversation records, and a note is only as current as its age says."
)

EMPTY_QUERY = "give some words to look for."

# --- note_propose: results ------------------------------------------------------------------

#: The result of an add. It says a proposal exists and a note does not.
PENDING_ADD = (
    "Proposed. A person will review it before anything changes. "
    "No note exists yet because of this."
)

#: The result of a revise or retire. It says the existing note is unchanged.
PENDING_CHANGE = (
    "Proposed. A person will review it before anything changes. "
    "The note stays as it is until then."
)

#: The results when approval is off and the proposal was applied at once. They say what happened
#: and that no one reviewed it. (Which of the two modes applies is decided in the database at the
#: moment of the call, not by the tool's description, which is the same in both.)
APPLIED_ADD = "Added. The note now exists. No one reviewed it."
APPLIED_CHANGE = "Done. The note was changed as proposed. No one reviewed it."
APPLIED_RETIRE = "Done. The note was retired as proposed. No one reviewed it."

# --- note_propose: refusals (each reaches the model as TOOL_ERROR) ----------------------------


def bad_action(value: object) -> str:
    return f"action must be one of add, revise, retire; got {value!r}."


def bad_subject_kind(value: object) -> str:
    return (
        f"subject_kind must be one of person, topic, project; got {value!r}. "
        f"A note is never about yourself."
    )


SUBJECT_EMPTY = "subject is empty; give a short label such as a name."


def subject_too_long(length: int, limit: int) -> str:
    return f"subject is {length} characters; the limit is {limit}. Use a short label."


TEXT_REQUIRED = "text is required to add or revise a note."

RETIRE_TAKES_NO_TEXT = "a retire takes no text; leave it out."


def text_too_long(length: int, limit: int) -> str:
    return (
        f"the note is {length} characters; the limit is {limit}. A note is one short fact; "
        f"split it into more than one note."
    )


NOTE_ID_REQUIRED = "note_id is required to revise or retire a note; get it from note_search."

ADD_TAKES_NO_NOTE_ID = "an add takes no note_id; leave it out."


def note_not_found(value: str) -> str:
    return (
        f"no active note has id {value!r}. Search again: it may have been retired or revised."
    )


NOTE_ID_AMBIGUOUS = "that id matches more than one note; give more of it."

QUOTES_REQUIRED = (
    "give at least one quote: exact words a person said that support this."
)


def quote_not_text(index: int) -> str:
    return f"quote {index} is not text; each quote must be a string of exact words."


def quote_too_short(chars: int, words: int) -> str:
    return (
        f"that quote is too short to identify a message; quote more of what was said "
        f"(at least {chars} characters and {words} words)."
    )


QUOTE_NOT_A_PERSONS_WORDS = (
    "that quote is not a person's words; a note's evidence has to be something a person said, "
    "not your own reply."
)

QUOTE_NO_MATCH = "that quote does not appear in any message."


def quote_ambiguous(count: int) -> str:
    return f"that quote appears in {count} messages; quote more of it so it identifies one."
