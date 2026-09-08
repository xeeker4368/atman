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
3. Retrieve.
4. Run the loop.
5. Persist the assistant's message, with the turn's tool trace on it.

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
from dataclasses import dataclass, field
from typing import Any

from program.engine import loop
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


def handle_user_message(
    actor: Actor,
    text: str,
    conversation_id: str | None = None,
    *,
    situation: str = "",
    registry: ToolRegistry | None = None,
) -> TurnOutcome:
    """Take one message from a person and produce one answer.

    ``situation`` is the current-situation block — timestamp, elapsed time and
    its confabulation pairing. **It is empty until the Phase 1 task that builds
    it lands**, and it is a parameter rather than something assembled here so
    that task drops in without touching the loop. Passing an elapsed-time
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

    result = loop.run_turn(
        db.get_conversation_messages(conversation_id),
        situation,
        _retrieve(content),
        registry=registry,
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

    assistant_message_id = db.save_message(
        conversation_id,
        actor.user_id,
        "assistant",
        result.text,
        tool_trace=json.dumps(result.trace) if result.trace else None,
    )

    return TurnOutcome(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        content=result.text,
        trace=result.trace,
        iterations=result.iterations,
        stop_reason=result.stop_reason,
        new_conversation=is_new,
    )
