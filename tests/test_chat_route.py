"""The chat endpoint: authentication, ownership, and the post-turn sweep.

The route is thin, so these tests are about the three things only it can be
wrong about — that it refuses an unauthenticated request, that the `Actor` it
builds is the authenticated one rather than an asserted one, and that the idle
sweep it schedules runs after the response with the active conversation
excluded.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from program import auth, config
from program.api.app import create_app
from program.engine import loop
from program.engine.ollama import OllamaUnreachable
from program.memory import db

SECRET = "test-signing-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    config.reload()
    auth.throttle.reset()

    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    db.set_password_hash(lyle, auth.hash_password(PASSWORD))
    db.set_password_hash(jodie, auth.hash_password(PASSWORD))
    # Keeps a live embedding call out of every request.
    from program.engine import turn

    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    yield {"lyle": lyle, "jodie": jodie}
    auth.throttle.reset()


@pytest.fixture
def model(monkeypatch):
    def fake(messages, *, model=None, options=None, tools=None, timeout=None):
        return {"message": {"role": "assistant", "content": "Answered."}}

    monkeypatch.setattr(loop.ollama, "chat", fake)
    return fake


@pytest.fixture
def client(store, model):
    return TestClient(create_app())


def token_for(client, name):
    response = client.post("/api/login", json={"name": name, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


# --- Authentication ---------------------------------------------------------


def test_an_unauthenticated_request_is_refused(client):
    response = client.post("/api/chat", json={"message": "hello"})

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication failed"}
    assert db.count_messages() == (0, 0), "nothing was written for an anonymous caller"


def test_a_bad_token_is_refused_the_same_way(client):
    response = client.post(
        "/api/chat",
        json={"message": "hello"},
        headers={"Authorization": "Bearer v1.nope.0.deadbeef"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication failed"}


def test_an_authenticated_turn_answers_and_is_attributed_to_that_user(client, store):
    response = client.post(
        "/api/chat", json={"message": "hello"}, headers=token_for(client, "Lyle")
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Answered."
    assert body["new_conversation"] is True
    assert body["stop_reason"] == "answered"
    assert body["trace"] == []

    rows = db.get_conversation_messages(body["conversation_id"])
    assert [r["user_id"] for r in rows] == [store["lyle"], store["lyle"]]


def test_the_actor_comes_from_the_token_not_from_the_request_body(client, store):
    """A body field naming another user changes nothing — there is no such field."""
    response = client.post(
        "/api/chat",
        json={"message": "hello", "user_id": store["lyle"]},
        headers=token_for(client, "Jodie"),
    )

    assert response.status_code == 200
    rows = db.get_conversation_messages(response.json()["conversation_id"])
    assert {r["user_id"] for r in rows} == {store["jodie"]}


# --- Ownership --------------------------------------------------------------


def test_posting_into_another_users_conversation_is_a_404(client, store):
    lyles = db.start_conversation(store["lyle"])

    response = client.post(
        "/api/chat",
        json={"message": "let me in", "conversation_id": lyles},
        headers=token_for(client, "Jodie"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "conversation not found"
    assert db.get_conversation_messages(lyles) == []


def test_an_unknown_conversation_looks_the_same_as_an_unowned_one(client):
    response = client.post(
        "/api/chat",
        json={"message": "hello", "conversation_id": "does-not-exist"},
        headers=token_for(client, "Lyle"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "conversation not found"


def test_continuing_a_conversation_keeps_its_id(client, store):
    headers = token_for(client, "Lyle")
    first = client.post("/api/chat", json={"message": "one"}, headers=headers).json()

    second = client.post(
        "/api/chat",
        json={"message": "two", "conversation_id": first["conversation_id"]},
        headers=headers,
    ).json()

    assert second["conversation_id"] == first["conversation_id"]
    assert second["new_conversation"] is False
    assert len(db.get_conversation_messages(first["conversation_id"])) == 4


# --- Failures ---------------------------------------------------------------


def test_an_empty_message_is_rejected_by_validation(client):
    response = client.post(
        "/api/chat", json={"message": ""}, headers=token_for(client, "Lyle")
    )

    assert response.status_code == 422


def test_an_unreachable_model_is_a_503_that_says_what_to_check(
    client, monkeypatch, store
):
    headers = token_for(client, "Lyle")

    def unreachable(*args, **kwargs):
        raise OllamaUnreachable("Cannot reach Ollama at http://localhost:11434.")

    monkeypatch.setattr(loop.ollama, "chat", unreachable)
    response = client.post("/api/chat", json={"message": "hello"}, headers=headers)

    assert response.status_code == 503
    assert "Cannot reach Ollama" in response.json()["detail"]
    # The user's message is still on record — the turn genuinely happened.
    assert db.count_messages() == (1, 1)


# --- The post-turn sweep ----------------------------------------------------


def test_the_sweep_runs_after_the_turn_and_never_closes_the_active_conversation(
    client, store
):
    """Scheduled as a background task with the active conversation excluded."""
    stale = db.start_conversation(store["lyle"])
    with db.transaction() as conn:
        conn.execute(
            "UPDATE conversations SET started_at = '2020-01-01T00:00:00+00:00' "
            "WHERE id = ?",
            (stale,),
        )

    body = client.post(
        "/api/chat", json={"message": "hello"}, headers=token_for(client, "Lyle")
    ).json()

    assert db.get_conversation(stale)["ended_at"] is not None, "the sweep did not run"
    assert db.get_conversation(body["conversation_id"])["ended_at"] is None


def test_a_lost_correction_link_is_not_a_500_on_a_turn_that_succeeded(
    client, store, monkeypatch
):
    """The user-visible half of the same guard.

    The route catches `ConversationAccessError`, `EmptyMessageError` and
    `OllamaError` and nothing else, so before `turn._after_durable` existed an
    `OperationalError` from a correction-link write past its retry deadline
    reached FastAPI as an unhandled 500 — on a turn whose answer was already in
    both stores. The person saw a server error and never saw the reply; the next
    turn's history then held an answer they were never shown.
    """
    import sqlite3

    from program.engine import turn

    monkeypatch.setattr(
        turn, "_record_corrections",
        lambda *a, **k: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")),
    )

    response = client.post(
        "/api/chat", json={"message": "hello"}, headers=token_for(client, "Lyle")
    )

    assert response.status_code == 200, "a succeeded turn must not surface as an error"
    body = response.json()
    assert body["content"] == "Answered."

    # and the answer the person was shown is the one on record
    rows = db.get_conversation_messages(body["conversation_id"])
    assert rows[-1]["content"] == "Answered."


# --- The post-turn checkpoint (merged-queue item 5, plan B1) -----------------


def _deterministic_embedding(text, **kwargs):
    import hashlib

    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[i % len(digest)] / 255.0) for i in range(768)]


def _turns(client, headers, n, conversation_id=None):
    for i in range(n):
        body = client.post(
            "/api/chat",
            json={"message": f"turn {i} about the kettle", "conversation_id": conversation_id},
            headers=headers,
        ).json()
        conversation_id = body["conversation_id"]
    return conversation_id


def test_a_sealed_group_is_chunked_after_the_turn_while_the_conversation_is_open(
    client, store, monkeypatch
):
    """`checkpoint_conversation` said it was "called after a completed assistant
    turn" and nothing called it, so an open conversation had zero chunks until
    idle-close. With the 8-turn cap, the ninth completed turn seals the first
    group, and it must be written then — not at close."""
    from program.memory import chunking

    monkeypatch.setattr(chunking.ollama, "embed", _deterministic_embedding)
    headers = token_for(client, "Lyle")

    cid = _turns(client, headers, 9)

    assert db.get_conversation(cid)["ended_at"] is None, "still open"
    chunks = db.get_conversation_chunks(cid)
    assert len(chunks) == 1, "the first eight turns are one sealed group"
    assert "turn 0 about the kettle" in chunks[0]["text"]
    assert "turn 8" not in chunks[0]["text"], "the open group is never indexed"


def test_nothing_is_chunked_while_only_the_open_group_exists(client, store, monkeypatch):
    from program.memory import chunking

    def forbidden(text, **kwargs):
        raise AssertionError("nothing has sealed, so nothing should be embedded")

    monkeypatch.setattr(chunking.ollama, "embed", forbidden)

    cid = _turns(client, token_for(client, "Lyle"), 3)

    assert db.get_conversation_chunks(cid) == []


def test_a_failed_checkpoint_does_not_fail_the_turn(client, store, monkeypatch):
    """Background, logged not raised: the answer already went out, and the unsealed
    groups are written by the next checkpoint or by idle-close."""
    from program.engine.ollama import OllamaUnreachable
    from program.memory import chunking

    def unreachable(text, **kwargs):
        raise OllamaUnreachable("nothing is listening")

    monkeypatch.setattr(chunking.ollama, "embed", unreachable)
    headers = token_for(client, "Lyle")

    cid = _turns(client, headers, 8)
    response = client.post(
        "/api/chat", json={"message": "the ninth", "conversation_id": cid}, headers=headers
    )

    assert response.status_code == 200
    assert db.get_conversation_chunks(cid) == []

    # and it recovers: the next checkpoint, with the model back, writes the group
    monkeypatch.setattr(chunking.ollama, "embed", _deterministic_embedding)
    client.post(
        "/api/chat", json={"message": "the tenth", "conversation_id": cid}, headers=headers
    )
    assert len(db.get_conversation_chunks(cid)) == 1


def test_an_over_long_message_is_413_and_nothing_is_stored(client, store, monkeypatch):
    monkeypatch.setenv("ANAM_CHAT_MAX_MESSAGE_CHARS", "50")
    config.reload()
    before = db.count_messages()

    response = client.post(
        "/api/chat", json={"message": "y" * 51}, headers=token_for(client, "Lyle")
    )

    assert response.status_code == 413
    assert "limit is 50" in response.json()["detail"]
    assert db.count_messages() == before
