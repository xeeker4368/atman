"""One conversational turn, end to end: persist, retrieve, run the loop,
persist. Task 2.2.

``loop.py`` is the model-and-tools half and touches no store. This module is
the half that touches everything else — conversations, messages, retrieval, the
tool trace — and it is where task 2.2's obligations about ordering live. The
HTTP shape is ``program/api/routes/chat.py``; nothing here imports FastAPI, for
the reason ``program/auth.py`` does not either: the substance should be testable
without a request.

The order is the correctness constraint
---------------------------------------
1. Resolve the conversation and check it belongs to this actor.
2. **Persist the user's message.**
3. Build the current-situation block.
4. Retrieve.
5. Run the loop.
6. **Run the fabrication gate** over the answer.
7. Persist the assistant's message, with the turn's tool trace and the gate's
   verdict on it.

Step 2 happens **before** step 4, and that ordering is obligation (b) of this
task rather than a preference. ``idle.py`` decides which of its two windows
applies by reading whether a conversation's last message came from the user or
the assistant: a user message with no reply means a turn may be in flight, and
gets the long grace window. Buffering both messages and writing them together
at the end would make a turn that is still generating look like a turn that
finished, and the short window would then apply to a conversation the model was
still answering. It is crash-safety too, but the correctness argument is the
one that decides it.

The visible consequence is that a turn which fails midway leaves a user message
with no reply. That is not a defect to clean up — it is an accurate record of
what happened, and the grace window is what covers it.

**Step 3 depends on step 2 having already happened**, and that is a trap rather
than a convenience. The message being answered is on record by the time the
situation block is built, so it is the most recent thing this person said —
measuring the gap without excluding it would report roughly zero every turn,
forever, plausibly and wrongly. Its id is passed as ``exclude_message_id``, which
makes the exclusion explicit and testable instead of an ordering coincidence.

The gate runs before the save, not after
----------------------------------------
Step 6 sits between generation and persistence deliberately (design F3). A
classification call measured **0.45 s warm** against a turn that runs 5–20
seconds, and running it here means the verdict exists before the answer becomes a
permanent record. Run it after step 7 and the only possible response to a
fabrication is to annotate something already said and already read.

**Stage 1 is flag-only**: the verdict is recorded and nothing about the answer
changes. See ``program/integrity/gate.py`` for why that is the shipping
behaviour rather than a placeholder.

**The gate never fails the turn.** An unreachable classifier produces a verdict
of ``unavailable`` — recorded as such, never as clean — and the answer is
returned. A checker being down is not a reason to withhold an answer that was
already generated.

Nothing after the answer is durable may fail the turn
-----------------------------------------------------
Once ``save_message`` has returned for the assistant's message, the answer is in
both stores and the turn has succeeded. Everything after that point is
bookkeeping *about* a turn that already happened — a verdict to file, an advisory
note, a correction link — and none of it is the answer.

Letting one of them raise turned a completed turn into an unhandled 500: the
route catches ``ConversationAccessError``, ``EmptyMessageError`` and
``OllamaError`` and nothing else, so a ``database is locked`` from a link write
past its retry deadline reached FastAPI as a server error. The person never saw a
reply that was sitting in the database, and the next turn's history contained an
answer they were never shown. Measured reachable: with a writer racing a backup
snapshot, ``database is locked`` fired 2 of 5 times, which is the contention the
background idle sweep and ``backup.py`` already produce.

``_after_durable`` is where that invariant lives, and it is deliberately the same
shape the gate uses for its own failure — log it, record nothing rather than
something false, return the answer.

Who the actor is
----------------
Obligation (c): the ``Actor`` comes from ``require_actor`` in the route, which
built it from a verified session token via ``db.get_actor()``.
``Actor.operator()`` is never constructed here. That sentinel is an
always-allowed unauthenticated path, correct only while nothing untrusted can
reach this code — which stops being true the moment a chat endpoint exists.

**No capability is registered for chat**, deliberately, on the rule
``permissions.py`` states: only capabilities something actually enforces get
registered, and an unregistered name raises rather than defaulting permissive.
What this module enforces is not a capability but **ownership** — a user may
only speak into their own conversation. That is a different axis, the same way
``NOW.md`` decision #20 keeps capability gating and data visibility separate.
Retrieval is deliberately *not* filtered by actor: see that decision.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from program.attribution import AttributionContext
from program.engine import loop
from program.engine import situation as situation_block
from program.integrity import corrections, gate
from program.memory import db, retrieval
from program.memory.retrieval import RetrievalResult
from program.settings.permissions import Actor
from program.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ConversationAccessError(PermissionError):
    """A conversation that does not exist, or does not belong to this actor.

    One exception for both cases, so a caller cannot use the difference to
    discover which conversation ids exist — the same reasoning as
    ``AUTH_DESIGN.md``'s single 401 (A8).
    """


class EmptyMessageError(ValueError):
    """An empty user message. Nothing to persist and nothing to answer."""


@dataclass(frozen=True)
class TurnOutcome:
    """What the turn did, for the route to render."""

    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    content: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    stop_reason: str = loop.ANSWERED
    #: True when the conversation the caller named could not be continued and a
    #: fresh one was started. The caller's own id is then stale, so this is
    #: reported rather than left to be noticed.
    new_conversation: bool = False
    #: The fabrication gate's verdict. Carried so the route and the eval harness
    #: can read it without re-querying; stage 1 does not act on it.
    integrity: gate.GateVerdict | None = None


def _resolve_conversation(actor: Actor, conversation_id: str | None) -> tuple[str, bool]:
    """Return ``(conversation_id, is_new)``.

    A **closed** conversation starts a new one rather than being reopened.
    Closing is what triggers final chunking and sets ``chunked``; appending to a
    conversation that has already been chunked in full would leave the appended
    turns indexed by nothing, which is precisely the state idle-close exists to
    prevent. The new id comes back in the response, so the client is told rather
    than silently redirected.

    An **unowned or unknown** conversation raises. Starting a fresh one instead
    would quietly turn "post into someone else's conversation" into a success.
    """
    if conversation_id is None:
        return db.start_conversation(actor.user_id), True

    row = db.get_conversation(conversation_id)
    if row is None or row["user_id"] != actor.user_id:
        raise ConversationAccessError(
            f"no conversation {conversation_id!r} belonging to {actor.name}."
        )
    if row["ended_at"] is not None:
        logger.info(
            "conversation %s is closed; starting a new one for %s",
            conversation_id[:8],
            actor.name,
        )
        return db.start_conversation(actor.user_id), True
    return conversation_id, False


def _retrieve(query: str) -> RetrievalResult | None:
    """Hybrid retrieval for this turn, or ``None`` if it could not run.

    **Degrades rather than aborts**, on the criterion recorded in ``prompt.py``:
    a person is waiting and nothing can be corrupted by answering without
    retrieved records. ``retrieval.search()`` already survives one leg being
    down on its own; this covers the case where the whole call fails, which
    would otherwise take a turn the model could still have answered from
    history.
    """
    try:
        return retrieval.search(query)
    except Exception as exc:  # noqa: BLE001 - degraded deliberately, see docstring
        logger.warning("retrieval failed for this turn, continuing without: %s", exc)
        return None


@contextmanager
def _after_durable(step: str, conversation_id: str) -> Iterator[None]:
    """A step that runs after the answer is durable must never fail the turn.

    ``step`` names what was being recorded, for the log line — the operator needs
    to know *which* piece of bookkeeping was lost, since each has a different
    consequence and none of them is the answer.

    **What is lost, per step, stated rather than left to be worked out.**
    The integrity verdict: ``messages.integrity_check`` stays ``NULL``, which
    ``gate.py`` defines as *no verdict recorded*, never as clean — so the record
    stays honest and the turn is simply unchecked. The advisory note: a
    non-authoritative signal is missing; it could never change a verdict. A
    correction link: a correction is missed, which leaves the record accurate and
    merely uncorrected — the same direction of failure ``corrections.py`` already
    chose when it decided never to link by default.

    Every one of those is better than the alternative, which is what this replaces:
    the person gets a 500 for a turn that succeeded and never sees an answer that
    is already in both stores.

    ``except Exception``, never ``BaseException`` — ``KeyboardInterrupt``,
    ``SystemExit`` and the test suite's ``StoreIsolationViolation`` must still
    propagate. That is the same line ``registry.dispatch`` draws, for the same
    reason.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - never past a durable answer
        logger.warning(
            "%s could not be recorded for conversation %s; the answer was already "
            "saved and is returned unchanged: %s: %s",
            step, conversation_id[:8], type(exc).__name__, exc,
        )


