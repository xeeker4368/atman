"""``note_propose``: the entity proposes a note, or a change to one. Notes piece 3.

Design of record: ``docs/NOTES_DESIGN.md`` N0, N2, N3, N4, N7. **It writes a proposal, never a
note.** A proposal is inserted ``pending`` and stays there: nothing here can approve, apply or
decide it (that is the operator's, piece 4, and auto-apply is a later piece behind its own switch),
and no note row is created, changed or retired. The result says so in words (``note_texts``) and
**names no id**.

What a call must carry, each refused with ``TOOL_ERROR`` and a message the model can act on:
a valid ``action`` and ``subject_kind`` (there is **no ``self`` kind**: a note is never about the
entity); a short ``subject``; ``text`` within ``notes.max_text_chars`` for an add or revise;
a ``note_id`` that names an **active** note for a revise or retire; and **at least one quote that
resolves to a message a person wrote** (``note_quotes``), so a fabricated quote, or the entity
citing its own reply, cannot enter a proposal. ``registry`` validates presence and top-level types
only, so the handler checks the enum values and every array item itself.

Declares ``takes_attribution`` (whose record: the person present) and ``takes_origin`` (which
exchange: conversation, the triggering message, the call). Neither is a parameter the model can set.

**The identity gate runs on the text, flag-only** (N7): ``gate.check_identity``, stored on the row.
A flag changes nothing about the proposal. It is **a noisy aid, not a control**: the control is the
reviewer reading the proposal (J8, ruling 2026-10-01). A retire has no text, so no verdict is
recorded and the column stays NULL, which means *no verdict recorded*, never clean.

Because this tool declares ``takes_attribution``, ``registry.side_effect_tools()`` includes it as
soon as it is in the catalogue, **even while Notes is dark**; ``tests/test_notes_tools.py`` proves
the gate's verdicts over every frozen case are byte-identical regardless.

**Receipts (piece 5, N8):** the call reports its proposal as a ``note_proposal`` record, and the
receipt reads that row's current status, so it says *proposed, awaiting a person's review* until a
person decides it, or *applied without review* when approval was off.

**Approval (piece 6).** The description is static and says nothing about review, because the setting
is not: `notes.approval_required` is read fresh, inside the insert's transaction, and the **result
text** says which happened (`PENDING_*` or `APPLIED_*`). With approval off the note change, the
proposal's flip to `applied_without_review` and the log row are one transaction.
"""

from __future__ import annotations

import logging

from program import config
from program.attribution import AttributionContext
from program.integrity import gate
from program.memory import db, notes
from program.origin import OriginContext
from program.tools import note_quotes
from program.tools import note_texts as texts
from program.tools.registry import Record, Tool, ToolOutput

logger = logging.getLogger(__name__)

ACTIONS = ("add", "revise", "retire")
SUBJECT_KINDS = ("person", "topic", "project")

#: The identity gate is one classifier call (``integrity.classifier_timeout_seconds`` bounds it at
#: 45 s), plus quote resolution and one insert. Above that bound, below the 120 s turn budget.
TIMEOUT_SECONDS = 60.0


def _resolve_target(note_id: str):
    matches = notes.get_active_by_id_prefix(note_id.strip())
    if not matches:
        raise texts.proposal_refused(texts.note_not_found(note_id))
    if len(matches) > 1:
        raise texts.proposal_refused(texts.NOTE_ID_AMBIGUOUS)
    return matches[0]


def propose_note(
    origin: OriginContext,
    attribution: AttributionContext,
    action: str,
    subject_kind: str,
    subject: str,
    quotes: list,
    text: str | None = None,
    note_id: str | None = None,
) -> ToolOutput:
    if action not in ACTIONS:
        raise texts.proposal_refused(texts.bad_action(action))
    if subject_kind not in SUBJECT_KINDS:
        raise texts.proposal_refused(texts.bad_subject_kind(subject_kind))

    subject = (subject or "").strip()
    if not subject:
        raise texts.proposal_refused(texts.SUBJECT_EMPTY)
    if len(subject) > config.notes_max_subject_chars():
        raise texts.proposal_refused(
            texts.subject_too_long(len(subject), config.notes_max_subject_chars()))

    text = text.strip() if isinstance(text, str) else text
    if action == "retire":
        if text:
            raise texts.proposal_refused(texts.RETIRE_TAKES_NO_TEXT)
        text = None
    else:
        if not text:
            raise texts.proposal_refused(texts.TEXT_REQUIRED)
        if len(text) > config.notes_max_text_chars():
            raise texts.proposal_refused(
                texts.text_too_long(len(text), config.notes_max_text_chars()))

    target_id = None
    if action == "add":
        if note_id:
            raise texts.proposal_refused(texts.ADD_TAKES_NO_NOTE_ID)
    else:
        if not note_id or not str(note_id).strip():
            raise texts.proposal_refused(texts.NOTE_ID_REQUIRED)
        target_id = _resolve_target(str(note_id))["id"]

    if not quotes:
        raise texts.proposal_refused(texts.QUOTES_REQUIRED)
    for index, quote in enumerate(quotes, start=1):
        if not isinstance(quote, str) or not quote.strip():
            raise texts.proposal_refused(texts.quote_not_text(index))
    evidence = [note_quotes.resolve_quote(quote, origin).to_dict() for quote in quotes]

    verdict = gate.check_identity(text) if text else None
    proposal_id = db.new_id()
    # Everything fallible is done BEFORE the insert. The row is the last thing this function does
    # that can fail: if anything after it raised, the call would be a `tool_error` with a proposal
    # row present, and the receipt would say "Nothing was recorded" about a record that exists.
    records = (Record("note_proposal", proposal_id),)
    results = {
        "pending": ToolOutput(
            texts.PENDING_ADD if action == "add" else texts.PENDING_CHANGE, records=records),
        "applied": ToolOutput(
            {"add": texts.APPLIED_ADD, "revise": texts.APPLIED_CHANGE,
             "retire": texts.APPLIED_RETIRE}[action], records=records),
    }
    logger.info("note proposal (%s, %s) recording, call %s", action, subject_kind,
                (origin.call_id or "")[:8])
    outcome = notes.record_proposal(
        proposal_id=proposal_id,
        action=action,
        target_note_id=target_id,
        subject_kind=subject_kind,
        subject=subject,
        text=text,
        evidence=evidence,
        conversation_id=origin.conversation_id,
        user_message_id=origin.user_message_id,
        call_id=origin.call_id,
        user_id=attribution.user_id,
        integrity_check=verdict.to_json() if verdict is not None else None,
    )
    return results[outcome]


NOTE_PROPOSE = Tool(
    name="note_propose",
    description=(
        "Propose a note about a person, topic or project, or a change to one. "
        "The result says what happened. One short fact per note. "
        "Evidence quotes are words a person said, copied exactly from a message "
        "(never a note's text). A retire takes no text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "subject_kind": {"type": "string", "enum": list(SUBJECT_KINDS)},
            "subject": {"type": "string", "description": "Short label"},
            "text": {
                "type": "string",
                "description": f"The note, max {config.notes_max_text_chars()} characters",
            },
            "quotes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Words a person said, copied exactly. Not a note's text.",
            },
            "note_id": {"type": "string", "description": "revise/retire: id from note_search"},
        },
        "required": ["action", "subject_kind", "subject", "quotes"],
    },
    handler=propose_note,
    takes_attribution=True,
    takes_origin=True,
    enabled=config.notes_enabled,
    timeout_seconds=TIMEOUT_SECONDS,
)
