"""A turn's correction calls, run after the response has gone out.

``docs/CORRECTION_DESIGN.md`` C8 (revised 2026-11-27). A turn's up-to-three correction
classifier calls used to run before the HTTP response returned, adding a mean 9.2 s to
the person's wait in run 6. They now run on one process-wide worker thread, for the chat
route only. Direct callers of ``turn.handle_user_message`` (scripts, harnesses, tests)
still run them inline by default.

How it holds together
=====================
* **Prepare inline, run later.** :func:`prepare` runs before the response. It makes no
  model call and only reads the database: it builds the candidate pool and freezes
  every input the calls need into a :class:`CorrectionJob`. :func:`run` does the
  classifier calls and the link writes. The pool is therefore exactly what the inline
  design built: a newer message saved in the conversation before the job runs cannot
  enter it.
* **One worker, FIFO.** A single thread serialises every job in the process, so link
  writes happen in turn order, and Ollama gets one classifier call at a time.
* **The next turn waits; nothing else does.** :func:`wait_for` is called by the next
  turn in the same conversation (after it takes the turn lock, before retrieval) and by
  idle close (before ending a conversation). It waits on that conversation's latest job
  up to ``corrections.pending_wait_seconds``, the job's own worst case. Past the bound
  it logs a WARNING and returns. A waited turn logs one INFO line with the seconds.
  Other conversations never wait.
* **The job is a leaf.** It never takes a turn lock, never waits on a future and never
  calls into ``turn``, so a waiter holding its own conversation's lock cannot deadlock
  with it. Its whole body is wrapped in ``try/except Exception``: a failure logs a
  WARNING naming both message ids and the error, and the worker moves on.
* **Graceful stop finishes pending jobs**: the app's lifespan calls
  :func:`shutdown` with ``wait=True``.

What it does not do: record pending work durably
================================================
**Log only, by decision (Lyle, 2026-11-27).** Nothing durable marks a job pending. A
**forced stop** (kill, crash, a second Ctrl-C) loses the queued and running jobs **with
no trace**: the turn's messages stand with no link and nothing says a correction was
due. That is the same end state as a correction the classifier declined, without even
a log line. A failed job leaves only its WARNING.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from program import config
from program.engine import call_timing
from program.integrity import classifier, corrections

logger = logging.getLogger(__name__)
#: The lines that moved here from ``turn.py`` with the split (a recorded link, D5's dropped
#: link, a classifier that could not run) keep the logger they had, so log readers, the
#: play kit's tripwire and the tests find them where they always were.
turn_logger = logging.getLogger("program.engine.turn")


@dataclass(frozen=True)
class CorrectionJob:
    """Everything a turn's correction calls need, frozen when the turn ends."""

    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    user_text: str
    answer_text: str
    actor_name: str
    pool: tuple[corrections.Candidate, ...]
    #: ``corrections.person_corrects_entity`` as it stood at the turn.
    person_corrects_entity: bool
    #: CO17's symmetric skip, decided from the turn's trace.
    skip_self_correction: bool
    #: The classifier model resolved at the turn; it inherits ``models.chat``, which is
    #: settings-backed and live-editable. The other classifier settings are
    #: bootstrap-only and cannot change while the process runs.
    classifier_model: str
    created_monotonic: float = field(default_factory=time.monotonic)


def prepare(
    *,
    pool: list[corrections.Candidate],
    actor_name: str,
    conversation_id: str,
    user_text: str,
    user_message_id: str,
    answer_text: str,
    assistant_message_id: str,
    answer_trace: list | None,
) -> CorrectionJob:
    """Freeze the turn's inputs into a job. No model call; reads configuration only.

    ``pool`` is built by the caller, ``turn._record_corrections`` (the only production
    caller of ``corrections.candidates``, pinned by ``tests/test_empty_result.py``), before
    the response, from the store as it stood at the turn.
    """
    return CorrectionJob(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        user_text=user_text,
        answer_text=answer_text,
        actor_name=actor_name,
        pool=tuple(pool),
        person_corrects_entity=config.corrections_person_corrects_entity(),
        skip_self_correction=corrections.entries_report_nothing_found(answer_trace),
        classifier_model=config.classifier_model(),
    )


