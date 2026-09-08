"""The chat endpoint. Task 2.2.

The HTTP shape around ``program/engine/turn.py``, which holds the substance —
the same split ``routes/auth.py`` has against ``program/auth.py``.

**This is the first authenticated route in the application.** ``require_actor``
turns the ``Authorization: Bearer`` header into an ``Actor`` built from a users
row, so the actor the turn records is proven rather than asserted. There is no
unauthenticated path to here and no ``Actor.operator()`` fallback: that sentinel
means *a human is at a shell on this machine*, which a request over the LAN is
not.

The idle sweep runs after the response, not during the turn
-----------------------------------------------------------
``idle.close_idle_conversations()`` is scheduled as a background task with the
active conversation excluded, so a sweep can never close the turn that
triggered it. Running it inline would put final chunking — an embedding call per
idle conversation — inside the user's wait, and would put an unbounded number of
them inside the in-flight-grace floor's arithmetic, since the floor has to cover
everything a turn can do. Deferring it keeps the floor derivable from the loop's
own limits.

The cost is that a sweep can now overlap the next turn's writes. That is the
write contention ``db.py``'s retry already handles and measures.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

from program.api.routes.auth import CurrentActor
from program.engine import ollama, turn
from program.memory import idle
from program.settings.permissions import Actor

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    #: Omit to start a new conversation. A conversation that has been closed
    #: cannot be continued; a new one is started and its id comes back with
    #: ``new_conversation`` set.
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    content: str
    iterations: int
    stop_reason: str
    new_conversation: bool
    #: The turn's tool-call trace, exactly as task 3.1 will read it. Returned
    #: rather than logged: it is what the Phase 8 interface renders as
    #: tool-call activity, and a first-class value in both places.
    trace: list[dict] = Field(default_factory=list)


def _sweep(conversation_id: str) -> None:
    """Close idle conversations, after the response has gone out.

    Failures are logged, never raised: this runs after the turn the user was
    waiting for has already succeeded, and ``IdleCloseError`` here would only
    surface as an unhandled background exception. The conversations it failed
    to close stay open and are retried on the next sweep.
    """
    try:
        result = idle.close_idle_conversations(exclude_conversation_id=conversation_id)
        if result.closed:
            logger.info(
                "post-turn sweep closed %d conversation(s), chunked %d",
                result.closed,
                result.chunked,
            )
    except Exception as exc:  # noqa: BLE001 - background, logged not raised
        logger.warning("post-turn idle sweep failed: %s", exc)


@router.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    background: BackgroundTasks,
    actor: Actor = CurrentActor,
) -> ChatResponse:
    """One turn: the user's message in, the entity's answer out."""
    try:
        outcome = turn.handle_user_message(
            actor, payload.message, payload.conversation_id
        )
    except turn.ConversationAccessError:
        # 404 rather than 403: a 403 would confirm the conversation exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
        ) from None
    except turn.EmptyMessageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from None
    except ollama.OllamaError as exc:
        # The specific exception carries what to check — an unreachable host, a
        # model that is not pulled, a timeout. Reported rather than flattened
        # into "something went wrong", because the operator can act on it.
        logger.error("chat turn failed against the model: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from None

    background.add_task(_sweep, outcome.conversation_id)

    return ChatResponse(
        conversation_id=outcome.conversation_id,
        message_id=outcome.assistant_message_id,
        content=outcome.content,
        iterations=outcome.iterations,
        stop_reason=outcome.stop_reason,
        new_conversation=outcome.new_conversation,
        trace=outcome.trace,
    )
