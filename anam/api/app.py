"""FastAPI application factory.

This module wires the app together and holds no route bodies of its own.
Routers live under ``anam/api/routes/``, split by domain from the first commit.

That split is deliberate. The reference build accumulated a single 1,824-line
routes module that became the place every new feature reached into, and
untangling it was its own project. Most of the routers here will be nearly
empty for several phases; that is the intended cost.
"""

from __future__ import annotations

from fastapi import FastAPI

from anam.api.routes import health


def create_app() -> FastAPI:
    """Build and return the application."""
    app = FastAPI(
        title="Project Anam",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(health.router)
    return app


app = create_app()
