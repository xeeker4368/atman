"""Chat-endpoint authentication — docs/AUTH_DESIGN.md's 13-item test plan, plus one.

Assertions are about outcomes, not about the presence of a check: that a wrong
password produces the *same bytes* as an unknown user, that a role change is
visible without a new login, that a missing secret stops the server.

**The TEST-ONLY route below.** There is no authenticated endpoint yet — the chat
endpoint is task 2.2, which is what depends on this. Rather than assert the
dependency's shape and hope, these tests mount a route that exists only in this
file, the same pattern `tests/test_tools.py` uses for its TEST-ONLY tools. The
production app never sees it.
"""

from __future__ import annotations

import base64
import time

import pytest
from fastapi.testclient import TestClient

from program import auth, config
from program.api.app import create_app
from program.api.routes.auth import CurrentActor
from program.memory import db
from program.settings.permissions import Actor, Role

SECRET = "test-signing-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    """A real store with two real users, one with a password and one without."""
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    # scrypt at the shipped n=2**16 costs ~92 ms per hash. Tests that are not
    # *about* the cost lower it; test_a_hash_survives_a_parameter_change uses
    # the real values.
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    config.reload()
    auth.throttle.reset()

    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    db.set_password_hash(lyle, auth.hash_password(PASSWORD))
    yield {"lyle": lyle, "jodie": jodie}
    auth.throttle.reset()


@pytest.fixture
def client(store):
    """The real app, plus one TEST-ONLY authenticated route to exercise."""
    app = create_app()

    @app.get("/api/test-only-whoami")
    def _whoami(actor: Actor = CurrentActor) -> dict[str, str]:
        return {"user_id": actor.user_id, "name": actor.name, "role": actor.role.value}

    return TestClient(app)


def _login(client, name=" Lyle", password=PASSWORD):
    return client.post("/api/login", json={"name": name.strip(), "password": password})


# --- 1, 2: the login itself, and what a failure reveals ---------------------


def test_correct_password_logs_in_and_wrong_password_does_not(client):
    ok = _login(client)
    assert ok.status_code == 200
    assert ok.json()["token"]
    assert ok.json()["expires_in_days"] == 30

    bad = _login(client, password="not the password")
    assert bad.status_code == 401
    assert "token" not in bad.json()


def test_unknown_user_and_wrong_password_are_indistinguishable(client):
    unknown = _login(client, name="Nobody", password=PASSWORD)
    wrong = _login(client, password="not the password")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert unknown.headers.get("www-authenticate") == wrong.headers.get(
        "www-authenticate"
    )


# --- 3: a NULL password_hash can never authenticate -------------------------


def test_a_user_with_no_password_set_can_never_log_in(client, store):
    row = db.get_user(store["jodie"])
    assert row["password_hash"] is None, "fixture precondition"

    for attempt in ("", "guess", PASSWORD):
        response = _login(client, name="Jodie", password=attempt)
        assert response.status_code == 401, f"empty hash accepted {attempt!r}"


def test_a_user_with_no_password_costs_the_same_as_an_unknown_user(
    client, store, monkeypatch
):
    """A8: a fast rejection would reveal which accounts have no password set.

    Counted rather than timed. A wall-clock assertion would be flaky at the
    lowered scrypt cost these tests run at, and counting KDF runs pins the
    property directly: the branch either pays for one or it does not.
    """
    auth._dummy_hash()  # warm the cache, so its own KDF run is not counted

    calls: list[int] = []
    real_scrypt = auth._scrypt

    def counting_scrypt(*args, **kwargs):
        calls.append(1)
        return real_scrypt(*args, **kwargs)

    monkeypatch.setattr(auth, "_scrypt", counting_scrypt)

    calls.clear()
    assert _login(client, name="Jodie", password="guess").status_code == 401
    no_password_set = len(calls)

    auth.throttle.reset()
    calls.clear()
    assert _login(client, name="Nobody", password="guess").status_code == 401
    unknown_user = len(calls)

    auth.throttle.reset()
    calls.clear()
    assert _login(client, password="wrong").status_code == 401
    wrong_password = len(calls)

    assert no_password_set == unknown_user == wrong_password == 1, (
        f"KDF runs differ per failure: no-password={no_password_set} "
        f"unknown-user={unknown_user} wrong-password={wrong_password}"
    )


# --- 4, 5, 6, 7: what makes a token stop working ----------------------------


def test_a_tampered_token_fails(client, store):
    token = auth.issue_token(store["lyle"])
    version, user_id, expires, signature = token.split(".")

    flipped = bytearray(base64.urlsafe_b64decode(signature + "=="))
    flipped[0] ^= 0x01
    tampered = ".".join(
        (
            version,
            user_id,
            expires,
            base64.urlsafe_b64encode(bytes(flipped)).decode().rstrip("="),
        )
    )

    assert auth.verify_token(tampered) is None
    assert _get_whoami(client, tampered).status_code == 401


def test_an_expired_token_fails(client, store):
    """Expiry is checked against frozen time, not slept through."""
    issued_at = time.time()
    token = auth.issue_token(store["lyle"], now=issued_at)
    lifetime = config.auth_session_lifetime_days() * 86400

    assert auth.verify_token(token, now=issued_at + lifetime - 10) == store["lyle"]
    assert auth.verify_token(token, now=issued_at + lifetime + 1) is None


def test_expiry_is_absolute_and_not_extended_by_use(client, store):
    """A5: a sliding window would keep a stolen token alive forever."""
    issued_at = time.time()
    token = auth.issue_token(store["lyle"], now=issued_at)
    lifetime = config.auth_session_lifetime_days() * 86400

    for offset in range(0, lifetime, lifetime // 4):
        assert auth.verify_token(token, now=issued_at + offset) is not None

    assert auth.verify_token(token, now=issued_at + lifetime + 1) is None


def test_rotating_the_session_secret_invalidates_existing_tokens(
    client, store, monkeypatch
):
    token = auth.issue_token(store["lyle"])
    assert auth.verify_token(token) == store["lyle"]

    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", "a-different-secret-of-good-length")
    config.reload()

    assert auth.verify_token(token) is None
    assert _get_whoami(client, token).status_code == 401


def test_a_token_for_a_deleted_user_fails_rather_than_raising(client, store):
    token = auth.issue_token(store["lyle"])
    with db.transaction() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (store["lyle"],))

    assert auth.actor_for_header(f"Bearer {token}") is None
    assert _get_whoami(client, token).status_code == 401


# --- 8, 14: how the credential may be presented -----------------------------


def _get_whoami(client, token=None, *, header=None):
    headers = {}
    if header is not None:
        headers["Authorization"] = header
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.get("/api/test-only-whoami", headers=headers)


def test_a_token_in_a_query_parameter_is_not_accepted(client, store):
    """A2: request paths reach logs/anam.log and uvicorn's access log."""
    token = auth.issue_token(store["lyle"])
    assert _get_whoami(client, token).status_code == 200

    response = client.get(f"/api/test-only-whoami?token={token}")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "token-with-no-scheme",
        "Bearer one two three",
        "bearer",
    ],
)
def test_a_malformed_authorization_header_is_a_plain_401(client, header):
    """The added 14th case: a header that is not `Bearer <token>` at all."""
    response = _get_whoami(client, header=header)
    assert response.status_code == 401
    assert response.json()["detail"] == "authentication failed"


