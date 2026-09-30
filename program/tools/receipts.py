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
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from program.memory import db
from program.tools.registry import side_effect_tools

logger = logging.getLogger(__name__)

SAVED = "saved"
NOT_SAVED = "not_saved"
UNKNOWN = "unknown"

#: Outcomes where the handler was never entered, so nothing can have been written.
#: Decided without the store, and therefore certain even if the lookup fails.
_NEVER_RAN = frozenset({"skipped", "unknown_tool", "invalid_arguments"})


@dataclass(frozen=True)
class Receipt:
    tool: str
    outcome: str
    artifact_id: str | None = None
    #: From the row, the primary record, never from the tool name.
    artifact_type: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def for_trace(trace: Sequence[dict[str, Any]]) -> list[Receipt]:
    """One receipt per artifact a side-effect call wrote, or per call if none.

    Never raises for a store failure: a receipt that cannot be confirmed is
    ``unknown``, which is true, while raising would lose the answer the person is
    waiting for and omitting would claim nothing was attempted.
    """
    watched = set(side_effect_tools())
    calls = [entry for entry in trace if entry.get("tool") in watched]
    if not calls:
        return []

    ids = [i for entry in calls for i in (entry.get("artifact_ids") or ())]
    rows: dict[str, Any] | None
    try:
        rows = db.get_artifacts_by_ids(ids)
    except Exception as exc:  # noqa: BLE001 - degrades to `unknown`, never to silence
        logger.warning(
            "receipts: could not read the artifacts rows (%s: %s); every receipt "
            "that depends on them is recorded as unknown", type(exc).__name__, exc,
        )
        rows = None

    receipts: list[Receipt] = []
    for entry in calls:
        tool = str(entry.get("tool"))
        outcome = str(entry.get("outcome", ""))

        if outcome in _NEVER_RAN:
            receipts.append(Receipt(tool, NOT_SAVED))
            continue
        if "artifact_ids" not in entry:
            # Recorded before the key existed: the trace cannot say what was written.
            receipts.append(Receipt(tool, UNKNOWN))
            continue

        written = list(entry.get("artifact_ids") or ())
        if not written:
            if outcome == "tool_error":
                receipts.append(Receipt(tool, NOT_SAVED))
            elif outcome == "timeout":
                receipts.append(Receipt(tool, UNKNOWN))
            else:
                logger.error(
                    "receipts: %s reported %r with no artifact ids; a side-effect "
                    "call that succeeded must name what it wrote", tool, outcome,
                )
                receipts.append(Receipt(tool, UNKNOWN))
            continue

        for artifact_id in written:
            if rows is None:
                receipts.append(Receipt(tool, UNKNOWN, artifact_id))
            elif artifact_id in rows:
                row = rows[artifact_id]
                receipts.append(Receipt(
                    tool, SAVED, artifact_id, row["artifact_type"], row["created_at"]))
            else:
                logger.error(
                    "receipts: %s reported artifact %s, and no such row exists",
                    tool, artifact_id,
                )
                receipts.append(Receipt(tool, UNKNOWN, artifact_id))
    return receipts
