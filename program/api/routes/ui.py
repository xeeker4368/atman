"""The chat page: ``GET /`` serves ``static/index.html``; its assets are under ``/static/``.

A no-build page (``docs/DECISIONS.md`` #31): plain HTML, CSS and JavaScript, no framework, no
bundler. Nothing is mounted at the root, so no path under ``/api`` can be shadowed by a file:
an unknown ``/api/...`` path still gets FastAPI's JSON 404.

The page is public, as ``/api/health`` is; it holds no data. Everything it shows comes from
the authenticated ``/api`` routes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

router = APIRouter()


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")