def test_a_missing_header_is_the_same_401_as_a_bad_one(client):
    missing = _get_whoami(client)
    malformed = _get_whoami(client, header="Bearer nonsense")
    assert missing.status_code == malformed.status_code == 401
    assert missing.json() == malformed.json()


# --- 9, 10, 11: the Actor this produces -------------------------------------


def test_the_actor_matches_the_database_row(client, store):
    token = auth.issue_token(store["lyle"])
    body = _get_whoami(client, token).json()

    row = db.get_user(store["lyle"])
    assert body == {"user_id": row["id"], "name": row["name"], "role": row["role"]}
    assert body["role"] == "admin"


def test_a_role_change_takes_effect_without_a_new_login(client, store):
    """A1.2: the role is not in the token, so it cannot go stale."""
    token = auth.issue_token(store["lyle"])
    assert _get_whoami(client, token).json()["role"] == "admin"

    with db.transaction() as conn:
        conn.execute("UPDATE users SET role = 'user' WHERE id = ?", (store["lyle"],))

    assert _get_whoami(client, token).json()["role"] == "user"


def test_no_request_can_produce_the_operator_sentinel(client, store):
    """A6: Actor.operator() is the shell carve-out, not an HTTP-reachable path."""
    operator = Actor.operator()
    token = auth.issue_token(store["lyle"])
    body = _get_whoami(client, token).json()

    assert body["user_id"] != operator.user_id
    assert body["name"] != operator.name

    # A token minted for the sentinel's id resolves to no user row at all.
    assert auth.verify_token(auth.issue_token(operator.user_id)) == operator.user_id
    assert auth.actor_for_header(f"Bearer {auth.issue_token(operator.user_id)}") is None


