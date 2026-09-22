"""The upload endpoint: authentication and attribution. Task 2.6.

The route is thin, so these cover only what it alone can get wrong — that it
refuses an unauthenticated caller, and that the uploader recorded is the one the
token names rather than anything the request body claims.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from program import auth, config
from program.api.app import create_app
from program.artifacts import indexing
from program.memory import db

SECRET = "test-signing-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"


def _deterministic_embedding(text: str, **kwargs) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[i % len(digest)] / 255.0) for i in range(768)]


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    config.reload()
    auth.throttle.reset()
    monkeypatch.setattr(indexing.ollama, "embed", _deterministic_embedding)

    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    for uid in (lyle, jodie):
        db.set_password_hash(uid, auth.hash_password(PASSWORD))
    yield {"lyle": lyle, "jodie": jodie}
    auth.throttle.reset()


@pytest.fixture
def client(store):
    return TestClient(create_app())


def token_for(client, name):
    response = client.post("/api/login", json={"name": name, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def upload(client, headers, content=b"Notes about espresso grind size.",
           filename="notes.txt"):
    return client.post(
        "/api/upload", files={"file": (filename, content, "text/plain")},
        headers=headers,
    )


def test_an_unauthenticated_upload_is_refused(client):
    response = client.post(
        "/api/upload", files={"file": ("notes.txt", b"anything", "text/plain")}
    )

    assert response.status_code == 401
    assert db.list_artifacts() == []


def test_an_authenticated_upload_is_stored_and_indexed(client, store):
    response = upload(client, token_for(client, "Lyle"))

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_status"] == "extracted"
    assert body["content_type"] == "text/plain"
    assert body["chunks_indexed"] >= 1
    assert db.get_artifact(body["artifact_id"])["user_id"] == store["lyle"]


def test_the_uploader_is_the_token_holder(client, store):
    """Jodie's upload is Jodie's, whatever the request otherwise contains."""
    response = upload(client, token_for(client, "Jodie"))

    artifact = db.get_artifact(response.json()["artifact_id"])
    assert artifact["user_id"] == store["jodie"]


def test_the_declared_content_type_is_ignored_in_favour_of_the_bytes(client):
    """The upload header is the client's to set; it must not pick the path."""
    response = client.post(
        "/api/upload",
        files={"file": ("photo.png", b"plain words, no magic bytes", "image/png")},
        headers=token_for(client, "Lyle"),
    )

    assert response.status_code == 200
    assert response.json()["content_type"] == "text/plain"


def test_a_file_over_the_limit_is_413(client, monkeypatch):
    monkeypatch.setenv("ANAM_INGESTION_MAX_UPLOAD_BYTES", "50")
    config.reload()

    response = upload(client, token_for(client, "Lyle"), content=b"x" * 500)

    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


def test_an_empty_file_is_400(client):
    response = upload(client, token_for(client, "Lyle"), content=b"")

    assert response.status_code == 400


def test_a_binary_upload_is_accepted_and_reported_as_unread(client):
    response = client.post(
        "/api/upload",
        files={"file": ("image.png", b"\x89PNG\r\n\x1a\n\x00data", "image/png")},
        headers=token_for(client, "Lyle"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_status"] == "metadata_only"
    assert body["chunks_indexed"] == 0
    assert "not read" in body["extraction_note"]
