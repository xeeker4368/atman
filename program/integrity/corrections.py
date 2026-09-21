"""The correction/supersession classifier. `NOW.md` decision #2, task 3.3.

Design of record: ``docs/CORRECTION_DESIGN.md`` (C1–C13, CO1–CO7).

A correction is **a link, never an edit**. The corrected message stays exactly as
it was said; a later message is recorded as superseding it, and retrieval
resolves the link so the current version is what surfaces (task 3.5). Nothing
here rewrites anything: *raw experience is never edited* (`PROJECT.md`,
`GUIDANCE.md`).

Links are message → message
===========================
Not chunk → chunk, which is what the table held until migration 5. A chunk packs
up to eight turns chosen by size, so marking one superseded asserts staleness for
content the correction says nothing about; the open trailing group is never
indexed, so the *normal* case — correcting something said a minute ago — has no
chunk to link to; and chunks are derived and rebuildable while links to them are
not. See C3, and migration 5 for the reasoning in the schema itself.

Who may correct whom
====================
**Same speaker, both ways** — and the asymmetry is deliberate rather than an
oversight:

* A person may correct **their own** earlier statement.
* The entity may correct **its own** earlier claim.
* **One user never supersedes the other** (decision #21/Q16).
* **The entity never supersedes a person's statement** (CO4). Not because the
  capability would be useless, but because an automated classifier's inference
  must not override a human's explicit self-report about their own words. An
  accurate raw record quietly informing retrieval is one thing; a generated
  judgment disputing what a person just said about themselves is another.

All four are enforced **by construction**, in :func:`candidates`: a target that
would breach them is never offered to the classifier, so it cannot be picked. The
same pattern as the fabrication gate's structural enforcement — a guarantee in
the code rather than an instruction in a prompt.

**Role parity is what makes one call enough.** Because a correction always comes
from the same speaker, the candidate's role determines which of the turn's two
messages is the corrector: a ``user`` candidate can only be superseded by this
turn's user message, an ``assistant`` candidate only by the answer.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass

from program import config
from program.engine import ollama
from program.integrity import classifier
from program.memory import chunking, db

logger = logging.getLogger(__name__)

#: How many prior claims are offered at once. A bound rather than a threshold:
#: the prompt has to fit, and a long list invites loose matching. Every claim
#: beyond it is simply not a candidate this turn.
MAX_CANDIDATES = 12

#: Characters of a candidate shown to the classifier. Long enough to judge a
#: claim, short enough that twelve of them do not dominate the prompt.
CANDIDATE_CHARS = 400

_PROMPT = """You are deciding whether a new message corrects one of the earlier
statements listed below. You are not judging tone, helpfulness, or whether
anything is a good answer.

EARLIER STATEMENTS:
{candidates}

NEW MESSAGE (from {speaker}):
{new_message}

A correction means the new message states that something in one of the earlier
statements is wrong. Replacing a fact, a number, a name or a date is a
correction. So is flatly contradicting one without saying what is true instead —
either way the earlier statement should no longer be read as current.

These are NOT corrections:
- adding detail that does not contradict the earlier statement
- doubting, asking whether something is right, or saying you will go and check:
  nothing has been asserted to be wrong yet
- repeating or rephrasing the earlier statement
- moving on to a related topic

Say which of two kinds it is:
REPLACED     - the new message gives the correct value
CONTRADICTED - the new message says the earlier one is wrong but does not give
               the correct value

