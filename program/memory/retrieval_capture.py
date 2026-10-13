"""What retrieval put into each assistant turn's prompt, recorded for review (decision #33).

Two tables from migration 9: ``turn_retrieval`` (one row per turn) and
``turn_retrieved_records`` (one row per ranked hit). :func:`record` is called by
``program/engine/turn.py`` after the assistant message is saved, in its own transaction
through ``db.transaction()``, like the gate's verdict, and retried on a lock error by
``db.retry_on_locked`` as ``notes.py``'s writers are. It changes nothing in ``db.py``.

**Write-only at runtime.** Nothing in ``program/`` reads these tables; :func:`read` exists
for the tests, and ``scripts/show_retrieval.py`` reads the store directly. A test scans
``program/`` for the table names, so a reader that would let the prompt depend on the
capture fails the suite.

**Captured-empty is not uncaptured.** A turn whose retrieval returned nothing has a row with
``outcome = 'empty'``; a turn whose capture failed after the message was saved has no row,
and the ``turn_capture_status`` view lists it as ``not_captured`` (migration 9).

What is recorded is the **final** prompt of the turn, the one that produced the reply. A
turn with tool calls assembles its prompt once per call, and the records can shrink between
calls (B21); the final assembly is the one the reply answered from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from program.engine.prompt import RenderedRecords
from program.memory import db

if TYPE_CHECKING:
    from program.memory.retrieval import RetrievalResult


def outcome_of(retrieved: RetrievalResult | None) -> str:
    """``not_run`` (retrieval did not run or failed), ``empty`` or ``records``."""
    if retrieved is None:
        return "not_run"
    return "records" if retrieved.results else "empty"


@db.retry_on_locked
def record(message_id: str, records: RenderedRecords | None,
           retrieved: RetrievalResult | None) -> None:
    """Write the capture for one assistant message, both tables in one transaction.

    ``records`` is the final assembled prompt's account (``AssembledPrompt.records``), or
    ``None`` when the turn assembled no prompt. ``retrieved`` is the turn's retrieval
    result, or ``None`` when retrieval did not run or failed; it gives the outcome and
    whether each search ran.
    """
    outcome = outcome_of(retrieved)
    records = records if records is not None and outcome == "records" else RenderedRecords(
        text="")
    legs: tuple[Any, ...] = (None, None, None, None)
    if retrieved is not None:
        legs = (int(retrieved.lexical.ran), retrieved.lexical.skip_reason,
                int(retrieved.vector.ran), retrieved.vector.skip_reason)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO turn_retrieval (message_id, outcome, lexical_ran, lexical_skip_reason, "
            "vector_ran, vector_skip_reason, records, block_chars, continuations_withheld, "
            "hits_left_out, truncated, captured_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, outcome, *legs, len(records.records), len(records.text),
             records.continuations_withheld, records.hits_left_out,
             int(records.truncated), db.now_iso()),
        )
        conn.executemany(
            "INSERT INTO turn_retrieved_records (message_id, rank, chunk_id, source_type, "
            "label, score, lexical_rank, vector_rank, rendered_chars, continuations_sent, "
            "continuations_withheld) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(message_id, r.rank, r.chunk_id, r.source_type, r.label, r.score,
              r.lexical_rank, r.vector_rank, r.rendered_chars, r.continuations_sent,
              r.continuations_withheld)
             for r in records.records],
        )


def read(message_id: str, conn=None) -> dict[str, Any] | None:
    """One turn's capture as ``{"turn": {...}, "records": [...]}``, or None if not captured.

    For the tests. Never called at runtime.
    """
    def _query(c) -> dict[str, Any] | None:
        turn = c.execute("SELECT * FROM turn_retrieval WHERE message_id = ?",
                         (message_id,)).fetchone()
        if turn is None:
            return None
        rows = c.execute("SELECT * FROM turn_retrieved_records WHERE message_id = ? "
                         "ORDER BY rank", (message_id,)).fetchall()
        return {"turn": dict(turn), "records": [dict(r) for r in rows]}

    if conn is not None:
        return _query(conn)
    with db.connection() as own:
        return _query(own)
