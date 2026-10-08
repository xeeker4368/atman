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
``docs/DECISIONS.md`` decision #20 keeps capability gating and data visibility separate.
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

from program import config
from program.attribution import AttributionContext
from program.engine import earlier_tools, loop, turn_locks
from program.engine import situation as situation_block

# Re-exported deliberately: a caller catches one name from this module beside the
# other turn-level errors, rather than importing the lock registry to name it.
from program.engine.turn_locks import TurnAlreadyRunning  # noqa: F401
from program.integrity import corrections, gate
from program.memory import db, retrieval
from program.memory import notes as notes_db
from program.memory.retrieval import RetrievalResult
from program.origin import OriginContext
from program.settings.permissions import Actor
from program.tools import registry as tool_registry
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


class MessageTooLongError(ValueError):
    """A user message over ``chat.max_message_chars``. Refused before persisting.

    Merged-queue item 16 (plan B6a). The message was unbounded: it went into the
    append-only archive before generation, every word was embedded at idle-close,
    and ``history.select_history`` sends the newest message even when it alone
    overflows the window — where the model server truncates it without an error.
    """


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
    answer_trace: list | None = None,
) -> None:
    """Link anything this turn corrected. Never edits.

    Up to three classifier calls — the person against their own statements, the
    person against the entity's, the entity against its own (stage 3, D1) — over candidates
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
        # Three calls, one per (speaker, candidate role) pair — CO4 as amended at
        # stage 3 (D1). The third runs only when it is switched on (a ship gate, D6)
        # and the pool holds entity candidates: `classify` makes no call when
        # nothing is eligible.
        own = corrections.classify(
            user_text, user_message_id, pool, "user", actor.name)
        of_entity = corrections.classify(
            user_text, user_message_id, pool, "user", actor.name,
            candidate_role="assistant",
        ) if config.corrections_person_corrects_entity() else None
        # CO17, the symmetric skip: an answer that reports only empty searches ("There is no note
        # about X") is not judged against the entity's earlier claims. Measured: with such a new
        # message every one of 640 canonical samples linked a genuine earlier claim as superseded.
        self_correction = None if corrections.entries_report_nothing_found(answer_trace) else \
            corrections.classify(
                answer_text, assistant_message_id, pool, "assistant", "the system")
    except Exception as exc:  # noqa: BLE001 — a missed link, never a failed turn
        logger.warning(
            "correction classifier could not run for conversation %s; no link "
            "written: %s: %s", conversation_id[:8], type(exc).__name__, exc,
        )
        return

    judged = [own, of_entity, _unless_person_took_it(
        of_entity, self_correction, conversation_id)]
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


def _unless_person_took_it(
    person: corrections.Correction | None,
    entity: corrections.Correction | None,
    conversation_id: str,
) -> corrections.Correction | None:
    """D5: when the person and the entity both supersede the same entity message in
    one turn, only the person's link is written.

    The two are not redundant rows the schema would merge (``UNIQUE`` is on the
    pair, and the superseding messages differ), so without this rule the same echo
    would carry two corrections. The person's is kept because they are the source
    of the value. The entity's is **logged, not silently discarded** — both
    verdicts and every id — so a human reading the log later can see the judgment
    that was set aside. When the two target different messages, both are written.
    """
    if (person is None or entity is None
            or person.superseded_message_id != entity.superseded_message_id):
        return entity
    logger.info(
        "correction dropped in conversation %s (D5: the person's link wins): "
        "target %s; kept %s (%s, %r); dropped %s (%s, %r)",
        conversation_id[:8], entity.superseded_message_id,
        person.superseding_message_id, person.replacement, person.rationale,
        entity.superseding_message_id, entity.replacement, entity.rationale,
    )
    return None


def _offers_a_tool_that_takes_origin(registry: ToolRegistry | None) -> bool:
    """Whether any tool this turn will offer declares ``takes_origin``.

    Resolves the registry exactly as the loop does: the one passed in, or **the default when none
    is** (``loop.run_turn`` does the same). With Notes dark nothing declares it, so the origin is
    never built and a turn does none of its work: no read of the messages behind retrieved chunks.
    """
    active = registry if registry is not None else tool_registry.default_registry()
    return any(tool.takes_origin for tool in active)


def _build_origin(
    conversation_id: str,
    user_message_id: str,
    history,
    retrieved: RetrievalResult | None,
) -> OriginContext:
    """Which exchange this turn is, for a tool that declares it takes origin (Notes piece 2).

    ``context_message_ids`` is the conversation's own messages (a superset of what the window
    sends, since the loop's window holds normalised dicts with no ids) **plus the messages behind
    the passive retrieval's chunks**. It is built here, before the loop runs, so it **cannot hold
    what a ``memory_search`` call returns during the turn**: a recorded gap (N18), not an oversight.

    A failure reading the chunks' messages degrades to the conversation's alone, with a warning:
    this only widens a *preference* tier for quote resolution, so losing it must not fail a turn.
    """
    ids = {row["id"] for row in history}
    chunk_ids = [hit.chunk_id for hit in retrieved.results] if retrieved else []
    if chunk_ids:
        try:
            ids.update(row["id"] for row in db.get_messages_in_chunks(chunk_ids))
        except Exception as exc:  # noqa: BLE001 - degrades; it must never fail the turn
            logger.warning(
                "origin: could not read the messages behind %d retrieved chunk(s) (%s: %s); "
                "context_message_ids holds the conversation's messages only",
                len(chunk_ids), type(exc).__name__, exc,
            )
    return OriginContext(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        context_message_ids=frozenset(ids),
    )


def untrusted_context_by_call(
    trace, registry: ToolRegistry | None = None
) -> dict[str, list[str]]:
    """For each successful ``note_propose`` call in the trace, the **untrusted-output tools that
    returned text earlier in the same trace** (N7).

    The handler cannot see its turn's earlier calls (the origin is built before the loop and
    dispatch passes no trace), so the turn fills this in afterwards. A tool counts when it
    **returned** text (``outcome == "ok"``): a failed or timed-out call gave the entity nothing
    written outside the household to read. Order is the order of the trace, so a proposal made
    before a page was fetched in the same round is not flagged for it. Names are listed once, in
    the order first seen. ``[]`` is a real answer: *recorded, none*.

    "Untrusted" is what the registry **in use** declares (``Tool.untrusted_output``), resolved as
    the loop resolves it: those are the tools that actually ran. (``registry.untrusted_tools()``
    answers the same question from the full catalogue, for a stored trace whose tool has since
    been disabled.)
    """
    active = registry if registry is not None else tool_registry.default_registry()
    untrusted = {tool.name for tool in active if tool.untrusted_output}
    seen: list[str] = []
    found: dict[str, list[str]] = {}
    for entry in loop.call_entries(trace):
        tool = entry.get("tool")
        if tool == "note_propose" and entry.get("outcome") == "ok" and entry.get("call_id"):
            found[entry["call_id"]] = list(seen)
        if tool in untrusted and entry.get("outcome") == "ok" and tool not in seen:
            seen.append(tool)
    return found


def _record_untrusted_context(trace, registry: ToolRegistry | None) -> None:
    """Fill ``note_proposals.untrusted_context`` for this turn's proposals. NULL (not recorded)
    is what a proposal keeps if this fails, so a failure never reads as *none*. Does nothing, and
    reads nothing, in a turn that proposed no note."""
    if not any(e.get("tool") == "note_propose" for e in trace):
        return
    for call_id, tools in untrusted_context_by_call(trace, registry).items():
        notes_db.set_untrusted_context(call_id, tools)


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

    Raises ``ConversationAccessError``, ``EmptyMessageError``,
    ``MessageTooLongError``, ``TurnAlreadyRunning``, and whatever
    ``ollama`` raises when the model cannot be reached.

    **One turn at a time per conversation.** The guard is taken after the
    conversation is resolved — it needs the id — and before anything is written,
    so a refused second send leaves no message and makes no model call. A send
    with no ``conversation_id`` is never refused: the id does not exist until
    ``db.start_conversation`` returns, so two first sends simply produce two
    conversations.
    """
    content = (text or "").strip()
    if not content:
        raise EmptyMessageError("an empty message has nothing to answer.")
    limit = config.chat_max_message_chars()
    if len(content) > limit:
        # Before anything is written: the archive is append-only, so an over-long
        # message stored first could never be taken back out.
        raise MessageTooLongError(
            f"the message is {len(content):,} characters; the limit is {limit:,}. "
            f"Longer text can be uploaded as a file instead."
        )

    conversation_id, is_new = _resolve_conversation(actor, conversation_id)

    with turn_locks.turn(conversation_id):
        return _answer(
            actor,
            content,
            conversation_id,
            is_new=is_new,
            situation=situation,
            registry=registry,
        )


