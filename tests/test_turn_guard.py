"""One turn at a time per conversation (piece 3.5, step 1).

Two shapes are used deliberately. The overlap tests run two **real threads**
through ``turn.handle_user_message`` with a model that blocks, because the defect
is concurrency and nothing weaker demonstrates it. The route tests hold the
conversation's lock from the test itself and then post, which exercises the same
guard without racing — the 409's body and "a refusal writes nothing" are about
the response, not about timing.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from program import auth, config
from program.api.app import create_app
from program.engine import loop, turn, turn_locks
from program.engine.ollama import OllamaUnreachable
from program.memory import db
from program.settings.permissions import Actor, Role

SECRET = "test-signing-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    return Actor(user_id=lyle, name="Lyle", role=Role.ADMIN)


@pytest.fixture
def blocking_model(monkeypatch):
    """A model that holds the turn open until the test releases it."""
    entered = threading.Event()
    release = threading.Event()

    def fake(messages, *, model=None, options=None, tools=None, timeout=None):
        entered.set()
        assert release.wait(timeout=10), "the test never released the model"
        return {"message": {"role": "assistant", "content": "Answered."}}

    monkeypatch.setattr(loop.ollama, "chat", fake)
    return entered, release


def run_in_thread(target):
    """Run ``target`` in a thread, keeping its result or its exception."""
    box: dict = {}

    def body():
        try:
            box["value"] = target()
        except BaseException as exc:  # noqa: BLE001 - re-raised by join_or_fail
            box["error"] = exc

    thread = threading.Thread(target=body)
    thread.start()
    return thread, box


def join_or_fail(thread, box, what):
    """Wait for a turn's thread and surface whatever it did.

    A turn here takes about 15 ms, so 30 s is not a timing assumption — it is a
    bound that distinguishes "it hung" from "it raised", and either failure says
    which rather than arriving as a missing key.
    """
    thread.join(timeout=30)
    assert not thread.is_alive(), f"{what} never finished"
    if "error" in box:
        raise AssertionError(f"{what} raised {box['error']!r}") from box["error"]
    return box["value"]


# --- The overlap itself -----------------------------------------------------


def test_two_turns_in_one_conversation_do_not_overlap(store, blocking_model):
    entered, release = blocking_model
    conversation_id = db.start_conversation(store.user_id)

    first, result = run_in_thread(
        lambda: turn.handle_user_message(store, "first question", conversation_id,
                                        situation="")
    )
    assert entered.wait(timeout=10), "the first turn never reached the model"

    with pytest.raises(turn.TurnAlreadyRunning):
        turn.handle_user_message(store, "second question", conversation_id,
                                 situation="")

    release.set()
    join_or_fail(first, result, "the first turn")
    contents = [row["content"] for row in db.get_conversation_messages(conversation_id)]
    assert contents == ["first question", "Answered."], (
        "the refused turn must leave no trace in the conversation"
    )


def test_a_turn_in_another_conversation_is_not_blocked(store, monkeypatch):
    """The lock is per conversation. Held here by the test, so nothing races."""
    held = db.start_conversation(store.user_id)
    other = db.start_conversation(store.user_id)
    monkeypatch.setattr(
        loop.ollama, "chat",
        lambda *a, **k: {"message": {"role": "assistant", "content": "Answered."}},
    )

    with turn_locks.turn(held):
        outcome = turn.handle_user_message(store, "elsewhere", other, situation="")

    assert outcome.conversation_id == other
    assert outcome.content == "Answered."


def test_two_first_sends_with_no_conversation_id_both_succeed(store, blocking_model):
    """Nothing to key a lock on yet, so neither send may be refused."""
    entered, release = blocking_model

    one, first = run_in_thread(lambda: turn.handle_user_message(store, "a", situation=""))
    assert entered.wait(timeout=30), "the first turn never reached the model"
    two, second = run_in_thread(lambda: turn.handle_user_message(store, "b", situation=""))
    release.set()
    first_outcome = join_or_fail(one, first, "the first send")
    second_outcome = join_or_fail(two, second, "the second send")

    assert first_outcome.conversation_id != second_outcome.conversation_id
    assert first_outcome.new_conversation and second_outcome.new_conversation


def test_a_failed_turn_releases_the_conversation(store, monkeypatch):
    conversation_id = db.start_conversation(store.user_id)
    monkeypatch.setattr(
        loop.ollama, "chat",
        lambda *a, **k: (_ for _ in ()).throw(OllamaUnreachable("nothing listening")),
    )

    with pytest.raises(OllamaUnreachable):
        turn.handle_user_message(store, "first", conversation_id, situation="")

    assert not turn_locks.held(conversation_id)
    monkeypatch.setattr(
        loop.ollama, "chat",
        lambda *a, **k: {"message": {"role": "assistant", "content": "Answered."}},
    )
    outcome = turn.handle_user_message(store, "second", conversation_id, situation="")
    assert outcome.content == "Answered."


# --- The route's 409 --------------------------------------------------------


@pytest.fixture
def client(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    config.reload()
    auth.throttle.reset()
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    db.set_password_hash(lyle, auth.hash_password(PASSWORD))
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    monkeypatch.setattr(
        loop.ollama, "chat",
        lambda *a, **k: {"message": {"role": "assistant", "content": "Answered."}},
    )
    client = TestClient(create_app())
    response = client.post("/api/login", json={"name": "Lyle", "password": PASSWORD})
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['token']}"}
    yield client, headers, lyle
    auth.throttle.reset()


def test_the_409_body_names_the_running_turn(client):
    client, headers, lyle = client
    conversation_id = db.start_conversation(lyle)

    with turn_locks.turn(conversation_id):
        response = client.post(
            "/api/chat",
            json={"message": "hello", "conversation_id": conversation_id},
            headers=headers,
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "a turn is already running in this conversation" in detail
    assert "seconds ago" in detail, detail


def test_a_refused_second_turn_writes_nothing(client):
    client, headers, lyle = client
    conversation_id = db.start_conversation(lyle)
    before = db.count_messages()

    with turn_locks.turn(conversation_id):
        response = client.post(
            "/api/chat",
            json={"message": "hello", "conversation_id": conversation_id},
            headers=headers,
        )

    assert response.status_code == 409
    assert db.count_messages() == before, "a refused turn wrote a message"
    with db.connection() as conn:
        conversations = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    assert conversations == 1, "a refused turn started a conversation"


def test_the_route_tells_the_sweep_which_conversations_are_busy(client, monkeypatch):
    """The sweep's in-process skip only works if the route hands it the predicate."""
    client, headers, lyle = client
    seen: dict = {}

    from program.api.routes import chat as chat_route

    def spy(**kwargs):
        seen.update(kwargs)
        return chat_route.idle.IdleCloseResult()

    monkeypatch.setattr(chat_route.idle, "close_idle_conversations", spy)

    assert client.post(
        "/api/chat", json={"message": "hello"}, headers=headers
    ).status_code == 200

    assert seen.get("is_busy") is turn_locks.held


