# 2026-09-08 — Chat endpoint authentication

**Tier 3 · design approved before implementation.**
Design of record: `docs/AUTH_DESIGN.md`, A1–A11, all seven open questions
resolved by Lyle before any code was written.

## Summary

There was no authentication anywhere: `users.password_hash` was a column written
by nothing and read by nothing, and `start.sh --lan` already binds `0.0.0.0`.
`POST /api/login` now exchanges a name and password for a signed, stateless
session token, and `require_actor` turns an `Authorization: Bearer` header into
the same `Actor` the role-gating system already consumes.

**Task 2.2's obligation (c) is now satisfiable rather than waived.**
`BUILD_PLAN.md` said that task "either depends on an authentication task or must
state plainly that the actor is asserted, not proven." This is that task.

## Files changed

Created: `program/auth.py`, `program/api/routes/auth.py`,
`scripts/set_password.py`, `tests/test_auth.py` (33), `docs/AUTH_DESIGN.md`.
Modified: `program/config.py` (six accessors, six env entries, one fallback
section), `program/api/app.py` (router + fail-closed startup),
`program/memory/db.py` (`set_password_hash`, `touch_last_seen`),
`config/defaults.toml` (`[auth]`), `BUILT.md`.

**No schema change, no migration, no new dependency.**

## What shipped, against each resolved question

| # | Decision | Where |
|---|---|---|
| A1 | Password login → session token | `POST /api/login` |
| A1.2 | Stateless HMAC-SHA256 token, no sessions table | `auth.issue_token` / `verify_token` |
| A1.3 | `auth.session_secret` bootstrap-only; startup fails closed | `config.auth_session_secret`, `app.lifespan` |
| A2 | `Authorization: Bearer` only — never a query parameter | `auth.actor_for_header` |
| A3 | `password_hash` used as designed, self-describing format | `scrypt$n=65536$r=8$p=1$<salt>$<dk>` |
| A4 | `hashlib.scrypt`, stdlib, explicit `maxmem` | `auth._scrypt` |
| A5 | 30 days, absolute expiry | `auth.session_lifetime_days` |
| A6 | Feeds the existing `Actor`; no capability registered | `require_actor` |
| A7 | One credential per person, one token per login | `scripts/set_password.py` |
| A8 | Identical 401s; per-name throttle at 5/minute | `auth.LoginThrottle`, `routes/auth._unauthorized` |
| A9 | Operator CLI via `getpass`, no self-service reset | `scripts/set_password.py` |
| A10 | No TLS — accepted, not overlooked | recorded below |

## Measured, on this machine

scrypt at the shipped parameters (`n=65536, r=8, p=1`), Python 3.14.5:
**98 ms for a real login end to end**, against the design's predicted 92.4 ms.
Paid once per login, not per request.

**The documented `maxmem` gotcha is real and is handled.** `hashlib.scrypt`
raises `ValueError: [digital envelope routines] memory limit exceeded` at
`n >= 2**15` with Python's default ceiling, while `n = 2**14` succeeds —
so an implementer who tests only at the lower cost never sees it.
`_scrypt()` passes `maxmem = 128 * n * r * 3` (~201 MB against the ~67 MB
actually needed). A test runs the shipped parameters specifically so the
gotcha cannot come back.

## Verified live, against a real server, not only through TestClient

```
no ANAM_AUTH_SESSION_SECRET  ->  ConfigError, "Application startup failed. Exiting."
POST /api/login  correct     ->  200 {"token":"v1.18e3bc64-…","expires_in_days":30}
POST /api/login  wrong pw    ->  401 {"detail":"authentication failed"}  www-authenticate: Bearer
POST /api/login  unknown user->  401 {"detail":"authentication failed"}  (byte-identical)
5 failures then correct pw   ->  401  + WARNING "login throttled for name 'Lyle'"
grep -c "v1\." server.log    ->  0        # the token never reaches the log
```

That last line is A2's entire reason for existing, so it was checked rather
than asserted: the access log records `POST /api/login` and no credential.

