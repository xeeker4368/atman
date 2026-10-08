"""Read routes for the chat page: who is logged in, their conversations, and one conversation.

All three sit behind ``require_actor``, and none writes anything. **Each person sees only their own
conversations here.** That is about which conversations the page lists and opens. It is separate
from retrieval, which stays unfiltered by who is asking (``docs/DECISIONS.md`` #20 and #26) and is
not touched here.

An unknown conversation and another user's conversation give the **same** 404, with the same
detail ``POST /api/chat`` uses, so the page cannot be used to learn that a conversation exists.

A past assistant turn's receipts are rebuilt from its stored ``tool_trace`` with
``receipts.for_trace()``, exactly as ``POST /api/chat`` builds them for a live turn (O23, F50), so
a reply looks the same on the page whether it was just sent or reopened later.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from program.api import trace_view
from program.api.routes.auth import CurrentActor
from program.memory import db
from program.settings.permissions import Actor
from program.tools import receipts

logger = logging.getLogger(__name__)

router = APIRouter()


class Me(BaseModel):
    name: str
    #: Read by the page to choose whether to show the trace panel. That is a display choice
    #: and protects nothing: every person's own trace already comes back from POST /api/chat.
    role: str


class ConversationSummary(BaseModel):
    id: str
    started_at: str
    ended_at: str | None
    #: The first user message, cut in SQL (``db.CONVERSATION_PREVIEW_CHARS``). Display only.
    preview: str | None
    preview_cut: bool


class StoredMessage(BaseModel):
    id: str
    role: str
    content: str
    #: ISO-8601 UTC, as stored.
    timestamp: str
    #: Assistant rows only; NULL for a user row. ``[]`` means no side-effect tool was
    #: called; NULL on an assistant row means its stored trace could not be read, so
    #: whether one was called is unknown (never shown as "none").
    receipts: list[dict] | None = None
    #: The stored trace, parsed, with the entity's own writing replaced by its length, as
    #: POST /api/chat returns it (``trace_view``). NULL when no tool was called, or when it
    #: could not be read.
    trace: list[dict] | None = None


class ConversationDetail(BaseModel):
    id: str
    started_at: str
    ended_at: str | None
    messages: list[StoredMessage]


def _not_found() -> HTTPException:
    # Word for word what POST /api/chat says, for an unknown and an unowned id alike.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")


def _trace_and_receipts(
    message_id: str, raw: str | None
) -> tuple[list[dict] | None, list[dict] | None]:
    """The parsed trace and its rebuilt receipts, for one assistant row.

    A NULL trace means no tool was called: no trace, no receipts. A trace that cannot be read
    is logged and comes back as NULL for both, never as a 500 and never as ``[]`` receipts,
    since an empty list would claim no side-effect tool ran.
    """
    if raw is None:
        return None, []
    try:
        trace: Any = json.loads(raw)
        if not isinstance(trace, list) or not all(isinstance(e, dict) for e in trace):
            raise ValueError("the stored trace is not a list of entries")
        rebuilt = [r.to_dict() for r in receipts.for_trace(trace)]
        return trace_view.for_response(trace), rebuilt
    except Exception as exc:  # noqa: BLE001 - one unreadable row must not fail the page
        logger.warning("conversation view: the stored trace of message %s could not be read "
                       "(%s: %s); shown without trace or receipts",
                       message_id[:8], type(exc).__name__, exc)
        return None, None


@router.get("/api/me", response_model=Me)
def me(actor: Actor = CurrentActor) -> Me:
    """The logged-in person's name and role."""
    return Me(name=actor.name, role=actor.role.value)


@router.get("/api/conversations", response_model=list[ConversationSummary])
def conversations(actor: Actor = CurrentActor) -> list[ConversationSummary]:
    """The caller's own conversations, newest first, open and closed."""
    return [
        ConversationSummary(
            id=row["id"], started_at=row["started_at"], ended_at=row["ended_at"],
            preview=row["preview"], preview_cut=bool(row["preview_cut"]),
        )
        for row in db.list_user_conversations(actor.user_id)
    ]


@router.get("/api/conversations/{conversation_id}/messages", response_model=ConversationDetail)
def conversation_messages(
    conversation_id: str, actor: Actor = CurrentActor
) -> ConversationDetail:
    """One of the caller's conversations, its messages in order."""
    conversation = db.get_conversation(conversation_id)
    if conversation is None or conversation["user_id"] != actor.user_id:
        raise _not_found()

    messages = []
    for row in db.get_conversation_messages(conversation_id):
        item = StoredMessage(id=row["id"], role=row["role"], content=row["content"],
                             timestamp=row["timestamp"])
        if row["role"] == "assistant":
            item.trace, item.receipts = _trace_and_receipts(row["id"], row["tool_trace"])
        messages.append(item)
    return ConversationDetail(
        id=conversation["id"], started_at=conversation["started_at"],
        ended_at=conversation["ended_at"], messages=messages,
    )