def run(job: CorrectionJob) -> None:
    """The classifier calls and the link writes. One call per (speaker, candidate role).

    A classifier failure is logged and nothing is written (as before). A link-write
    failure propagates: inline, ``turn._after_durable`` catches it; on the worker,
    :func:`_job` does.
    """
    pool = list(job.pool)
    try:
        with classifier.pinned_model(job.classifier_model):
            own = corrections.classify(
                job.user_text, job.user_message_id, pool, "user", job.actor_name)
            of_entity = corrections.classify(
                job.user_text, job.user_message_id, pool, "user", job.actor_name,
                candidate_role="assistant",
            ) if job.person_corrects_entity else None
            # CO17, the symmetric skip: see turn.py's history of this rule.
            self_correction = None if job.skip_self_correction else corrections.classify(
                job.answer_text, job.assistant_message_id, pool, "assistant", "the system")
    except Exception as exc:  # noqa: BLE001 — a missed link, never a failed turn
        classifier_could_not_run(
            job.conversation_id, job.user_message_id, job.assistant_message_id, exc)
        return

    judged = [own, of_entity, unless_person_took_it(
        of_entity, self_correction, job.conversation_id)]
    with classifier.pinned_model(job.classifier_model):
        for correction in judged:
            if correction is None:
                continue
            if corrections.record(correction) is not None:
                turn_logger.info(
                    "correction recorded in conversation %s: %s supersedes %s",
                    job.conversation_id[:8],
                    correction.superseding_message_id[:8],
                    correction.superseded_message_id[:8],
                )


def unless_person_took_it(
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
    turn_logger.info(
        "correction dropped in conversation %s (D5: the person's link wins): "
        "target %s; kept %s (%s, %r); dropped %s (%s, %r)",
        conversation_id[:8], entity.superseded_message_id,
        person.superseding_message_id, person.replacement, person.rationale,
        entity.superseding_message_id, entity.replacement, entity.rationale,
    )
    return None


def classifier_could_not_run(conversation_id, user_message_id, assistant_message_id, exc):
    # The prefix is what the play kit's tripwire matches; keep it.
    turn_logger.warning(
        "correction classifier could not run for conversation %s (user message %s, "
        "reply %s); no link written: %s: %s",
        conversation_id[:8], user_message_id, assistant_message_id, type(exc).__name__, exc,
    )


# --- the worker and the per-conversation registry ---------------------------------------

_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
#: ``conversation_id -> Future`` of that conversation's most recently submitted job.
#: An entry is removed when its job finishes, unless a newer one has replaced it.
_latest: dict[str, Future] = {}


def submit(job: CorrectionJob) -> Future:
    """Queue ``job`` on the process-wide worker and register it for its conversation."""
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="corrections")
        future = _executor.submit(_job, job)
        _latest[job.conversation_id] = future
    future.add_done_callback(lambda f, cid=job.conversation_id: _forget(cid, f))
    return future


def _forget(conversation_id: str, future: Future) -> None:
    with _lock:
        if _latest.get(conversation_id) is future:
            del _latest[conversation_id]


def _job(job: CorrectionJob) -> None:
    """The worker's whole body. Never raises."""
    try:
        # A worker thread starts with an empty context: give the calls their turn's
        # timing context, so the ``corrections`` step lines still name the turn.
        with call_timing.turn(job.conversation_id):
            call_timing.set_message(job.conversation_id, job.user_message_id)
            with call_timing.step("corrections"):
                run(job)
    except Exception as exc:  # noqa: BLE001 — a background job must never raise
        logger.warning(
            "correction job failed for conversation %s (user message %s, reply %s); "
            "links it had not written are lost: %s: %s",
            job.conversation_id[:8], job.user_message_id, job.assistant_message_id,
            type(exc).__name__, exc,
        )


