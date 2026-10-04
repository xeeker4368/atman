"""FastAPI application factory.

This module wires the app together and holds no route bodies of its own.
Routers live under ``program/api/routes/``, split by domain from the first commit.

That split is deliberate. The reference build accumulated a single 1,824-line
routes module that became the place every new feature reached into, and
untangling it was its own project. Most of the routers here will be nearly
empty for several phases; that is the intended cost.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from program import config
from program.api.routes import auth, chat, health, upload
from program.memory import capability, db, vectors


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown.

    Builds the vector store at boot. ``get_vector_store()`` constructs on first
    use, so nothing strictly needs this — but without it, a broken or unwritable
    ChromaDB directory would first surface partway through a conversation
    instead of at startup. Same error, far better moment.

    Also **fails closed on a missing ``auth.session_secret``** (AUTH_DESIGN A1.3).
    Reading it here rather than in ``create_app()`` is deliberate: this is the
    real startup path — ``run_server.py`` and uvicorn both run it — while
    ``create_app()`` is also called by tests that never serve a request. An
    unconfigured secret must stop the server, not merely stop it being imported.

    **Creates and migrates the databases** (merged-queue item 12, plan B4). Nothing
    called ``db.init_databases()`` at startup — only the seed script did — while
    ``docs/DB_SCHEMA.md`` said it re-ran on every startup and the go-live wipe
    procedure depends on that. On a fresh data directory ChromaDB would create the
    directory, SQLite would create an empty ``working.db`` with no tables on the
    first request, and the first login was an unhandled 500. It is idempotent
    (``CREATE ... IF NOT EXISTS`` plus versioned migrations, which run inside their
    transaction since B3), so an existing store is left as it is. It runs after the
    secret check, so an unconfigured server stops without touching the store, and
    before the vector store, so a failed migration stops startup before anything
    else is built.

    **A capability probe runs first** (piece 4a): it builds the whole schema in an in-memory
    SQLite, so a build without FTS5 or the JSON functions stops startup with a plain message
    before any file is touched (``program/memory/capability.py``).
    """
    config.auth_session_secret()
    capability.probe()
    db.init_databases()
    vectors.get_vector_store()
    yield


def create_app() -> FastAPI:
    """Build and return the application."""
    app = FastAPI(
        title="Project Anam",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(upload.router)
    return app


app = create_app()