Reply in exactly this form and nothing else:
NONE
or
CORRECTS <number> REPLACED
or
CORRECTS <number> CONTRADICTED
- <what changed> | <why this is a correction rather than an addition>
"""

#: This task's own grammar, parsed here rather than in `classifier.py`. O18's
#: precedent: the shared layer carries the call, the settings and the principle
#: that an unusable reply is never a pass — not each consumer's vocabulary. The
#: gate's CONSISTENT/CONTRADICTS means nothing here, and NONE/CORRECTS would mean
#: nothing there.
#: The whole leading number list, not just the first number. The model writes
#: `CORRECTS 1, 2` on ONE line when it thinks two claims are implicated, and a
#: pattern capturing only the first number reads that as a confident single
#: verdict and links candidate 1 — CO5's guard never fires, because it counts
#: matches. Measured: `G2-ambiguous-two-claims` false-linked 20/20 through exactly
#: this path. The trailing alternation stops at the first non-separator, so digits
#: in a rationale on the same line are not swept in.
_CORRECTS = re.compile(
    r"^\s*CORRECTS\s+(\d+(?:\s*(?:,|and|&)\s*\d+)*)\s*([A-Za-z]+)?",
    re.IGNORECASE | re.MULTILINE)
_NUMBER = re.compile(r"\d+")

#: The two states migration 6's CHECK accepts, as the classifier spells them.
_STATES = {"replaced": "replaced", "contradicted": "contradicted"}
_NONE = re.compile(r"^\s*NONE\b", re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    """One earlier statement that could be corrected."""

    message_id: str
    role: str
    content: str
    timestamp: str


@dataclass(frozen=True)
class Correction:
    """A judged correction, before it is written."""

    superseding_message_id: str
    superseded_message_id: str
    rationale: str
    #: ``replaced`` (the correction gave the new value) or ``contradicted`` (it
    #: said the earlier claim was wrong and gave none). CO8; migration 6. No
    #: default — task 3.5 renders the two differently and nothing else on the row
    #: recovers the distinction.
    replacement: str
    #: Always ``None`` today. See C7: the classifier emits no calibrated number,
    #: and a self-reported confidence would be an unmeasured constant of exactly
    #: the kind this build refuses. The column stays so a measured one can land
    #: later without a migration.
    confidence: float | None = None


def _row_candidate(row: sqlite3.Row) -> Candidate:
    return Candidate(
        message_id=row["id"],
        role=row["role"],
        content=row["content"],
        timestamp=row["timestamp"],
    )


def candidates(
    user_id: str,
    conversation_id: str,
    retrieved_chunk_ids: list[str],
    exclude_message_ids: tuple[str, ...] = (),
    user_name: str = "the person",
) -> list[Candidate]:
    """Prior statements this turn could correct, drawn from what it already paid for.

    Two sources, and the second is not optional:

    1. **The chunks retrieval returned this turn**, resolved to their messages.
       Free — the turn already ran that retrieval.
    2. **The open trailing group of this conversation**, via
       :func:`chunking.open_group_messages`. Retrieval *cannot* return these:
       chunking deliberately never indexes the trailing group. Without this
       source, correcting something said a minute ago — the most likely case —
       would be invisible. "Recent" is that boundary rather than a new constant
       of this module's own (CO3).

    Filtered to ``user_id``'s own record, which is where Q16 and CO4 are
    enforced: a message belonging to the other household member, or one whose
    role differs from the speaker who could correct it, is never offered.
    """
    seen: dict[str, Candidate] = {}

    for chunk_id in retrieved_chunk_ids:
        for row in db.get_messages_in_chunk(chunk_id):
            if row["user_id"] == user_id and row["id"] not in exclude_message_ids:
                seen[row["id"]] = _row_candidate(row)

    open_group = chunking.open_group_messages(
        db.get_conversation_messages(conversation_id), user_name
    )
    for row in open_group:
        if row["user_id"] == user_id and row["id"] not in exclude_message_ids:
            seen[row["id"]] = _row_candidate(row)

    ordered = sorted(seen.values(), key=lambda c: c.timestamp, reverse=True)
    return ordered[:MAX_CANDIDATES]


def _render(pool: list[Candidate], user_name: str) -> str:
    lines = []
    for number, candidate in enumerate(pool, start=1):
        who = user_name if candidate.role == "user" else "the system"
        text = candidate.content.strip()[:CANDIDATE_CHARS]
        lines.append(f"{number}. ({who}, {candidate.timestamp[:16]}) {text}")
    return "\n".join(lines)


def _parse(reply: str) -> tuple[int | None, str, str]:
    """``(candidate number, rationale, replacement state)``; number ``None`` for NONE.

    Raises on an unusable reply, keeping the shared framework's principle — a
    model that answered neither form has not said "no correction", and reading
    silence as a verdict is what makes "checked" unfalsifiable. The caller turns
    the raise into *no link*.

    **An unlabelled ``CORRECTS <n>`` is unusable, not defaulted.** Migration 6's
    column is ``NOT NULL`` with no default for the same reason: picking a state on
    the model's behalf would manufacture whichever annotation is cheaper to render,
    and "the classifier did not say" would become indistinguishable from "the
    classifier said contradicted". The cost is real and is recorded in
    RETRIEVAL_SUPERSESSION_DESIGN R4 — a label the model omits turns a correction
    it did identify into a miss.
    """
    text = (reply or "").strip()
    if not text:
        raise ollama.OllamaResponseError("the correction classifier returned nothing")
    if _NONE.match(text):
        return None, "", ""

    matches = _CORRECTS.findall(text)
    numbers = [n for group, _label in matches for n in _NUMBER.findall(group)]
    if not numbers:
        raise ollama.OllamaResponseError(
            f"the correction classifier gave no usable verdict: {text[:120]!r}"
        )
    if len(numbers) != 1:
        # CO5: a reply naming several candidates has not obeyed the grammar, so
        # its judgment is not trustworthy enough to write. Dropped rather than
        # resolved to a guess, and logged so 3.4 can see it happening.
        logger.info(
            "correction classifier named %d candidates; no link written", len(numbers))
        return None, "", ""

    state = _STATES.get(matches[0][1].strip().lower())
    if state is None:
        raise ollama.OllamaResponseError(
            "the correction classifier named a candidate without saying whether the "
            f"value was replaced or only contradicted: {text[:120]!r}"
        )

    rationale = ""
    for line in text.splitlines()[1:]:
        if line.strip().startswith("-") and line.strip("- ").strip():
            rationale = line.lstrip("-").strip()
            break
    return int(numbers[0]), rationale, state


def classify(
    new_message: str,
    new_message_id: str,
    pool: list[Candidate],
    speaker_role: str,
    speaker_label: str = "the person",
) -> Correction | None:
    """Judge whether ``new_message`` corrects one of ``pool``. One call.

    Returns ``None`` — **write no link** — for every unclear outcome: no
    candidates, a ``NONE`` verdict, a number outside the list, or a reply naming
    more than one candidate. The gate's rule is *never clean by default*; this
    mechanism's is **never linked by default**, because the failures are not
    symmetric. A missed correction leaves the record accurate and merely
    uncorrected; a wrong link makes retrieval present the wrong claim as current.
    """
    eligible = [c for c in pool if c.role == speaker_role]
    if not eligible:
        return None

    prompt = _PROMPT.format(
        candidates=_render(eligible, speaker_label),
        speaker=speaker_label,
        new_message=new_message.strip(),
    )
    number, rationale, replacement = _parse(classifier.classify(prompt))
    if number is None:
        return None
    index = number - 1
    if not 0 <= index < len(eligible):
        logger.info(
            "correction classifier named candidate %s, which is not in the list; "
            "no link written", number)
        return None

    return Correction(
        superseding_message_id=new_message_id,
        superseded_message_id=eligible[index].message_id,
        rationale=rationale[:500],
        replacement=replacement,
    )


def record(correction: Correction) -> str | None:
    """Write the link. Returns the link id, or ``None`` if the schema refused it.

    The cycle guard and the uniqueness constraint live in the schema precisely
    because the writer is not always this module, so a refusal here is expected
    behaviour rather than an error: it means the link would have closed a loop or
    already existed.
    """
    try:
        return db.create_supersedes_link(
            correction.superseding_message_id,
            correction.superseded_message_id,
            correction.replacement,
            classifier_model=config.classifier_model(),
            confidence=correction.confidence,
            rationale=correction.rationale,
        )
    except sqlite3.IntegrityError as exc:
        logger.warning(
            "correction link refused by the schema (%s): %s -> %s",
            exc, correction.superseding_message_id[:8],
            correction.superseded_message_id[:8],
        )
        return None
