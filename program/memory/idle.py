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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from program import config
from program.memory import chunking, db

logger = logging.getLogger(__name__)

ASSISTANT = "assistant"


class IdleCloseError(RuntimeError):
    """One or more conversations failed to close during a sweep.

    Raised at the *end* of the sweep, carrying every failure. A single
    unreachable-model failure must not stop every other idle conversation from
    closing, but it must not pass silently either.
    """


@dataclass(frozen=True)
class IdleCandidate:
    """One conversation the sweep judged idle, and the snapshot it judged on.

    ``last_message_at`` is carried because the close is conditional on it: the
    sweep decides at one moment and closes at another, and a message arriving in
    between means the conversation is not idle after all.
    """

    conversation_id: str
    reason: str
    last_message_at: str


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
    #: Candidates that were not closed after all: a message arrived after the
    #: snapshot, or a turn is running in them. Counted rather than silent,
    #: because "examined 3, closed 1" otherwise reads as two failures.
    skipped_recently_active: int = 0
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
) -> list[IdleCandidate]:
    """Open conversations past their window, each with the snapshot judged."""
    now = now or datetime.now(timezone.utc)
    idle: list[IdleCandidate] = []

    for row in db.get_open_conversations_with_activity():
        if exclude_conversation_id and row["id"] == exclude_conversation_id:
            continue
        window = _window_for(row["last_role"])
        quiet_for = now - _parse(row["last_message_at"])
        if quiet_for >= window:
            kind = "in-flight grace" if row["last_role"] not in (ASSISTANT, None) else "idle"
            idle.append(
                IdleCandidate(
                    conversation_id=row["id"],
                    reason=(
                        f"{kind}: quiet {quiet_for.total_seconds() / 60:.1f}m "
                        f"of {window.total_seconds() / 60:.0f}m"
                    ),
                    last_message_at=row["last_message_at"],
                )
            )
    return idle


def close_idle_conversations(
    now: datetime | None = None,
    exclude_conversation_id: str | None = None,
    dry_run: bool = False,
    is_busy: Callable[[str], bool] | None = None,
) -> IdleCloseResult:
    """Close every conversation past its idle window.

    ``exclude_conversation_id`` is the conversation the caller is currently
    using. Task 2.2 passes the active one, so a sweep can never close the turn
    that triggered it.

    ``is_busy`` is asked, when given, whether a turn is running in a candidate;
    the chat route passes ``turn_locks.held``. It is passed in rather than
    imported so this module keeps knowing nothing about turns, and it is an
    in-process answer only — the conditional close below is what covers another
    process, and the two are deliberately both present.

    **The close is conditional on the snapshot** this sweep judged
    (``IdleCandidate.last_message_at``): a message that arrived since means the
    conversation is not idle after all, so it is left open, counted in
    ``skipped_recently_active``, and **not chunked** — finalising it would seal
    its trailing group while the conversation is still live.

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
        result.closed_ids = [c.conversation_id for c in candidates]
        return result

    for candidate in candidates:
        conversation_id, reason = candidate.conversation_id, candidate.reason
        if is_busy is not None and is_busy(conversation_id):
            result.skipped_recently_active += 1
            logger.info(
                "Left conversation %s open: a turn is running in it",
                conversation_id[:8],
            )
            continue
        try:
            if not db.end_conversation(
                conversation_id, not_after=candidate.last_message_at
            ):
                result.skipped_recently_active += 1
                logger.info(
                    "Left conversation %s open: it has a message newer than the "
                    "sweep's snapshot (%s)",
                    conversation_id[:8],
                    candidate.last_message_at,
                )
                continue
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


@dataclass
class DrainResult:
    """What a drain of the recovery queue did."""

    queued: int = 0
    attempted: int = 0
    chunked: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def drain_recovery_queue(limit: int = 1) -> DrainResult:
    """Finish final chunking for conversations that were closed but not chunked.

    ``db.get_unchunked_ended_conversations()`` has existed since task 1.3 and
    nothing drained it: a close whose chunking failed left the conversation's
    trailing turns unretrievable with no retry anywhere. This is that retry.

    **Bounded, and deliberately not at startup.** Final chunking embeds, so a
    drain makes model calls; the chat route runs it after the response with
    ``limit=1``, which keeps it out of the person's wait and off the startup path
    (a slow or absent model must not delay or fail serving). The script runs it
    with whatever limit the operator asks for.

    Failures are collected rather than raised: a conversation that fails stays in
    the queue, and the next drain tries it again.
    """
    result = DrainResult()
    rows = db.get_unchunked_ended_conversations(limit=limit)
    result.queued = len(rows)
    for row in rows:
        result.attempted += 1
        try:
            chunking.finalise_conversation(row["id"])
            result.chunked += 1
            logger.info("Drained the recovery queue: chunked %s", row["id"][:8])
        except Exception as exc:  # noqa: BLE001 - collected, the row stays queued
            result.failures.append((row["id"], str(exc)))
            logger.warning(
                "The recovery queue still holds %s: final chunking failed: %s",
                row["id"][:8], exc,
            )
    return result
