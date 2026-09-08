"""Password hashing, session tokens, and login throttling.

Design of record: ``docs/AUTH_DESIGN.md``, A1-A11.

**What this buys, stated narrowly.** One household member cannot trivially act
as the other. It is *not* a defence against someone who can capture LAN traffic:
there is no TLS in this build, so the password crosses the network in the clear
on login and the token does so on every request afterwards (A10, accepted
deliberately, not overlooked).

**Why the primitives live here rather than in the API layer.** ``scripts/
set_password.py`` needs to hash a password with no FastAPI import anywhere in
its path, and the request-shaped half of authentication is three lines of
exception translation. So this module holds everything that can be tested
without a request, and ``program/api/routes/auth.py`` holds the route and the
dependency that wrap it.

**The signing secret is validated in one place, and it is not here.**
``config.auth_session_secret()`` raises when it is unset or too short; this
module reads it and assumes it is sound. Restating the minimum length as a
constant here would put the number in two files with nothing keeping them in
step — a second copy that could drift out of agreement with the check that
actually runs is worse than no copy at all.

**Failure is signalled by returning ``None``, never by a specific exception.**
A8 requires every failure — unknown user, wrong password, missing token,
malformed token, bad signature, expired token, deleted user — to be
indistinguishable to the caller. Returning ``None`` uniformly makes the
uninformative 401 the easy thing to write; a rich exception hierarchy here
would invite a route to leak the distinction back out.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass

from program import config
from program.memory import db
from program.settings.permissions import Actor

logger = logging.getLogger(__name__)

#: Stored-hash format tag. A stored hash names its own algorithm and parameters
#: (A3), so a row written at one cost keeps verifying after the configured cost
#: changes. Reading parameters from config at verification time would silently
#: invalidate every existing password the moment one was tuned.
_HASH_ALGORITHM = "scrypt"

#: Token format tag. Present so a future format change is detectable rather
#: than ambiguous — a v2 token must not be silently parsed by v1 rules.
_TOKEN_VERSION = "v1"

#: Salt width. 16 bytes is the usual floor for a password salt; it only has to
#: be unique, not secret.
_SALT_BYTES = 16

#: Derived-key width, matching SHA-256's output width.
_DK_BYTES = 32

#: A real hash, of a value no one can log in with, used to spend the same time
#: verifying a password for a user who does not exist as for one who does (A8).
#: Built once on first use: it costs a full KDF run.
_DUMMY_HASH: str | None = None
_DUMMY_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Password hashing (A3, A4)
# ---------------------------------------------------------------------------


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """Run scrypt with an explicit memory ceiling.

    ``maxmem`` is passed because Python's default is too small for the
    parameters this project ships: ``hashlib.scrypt`` raises
    ``ValueError: [digital envelope routines] memory limit exceeded`` at
    ``n >= 2**15`` without it, while ``n = 2**14`` succeeds. Measured, not read
    off the documentation — an implementer who only tests at the lower cost
    never sees it (A4).

    The ceiling is three times the ~``128 * n * r`` bytes scrypt actually needs,
    which is headroom rather than a target: at the shipped parameters that is
    ~67 MB needed against a ~201 MB ceiling.
    """
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_DK_BYTES,
        maxmem=128 * n * r * 3,
    )


def hash_password(password: str) -> str:
    """Hash a password into the self-describing stored format.

    Returns ``scrypt$n=65536$r=8$p=1$<salt_b64>$<derived_b64>``. The parameters
    travel with the hash so they can change later without a migration and
    without invalidating rows written under the old cost.
    """
    if not password:
        raise ValueError("refusing to hash an empty password")

    n = config.auth_scrypt_n()
    r = config.auth_scrypt_r()
    p = config.auth_scrypt_p()
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = _scrypt(password, salt, n, r, p)
    return "$".join(
        (
            _HASH_ALGORITHM,
            f"n={n}",
            f"r={r}",
            f"p={p}",
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Check a password against a stored hash. ``None`` never verifies.

    A ``NULL`` ``password_hash`` means no password has been set, and it must
    never authenticate — that is what makes the system fail closed on the day
    this lands, with both seed users unable to log in until an operator sets a
    password (A3, A9).
    """
    if not stored or not password:
        return False

    try:
        algorithm, n_part, r_part, p_part, salt_b64, dk_b64 = stored.split("$")
        if algorithm != _HASH_ALGORITHM:
            return False
        n = int(n_part.removeprefix("n="))
        r = int(r_part.removeprefix("r="))
        p = int(p_part.removeprefix("p="))
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
    except (ValueError, TypeError):
        # A corrupt or unrecognised stored hash is a failed verification, not a
        # crash: a single bad row must not take the login route down.
        logger.warning("unparseable password hash encountered; treating as no match")
        return False

    try:
        candidate = _scrypt(password, salt, n, r, p)
    except ValueError:
        logger.warning("stored hash names scrypt parameters this host cannot run")
        return False

    return hmac.compare_digest(candidate, expected)


def _dummy_hash() -> str:
    """A hash to verify against when the named user does not exist (A8)."""
    global _DUMMY_HASH
    with _DUMMY_LOCK:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
        return _DUMMY_HASH


# ---------------------------------------------------------------------------
# Session tokens (A1.2)
# ---------------------------------------------------------------------------


def _signature(payload: str) -> str:
    secret = config.auth_session_secret()
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256)
    return base64.urlsafe_b64encode(digest.digest()).decode("ascii").rstrip("=")