def test_the_post_turn_task_drains_one_from_the_recovery_queue(client, monkeypatch):
    """The drain is reachable in production, not only by a direct call."""
    client, headers, lyle = client
    from program.memory import chunking

    monkeypatch.setattr(chunking.ollama, "embed", lambda text, **kw: [0.1] * 768)
    queued = db.start_conversation(lyle)
    db.save_message(queued, lyle, "user", "a question about the kettle")
    db.save_message(queued, lyle, "assistant", "an answer about the kettle")
    db.end_conversation(queued)
    assert [row["id"] for row in db.get_unchunked_ended_conversations()] == [queued]

    assert client.post(
        "/api/chat", json={"message": "hello"}, headers=headers
    ).status_code == 200

    assert db.get_unchunked_ended_conversations() == [], (
        "the post-response task did not drain the recovery queue"
    )


def test_a_failing_drain_does_not_fail_the_turn(client, monkeypatch):
    client, headers, lyle = client
    from program.api.routes import chat as chat_route

    def explode(**kwargs):
        raise RuntimeError("the drain exploded")

    monkeypatch.setattr(chat_route.idle, "drain_recovery_queue", explode)

    response = client.post("/api/chat", json={"message": "hello"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["content"] == "Answered."


def test_a_turn_after_a_refusal_works(client):
    """The refusal is per attempt, not a state the conversation gets stuck in."""
    client, headers, lyle = client
    conversation_id = db.start_conversation(lyle)

    with turn_locks.turn(conversation_id):
        assert client.post(
            "/api/chat",
            json={"message": "hello", "conversation_id": conversation_id},
            headers=headers,
        ).status_code == 409

    response = client.post(
        "/api/chat",
        json={"message": "hello", "conversation_id": conversation_id},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["conversation_id"] == conversation_id
