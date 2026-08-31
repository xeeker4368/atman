"""Application skeleton and health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from anam.api.app import create_app


def test_app_builds():
    assert create_app() is not None


def test_health_returns_ok():
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_is_404():
    client = TestClient(create_app())
    assert client.get("/api/nope").status_code == 404


def test_openapi_and_docs_are_disabled():
    """No schema or docs surface. Nothing here is public-facing yet, and an
    always-on API description is a capability nobody asked for."""
    client = TestClient(create_app())
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