def issue_token(user_id: str, *, now: float | None = None) -> str:
    """Issue a signed, stateless session token for ``user_id``.

    ``v1.<user_id>.<expires_at_epoch>.<signature>``. Nothing is stored: there is
    no sessions table, so there is no migration, no schema pass, and no database
    write on the request path (A1.2).

    **The role is deliberately absent.** Only the user id travels; the role is
    read from the database on every request, so a role change takes effect on
    the next request rather than the next login, and a token can never assert a
    role the database disagrees with.
    """
    if "." in user_id:
        # The separator is structural. A user id containing one would produce a
        # token that reparses into different fields than it was signed with.
        raise ValueError("user id must not contain '.'")

    seconds = config.auth_session_lifetime_days() * 86400
    expires_at = int((time.time() if now is None else now) + seconds)
    payload = f"{_TOKEN_VERSION}|{user_id}|{expires_at}"
    return ".".join((_TOKEN_VERSION, user_id, str(expires_at), _signature(payload)))


def verify_token(token: str | None, *, now: float | None = None) -> str | None:
    """Return the ``user_id`` a token attests to, or ``None``.

    ``None`` covers every failure — malformed, wrong version, bad signature,
    expired — because A8 requires them to be indistinguishable to the caller.
    """
    if not token:
        return None

    parts = token.split(".")
    if len(parts) != 4:
        return None
    version, user_id, expires_raw, provided = parts
    if version != _TOKEN_VERSION or not user_id:
        return None

    try:
        expires_at = int(expires_raw)
    except ValueError:
        return None

    payload = f"{version}|{user_id}|{expires_raw}"
    if not hmac.compare_digest(provided, _signature(payload)):
        return None

    # Absolute expiry, never sliding: a sliding window renews on every request,
    # so a stolen token would stay valid indefinitely as long as it was used
    # (A5).
    if (time.time() if now is None else now) >= expires_at:
        return None

    return user_id


# ---------------------------------------------------------------------------
# Login throttling (A8)
# ---------------------------------------------------------------------------


@dataclass
class LoginThrottle:
    """A per-name attempt counter over a rolling one-minute window.

    **Honest about its limits:** in-process, so it resets on restart and does
    not exist across processes, and it is keyed by the submitted name so it
    cannot slow an attacker spreading guesses across names. It raises the cost
    of guessing one person's password from "as fast as scrypt allows" to five
    attempts a minute, which is the whole claim.
    """

    def __post_init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, name: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(name, []) if now - t < 60.0]
        if recent:
            self._failures[name] = recent
        else:
            self._failures.pop(name, None)
        return recent

    def allows(self, name: str, *, now: float | None = None) -> bool:
        """Whether another attempt for ``name`` may be made right now."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            return len(self._prune(name, moment)) < config.auth_login_max_attempts_per_minute()

    def record_failure(self, name: str, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            recent = self._prune(name, moment)
            self._failures[name] = [*recent, moment]

    def clear(self, name: str) -> None:
        """Forget a name's failures. Called on a successful login."""
        with self._lock:
            self._failures.pop(name, None)

    def reset(self) -> None:
        """Forget everything. For tests and for an operator at a shell."""
        with self._lock:
            self._failures.clear()


#: Process-wide throttle. One instance, because "in-process" is the honest
#: scope of the guarantee (A8).
throttle = LoginThrottle()


# ---------------------------------------------------------------------------
# The two entry points a request path uses
# ---------------------------------------------------------------------------


def login(name: str, password: str) -> str | None:
    """Verify a name and password, returning a token, or ``None``.

    Every failure returns ``None``: unknown name, wrong password, a user with no
    password set, and a throttled caller alike. The caller turns that into one
    uninformative 401 (A8).
    """
    if not throttle.allows(name):
        # Deliberately not a distinct return value. A caller that could tell
        # "throttled" from "wrong password" would hand an attacker a probe, and
        # would tempt a future route into reporting the difference.
        logger.warning("login throttled for name %r", name)
        return None

    row = db.get_user_by_name(name)
    stored = row["password_hash"] if row is not None else None

    if row is None:
        # Spend the same time as a real verification, so response latency does
        # not reveal whether a name exists (A8).
        verify_password(password, _dummy_hash())
        throttle.record_failure(name)
        return None

    if stored is None:
        # A user who exists but has no password set pays the same KDF as one who
        # does not exist at all. ``verify_password`` rejects a ``None`` hash
        # before running scrypt, which is correct for it and wrong here: the
        # fast rejection would time-separate "no password set" from every other
        # failure, revealing which accounts are unclaimed. Verifying against the
        # dummy hash cannot authenticate — its password is a random token no
        # caller has ever seen.
        verify_password(password, _dummy_hash())
        throttle.record_failure(name)
        return None

    if not verify_password(password, stored):
        throttle.record_failure(name)
        return None

    throttle.clear(name)
    # Recorded once per login rather than per request: a write on the request
    # path would put database contention on every chat turn (A6).
    db.touch_last_seen(row["id"])
    return issue_token(row["id"])


def actor_for_header(authorization: str | None) -> Actor | None:
    """Resolve an ``Authorization`` header to an :class:`Actor`, or ``None``.

    Header only. A token in a query parameter is not accepted anywhere, because
    request paths are written to ``logs/anam.log`` and to uvicorn's access log —
    a token there is a credential in plaintext on disk on every request (A2).

    The actor is loaded from the database rather than reconstructed from the
    token, so ``role`` is always current and a deleted user's token stops
    working immediately.
    """
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None

    user_id = verify_token(token.strip())
    if user_id is None:
        return None

    # get_actor returns None for an unknown id rather than inventing an actor,
    # which is what makes a token for a deleted user fail rather than crash.
    return db.get_actor(user_id)
