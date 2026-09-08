"""Login route and the request-authentication dependency.

Design of record: ``docs/AUTH_DESIGN.md``. The substance lives in
``program/auth.py``, which has no FastAPI import and is testable without a
request; this module is the HTTP shape around it.

**Every failure is the same 401.** Unknown name, wrong password, no password
set, throttled, missing token, malformed header, bad signature, expired token,
deleted user — one status, one body, one ``WWW-Authenticate`` header. The
client's response to all of them is identical (send the user to the login
form), so distinguishing them would leak information to an attacker while
telling a legitimate user nothing they can act on (A8).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from program import auth
from program.settings.permissions import Actor

router = APIRouter()

#: One message for every authentication failure, by design (A8).
_FAILED = "authentication failed"
_HEADERS = {"WWW-Authenticate": "Bearer"}


class LoginRequest(BaseModel):
    """Credentials, in the body rather than the query string.

    A query parameter would put the password into ``logs/anam.log`` and
    uvicorn's access log on every attempt (A2).
    """

    name: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in_days: int


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=_FAILED, headers=_HEADERS
    )


@router.post("/api/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    """Exchange a name and password for a session token."""
    from program import config

    token = auth.login(payload.name, payload.password)
    if token is None:
        raise _unauthorized()
    return LoginResponse(token=token, expires_in_days=config.auth_session_lifetime_days())


def require_actor(request: Request) -> Actor:
    """FastAPI dependency: the authenticated :class:`Actor`, or 401.

    This is what task 2.2 depends on. Its obligation (c) — *"construct a real
    ``Actor`` from the request rather than reaching for ``Actor.operator()``"* —
    is satisfied by depending on this rather than by building an actor inline.

    ``Actor.operator()`` is unreachable from here by construction: this function
    only ever returns what ``db.get_actor()`` loaded from a users row, and the
    operator sentinel is not a row.
    """
    actor = auth.actor_for_header(request.headers.get("Authorization"))
    if actor is None:
        raise _unauthorized()
    return actor


#: Spelled once so routes read ``actor: Actor = CurrentActor`` rather than
#: repeating the Depends() call at every call site.
CurrentActor = Depends(require_actor)