def pending(conversation_id: str) -> bool:
    """Whether a job for this conversation is queued or running. For tests and logs."""
    with _lock:
        future = _latest.get(conversation_id)
    return future is not None and not future.done()


def wait_for(conversation_id: str, *, before: str = "this turn") -> float | None:
    """Wait for this conversation's pending job, up to the bound. Seconds waited, or None.

    ``before`` names what is waiting, for the log lines.
    """
    with _lock:
        future = _latest.get(conversation_id)
    if future is None or future.done():
        return None
    bound = config.corrections_pending_wait_seconds()
    start = time.monotonic()
    try:
        future.result(timeout=bound)
    except FutureTimeout:
        waited = time.monotonic() - start
        logger.warning(
            "pending corrections in conversation %s still running after %.1f s "
            "(bound %.0f s); %s proceeds without waiting further",
            conversation_id[:8], waited, bound, before,
        )
        return waited
    except Exception:  # noqa: BLE001 — cancelled; _job itself never raises
        pass
    waited = time.monotonic() - start
    logger.info("waited %.2f s for pending corrections in conversation %s before %s",
                waited, conversation_id[:8], before)
    return waited


#: How long :func:`drain` waits before it reads its configured bound. Nothing pending (the
#: usual teardown) finishes in microseconds; only a job still running after this needs the
#: bound, so a test that leaves the configuration broken does not fail every later teardown.
_DRAIN_FAST_SECONDS = 1.0


class DrainTimeout(RuntimeError):
    """:func:`drain` gave up: a queued or running job did not finish within its bound."""


def drain(timeout: float | None = None) -> None:
    """Block until every job queued so far has finished. FIFO makes one marker enough.

    **Called only from tests**: ``tests/conftest.py`` (``isolated_data_dir``'s teardown, so no
    job outlives its test's store) and ``tests/test_correction_jobs.py``. Production never
    drains; the lifespan's :func:`shutdown` is the graceful stop.

    **Bounded**, so a job stuck in a test fails that test with a message instead of hanging
    the run. ``timeout`` defaults to ``corrections.pending_wait_seconds``, the bound a turn
    already waits on a pending job (a job's own worst case: three classifier timeouts plus a
    stated margin). Tests fake the model, so a real drain finishes in milliseconds and the
    bound is reached only by a job that is stuck. Past it: a WARNING, then
    :class:`DrainTimeout`. The configuration is read only if the queue has not drained
    within ``_DRAIN_FAST_SECONDS``.
    """
    with _lock:
        executor = _executor
    if executor is None:
        return
    try:
        marker = executor.submit(lambda: None)
    except RuntimeError:  # shut down meanwhile: nothing more will run
        return
    if timeout is None:
        # The common case (nothing pending) finishes at once, and must not need the
        # configuration: a test may leave it deliberately broken at teardown.
        try:
            marker.result(timeout=_DRAIN_FAST_SECONDS)
            return
        except FutureTimeout:
            pass
    bound = config.corrections_pending_wait_seconds() if timeout is None else timeout
    try:
        marker.result(timeout=bound)
    except FutureTimeout:
        logger.warning(
            "correction worker did not drain within %.1f s: a queued or running job is stuck",
            bound)
        raise DrainTimeout(
            f"correction jobs still pending after {bound:.1f} s; a job is stuck "
            f"(a held classifier call never released?)") from None


def shutdown(*, wait: bool = True, cancel_pending: bool = False) -> None:
    """Stop the worker. ``wait=True`` (the lifespan's call) finishes every pending job.

    ``cancel_pending`` drops jobs not yet started; with ``wait=False`` this is the
    nearest in-process stand-in for a forced stop, and is what the tests use.
    """
    global _executor
    with _lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=cancel_pending)
