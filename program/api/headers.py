"""Security headers on every response.

The chat page is plain HTML with its scripts and styles in their own files under
``program/api/static/``, so the policy can forbid everything inline: no inline script, no
inline style, no ``style=`` attribute, nothing from another origin. A reply that somehow
reached the page as markup still could not run a script, because the browser refuses any
script not loaded from this origin. That is the second line; the first is that the page sets
every server string as text (``static/render.js``).

Pure ASGI rather than ``BaseHTTPMiddleware``, so it adds headers at ``http.response.start``
and leaves the body, streaming and background tasks (the post-turn sweep) exactly as they were.
"""

from __future__ import annotations

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
)


class SecurityHeaders:
    """Adds :data:`SECURITY_HEADERS` to every HTTP response, replacing any of the same name."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        names = {name for name, _ in SECURITY_HEADERS}

        async def with_headers(message) -> None:
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in names]
                message = {**message, "headers": kept + list(SECURITY_HEADERS)}
            await send(message)

        await self.app(scope, receive, with_headers)
