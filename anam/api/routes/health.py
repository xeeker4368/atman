"""Health check.

Liveness only: the process is up and can serve a request. Nothing here reports
on the database, the model, or any other dependency, because none of them exist
yet and a health check that claims more than it verified is worse than none.

Richer status reporting arrives with the things it would report on, and belongs
on the admin surface rather than here — this endpoint stays public.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, str]:
    """Return a flat liveness response."""
    return {"status": "ok"}
