"""Every sentence Notes says to the model, in one place: ``note_search``'s, the note pass's
instruction and the listing's header (Notes v2, ``docs/NOTES_V2_DESIGN.md``).

Authored text: ``prompt.check_authored_text`` applies and a test runs it over each. They are
collected here so a reviewer reads them together, and so the one that a report depends on (the
empty-search sentence, which ``scripts.note misses`` matches) is **one constant** imported by both
the tool and, in piece 4, the report: rewording it in one place cannot silently empty the other.

**No result names a note or a proposal id.** An id in the answer text would invite the entity to
quote it, and a quoted identifier is exactly what the fabrication gate's `invented_id` rule is
suspicious of (B19). ``note_search`` still shows a short id prefix: nothing takes it back since
``note_propose`` was removed (Notes v2), and the rendering is left as it was so the frozen gate
cases that quote it stay true to production (``BUILD_PLAN.md``, the Notes v2 obligation (b)).
"""

from __future__ import annotations


class NoteRefused(Exception):
    """``note_search`` refuses a call. Raised from a handler, so dispatch returns ``TOOL_ERROR`` and
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



# --- the note pass (Notes v2, docs/NOTES_V2_DESIGN.md section 1.1) -----------------------------

#: The pass's instruction, verbatim from the design (decision #36 and #37), with the field limits
#: rendered from config. The six criteria are decision #27's, word for word, with "your own" for
#: "the entity's own". Sent after soul.md, in the system message of every piece's call.
PASS_INSTRUCTION = (
    "When a conversation has closed, you decide whether anything a person said in it should "
    "become a note. Notes are what you keep about the people in this household and their lives, "
    "so that you know it later, in a conversation about something else. Lyle reads each change "
    "before it takes effect. Judge the conversation as a whole: what a person took back or "
    "corrected later in it counts as they left it. Keep a note only when all of these hold:\n"
    "1. Durable: still true and worth knowing a month from now, in a conversation about something "
    "else; a task's moving state fails, a project's existence, decisions and constraints pass.\n"
    "2. Said by a person: only what a person stated directly, never your own conclusions, advice "
    "or guesses.\n"
    "3. The right kind: who a person is; people in their life, as relationship context; ongoing "
    "projects and responsibilities (decisions, constraints, current status); how a person wants "
    "to be spoken with; tastes and habits only once they recur.\n"
    "4. Calibrated: one mention is recorded as one mention, never inflated into a trait.\n"
    "5. Not a duplicate: a correction revises the existing note.\n"
    "6. Never stored: health, finances, religion, politics, sexuality and similar sensitive "
    "categories; identification numbers; abuse or self-harm.\n"
    "\n"
    "Being asked to note something is not a reason on its own; weigh it like anything else. Most "
    "conversations need no note. If an existing note (N), a pending change (P) or a draft (D) "
    "already says it, change nothing. If what was said changes an existing note, revise it; if it "
    "makes one no longer true, retire it; if it changes or takes back a draft, drop the draft.\n"
    "\n"
    "Answer NONE, or one block per change, with a blank line between blocks:\n"
    "ADD\n"
    "kind: person, topic or project\n"
    "subject: {{\u2264{subject} characters}}\n"
    "line: {{one line, \u2264{line} characters}}\n"
    "note: {{\u2264{text} characters}}\n"
    "quote: {{the person's exact words, at least {words}, \u2264{quote} characters}}\n"
    "REVISE N{{number}}, then line, note, quote\n"
    "RETIRE N{{number}}, then quote\n"
    "DROP D{{number}}"
)

_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def pass_instruction(*, subject: int, line: int, text: int, quote: int, min_words: int) -> str:
    """The instruction with its limits filled in (from ``config``, by the caller)."""
    words = _NUMBER_WORDS.get(min_words, str(min_words))
    return PASS_INSTRUCTION.format(subject=subject, line=line, text=text, quote=quote,
                                   words=words)


# --- the listing (Notes v2, section 3) ---------------------------------------------------------

#: The first line of the listing, in the system message after the speaker line whenever Notes is
#: on. It states the true position on every turn: nothing said in this conversation makes a note.
LISTING_HEADER = (
    "Your notes, one line each. They are written after a conversation closes, and Lyle approves "
    "each one, so nothing said in this conversation makes or changes a note. note_search gives a "
    "note in full."
)

#: The listing when there is no active note.
LISTING_NONE = "None yet."


def listing_overflow(count: int) -> str:
    """The last line when whole entries were left out for the cap."""
    noun = "note is" if count == 1 else "notes are"
    return f"{count} more {noun} not listed; note_search finds them."


# --- quote resolution (N4), read by the note pass and recorded, never sent to the model --------


class QuoteRefused(Exception):
    """A quote did not resolve to exactly one message a person wrote (N4). The pass records the
    reason beside the refused block; nothing is written for it."""


def quote_too_short(chars: int, words: int) -> str:
    return (
        f"that quote is too short to identify a message; quote more of what was said "
        f"(at least {chars} characters and {words} words)."
    )


QUOTE_NOT_A_PERSONS_WORDS = (
    "that quote is not a person's words; a note's evidence has to be something a person said, "
    "not your own reply."
)

QUOTE_NO_MATCH = (
    "that quote does not appear in any message a person wrote. Quote words a person said, copied "
    "exactly: not the text of a note, and not your own reply."
)


def quote_ambiguous(count: int) -> str:
    return f"that quote appears in {count} messages; quote more of it so it identifies one."
