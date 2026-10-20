"""The Brave Search API: ``web_search``'s paid fallback (decisions #38, #39).

Design of record: ``docs/BRAVE_SEARCH_DESIGN.md`` (revision 2). SearXNG is asked first
(``web_search.py``); this module is called only when SearXNG fails, and holds everything that
touches the key or the money.

The key
-------
Read by ``config.brave_api_key()`` from ``config/local.toml`` only, at the moment of the call,
and held in one local variable. It travels only in the ``X-Subscription-Token`` header, never in
the URL. **No string this module builds contains it**: a failure is described by its kind and
HTTP status, never by an exception's text or Brave's response body, and every such string still
passes through :func:`_scrub`. ``tests/test_brave.py``'s leak test runs every path with logging
at DEBUG and looks for a fake key everywhere it could surface.

The count
---------
Every call is a row in ``working.db`` ``brave_calls`` (migration 10), **reserved before the call
is sent** with one ``INSERT ... SELECT ... WHERE count < stop``. SQLite runs that statement
atomically, so two turns at once (Lyle's and Jodie's) cannot both pass the stop. A sent call
counts whatever happens to it. The month is the UTC calendar month from :func:`now`, which tests
replace with a fake clock.

* At ``search.brave_warn_monthly`` (900): a WARNING in the server log on the call that reaches it
  and every tenth call after, and a notice for the admin role (:func:`notices`).
* At ``search.brave_stop_monthly`` (980): no call is made, and the reason says the paid search is
  used up until the 1st of next month (UTC).
* Brave's monthly ``X-RateLimit-Remaining`` is an earlier stop **only when its monthly limit is
  above 0**. On a credit-billed plan Brave sends ``limit "50, 0"`` and ``remaining "49, 0"``
  (Lyle's run, 2026-10-19), and taking that 0 at its word would stop every call.

Per turn
--------
``search.brave_max_per_turn`` (3) calls a turn, counted in a ``ContextVar`` that the agent loop
sets for each turn (:func:`turn_scope`); ``registry._run_in_thread`` copies the context into the
tool's thread. It is not an argument, so the model cannot set it. A caller outside a turn (a
script) has no per-turn count and only the monthly stop applies to it.
"""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from program import config
from program.memory import db

logger = logging.getLogger(__name__)