def test_the_authenticated_actor_satisfies_the_existing_permission_system(
    client, store
):
    """This feeds role gating; it does not reimplement it."""
    from program.settings import permissions

    actor = auth.actor_for_header(f"Bearer {auth.issue_token(store['lyle'])}")
    assert isinstance(actor, Actor) and actor.role is Role.ADMIN
    permissions.require(actor, "settings.write")

    jodie = auth.actor_for_header(f"Bearer {auth.issue_token(store['jodie'])}")
    with pytest.raises(permissions.PermissionDenied):
        permissions.require(jodie, "settings.write")


# --- 12: stored parameters, not configured ones -----------------------------


def test_a_hash_survives_a_parameter_change(store, monkeypatch):
    """A3: parameters travel with the hash, so tuning cost breaks nothing."""
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", str(2**14))
    config.reload()
    stored = auth.hash_password(PASSWORD)
    assert "n=16384" in stored

    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", str(2**16))
    config.reload()
    assert auth.verify_password(PASSWORD, stored) is True
    assert auth.verify_password("wrong", stored) is False
    assert "n=65536" in auth.hash_password(PASSWORD)


def test_the_shipped_scrypt_parameters_actually_run(store, monkeypatch):
    """The maxmem gotcha: n >= 2**15 raises unless maxmem is passed."""
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", str(2**16))
    config.reload()
    stored = auth.hash_password(PASSWORD)
    assert auth.verify_password(PASSWORD, stored) is True


def test_a_corrupt_stored_hash_does_not_crash_the_login_route(client, store):
    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            ("not-a-real-hash", store["lyle"]),
        )
    assert _login(client).status_code == 401


# --- 13: fail closed on a missing secret ------------------------------------


def test_a_missing_session_secret_stops_the_server_from_starting(
    isolated_data_dir, monkeypatch
):
    monkeypatch.delenv("ANAM_AUTH_SESSION_SECRET", raising=False)
    config.reload()

    with pytest.raises(config.ConfigError, match="auth.session_secret"):
        with TestClient(create_app()):
            pass


def test_a_short_session_secret_is_refused(monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", "too-short")
    config.reload()
    with pytest.raises(config.ConfigError, match="at least 32"):
        config.auth_session_secret()


# --- A8: the throttle -------------------------------------------------------


def test_repeated_failures_throttle_further_attempts(client, store, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_LOGIN_MAX_ATTEMPTS_PER_MINUTE", "3")
    config.reload()

    for _ in range(3):
        assert _login(client, password="wrong").status_code == 401

    # The correct password now fails too: the throttle is on the name, and it
    # returns the same 401 as everything else rather than announcing itself.
    throttled = _login(client)
    assert throttled.status_code == 401
    assert throttled.json()["detail"] == "authentication failed"

    auth.throttle.reset()
    assert _login(client).status_code == 200


def test_a_successful_login_clears_the_name_s_failures(client, store, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_LOGIN_MAX_ATTEMPTS_PER_MINUTE", "3")
    config.reload()

    assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 200

    # Two more failures would exceed the limit if the success had not cleared.
    for _ in range(2):
        assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 200


def test_the_throttle_window_is_rolling(store):
    """Failures older than a minute do not count against a name."""
    auth.throttle.reset()
    for offset in range(5):
        auth.throttle.record_failure("Lyle", now=1000.0 + offset)

    assert auth.throttle.allows("Lyle", now=1004.0) is False
    assert auth.throttle.allows("Lyle", now=1065.0) is True


def test_the_throttle_is_per_name(store):
    auth.throttle.reset()
    for _ in range(10):
        auth.throttle.record_failure("Lyle")

    assert auth.throttle.allows("Lyle") is False
    assert auth.throttle.allows("Jodie") is True


# --- login side effects -----------------------------------------------------


def test_last_seen_is_written_on_login_only(client, store):
    assert db.get_user(store["lyle"])["last_seen_at"] is None

    _login(client)
    after_login = db.get_user(store["lyle"])["last_seen_at"]
    assert after_login is not None

    token = _login(client).json()["token"]
    before_requests = db.get_user(store["lyle"])["last_seen_at"]
    for _ in range(3):
        assert _get_whoami(client, token).status_code == 200
    assert db.get_user(store["lyle"])["last_seen_at"] == before_requests


def test_the_password_is_never_stored_in_the_clear(client, store):
    stored = db.get_user(store["lyle"])["password_hash"]
    assert PASSWORD not in stored
    assert stored.startswith("scrypt$n=")


def test_the_health_endpoint_stays_public(client):
    """start.sh polls it for readiness; authentication must not break that."""
    assert client.get("/api/health").status_code == 200
