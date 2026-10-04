"""One turn at a time per conversation.

Two turns in one conversation overlapping is not a theoretical race. ``POST
/api/chat`` is a synchronous handler, so uvicorn runs it in a worker thread and
two sends arriving together run side by side; both would persist a user message,
both would build history from a store the other is writing, and the second's
answer would be generated against a prompt missing the first's question. The
reply that lands second wins the record.

So a turn takes a lock for its conversation, and a second turn is **refused**
rather than queued. Queueing would hold the second request for as long as the
first turn can run, which is bounded by the loop's own limits at roughly
35 minutes (``config.IN_FLIGHT_GRACE_FLOOR_MINUTES``) — a request that may be
answered in half an hour is worse than one refused now, and a refusal makes a
client retry harmless.

What this is not
----------------
**It is in-process only.** ``run_server.py`` runs one uvicorn worker, so one lock
registry covers every request this server handles. It is not a database lock and
it does not protect against a second process — that backstop is
``db.save_message`` refusing a conversation whose ``ended_at`` is set, and the
idle sweep's close being conditional on its own snapshot.

The registry lives in its own module so that ``idle.py`` can ask whether a
conversation is busy without importing ``turn.py``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

#: ``conversation_id -> lock``, and the monotonic start time of the turn holding
#: it. One entry per conversation this process has seen a turn for; the same
#: unbounded-by-design growth as ``chunking._locks``, at the same magnitude.
_locks: dict[str, threading.Lock] = {}
_started: dict[str, float] = {}
_guard = threading.Lock()


class TurnAlreadyRunning(RuntimeError):
    """A turn is already running in this conversation.

    Carries how long the running turn has been going, because the useful
    question for whoever is refused is "is this a double-send, or is something
    stuck?" and the answer is the age.
    """

    def __init__(self, conversation_id: str, running_for_seconds: float):
        self.conversation_id = conversation_id
        self.running_for_seconds = running_for_seconds
        super().__init__(
            "a turn is already running in this conversation; it started "
            f"{round(running_for_seconds)} seconds ago."
        )


def _lock_for(conversation_id: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(conversation_id, threading.Lock())


def held(conversation_id: str) -> bool:
    """Whether a turn is running in this conversation, in this process.

    What the idle sweep asks before closing a conversation. It is a snapshot and
    nothing more: the answer can be stale the moment it is returned, which is why
    it is a cheap improvement on top of the conditional close rather than a
    substitute for it.
    """
    with _guard:
        lock = _locks.get(conversation_id)
    return bool(lock and lock.locked())


@contextmanager
def turn(conversation_id: str) -> Iterator[None]:
    """Hold this conversation for the duration of one turn.

    Raises :class:`TurnAlreadyRunning` immediately rather than waiting. Released
    on every exit — a returned answer, a raised exception, a client that
    disconnected (nothing cancels a synchronous handler, so the function runs to
    completion either way).
    """
    lock = _lock_for(conversation_id)
    if not lock.acquire(blocking=False):
        with _guard:
            started = _started.get(conversation_id)
        age = (time.monotonic() - started) if started is not None else 0.0
        raise TurnAlreadyRunning(conversation_id, age)
    with _guard:
        _started[conversation_id] = time.monotonic()
    try:
        yield
    finally:
        with _guard:
            _started.pop(conversation_id, None)
        lock.release()
