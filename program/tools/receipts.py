"""Receipts: what the person is shown when a side-effect tool was called. O23, F50.

Design of record: ``docs/FABRICATION_GATE_DESIGN.md`` F50, approved at review
2026-09-30.

Why this exists
===============
The gate's ACTION trigger has to recognise every way the entity might *claim* to
have made or saved something, and O23 measured that it cannot. A receipt asks a
different question, one the system answers with certainty: **did a side-effect
tool actually run, and is its record in the store?** It does not detect a false
claim. It puts the truth beside it, whatever the entity's prose says.

What a receipt is, and is not
=============================
* **Mechanical only**: the tool, an outcome, the artifact id, the row's type and
  time. Never a title, a prompt or content, which are the entity's own work and
  stay under its discretion (decision #10, clarified in F50).
* **Never part of the entity's message.** The content is the entity's words; it
  goes to the append-only archive, is resent as history, is chunked into
  retrieval and is read by the gate. A receipt travels beside it, as a separate
  field, and is rebuilt from the stored trace whenever it is needed.
* **Independent of the gate.** Nothing here reads a verdict, and the gate does not
  read receipts. Neither can hide the other's failure.

The outcome, and why it keys on the row
=======================================
``saved`` needs the ``artifacts`` row, not the trace's outcome label, because the
label can be wrong about the thing that matters: a write that fails *after* its
row is committed comes back ``tool_error`` with the row present (measured
2026-09-30). Three outcomes, keeping *unknown* distinct from *not saved*:

====================================================  ===========
trace entry                                           receipt
====================================================  ===========
an id whose row exists (any outcome)                  saved
the handler never ran (skipped, unknown, invalid)     not_saved
``tool_error`` with no ids                            not_saved
``timeout`` with no ids                               unknown
``ok`` with no ids, or an id with no row              unknown (defect, logged)
no ``artifact_ids`` key (recorded before O23)         unknown
the row lookup itself failed                          unknown
====================================================  ===========

**Silence has one meaning**: no receipts means no side-effect tool was called.
So the function never returns fewer receipts than there were side-effect calls,
and a failed lookup degrades to ``unknown``, never to an empty list.

Piece 5 (N8, option A): more than one kind of record
====================================================
A trace entry carries ``records: [{"kind", "id"}]``, and each kind is read from **its own
row** through ``READERS`` (the same data-not-conditionals shape as ``kinds.KINDS``):

* ``artifact``: the table above, unchanged. Its receipt's JSON is byte-identical to before.
* ``note_proposal``: the row's **current** status. The receipt built at the turn reads
  *pending*, because that is what the row says; a later rebuild says what the row says by
  then. A missing row or a failed lookup is ``unknown``, never inferred from the trace.

====================  ==========================  ===============================================
row status            outcome                     text
====================  ==========================  ===============================================
pending               proposed                    Proposed, awaiting a person's review.
approved, edited      accepted                    Reviewed and accepted.
rejected              declined                    Reviewed and not accepted.
applied_without_...   applied_without_review      Applied without review.
(never ran, error)    not_proposed                Not proposed. Nothing was recorded.
(anything else)       unknown                     Unknown. The proposal could not be confirmed.
====================  ==========================  ===============================================

No proposal receipt says or implies a note exists unless the row says a person accepted it.

**Old traces keep rendering the same receipts.** An entry with ``artifact_ids`` and no
``records`` is read as artifact records (:func:`records_of`); one with neither predates O23.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from program.memory import db
from program.tools.registry import side_effect_tools

logger = logging.getLogger(__name__)

SAVED = "saved"
NOT_SAVED = "not_saved"
UNKNOWN = "unknown"

PROPOSED = "proposed"
ACCEPTED = "accepted"
DECLINED = "declined"
APPLIED = "applied_without_review"
NOT_PROPOSED = "not_proposed"

#: What the person reads for each proposal outcome. System-written, plain, and never a claim that
#: a note exists unless a person accepted the proposal (or it was applied without review).
PROPOSAL_TEXTS = {
    PROPOSED: "Proposed, awaiting a person's review.",
    ACCEPTED: "Reviewed and accepted.",
    DECLINED: "Reviewed and not accepted.",
    APPLIED: "Applied without review.",
    NOT_PROPOSED: "Not proposed. Nothing was recorded.",
    UNKNOWN: "Unknown. The proposal's record could not be confirmed.",
}

#: Row status to outcome. ``approved`` and ``edited`` both read *reviewed and accepted*; the
#: receipt's ``status`` field keeps the row's own word.
_PROPOSAL_STATUS = {
    "pending": PROPOSED,
    "approved": ACCEPTED,
    "edited": ACCEPTED,
    "rejected": DECLINED,
    "applied_without_review": APPLIED,
}

#: Outcomes where the handler was never entered, so nothing can have been written.
#: Decided without the store, and therefore certain even if the lookup fails.
_NEVER_RAN = frozenset({"skipped", "unknown_tool", "invalid_arguments"})

ARTIFACT = "artifact"
NOTE_PROPOSAL = "note_proposal"

#: kind -> the one-query reader of that kind's own table. Must name exactly
#: ``registry.RECORD_KINDS`` (tested), so a kind cannot be reportable and unreadable.
READERS: dict[str, Callable[[Sequence[str]], dict[str, Any]]] = {
    ARTIFACT: lambda ids: db.get_artifacts_by_ids(ids),
    NOTE_PROPOSAL: lambda ids: db.get_note_proposals_by_ids(ids),
}


@dataclass(frozen=True)
class Receipt:
    tool: str
    outcome: str
    artifact_id: str | None = None
    #: From the row, the primary record, never from the tool name.
    artifact_type: str | None = None
    created_at: str | None = None
    #: Which kind of record this describes. ``artifact`` receipts serialise exactly as they did
    #: before piece 5; the fields below exist only for other kinds.
    kind: str = ARTIFACT
    record_id: str | None = None
    #: The row's own status word (a proposal's ``approved`` or ``edited``), from the row.
    status: str | None = None
    text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.kind == ARTIFACT:
            return {"tool": self.tool, "outcome": self.outcome, "artifact_id": self.artifact_id,
                    "artifact_type": self.artifact_type, "created_at": self.created_at}
        return {"tool": self.tool, "kind": self.kind, "outcome": self.outcome,
                "record_id": self.record_id, "status": self.status, "text": self.text,
                "created_at": self.created_at}


def records_of(entry: dict[str, Any]) -> list[dict[str, str]] | None:
    """The records a trace entry says its call wrote, or ``None`` when it cannot say.

    ``records`` wins when present. An entry with ``artifact_ids`` and no ``records`` is a trace
    stored before N8 and reads as artifact records. An entry with neither predates O23: ``None``,
    which a receipt reports as unknown."""
    if "records" in entry:
        return [r for r in (entry.get("records") or ()) if isinstance(r, dict)]
    if "artifact_ids" in entry:
        return [{"kind": ARTIFACT, "id": i} for i in (entry.get("artifact_ids") or ())]
    return None


def _nothing_written(tool: str, outcome: str, kind: str) -> Receipt:
    """A call that named no record. ``kind`` is the tool's own (see :func:`_home_kind`)."""
    if outcome in _NEVER_RAN or outcome == "tool_error":
        result = NOT_PROPOSED if kind == NOTE_PROPOSAL else NOT_SAVED
    elif outcome == "timeout":
        result = UNKNOWN
    else:
        logger.error(
            "receipts: %s reported %r with no records; a side-effect call that succeeded "
            "must name what it wrote", tool, outcome,
        )
        result = UNKNOWN
    return _bare(tool, result, kind)