def _answer(
    actor: Actor,
    content: str,
    conversation_id: str,
    *,
    is_new: bool,
    situation: str | None,
    registry: ToolRegistry | None,
) -> TurnOutcome:
    """The turn itself, with the conversation already resolved and held.

    Split out of :func:`handle_user_message` so the guard reads as one line there
    rather than as an indented block around everything.
    """
    # Before generation. See the module docstring — this ordering is what makes
    # an in-flight turn distinguishable from a finished one.
    try:
        user_message_id = db.save_message(
            conversation_id, actor.user_id, "user", content)
    except db.ConversationClosed:
        # A close landed between resolving the conversation and writing into it.
        # Nothing has been generated yet, so this is simply the closed-conversation
        # path arriving a moment later: start a fresh one and answer there. One
        # attempt only — a brand new conversation cannot be closed, so a second
        # refusal would be a defect and is left to raise.
        conversation_id = db.start_conversation(actor.user_id)
        is_new = True
        logger.warning(
            "the conversation closed before this turn's question could be saved; "
            "continuing in %s", conversation_id[:8],
        )
        user_message_id = db.save_message(
            conversation_id, actor.user_id, "user", content)

    if situation is None:
        situation = _build_situation(actor, user_message_id)

    retrieved = _retrieve(content)
    history = db.get_conversation_messages(conversation_id)
    result = loop.run_turn(
        # Earlier turns carry a one-line system record of the tools they called (3.3, B);
        # the rows themselves stay as stored, for the origin below.
        earlier_tools.with_tool_records(history),
        situation,
        retrieved,
        registry=registry,
        # Q2b: the person present. They asked for the thing, so the record of it
        # belongs in their history. This is attribution, not authorization — the
        # actor's role is deliberately not carried across (see
        # `program/attribution.py`), so nothing downstream can gate on it.
        attribution=AttributionContext(user_id=actor.user_id),
        # Which exchange this is, for a tool that declares it takes origin. Built only when a
        # tool this turn offers declares it, so a turn with Notes dark does none of that work.
        # Passed through the loop unread; it reaches no tool built before Notes.
        origin=(
            _build_origin(conversation_id, user_message_id, history, retrieved)
            if _offers_a_tool_that_takes_origin(registry) else None
        ),
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
    # The gate reasons over tool calls; B21's window-event markers are not calls.
    verdict = gate.check(result.text, loop.call_entries(result.trace), situation)
    if not verdict.clean:
        logger.warning(
            "integrity gate: turn in conversation %s recorded as %s (%d finding(s): %s)",
            conversation_id[:8],
            verdict.status.value,
            len(verdict.findings),
            ", ".join(f.rule for f in verdict.findings) or verdict.semantic_error,
        )

    trace_json = json.dumps(result.trace) if result.trace else None
    try:
        assistant_message_id = db.save_message(
            conversation_id,
            actor.user_id,
            "assistant",
            result.text,
            tool_trace=trace_json,
        )
    except db.ConversationClosed:
        # The conversation closed while this turn was running (another process's
        # sweep, or the operator). The answer exists and the person is waiting for
        # it, so it is saved into a new conversation and the new id is reported —
        # the same contract the caller already handles for a conversation that was
        # closed before the turn began.
        #
        # The reply goes there ALONE. The question is already on record in the old
        # conversation, and chunked with it, and copying it would put a second
        # archive row under one utterance — a record of something that did not
        # happen. The cost is that the new conversation's first chunk is an answer
        # with no question in front of it, and that nothing links the two.
        closed_conversation_id = conversation_id
        conversation_id = db.start_conversation(actor.user_id)
        is_new = True
        logger.warning(
            "conversation %s closed while a turn was running; the reply is saved "
            "in %s, on its own",
            closed_conversation_id[:8], conversation_id[:8],
        )
        assistant_message_id = db.save_message(
            conversation_id,
            actor.user_id,
            "assistant",
            result.text,
            tool_trace=trace_json,
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

    with _after_durable("the untrusted context of note proposals", conversation_id):
        _record_untrusted_context(result.trace, registry)

    with _after_durable("correction links", conversation_id):
        _record_corrections(actor, conversation_id, retrieved, content,
                            user_message_id, result.text, assistant_message_id,
                            answer_trace=loop.call_entries(result.trace))

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
