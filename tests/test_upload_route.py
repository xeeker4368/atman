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


class _CountingFile:
    """Stands in for `UploadFile`: serves `size` bytes and counts what was read."""

    def __init__(self, size: int):
        self.remaining = size
        self.served = 0

    async def read(self, n: int = -1) -> bytes:
        n = self.remaining if n < 0 else min(n, self.remaining)
        self.remaining -= n
        self.served += n
        return b"x" * n


def test_an_oversize_upload_is_refused_without_reading_it_all():
    """Merged-queue item 15: the limit used to apply after `file.read()` had pulled
    the whole body into memory. Now reading stops one piece past the limit."""
    import asyncio

    from program.api.routes import upload as route

    limit = 3 * route._READ_SIZE
    body = _CountingFile(50 * route._READ_SIZE)

    assert asyncio.run(route._read_capped(body, limit)) is None
    assert body.served <= limit + route._READ_SIZE
    assert body.remaining > 0  # most of the body was never read


def test_an_upload_exactly_at_the_limit_is_read_whole():
    import asyncio

    from program.api.routes import upload as route

    limit = 2 * route._READ_SIZE + 7
    data = asyncio.run(route._read_capped(_CountingFile(limit), limit))

    assert data is not None and len(data) == limit


def test_an_oversize_upload_never_reaches_ingestion(client, monkeypatch):
    monkeypatch.setenv("ANAM_INGESTION_MAX_UPLOAD_BYTES", "50")
    config.reload()

    def forbidden(*args, **kwargs):
        raise AssertionError("an oversize upload must be refused before ingest()")

    from program.artifacts import ingest

    monkeypatch.setattr(ingest, "ingest", forbidden)

    response = upload(client, token_for(client, "Lyle"), content=b"x" * 500)

    assert response.status_code == 413
    assert db.list_artifacts() == []


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


# --- piece 4a: the route does not hold the event loop; overlapping uploads stay one row -----------


def test_an_upload_does_not_block_the_event_loop(store, monkeypatch):
    """While one upload is inside a slow ingest, a health check must still be answered. On the
    event loop the health check waited for the whole ingest."""
    import asyncio
    import time

    import httpx

    from program.artifacts import ingest

    real = ingest.ingest

    def slow(*args, **kwargs):
        time.sleep(0.8)
        return real(*args, **kwargs)

    monkeypatch.setattr(ingest, "ingest", slow)
    app = create_app()

    async def scenario():
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            login = await http.post("/api/login", json={"name": "Lyle", "password": PASSWORD})
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            posting = asyncio.create_task(http.post(
                "/api/upload", files={"file": ("a.txt", b"Notes about grinders.", "text/plain")},
                headers=headers))
            started = time.monotonic()
            await asyncio.sleep(0.2)  # let the upload reach the slow ingest
            health = await http.get("/api/health")
            answered_after = time.monotonic() - started
            return answered_after, health, await posting

    answered_after, health, uploaded = asyncio.run(scenario())

    assert health.status_code == 200
    assert uploaded.status_code == 200
    # The ingest sleeps 0.8s. Answered by 0.5s means it was not stuck behind it.
    assert answered_after < 0.5, f"the health check was answered after {answered_after:.2f}s"


def test_concurrent_identical_uploads_make_one_row(store, monkeypatch):
    """Two byte-identical uploads that overlap must not both pass the duplicate check (B9)."""
    import threading
    import time

    from program.artifacts import extract, ingest

    real_extract = extract.extract

    def slow_extract(*args, **kwargs):
        time.sleep(0.3)  # long enough that both threads are past the duplicate check without a lock
        return real_extract(*args, **kwargs)

    monkeypatch.setattr(extract, "extract", slow_extract)
    results: list = []

    def upload_once():
        results.append(ingest.ingest(b"One note about a kettle.", "k.txt", store["lyle"]))

    threads = [threading.Thread(target=upload_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(db.list_artifacts()) == 1
    assert sorted(r.duplicate_of is None for r in results) == [False, True]
