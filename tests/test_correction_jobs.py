"""The correction calls after the response: ``program/engine/correction_jobs.py``.

CORRECTION_DESIGN C8 (revised 2026-11-27). Faked over the model throughout: the chat
reply, the gate and the correction classifier are scripted, and nothing reaches Ollama.
A correction call can be made to block on an ``Event`` so the tests can hold a job
"running" while the next turn, a close or a shutdown happens. Every block has a timeout,
so a failing test cannot hang the suite.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import tomllib

import pytest
from fastapi.testclient import TestClient

from program import config
from program.api.app import create_app
from program.api.routes import chat as chat_route
from program.engine import correction_jobs, loop, turn, turn_locks
from program.integrity import classifier, corrections
from program.memory import db, idle
from program.settings.permissions import Actor, Role

SECRET = "x" * 64
CLAIM, CORRECTION = "The dentist is on Tuesday.", "The dentist is Wednesday."


@pytest.fixture
def lyle(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    monkeypatch.delenv("ANAM_CORRECTIONS_PERSON_CORRECTS_ENTITY", raising=False)
    config.reload()
    db.init_databases()
    user_id = db.create_user("Lyle", role="admin")
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    monkeypatch.setattr(loop.ollama, "chat", lambda *a, **k: {
        "message": {"role": "assistant", "content": "Noted."}})
    return Actor(user_id=user_id, name="Lyle", role=Role.ADMIN)


class Model:
    """The classifier, scripted. Correction prompts can be held until ``release()``."""

    def __init__(self):
        self.gate = threading.Event()
        self.gate.set()
        self.started = threading.Event()
        self.prompts: list[str] = []
        self.raise_on_correction: Exception | None = None

    def hold(self):
        self.gate.clear()
        self.started.clear()

    def release(self):
        self.gate.set()

    def __call__(self, prompt, **kwargs):
        if "EARLIER STATEMENTS:" not in prompt:
            return "CONSISTENT"                      # the gate
        self.prompts.append(prompt)
        self.started.set()
        assert self.gate.wait(timeout=10), "a held correction call was never released"
        if self.raise_on_correction is not None:
            raise self.raise_on_correction
        if "NEW MESSAGE (from the system)" in prompt:
            return "NONE"                            # no self-correction
        return "CORRECTS 1 REPLACED\n- Tuesday to Wednesday | replaces the day"


@pytest.fixture
def model(monkeypatch):
    m = Model()
    monkeypatch.setattr(classifier, "classify", m)
    yield m
    m.release()


def links():
    with db.connection() as conn:
        return {(r["superseding_message_id"], r["superseded_message_id"])
                for r in conn.execute("SELECT * FROM supersedes")}


def first_turn(actor):
    """A claim on record, inline, so the next turn has a candidate to correct."""
    out = turn.handle_user_message(actor, CLAIM, situation="")
    claim_id = db.get_conversation_messages(out.conversation_id)[0]["id"]
    return out.conversation_id, claim_id


def deferred(actor, text, conversation_id):
    return turn.handle_user_message(actor, text, conversation_id, situation="",
                                    defer_corrections=True)


def in_thread(fn, *args):
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("out", fn(*args)), daemon=True)
    thread.start()
    return thread, box


# --- 1. The pool is the turn's, not the store's when the job runs -------------------------


def test_the_pool_is_frozen_at_the_turn_and_a_newer_message_never_enters_it(lyle, model):
    conv, claim = first_turn(lyle)
    other, _ = first_turn(lyle)
    model.hold()
    deferred(lyle, CORRECTION, other)         # holds the worker
    assert model.started.wait(5)
    out = deferred(lyle, CORRECTION, conv)     # queued: its job has not started
    # A newer message by the same person lands before the job runs at all.
    db.save_message(conv, lyle.user_id, "user", "NEWER MESSAGE, never a candidate")
    model.release()
    correction_jobs.drain()

    assert model.prompts and all("NEWER MESSAGE" not in p for p in model.prompts)
    assert (out.user_message_id, claim) in links()


# --- 2 and 3. The next turn in the same conversation waits, up to the bound ------------------


def test_the_next_turn_waits_for_the_pending_job_before_it_retrieves(
        lyle, model, monkeypatch, caplog):
    conv, claim = first_turn(lyle)
    seen = {}

    def retrieval(query):
        seen.setdefault(query, links())
        return None

    monkeypatch.setattr(turn.retrieval, "search", retrieval)
    caplog.set_level(logging.INFO, logger="program.engine.correction_jobs")
    model.hold()
    n = deferred(lyle, CORRECTION, conv)
    assert model.started.wait(5)
    assert correction_jobs.pending(conv) and (n.user_message_id, claim) not in links()

    thread, _ = in_thread(deferred, lyle, "And the time is ten.", conv)
    time.sleep(0.3)
    assert "And the time is ten." not in seen, "retrieval ran before the job finished"
    model.release()
    thread.join(10)

    assert (n.user_message_id, claim) in seen["And the time is ten."]
    waited = [r for r in caplog.records if r.getMessage().startswith("waited ")]
    assert len(waited) == 1 and conv[:8] in waited[0].getMessage()


def test_past_the_bound_the_next_turn_proceeds_and_the_link_still_lands(
        lyle, model, monkeypatch, caplog):
    conv, claim = first_turn(lyle)
    monkeypatch.setattr(config, "corrections_pending_wait_seconds", lambda: 0.2)
    caplog.set_level(logging.INFO, logger="program.engine.correction_jobs")
    model.hold()
    n = deferred(lyle, CORRECTION, conv)
    assert model.started.wait(5)

    thread, box = in_thread(deferred, lyle, "Next.", conv)
    thread.join(5)
    try:
        assert not thread.is_alive(), "the next turn waited past the bound"
        assert box["out"].content == "Noted."
        warning = [r for r in caplog.records if r.levelno == logging.WARNING
                   and "still running after" in r.getMessage()]
        assert len(warning) == 1
    finally:
        model.release()
        thread.join(10)
    correction_jobs.drain()
    assert (n.user_message_id, claim) in links()


# --- 4. Other conversations never wait ---------------------------------------------------


def test_another_conversation_does_not_wait(lyle, model, caplog):
    conv, _ = first_turn(lyle)
    caplog.set_level(logging.INFO, logger="program.engine.correction_jobs")
    model.hold()
    deferred(lyle, CORRECTION, conv)
    assert model.started.wait(5)

    thread, box = in_thread(turn.handle_user_message, lyle, "Unrelated.", None)
    thread.join(5)
    try:
        assert not thread.is_alive(), "a turn in another conversation waited"
        assert not [r for r in caplog.records if r.getMessage().startswith("waited ")]
    finally:
        model.release()


# --- 5. One worker: jobs run in submission order ------------------------------------------


def test_jobs_run_one_at_a_time_in_turn_order(lyle, model, monkeypatch):
    first_conv, first_claim = first_turn(lyle)
    second_conv, second_claim = first_turn(lyle)
    model.hold()
    a = deferred(lyle, CORRECTION, first_conv)
    assert model.started.wait(5)
    # The second job's correction calls are not held, but it queues behind the first.
    calls_before = len(model.prompts)
    b = deferred(lyle, CORRECTION, second_conv)
    time.sleep(0.3)
    assert len(model.prompts) == calls_before, "a second job ran beside the first"
    assert (b.user_message_id, second_claim) not in links()
    model.release()
    correction_jobs.drain()
    assert {(a.user_message_id, first_claim), (b.user_message_id, second_claim)} <= links()


# --- 6. A failing job is isolated and logged with both ids --------------------------------


def test_a_classifier_failure_in_the_job_is_logged_with_both_ids(lyle, model, caplog):
    conv, _ = first_turn(lyle)
    model.raise_on_correction = RuntimeError("model fell over")
    caplog.set_level(logging.WARNING)
    out = deferred(lyle, CORRECTION, conv)
    correction_jobs.drain()

    (line,) = [r.getMessage() for r in caplog.records
               if "correction classifier could not run" in r.getMessage()]
    assert out.user_message_id in line and out.assistant_message_id in line
    assert "model fell over" in line


def test_a_failing_link_write_is_logged_with_both_ids_and_the_next_job_still_runs(
        lyle, model, monkeypatch, caplog):
    conv, _ = first_turn(lyle)
    other_conv, other_claim = first_turn(lyle)
    real_record = corrections.record
    calls = {"n": 0}

    def flaky(correction):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_record(correction)

    monkeypatch.setattr(corrections, "record", flaky)
    caplog.set_level(logging.WARNING)
    bad = deferred(lyle, CORRECTION, conv)
    good = deferred(lyle, CORRECTION, other_conv)
    correction_jobs.drain()

    (line,) = [r.getMessage() for r in caplog.records
               if r.getMessage().startswith("correction job failed")]
    assert bad.user_message_id in line and bad.assistant_message_id in line
    assert "database is locked" in line
    assert (good.user_message_id, other_claim) in links()


def test_through_the_route_a_failing_job_never_cancels_checkpoint_or_sweep(
        lyle, model, monkeypatch):
    from program import auth

    auth.throttle.reset()
    db.set_password_hash(lyle.user_id, auth.hash_password("pw-for-this-test"))
    ran = []
    monkeypatch.setattr(chat_route, "_checkpoint", lambda cid: ran.append("checkpoint"))
    monkeypatch.setattr(chat_route, "_sweep", lambda cid: ran.append("sweep"))
    monkeypatch.setattr(corrections, "record",
                        lambda c: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    client = TestClient(create_app())
    login = client.post("/api/login", json={"name": "Lyle", "password": "pw-for-this-test"})
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    first = client.post("/api/chat", json={"message": CLAIM}, headers=headers).json()
    second = client.post("/api/chat", json={"message": CORRECTION,
                                            "conversation_id": first["conversation_id"]},
                         headers=headers)
    correction_jobs.drain()
    auth.throttle.reset()
    assert second.status_code == 200
    assert ran == ["checkpoint", "sweep", "checkpoint", "sweep"]


# --- 7. A forced stop: the job is lost, and nothing stale is left behind -------------------


def test_a_discarded_worker_loses_its_queued_job_and_leaves_no_stale_state(lyle, model):
    running_conv, _ = first_turn(lyle)
    lost_conv, lost_claim = first_turn(lyle)
    model.hold()
    deferred(lyle, CORRECTION, running_conv)
    assert model.started.wait(5)
    lost = deferred(lyle, CORRECTION, lost_conv)          # queued behind the first
    assert correction_jobs.pending(lost_conv)

    correction_jobs.shutdown(wait=False, cancel_pending=True)   # the in-process "kill"
    try:
        assert not correction_jobs.pending(lost_conv)
        assert lost_conv not in correction_jobs._latest, "a stale registry entry was left"
        assert correction_jobs.wait_for(lost_conv) is None
        assert not turn_locks.held(lost_conv), "a turn lock was left held"
        # The conversation carries on, and the lost job's link is simply never written.
        nxt = turn.handle_user_message(lyle, "Carry on.", lost_conv, situation="")
        assert nxt.content == "Noted."
    finally:
        model.release()
        time.sleep(0.2)
    assert (lost.user_message_id, lost_claim) not in links()


# --- 8. A graceful stop finishes pending jobs ---------------------------------------------


def test_a_graceful_shutdown_waits_for_the_running_job(lyle, model):
    conv, claim = first_turn(lyle)
    model.hold()
    out = deferred(lyle, CORRECTION, conv)
    assert model.started.wait(5)
    stopper, _ = in_thread(correction_jobs.shutdown)
    time.sleep(0.3)
    assert stopper.is_alive(), "shutdown returned while a job was still running"
    model.release()
    stopper.join(10)
    assert (out.user_message_id, claim) in links()


def test_the_app_lifespan_shuts_the_worker_down_waiting(lyle, monkeypatch):
    calls = []
    monkeypatch.setattr(correction_jobs, "shutdown", lambda **k: calls.append(k))
    with TestClient(create_app()):
        pass
    assert calls == [{"wait": True}]


# --- 9. Submitting the job fails: the turn still answers ----------------------------------


def test_a_failed_submit_is_logged_and_the_turn_still_answers(lyle, model, monkeypatch, caplog):
    conv, _ = first_turn(lyle)
    monkeypatch.setattr(correction_jobs, "submit",
                        lambda job: (_ for _ in ()).throw(RuntimeError("worker gone")))
    caplog.set_level(logging.WARNING)
    out = deferred(lyle, CORRECTION, conv)
    assert out.content == "Noted."
    assert any("correction links could not be recorded" in r.getMessage()
               and "worker gone" in r.getMessage() for r in caplog.records)


# --- 10. The response leaves before the corrections -----------------------------------------


def test_a_deferred_turn_returns_while_its_job_is_still_running(lyle, model):
    conv, claim = first_turn(lyle)
    model.hold()
    started = time.monotonic()
    out = deferred(lyle, CORRECTION, conv)
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 5, "the turn waited for its own corrections"
        assert correction_jobs.pending(conv)
        assert (out.user_message_id, claim) not in links()
    finally:
        model.release()
    correction_jobs.drain()
    assert (out.user_message_id, claim) in links()


def test_the_chat_route_opts_in_and_nothing_else_does():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(chat_route))
    opted = [k for n in ast.walk(tree) if isinstance(n, ast.Call)
             for k in n.keywords if k.arg == "defer_corrections"]
    assert len(opted) == 1 and getattr(opted[0].value, "value", None) is True


# --- 11. Idle close waits; the route passes the wait --------------------------------------


def test_idle_close_waits_for_a_pending_job_before_ending_the_conversation(
        lyle, model, monkeypatch):
    conv, claim = first_turn(lyle)
    model.hold()
    out = deferred(lyle, CORRECTION, conv)
    assert model.started.wait(5)
    at_end = {}
    real_end = db.end_conversation

    def end(cid, **kw):
        at_end[cid] = links()
        return real_end(cid, **kw)

    monkeypatch.setattr(db, "end_conversation", end)
    monkeypatch.setattr(idle.chunking, "finalise_conversation", lambda cid: None)  # no embedding
    threading.Timer(0.3, model.release).start()
    from datetime import datetime, timedelta, timezone
    idle.close_idle_conversations(
        now=datetime.now(timezone.utc) + timedelta(days=1),
        wait_pending=lambda cid: correction_jobs.wait_for(cid, before="closing it"))
    assert (out.user_message_id, claim) in at_end[conv]


def test_the_route_sweep_passes_the_wait(monkeypatch):
    seen = {}
    monkeypatch.setattr(chat_route.idle, "close_idle_conversations",
                        lambda **kw: seen.update(kw) or idle.IdleCloseResult())
    monkeypatch.setattr(chat_route.idle, "drain_recovery_queue",
                        lambda limit: idle.DrainResult())
    chat_route._sweep("conv")
    assert callable(seen.get("wait_pending"))


# --- 12. Direct callers keep the inline behaviour ------------------------------------------


def test_a_direct_caller_runs_the_corrections_inline_by_default(lyle, model):
    conv, claim = first_turn(lyle)
    out = turn.handle_user_message(lyle, CORRECTION, conv, situation="")
    assert (out.user_message_id, claim) in links(), "the link exists when the call returns"
    assert not correction_jobs.pending(conv)


# --- 13. The bound is the job's own worst case, as derived -----------------------------------


def test_the_wait_bound_is_three_classifier_timeouts_plus_the_stated_margin():
    raw = tomllib.loads((config.PROJECT_ROOT / "config" / "defaults.toml").read_text())
    margin = 15
    assert raw["corrections"]["pending_wait_seconds"] == (
        3 * raw["integrity"]["classifier_timeout_seconds"] + margin)


# --- 14. The deferred job's timing lines still name the turn -------------------------------


def test_the_deferred_calls_are_timed_under_the_turns_user_message(lyle, monkeypatch, caplog):
    """The worker thread starts with no timing context; the job opens its turn's."""
    import json

    from program.engine import call_timing

    conv, _ = first_turn(lyle)

    def chat(messages, **kwargs):                 # the whole client, below the classifier
        text = messages[-1]["content"]
        reply = "NONE" if "EARLIER STATEMENTS:" in text else (
            "CONSISTENT" if "CONSISTENT" in text or "rubric" in text.lower() else "Noted.")
        data = {"message": {"role": "assistant", "content": reply}, "eval_count": 1}
        call_timing.record("chat", kwargs.get("model") or "m", data)
        return data

    monkeypatch.setattr(classifier.ollama, "chat", chat)
    caplog.set_level(logging.INFO, logger="program.engine.call_timing")
    out = deferred(lyle, CORRECTION, conv)
    correction_jobs.drain()
    lines = [json.loads(r.getMessage()[len("ollama call timing "):]) for r in caplog.records
             if r.getMessage().startswith("ollama call timing {")]
    corrections_lines = [x for x in lines if x["step"] == "corrections"]
    assert corrections_lines
    assert {x["message_id"] for x in corrections_lines} == {out.user_message_id}