def _bare(tool: str, outcome: str, kind: str, record_id: str | None = None) -> Receipt:
    if kind == NOTE_PROPOSAL:
        return Receipt(tool, outcome, kind=kind, record_id=record_id,
                       text=PROPOSAL_TEXTS[outcome])
    return Receipt(tool, outcome, record_id)


def _home_kind(tool: str) -> str:
    """The kind of record a tool writes, for a call that named none. Only ``note_propose``
    writes proposals; every other side-effect tool writes artifacts."""
    return NOTE_PROPOSAL if tool == "note_propose" else ARTIFACT


def for_trace(trace: Sequence[dict[str, Any]]) -> list[Receipt]:
    """One receipt per record a side-effect call wrote, or per call if none.

    Never raises for a store failure: a receipt that cannot be confirmed is
    ``unknown``, which is true, while raising would lose the answer the person is
    waiting for and omitting would claim nothing was attempted.
    """
    watched = set(side_effect_tools())
    calls = [entry for entry in trace if entry.get("tool") in watched]
    if not calls:
        return []

    wanted: dict[str, list[str]] = {}
    for entry in calls:
        for record in records_of(entry) or ():
            if record.get("kind") in READERS:
                wanted.setdefault(record["kind"], []).append(record.get("id"))
    rows: dict[str, dict[str, Any] | None] = {}
    for kind, ids in wanted.items():
        try:
            rows[kind] = READERS[kind](ids)
        except Exception as exc:  # noqa: BLE001 - degrades to `unknown`, never to silence
            logger.warning(
                "receipts: could not read the %s rows (%s: %s); every receipt that "
                "depends on them is recorded as unknown", kind, type(exc).__name__, exc,
            )
            rows[kind] = None

    receipts: list[Receipt] = []
    for entry in calls:
        tool = str(entry.get("tool"))
        outcome = str(entry.get("outcome", ""))
        home = _home_kind(tool)

        if outcome in _NEVER_RAN:
            receipts.append(_nothing_written(tool, outcome, home))
            continue
        written = records_of(entry)
        if written is None:
            # Recorded before the key existed: the trace cannot say what was written.
            receipts.append(_bare(tool, UNKNOWN, home))
            continue
        if not written:
            receipts.append(_nothing_written(tool, outcome, home))
            continue
        for record in written:
            receipts.append(_receipt_for(tool, record, rows))
    return receipts


def _receipt_for(tool: str, record: dict[str, Any], rows: dict[str, Any]) -> Receipt:
    kind, record_id = record.get("kind"), record.get("id")
    if kind not in READERS:
        logger.error("receipts: %s reported a record of unknown kind %r", tool, kind)
        return _bare(tool, UNKNOWN, ARTIFACT, record_id)
    found = rows.get(kind)
    if kind == ARTIFACT:
        if found is None:
            return Receipt(tool, UNKNOWN, record_id)
        if record_id in found:
            row = found[record_id]
            return Receipt(tool, SAVED, record_id, row["artifact_type"], row["created_at"])
        logger.error("receipts: %s reported artifact %s, and no such row exists", tool, record_id)
        return Receipt(tool, UNKNOWN, record_id)

    # note_proposal: the row's CURRENT status is the whole answer.
    if found is None or record_id not in found:
        if found is not None:
            logger.error("receipts: %s reported proposal %s, and no such row exists",
                         tool, record_id)
        return _bare(tool, UNKNOWN, kind, record_id)
    row = found[record_id]
    outcome = _PROPOSAL_STATUS.get(row["status"])
    if outcome is None:
        logger.error("receipts: proposal %s has an unrecognised status %r", record_id,
                     row["status"])
        return _bare(tool, UNKNOWN, kind, record_id)
    return Receipt(tool, outcome, kind=kind, record_id=record_id, status=row["status"],
                   text=PROPOSAL_TEXTS[outcome], created_at=row["created_at"])
