"""Which exchange produced a write. Notes piece 2 (`docs/NOTES_BUILD_PLAN.md`).

Design of record: ``docs/NOTES_DESIGN.md`` N3, option (b).

Attribution (``program/attribution.py``) answers *whose record a write belongs to*. This answers
a different question, *which exchange produced it*: the conversation, the message that triggered
the turn, the call that made it, and the messages the entity could have drawn on. A note proposal
stores it so a reviewer sees what prompted the proposal, and so a quote the entity gives as
evidence can be resolved to a real message (N4) rather than taken on trust.

**It is not an ``Actor`` and not an ``AttributionContext``.** It carries no user, no role and no
permission, so nothing in the tool path can authorize anything on it, and ``AttributionContext``
keeps its single field. Only a tool that declares ``Tool.takes_origin`` receives it, with the
same four guards attribution has (declared; never model-settable; never in the recorded arguments
or trace; passed through the loop unread). ``dispatch`` fills ``call_id``, because only dispatch
creates it.

``context_message_ids`` is what ``turn.py`` can name **before the loop runs**: the conversation's
own messages, and the messages behind the passive retrieval's chunks. **It does not include
anything a ``memory_search`` call surfaces during the turn** (a recorded gap, N18): the origin is
built before any tool runs, and the loop passes it through unread.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OriginContext:
    conversation_id: str
    #: The message that triggered this turn, already saved before generation began.
    user_message_id: str
    #: Messages the entity could have drawn on this turn. A frozenset, so the context is
    #: hashable and cannot be altered by the tool that receives it.
    context_message_ids: frozenset[str] = frozenset()
    #: Filled by ``registry.dispatch`` (only dispatch creates it); ``None`` until then.
    call_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("conversation_id", "user_message_id"):
            value = getattr(self, name)
            if not value or not str(value).strip():
                raise ValueError(
                    f"OriginContext needs a real {name}. An origin that names no exchange "
                    f"cannot say what produced a write, and would fail later, at the write, "
                    f"where the caller is no longer visible."
                )
