"""Ollama's own timing fields, captured for every model call a chat turn makes.

Speed investigation, part C (A). A turn makes several model calls one after another:
the query embedding, one chat call per loop iteration, the gate's classifier and up to
three correction classifiers. Ollama reports what each cost, and until now nothing kept
it. This module turns each response's counters into one log line, tied to the turn.

**A log line, not a table.** It is written by the ``program.engine.call_timing`` logger
at INFO, as ``ollama call timing {json}``. It lands in whatever file the server logs to
(``logs/anam.log`` under ``run_server.py``; the play kit's own server log), so it needs
no schema change and is archived with a run's logs. One line per call:

* ``conversation_id`` and ``message_id``: the **user message** that started the turn.
  It is the turn's id from its first call onward; the reply's id does not exist until
  after the main calls and the gate.
* ``step``: ``query_embedding``, ``main`` (the agent loop: its chat calls, and any embed
  a tool makes inside it), ``gate`` or ``corrections``. ``n`` counts calls within the
  step (for ``main`` chat calls, the iteration). ``seq`` counts calls within the turn.
* ``endpoint`` (``chat`` or ``embed``) and ``model``.
* Ollama's fields as it sent them, durations in **nanoseconds**: for chat,
  ``load_duration``, ``prompt_eval_count``, ``prompt_eval_duration``, ``eval_count``,
  ``eval_duration``, ``total_duration``; for embed, ``load_duration``,
  ``total_duration``, ``prompt_eval_count``. A field Ollama left out is ``null`` and
  is named in ``missing``.

**``prompt_eval_count`` counts the whole prompt**, even when most of it was reused from
the prompt cache: measured 2026-11-25, an identical repeat of a 6,565-token prompt
reported 6,565 in 0.06 s. ``prompt_eval_duration`` is the honest signal of how much
prompt work was done.

**Capture never breaks a turn.** :func:`record` swallows every exception and logs the
miss; it never changes what a call returns or raises. Outside a turn (the note pass,
the journal, chunking after the response) nothing is recorded.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: The fields kept from each endpoint's response, in the order they are logged.
FIELDS: dict[str, tuple[str, ...]] = {
    "chat": ("load_duration", "prompt_eval_count", "prompt_eval_duration",
             "eval_count", "eval_duration", "total_duration"),
    "embed": ("load_duration", "total_duration", "prompt_eval_count"),
}


@dataclass
class _Turn:
    conversation_id: str
    message_id: str | None = None
    step: str | None = None
    seq: int = 0
    per_step: dict[str, int] = field(default_factory=dict)


_current: ContextVar[_Turn | None] = ContextVar("call_timing_turn", default=None)


@contextmanager
def turn(conversation_id: str) -> Iterator[None]:
    """Mark the calls made inside this block as one turn's."""
    token = _current.set(_Turn(conversation_id))
    try:
        yield
    finally:
        _current.reset(token)


def set_message(conversation_id: str, message_id: str) -> None:
    """Name the turn by its user message, once that message is saved.

    The conversation is passed again because a turn can move to a new one (a close
    landing before the question was saved).
    """
    current = _current.get()
    if current is not None:
        current.conversation_id = conversation_id
        current.message_id = message_id


@contextmanager
def step(name: str) -> Iterator[None]:
    """Label the calls made inside this block with the turn step they belong to."""
    current = _current.get()
    previous = current.step if current else None
    if current is not None:
        current.step = name
    try:
        yield
    finally:
        if current is not None:
            current.step = previous


def record(endpoint: str, model: str, data: Any) -> None:
    """Log one call's timing fields. Never raises; does nothing outside a turn."""
    try:
        current = _current.get()
        if current is None:
            return
        current.seq += 1
        name = current.step or "unlabelled"
        current.per_step[name] = current.per_step.get(name, 0) + 1
        source = data if isinstance(data, dict) else {}
        fields = FIELDS.get(endpoint, ())
        entry: dict[str, Any] = {
            "conversation_id": current.conversation_id,
            "message_id": current.message_id,
            "step": name,
            "n": current.per_step[name],
            "seq": current.seq,
            "endpoint": endpoint,
            "model": model,
            **{key: source.get(key) for key in fields},
        }
        missing = [key for key in fields if source.get(key) is None]
        if missing:
            entry["missing"] = missing
        _write(entry)
    except Exception as exc:  # noqa: BLE001 - timing is bookkeeping, never the turn
        logger.warning("ollama call timing not recorded (%s call): %s: %s",
                       endpoint, type(exc).__name__, exc)


def _write(entry: dict[str, Any]) -> None:
    """The one place a record leaves this module. Separate so a failed write is testable."""
    logger.info("ollama call timing %s", json.dumps(entry, sort_keys=False))
