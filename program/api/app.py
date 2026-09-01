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

from program.api.routes import health
from program.memory import vectors


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown.

    Builds the vector store at boot. ``get_vector_store()`` constructs on first
    use, so nothing strictly needs this — but without it, a broken or unwritable
    ChromaDB directory would first surface partway through a conversation
    instead of at startup. Same error, far better moment.
    """
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
    return app


app = create_app()
