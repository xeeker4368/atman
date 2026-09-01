"""Closing conversations that have gone quiet.

A conversation with no new message for its idle window is closed, which runs
final chunking and sets ``chunked``. This is not housekeeping — it is
load-bearing for retrieval. Chunking deliberately never indexes the open
trailing group, so a conversation that is never closed leaves its last turns
permanently unretrievable from anywhere except itself.

**Idle is measured from the last message's timestamp**, never from request
activity. A conversation open for three days with a message ten minutes ago is
not idle; one opened ten minutes ago whose only message was nine minutes ago
nearly is.

Two windows
-----------
Which applies depends on whether the last message came from the assistant or
the user:

* **Last message from the assistant** — the turn completed and nothing is in
  flight. ``idle_close_minutes`` (15). No correctness floor: closing early only
  fragments a conversation someone paused in the middle of.
* **Last message from the user** — a turn may be running, or the process died
  after the user spoke. Both look identical, so both get
  ``in_flight_grace_minutes`` (30), floored at 20.

The floor is the correctness constraint. A worst-case turn on this hardware runs
into minutes — see ``config/defaults.toml`` for the measurements — and a window
below it closes conversations while the model is still answering them.

This distinction depends on the chat route persisting the user's message
*before* generation starts. Recorded against task 2.2 in ``BUILD_PLAN.md``.

Lazy, not scheduled
-------------------
No daemon, no timer. ``close_idle_conversations()`` is called by whoever is
already doing work. Conversation state only changes when a message arrives, so a
timer would mostly wake to find nothing changed.

**Nothing calls this automatically today.** There is no chat endpoint yet; the
per-request sweep arrives with task 2.2. Until then the callers are
``scripts/close_idle_conversations.py`` and the tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from anam import config
from anam.memory import chunking, db

logger = logging.getLogger(__name__)

ASSISTANT = "assistant"


class IdleCloseError(RuntimeError):
    """One or more conversations failed to close during a sweep.

    Raised at the *end* of the sweep, carrying every failure. A single
    unreachable-model failure must not stop every other idle conversation from
    closing, but it must not pass silently either.
    """


@dataclass
class IdleCloseResult:
    """What a sweep did.

    ``closed`` and ``chunked`` are separate counts because they can differ: a
    conversation is closed first and chunked second, so a chunking failure
    leaves it closed-but-unchunked, waiting in the recovery queue.
    """

    examined: int = 0
    closed: int = 0
    chunked: int = 0
    skipped_active: int = 0
    closed_ids: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)


def _parse(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _window_for(last_role: str | None) -> timedelta:
    """How long this conversation must be quiet before it can close.

    A conversation with no messages at all (``last_role`` is None) takes the
    completed-turn window: there is no turn in flight and nothing to chunk.
    """
    if last_role == ASSISTANT or last_role is None:
        return timedelta(minutes=config.idle_close_minutes())
    return timedelta(minutes=config.in_flight_grace_minutes())


def find_idle_conversations(
    now: datetime | None = None,
    exclude_conversation_id: str | None = None,
) -> list[tuple[str, str]]:
    """Open conversations past their window. Returns ``(id, reason)`` pairs."""
    now = now or datetime.now(timezone.utc)
    idle: list[tuple[str, str]] = []

    for row in db.get_open_conversations_with_activity():
        if exclude_conversation_id and row["id"] == exclude_conversation_id:
            continue
        window = _window_for(row["last_role"])
        quiet_for = now - _parse(row["last_message_at"])
        if quiet_for >= window:
            kind = "in-flight grace" if row["last_role"] not in (ASSISTANT, None) else "idle"
            idle.append(
                (
                    row["id"],
                    f"{kind}: quiet {quiet_for.total_seconds() / 60:.1f}m "
                    f"of {window.total_seconds() / 60:.0f}m",
                )
            )
    return idle


def close_idle_conversations(
    now: datetime | None = None,
    exclude_conversation_id: str | None = None,
    dry_run: bool = False,
) -> IdleCloseResult:
    """Close every conversation past its idle window.

    ``exclude_conversation_id`` is the conversation the caller is currently
    using. Task 2.2 passes the active one, so a sweep can never close the turn
    that triggered it.

    **Ordering matters:** ``ended_at`` is set first, then chunking runs. If
    chunking fails the conversation is still closed, ``chunked`` stays 0, and it
    appears in ``db.get_unchunked_ended_conversations()`` for a later retry. The
    reverse order would leave a chunked-but-open conversation, a state nothing
    else in the system expects.

    **Errors do not abort the sweep**, deliberately — unlike the chunking
    pipeline, where a failure means stop. One unreachable model should not
    prevent every other idle conversation from closing. Failures are collected
    and raised together at the end, so they are visible without being fatal
    mid-sweep.
    """
    now = now or datetime.now(timezone.utc)
    result = IdleCloseResult()

    candidates = find_idle_conversations(now, exclude_conversation_id)
    result.examined = len(candidates)
    if exclude_conversation_id:
        result.skipped_active = 1

    if dry_run:
        result.closed_ids = [cid for cid, _ in candidates]
        return result

    for conversation_id, reason in candidates:
        try:
            db.end_conversation(conversation_id)
            result.closed += 1
            result.closed_ids.append(conversation_id)
            logger.info("Closed conversation %s (%s)", conversation_id[:8], reason)
        except Exception as exc:  # noqa: BLE001 - collected and re-raised below
            result.failures.append((conversation_id, f"close failed: {exc}"))
            continue

        try:
            chunking.finalise_conversation(conversation_id)
            result.chunked += 1
        except Exception as exc:  # noqa: BLE001 - collected and re-raised below
            # Closed but unchunked. Recoverable, and visible in the recovery
            # queue; the sweep continues to the next conversation.
            result.failures.append((conversation_id, f"chunking failed: {exc}"))
            logger.warning(
                "Closed %s but final chunking failed: %s", conversation_id[:8], exc
            )

    if result.failures:
        detail = "\n".join(f"  - {cid[:8]}: {msg}" for cid, msg in result.failures)
        raise IdleCloseError(
            f"{len(result.failures)} of {result.examined} conversation(s) failed "
            f"during the sweep; {result.closed} closed, {result.chunked} chunked:\n"
            f"{detail}"
        )

    return result