def _record_corrections(
    actor: Actor,
    conversation_id: str,
    retrieved,
    user_text: str,
    user_message_id: str,
    answer_text: str,
    assistant_message_id: str,
) -> None:
    """Link anything this turn corrected. Never edits.

    One classifier call per speaker who said something this turn, over candidates
    drawn from the turn's own retrieval plus the open trailing group — see
    ``docs/CORRECTION_DESIGN.md`` C4.

    **A classifier failure is not a turn failure.** The answer has already been
    generated and saved; losing a link is a missed correction, which leaves the
    record accurate and merely uncorrected. Same shape as the fabrication gate
    declining to take a turn down with it.

    **The "never raises" half of that promise is the caller's**, and it used to be
    nobody's. The ``try`` below covers assembling and classifying; the write loop
    after it does not, and ``corrections.record()`` catches only
    ``sqlite3.IntegrityError`` — an expected schema refusal — while
    ``db.create_supersedes_link`` can still raise ``OperationalError`` past its
    retry deadline. That reached FastAPI as a 500 on a turn whose answer was
    already saved. The guarantee now lives in ``_after_durable`` at the call site,
    once, rather than being claimed here and enforced nowhere.
    """
    chunk_ids = [hit.chunk_id for hit in retrieved.results] if retrieved else []
    try:
        pool = corrections.candidates(
            actor.user_id,
            conversation_id,
            chunk_ids,
            exclude_message_ids=(user_message_id, assistant_message_id),
            user_name=actor.name,
        )
        judged = [
            corrections.classify(user_text, user_message_id, pool, "user", actor.name),
            corrections.classify(
                answer_text, assistant_message_id, pool, "assistant", "the system"),
        ]
    except Exception as exc:  # noqa: BLE001 — a missed link, never a failed turn
        logger.warning(
            "correction classifier could not run for conversation %s; no link "
            "written: %s: %s", conversation_id[:8], type(exc).__name__, exc,
        )
        return

    for correction in judged:
        if correction is None:
            continue
        if corrections.record(correction) is not None:
            logger.info(
                "correction recorded in conversation %s: %s supersedes %s",
                conversation_id[:8],
                correction.superseding_message_id[:8],
                correction.superseded_message_id[:8],
            )