# --- 15. The job uses the classifier model resolved at the turn ----------------------------


def test_the_job_keeps_the_classifier_model_the_turn_resolved(lyle, model, monkeypatch):
    conv, _ = first_turn(lyle)
    used = []
    monkeypatch.setattr(classifier, "classify",
                        lambda prompt, **k: (used.append(classifier.current_model()), "NONE")[1])
    monkeypatch.setattr(config, "classifier_model", lambda: "model-at-the-turn")
    pool = [corrections.Candidate("u1", "user", CLAIM, "2026-11-27T10:00:00")]
    job = correction_jobs.prepare(
        pool=pool, actor_name="Lyle", conversation_id=conv, user_text=CORRECTION,
        user_message_id="u2", answer_text="Noted.", assistant_message_id="a2",
        answer_trace=None)
    monkeypatch.setattr(config, "classifier_model", lambda: "model-changed-later")
    correction_jobs.run(job)
    assert used and set(used) == {"model-at-the-turn"}


# --- drain() is bounded: a stuck job fails the test instead of hanging the run -------------


def test_drain_gives_up_on_a_stuck_job_with_a_warning_and_a_raise(lyle, model, caplog):
    conv, claim = first_turn(lyle)
    model.hold()
    out = deferred(lyle, CORRECTION, conv)
    assert model.started.wait(5)
    caplog.set_level(logging.WARNING, logger="program.engine.correction_jobs")
    try:
        started = time.monotonic()
        with pytest.raises(correction_jobs.DrainTimeout, match="still pending after 0.2 s"):
            correction_jobs.drain(timeout=0.2)
        assert time.monotonic() - started < 5
        assert any("did not drain within 0.2 s" in r.getMessage() for r in caplog.records)
    finally:
        model.release()
    correction_jobs.drain()
    assert (out.user_message_id, claim) in links()