## Five guards, each proven to bite

A test that only passes after a fix cannot distinguish "fixed" from "never
reproduced" — the same discipline `test_without_retry_contention_actually_fails`
established. Each new check was **broken deliberately** and the suite re-run:

| Guard broken | Test that failed |
|---|---|
| `config.auth_session_secret()` removed from `lifespan` | `test_a_missing_session_secret_stops_the_server_from_starting` |
| signature comparison forced true | `test_a_tampered_token_fails`, `test_rotating_the_session_secret_invalidates_existing_tokens` |
| expiry check removed | `test_an_expired_token_fails` |
| throttle check removed | `test_repeated_failures_throttle_further_attempts` |
| `NULL` hash treated as a match | `test_a_user_with_no_password_set_can_never_log_in` |

All five bit. Restored and re-run clean afterwards.

## One gap found in review, closed before commit

The first implementation only ran the dummy-hash KDF for unknown users. A known
user with no password set (`stored is None`) hit `verify_password`'s own early
rejection and returned in the fast path — a timing side channel between "this
account doesn't exist" and "this account exists but has no password yet", which
is exactly what A8's uniform-401 requirement was meant to prevent. Found in
review, before commit.

Closed by adding an explicit third branch to `login()` that runs
`verify_password(password, _dummy_hash())` for this case too, and pinned by
`test_a_user_with_no_password_costs_the_same_as_an_unknown_user` — which counts
KDF calls rather than timing wall-clock, to avoid flakiness at the lowered
scrypt cost the tests run at.

## Decisions made inside the design's boundaries

Three things the design did not spell out, decided here and flagged rather than
buried:

1. **Module placement.** Primitives live in `program/auth.py` with no FastAPI
   import — `scripts/set_password.py` must hash a password without pulling the
   web framework in, and the request-shaped half is three lines of exception
   translation in `program/api/routes/auth.py`. A single module would have
   coupled the CLI to FastAPI; two modules both named `auth` in the same package
   would have been worse.
2. **A 32-character minimum on `auth.session_secret`** (`ConfigError` below it).
   Not in the design. A short signing key fails silently rather than loudly,
   which is the opposite of this module's posture. **Strike it if you would
   rather the design be followed to the letter** — it is one guard clause.
3. **A throttled login returns the same 401 as a wrong password**, and logs at
   WARNING. A 429 would tell an attacker their probing was working, and A8's
   rule is that failures are indistinguishable. The operator gets the
   distinction in the log, where it belongs.

**No capability was registered for password setting**, on A6's own reasoning: the
operator sentinel satisfies every capability, so one here would deny nothing, and
`permissions.py` keeps its registry to capabilities that are actually
enforceable. `scripts/set_password.py` constructs `Actor.operator()` explicitly
to state the posture, and there is no gated call to hand it to.

## The TEST-ONLY route

There is no authenticated endpoint yet — the chat endpoint is task 2.2, which is
what depends on this. Rather than assert the dependency's shape and hope,
`tests/test_auth.py` mounts a `/api/test-only-whoami` route that exists only in
that file, the same pattern `tests/test_tools.py` uses for its TEST-ONLY tools.
The production app never sees it, and `create_app()` is unchanged by its
existence.

This is deliberately *not* R2's rejected pattern. R2 refused to **ship** an
unmounted loopback gate; here the thing being shipped (`require_actor`) has a
named consumer landing next, and the substance it wraps —
`auth.actor_for_header` — is a plain function tested without any route at all.

## Tests: 33 new, 426 total

`ruff check .` clean. Suite verified order-independent across repeated runs.
Baseline before this task was 393.

Coverage is the design's own 13-item plan plus the 14th case added at approval
(a malformed `Authorization` header, parametrised over seven shapes: empty,
`Bearer` with nothing after it, `Basic`, a bare token with no scheme, a
three-part value, and a lowercase scheme with no token). Beyond the plan:
the throttle's rolling window and per-name isolation, `last_seen_at` written on
login and *not* on subsequent requests, a corrupt stored hash returning 401
rather than a 500, and the authenticated actor actually passing through
`permissions.require()` for `settings.write` as admin and being denied as user.