#: Fixed, like ``MOLTBOOK_HOST``: a config typo cannot send the key to another host.
ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveUnavailable(Exception):
    """Brave was not called, or was called and failed.

    ``reason`` completes the sentence "The fallback search (Brave) …" and is prompt-facing. It
    never holds the key, an exception's text or Brave's response body.
    """

    def __init__(self, reason: str, *, used_up: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.used_up = used_up


# --- the clock and the month ---------------------------------------------------

def _system_clock() -> datetime:
    return datetime.now(timezone.utc)


#: Replaced by tests with a fake clock. Always returns an aware UTC datetime.
_clock: Callable[[], datetime] = _system_clock


def now() -> datetime:
    return _clock().astimezone(timezone.utc)


def month_of(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m")


def next_month_start(moment: datetime) -> datetime:
    moment = moment.astimezone(timezone.utc)
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _until(moment: datetime) -> str:
    start = next_month_start(moment)
    return f"{start.day} {start.strftime('%B %Y')} (UTC)"


# --- per turn ------------------------------------------------------------------

_turn_calls: ContextVar[list[int] | None] = ContextVar("brave_turn_calls", default=None)


@contextmanager
def turn_scope() -> Iterator[None]:
    """One turn's Brave count. Set by ``loop.run_turn`` around the whole turn."""
    token = _turn_calls.set([0])
    try:
        yield
    finally:
        _turn_calls.reset(token)


# --- the count -----------------------------------------------------------------

@dataclass(frozen=True)
class Usage:
    """This month's Brave calls, as ``working.db`` has them."""

    month: str
    used: int
    warn: int
    stop: int
    #: Brave's monthly figures from the latest call that sent them, or None.
    header_limit: int | None
    header_remaining: int | None

    @property
    def header_stops(self) -> bool:
        """Brave's own monthly count says stop. Only when its monthly limit is above 0."""
        return (self.header_limit is not None and self.header_limit > 0
                and self.header_remaining is not None
                and self.header_remaining <= config.BRAVE_UNSEEN_CALLS)

    @property
    def stopped(self) -> bool:
        return self.used >= self.stop or self.header_stops


def usage(at: datetime | None = None, conn: sqlite3.Connection | None = None) -> Usage:
    """This month's usage. ``conn`` lets a read-only operator script pass its own connection."""
    month = month_of(at or now())

    def _read(c: sqlite3.Connection) -> Usage:
        used = c.execute(
            "SELECT COUNT(*) FROM brave_calls WHERE month = ?", (month,)).fetchone()[0]
        header = c.execute(
            "SELECT month_limit, month_remaining FROM brave_calls WHERE month = ? "
            "AND month_limit IS NOT NULL ORDER BY rowid DESC LIMIT 1", (month,)).fetchone()
        return Usage(
            month=month, used=int(used), warn=config.brave_warn_monthly(),
            stop=config.brave_stop_monthly(),
            header_limit=None if header is None else header[0],
            header_remaining=None if header is None else header[1],
        )

    if conn is not None:
        return _read(conn)
    with db.connection() as own:
        return _read(own)


#: One statement, so the count and the insert cannot be separated by another writer. The
#: header condition applies only to a row whose monthly limit was above 0.
_RESERVE = """
    INSERT INTO brave_calls (id, month, called_at, outcome)
    SELECT ?, ?, ?, 'sent'
    WHERE (SELECT COUNT(*) FROM brave_calls WHERE month = ?) < ?
      AND NOT EXISTS (
          SELECT 1 FROM (SELECT month_limit, month_remaining FROM brave_calls
                         WHERE month = ? AND month_limit IS NOT NULL
                         ORDER BY rowid DESC LIMIT 1)
          WHERE month_limit > 0 AND month_remaining <= ?)
"""


def _reserve(at: datetime) -> tuple[str, int] | None:
    """Reserve one call. Returns ``(id, calls this month including it)``, or None at the stop."""
    month = month_of(at)
    call_id = uuid.uuid4().hex
    with db.transaction() as conn:
        inserted = conn.execute(
            _RESERVE,
            (call_id, month, at.isoformat(), month, config.brave_stop_monthly(), month,
             config.BRAVE_UNSEEN_CALLS),
        ).rowcount
        if inserted != 1:
            return None
        used = conn.execute(
            "SELECT COUNT(*) FROM brave_calls WHERE month = ?", (month,)).fetchone()[0]
    return call_id, int(used)


def _finish(call_id: str, outcome: str, status: int | None, latency: float,
            month_limit: int | None, month_remaining: int | None) -> None:
    """Record how the call went. A failure here is logged; the reserved row still counts."""
    try:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE brave_calls SET outcome = ?, http_status = ?, latency_seconds = ?, "
                "month_limit = ?, month_remaining = ? WHERE id = ?",
                (outcome, status, round(latency, 3), month_limit, month_remaining, call_id),
            )
    except sqlite3.Error as exc:
        logger.warning("Brave search: the outcome of call %s was not recorded (%s); the call "
                       "still counts as sent", call_id[:8], type(exc).__name__)


#: Months whose stop has been logged by this process, so the stop warns once a month.
_stop_logged: set[str] = set()


def _warn_if_due(used: int) -> None:
    warn, stop = config.brave_warn_monthly(), config.brave_stop_monthly()
    if used >= warn and (used - warn) % 10 == 0:
        logger.warning("Brave search: %d of %d paid fallback calls used this month "
                       "(warning from %d). scripts/brave_usage.py has the detail.",
                       used, stop, warn)


def _used_up(at: datetime) -> BraveUnavailable:
    month = month_of(at)
    if month not in _stop_logged:
        _stop_logged.add(month)
        logger.warning("Brave search: the paid fallback is used up for %s (the stop is %d "
                       "calls, or Brave's own monthly count when its limit is above 0); web "
                       "search stays on SearXNG until %s", month, config.brave_stop_monthly(),
                       _until(at))
    return BraveUnavailable(
        f"was not used: the paid search is used up for this month, until {_until(at)}",
        used_up=True)


def notices(at: datetime | None = None) -> list[str]:
    """Usage notices for the admin role (``GET /api/me``). Empty below the warning."""
    current = usage(at)
    if current.stopped:
        return [f"Brave search, the paid fallback, is used up for {current.month}: web search "
                f"runs on SearXNG alone until {_until(at or now())}. "
                f"scripts/brave_usage.py has the detail."]
    if current.used >= current.warn:
        return [f"Brave search, the paid fallback: {current.used} of {current.stop} calls used "
                f"in {current.month} (warning from {current.warn}). "
                f"scripts/brave_usage.py has the detail."]
    return []


# --- the call ------------------------------------------------------------------

def _scrub(text: str, key: str) -> str:
    return text.replace(key, "<key>") if key else text


def _monthly(header: str | None) -> int | None:
    """The per-month figure of a ``"per-second, per-month"`` rate-limit header, or None."""
    if not header:
        return None
    parts = [p.strip() for p in header.split(",")]
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


_TAG = re.compile(r"<[^>]+>")


def _plain(text: Any) -> str:
    """Brave marks matches with ``<strong>``; the renderer wants plain text."""
    return html.unescape(_TAG.sub("", str(text or ""))).strip()


def _results(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("not an object")
    web = payload.get("web") or {}
    if not isinstance(web, dict):
        raise ValueError("web is not an object")
    found = web.get("results") or []
    if not isinstance(found, list):
        raise ValueError("web.results is not a list")
    return [
        {"title": _plain(r.get("title")), "url": str(r.get("url") or "").strip(),
         "content": _plain(r.get("description"))}
        for r in found if isinstance(r, dict)
    ]


def search(query: str, count: int) -> list[dict[str, Any]]:
    """One Brave search, in Brave's order, as ``{title, url, content}`` dicts.

    Raises :class:`BraveUnavailable` when Brave is not called (this turn's cap, the key could
    not be read, the monthly stop, the count could not be written) or fails. An empty list is a
    real answer: Brave ran and found nothing.

    **One database step before the call**, the reservation, which is also the stop check, so
    the tool's timeout has one lock wait to allow for there (``web_search.py``, the timeout).
    """
    at = now()
    turn = _turn_calls.get()
    cap = config.brave_max_per_turn()
    if turn is not None and turn[0] >= cap:
        raise BraveUnavailable(
            f"was not used: this turn has already made {turn[0]} paid searches, the most one "
            f"turn may make")

    try:
        key = config.brave_api_key()
    except config.ConfigError:
        # The message names the file and the key, never the value; it goes to the log only.
        logger.warning("Brave search: the key could not be read; see the startup check")
        raise BraveUnavailable("was not used: its key could not be read") from None

    try:
        reserved = _reserve(at)
    except sqlite3.Error as exc:
        logger.warning("Brave search: the call could not be counted (%s), so it was not made",
                       type(exc).__name__)
        raise BraveUnavailable("was not used: its usage count could not be recorded") from None
    if reserved is None:
        raise _used_up(at)
    call_id, used = reserved
    if turn is not None:
        turn[0] += 1
    _warn_if_due(used)

    timeout = config.brave_timeout_seconds()
    started = time.monotonic()
    status: int | None = None
    month_limit = month_remaining = None
    try:
        try:
            response = requests.get(
                ENDPOINT,
                params={"q": query, "count": count},
                headers={"Accept": "application/json", "X-Subscription-Token": key},
                timeout=timeout,
            )
        except requests.exceptions.Timeout:
            raise BraveUnavailable(f"failed: it did not respond within {timeout:g}s") from None
        except requests.exceptions.RequestException as exc:
            # The kind of failure only: a requests error's text carries the URL and query.
            logger.warning("Brave search: request failed (%s)", type(exc).__name__)
            raise BraveUnavailable("failed: it could not be reached") from None
        status = response.status_code
        month_limit = _monthly(response.headers.get("X-RateLimit-Limit"))
        month_remaining = _monthly(response.headers.get("X-RateLimit-Remaining"))
        if status != 200:
            raise BraveUnavailable(f"failed: it answered HTTP {status}")
        try:
            found = _results(response.json())
        except ValueError:
            raise BraveUnavailable("failed: it returned an answer that was not usable") from None
    except BraveUnavailable as failure:
        _finish(call_id, "failed", status, time.monotonic() - started, month_limit,
                month_remaining)
        logger.warning("Brave search %s", _scrub(failure.reason, key))
        raise
    _finish(call_id, "ok", status, time.monotonic() - started, month_limit, month_remaining)
    logger.info("Brave search: %d result(s), call %d this month", len(found), used)
    return found