def _build_situation(actor: Actor, user_message_id: str) -> str:
    """The current-situation block for this turn (decision #5).

    ``user_message_id`` is excluded from the lookup because it has already been
    persisted — see the module docstring. The figure is scoped to **this
    actor's** own messages across every conversation: it is the entity's sense of
    time with the person in front of it, not a question about what anyone else
    has been doing, and not conversation-scoped (idle-close would then
    manufacture a "first message" on nearly every session).
    """
    previous = db.get_previous_user_message_time(actor.user_id, user_message_id)
    return situation_block.build_situation(
        now=datetime.now(timezone.utc),
        previous_message_at=datetime.fromisoformat(previous) if previous else None,
        speaker=actor.name,
    )


def handle_user_message(
    actor: Actor,
    text: str,
    conversation_id: str | None = None,
    *,
    situation: str | None = None,
    registry: ToolRegistry | None = None,
) -> TurnOutcome:
    """Take one message from a person and produce one answer.

    ``situation`` is the current-situation block — timestamp, elapsed time and
    its confabulation pairing. It is **built here when not supplied**; passing a
    string overrides that, and passing ``""`` deliberately sends no block at all,
    which is what most tests of other behaviour want. Passing an elapsed-time
    statement without its pairing raises in ``prompt.build_system_prompt()``
    rather than reaching the model.

    Raises ``ConversationAccessError``, ``EmptyMessageError``, and whatever
    ``ollama`` raises when the model cannot be reached.
    """
    content = (text or "").strip()
    if not content:
        raise EmptyMessageError("an empty message has nothing to answer.")

    conversation_id, is_new = _resolve_conversation(actor, conversation_id)

    # Before generation. See the module docstring — this ordering is what makes
    # an in-flight turn distinguishable from a finished one.
    user_message_id = db.save_message(conversation_id, actor.user_id, "user", content)

    if situation is None:
        situation = _build_situation(actor, user_message_id)

    retrieved = _retrieve(content)
    result = loop.run_turn(
        db.get_conversation_messages(conversation_id),
        situation,
        retrieved,
        registry=registry,
        # Q2b: the person present. They asked for the thing, so the record of it
        # belongs in their history. This is attribution, not authorization — the
        # actor's role is deliberately not carried across (see
        # `program/attribution.py`), so nothing downstream can gate on it.
        attribution=AttributionContext(user_id=actor.user_id),
    )

    if not result.text.strip():
        # Visible rather than papered over: an empty answer is a real event
        # (usually a model that emitted only tool calls), and inventing text
        # here would be the system speaking in the entity's voice.
        logger.warning(
            "turn in conversation %s produced no text after %d iteration(s) "
            "(stop reason: %s)",
            conversation_id[:8],
            result.iterations,
            result.stop_reason,
        )

    # Before the save. Both users go through this identically — see the gate's
    # own docstring on why fabrication is not a permissions question.
    verdict = gate.check(result.text, result.trace, situation)
    if not verdict.clean:
        logger.warning(
            "integrity gate: turn in conversation %s recorded as %s (%d finding(s): %s)",
            conversation_id[:8],
            verdict.status.value,
            len(verdict.findings),
            ", ".join(f.rule for f in verdict.findings) or verdict.semantic_error,
        )

    assistant_message_id = db.save_message(
        conversation_id,
        actor.user_id,
        "assistant",
        result.text,
        tool_trace=json.dumps(result.trace) if result.trace else None,
    )

    # --- the answer is durable from here. Nothing below may fail the turn. ---

    with _after_durable("the integrity verdict", conversation_id):
        db.set_message_integrity_check(assistant_message_id, verdict.to_json())
    if verdict.advisory:
        # A record, never a verdict: this column cannot change whether the turn
        # was flagged. See docs/FABRICATION_GATE_DESIGN.md revision 7.
        with _after_durable("the integrity advisory", conversation_id):
            db.set_message_integrity_advisory(
                assistant_message_id, verdict.advisory_json())
            logger.info(
                "integrity gate: %d advisory note(s) on a turn the rules did not "
                "flag (conversation %s)",
                len(verdict.advisory), conversation_id[:8],
            )

    with _after_durable("correction links", conversation_id):
        _record_corrections(actor, conversation_id, retrieved, content,
                            user_message_id, result.text, assistant_message_id)

    return TurnOutcome(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        content=result.text,
        trace=result.trace,
        iterations=result.iterations,
        stop_reason=result.stop_reason,
        new_conversation=is_new,
        integrity=verdict,
    )