## Known limitations

- **No TLS, accepted deliberately (A10).** The password crosses the LAN in
  plaintext on login and the token does so on every request afterwards. A
  household member has the wifi key by assumption, so this is *inside* the
  stated threat model, not outside it. Upgrade path if that stops being
  acceptable: a self-signed certificate, or a WireGuard/Tailscale overlay.
- **No per-device revocation.** Stateless tokens cannot be revoked
  individually; rotating `auth.session_secret` invalidates everyone's at once
  and requires a restart, since it is bootstrap-only. At two users that is a
  blunt instrument, not an outage. The upgrade is a sessions table.
- **The throttle is in-process.** It resets on restart, does not exist across
  processes, and is keyed by submitted name, so it cannot slow an attacker
  spreading guesses across names. It raises the cost of guessing one person's
  password from "as fast as scrypt allows" to five attempts a minute. That is
  the whole claim.
- **Nothing consumes `require_actor` in production yet.** Task 2.2 is its
  consumer. Until then the only authenticated route is the one in the test file.
- **`auth.session_lifetime_days = 30` is unmeasured judgment**, same standing as
  `CHUNK_MAX_TURNS`. So is `login_max_attempts_per_minute = 5`.
- **No password strength rules, no lockout, no MFA, no reset flow.** The
  operator CLI is the reset path. `scripts/set_password.py` refuses only an
  empty password and a mismatched confirmation.
- **Authentication is now real; authorization above it is unchanged.** An
  `Actor` from a token is proven rather than asserted, but the capability
  registry still holds only `settings.read` and `settings.write`.

## Follow-up — BUILD_PLAN.md row, DRAFTED NOT APPLIED

Per the task, the exact line to insert in Phase 2's table **immediately before
the Agent loop row**, for review before it lands:

```markdown
| **Chat endpoint authentication** — `POST /api/login` exchanging a name and password for a signed, stateless session token; `require_actor` dependency turning an `Authorization: Bearer` header into the `Actor` role gating already consumes. Design of record `docs/AUTH_DESIGN.md` (A1–A11). No sessions table and no schema change: the token is HMAC-SHA256 signed and carries only `user_id` and an absolute expiry, so `role` is read fresh per request and cannot go stale. `hashlib.scrypt` from the standard library — no new dependency. `auth.session_secret` is bootstrap-only and the server **refuses to start without it**. **The agent loop depends on this**: it is what makes 2.2's obligation (c) — construct a real `Actor` rather than reaching for `Actor.operator()` — satisfiable rather than waived. **A network-position check is not authentication**; the loopback gate (`docs/ROLE_GATING_DESIGN.md` R2) is separate and still owed by the admin-panel task. | **3** | Opus |
```

Also owed, and not done here because it is that task's to make: the Agent loop
row's obligation (c) currently ends *"this task either depends on an
authentication task or must state plainly that the actor is asserted, not
proven."* Once the row above lands, that sentence should name this task instead
of describing a choice.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes** — no memory path touched; the only new
   writes are `password_hash` and `last_seen_at` on `users`.
5. Traceable derived artifacts? **N/A.**
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Unchanged.**
9. Autonomy more cumulative? **Neutral** — this is a gate, not a capability.
10. Anam/entity distinction preserved? **Yes** — no entity-facing text.
11. Migration required? **No.** `password_hash` and `last_seen_at` already
    existed on `users`; both were previously written by nothing.
12. Tests? **Yes**, 33, including five deliberately-broken-guard checks.
13. Core substrate changed unnecessarily? **No.** `db.py` gained two write
    functions, both decorated with the existing `@retry_on_locked`; nothing in
    `transaction()`, `connection()` or `_configure()` changed.
14. External dependencies added? **None** — stdlib `hashlib`, `hmac`,
    `secrets`, `base64`, `threading`, `getpass`.
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** The reference build was not
    consulted; this task does not point at it.
