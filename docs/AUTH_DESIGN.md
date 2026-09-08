# Chat endpoint authentication — design for approval

**Tier 3 · Opus · design only, no code written.**

Decisions are numbered `A1`–`A11` so later work can cite them the way task 1.5's
design is cited (`the retrieval design, D4`).

This is a **dependency of task 2.2**, not part of it. `BUILD_PLAN.md`'s 2.2 entry
already states the problem in its own words: *"authentication itself does not
exist: `users.password_hash` is written by nothing and read by nothing, so this
task either depends on an authentication task or must state plainly that the
actor is asserted, not proven."* This is that task.

**Out of scope, deliberately:** the loopback gate for the admin panel. That is
specified in `docs/ROLE_GATING_DESIGN.md` R2 and recorded against the admin-panel
task in `BUILD_PLAN.md`, which already says the thing worth repeating here —
**a network-position check is not authentication; both are needed.** Loopback
answers *which machine*; this answers *which person*.

---

## 0. What this consumes, unchanged

| Component | Status | Verified |
|---|---|---|
| `users.password_hash TEXT` (nullable) | exists, written by nothing, read by nothing | `program/memory/schema/working.sql:57` |
| `users.role` (`admin` \| `user`, CHECK-constrained) | exists | same file, line 56 |
| `users.last_seen_at TEXT` (nullable) | exists, written by nothing | same file, line 59 |
| `db.get_actor(user_id) -> Actor \| None` | exists; returns `None` for unknown id | `program/memory/db.py:302` |
| `Actor`, `Role`, `require()`, `CAPABILITIES` | exist, frozen | `program/settings/permissions.py` |
| `hashlib.scrypt` | present, OpenSSL-backed | Python 3.14.5 on this machine |

**No schema change is proposed.** No new table, no new column, no migration.
That is a deliberate constraint on this design, not a happy accident — see A1.2.

---

## 0b. Threat model, stated before the decisions

Two people, one LAN, no public exposure (`PROJECT.md`). The property being
bought is: **one household member cannot trivially act as the other.** Concretely
— Jodie cannot reach Lyle's admin surface or write memory attributed to Lyle,
and a device on the LAN that has never been given a credential cannot chat at
all.

Three things are explicitly **not** in the model, and each is stated again where
it bites:

1. **A determined attacker with LAN access.** They have the wifi key by
   assumption in a household, and there is no TLS (A2, limitation 3).
2. **Lyle.** He is admin, holds the database file, and can read or write any
   row. No design here constrains him, and none should pretend to.
3. **Compromise of the Mac mini itself.** If the host is owned, everything here
   is moot.

---

## A1 — Mechanism: password login issuing a signed session token

**Recommended, and flagged as a genuine choice — see open question 1.**

The two candidates, argued on this project's own terms rather than in general:

### A1a — Static per-user shared secret (no login flow)

A long random string per person, presented on every request. Minimal: no login
route, no password hashing, no token format, no expiry.

**What it costs is the recovery story.** The secret *is* the credential,
permanently. If a phone is lost, the only remedy is to generate a new secret and
re-enter it by hand on every other device that person owns — at exactly the
moment you least want a provisioning chore. There is also no way to type it: it
is pasted, so it must live in a password manager or a note, which is a second
place it can leak from.

### A1b — Password login issuing a session token *(recommended)*

`POST /api/login` with name + password → an opaque token the client sends on
every subsequent request. Password verified against `users.password_hash`.

**The two things that make this look expensive both collapse under inspection:**

- *"It needs a password-hashing dependency."* It does not — `hashlib.scrypt` is
  in the standard library and is measured on this machine in A4. **No new
  dependency.**
- *"It needs a sessions table, which is a schema change and its own Tier 3
  pass."* It does not — see A1.2.

What it buys: a credential a human can type, revocation that does not require
touching any device (A1.2), and `password_hash` used for the purpose the schema
already carved out for it — the working schema's own comment says *"Web
credentials live on `users` instead."*

**Recommendation: A1b.** The deciding argument is not the happy path, where both
are equivalent, but the failure path, where A1a requires per-device manual work
and A1b requires changing one value. The extensibility argument (guests later)
is *not* being leaned on: this project refuses seams for hypothetical futures
(decision #15), and it should not adopt one here either.

### A1.2 — Stateless signed token, not a sessions table

The token carries its own claims and a signature; the server stores nothing.

```
token   = "v1" . user_id . expires_at_epoch . signature
signature = HMAC-SHA256(auth.session_secret, "v1|user_id|expires_at")
```

Verified with `hmac.compare_digest`. Rejected if the signature fails, if
`expires_at` has passed, or if `db.get_actor(user_id)` returns `None`.

**Why stateless.** A sessions table is a working-schema addition — a migration,
a Tier 3 schema pass, and a new write path on the hot request route, in a
codebase whose one known concurrency problem is write contention
(`docs/DB_CONTENTION_DESIGN.md`). Stateless costs none of that and needs no
migration.

**What stateless gives up, stated plainly: there is no per-device revocation.**
Rotating `auth.session_secret` invalidates every token for every person at once.
At two people that is an acceptable blunt instrument — "everyone logs in again"
is a minor inconvenience, not an outage. **If per-device revocation is ever
wanted, the upgrade is a sessions table**, and it is recorded here so it is not
rediscovered from scratch.

**`role` is deliberately NOT in the token.** Only `user_id` is. Role is read
fresh from the database on every request via `db.get_actor()`, so a role change
takes effect on the next request rather than at the next login, and a token can
never assert a role the database disagrees with.

### A1.3 — Where the signing secret lives

`auth.session_secret`, **bootstrap-only** — read from `ANAM_AUTH_SESSION_SECRET`
or `config/local.toml`, never settings-backed. This follows `config.py`'s
existing bootstrap-only class (paths, ports, hosts): those keys are read before
or outside the settings table, and a signing key that the admin panel could
edit is a signing key the admin panel can accidentally rotate.

**If it is unset, the application refuses to start** — `ConfigError`, the same
fail-closed treatment `config.py` already gives bad values. Serving chat
unauthenticated because a config key was missing is the one failure mode this
whole task exists to prevent.

Generated once by the operator: `python -c "import secrets;
print(secrets.token_urlsafe(32))"`. Never auto-generated, because silent
generation means a wipe or a fresh checkout rotates it without anyone noticing
and every device is logged out for no visible reason.

---

## A2 — The credential is presented in the `Authorization` header

```
Authorization: Bearer <token>
```

**Query parameters are ruled out**, not weighed: `logs/anam.log` and uvicorn's
access log both record request paths, so a token in the query string is a
credential written to disk in plaintext on every request, and it travels in
`Referer` headers and browser history besides.

**Cookies were considered and rejected for this build.** An `httpOnly` cookie is
genuinely better against token theft by XSS. It also introduces ambient
authority — the browser attaches it to any request to the origin, which is CSRF,
which then needs `SameSite` plus origin checking to close. The React chat client
is a same-origin `fetch` client, so an explicit header costs it nothing and
eliminates CSRF by construction rather than by mitigation.

**The honest cost, recorded rather than glossed:** a header token has to live in
JavaScript-reachable storage, so any XSS in the chat UI can read it. That risk
is lower here than in a typical web app — LAN-only, no third-party scripts, no
ad tech, two users — but it is not zero, and "we chose the XSS-exposed option"
should be findable later.

---

## A3 — `users.password_hash` is used as designed

Used as intended: a password hash, compared on login. Not repurposed.

**`NULL` means "no password set", and a `NULL` hash can never authenticate.**
This is load-bearing: the seed-corpus users (Lyle, Jodie) have `NULL` today, so
the system fails closed on the day this lands and stays closed until an operator
sets a password (A9).

**Stored format is self-describing**, so parameters can change without a
migration and without invalidating existing rows:

```
scrypt$n=65536$r=8$p=1$<salt_b64>$<derived_b64>
```

Verification parses the parameters out of the stored string rather than reading
them from config. A row hashed at old parameters keeps verifying; config values
apply to the *next* hash written. Reading current config at verification time
would silently break every existing password the moment a parameter changed.

---

## A4 — Hashing: `hashlib.scrypt`, standard library, **no new dependency**

This project is deliberately dependency-averse — `requirements.txt` opens with
*"Later phases add their own, each with a note in the changelog entry saying why
it was needed."* So this is stated explicitly rather than slipped in:
**`argon2-cffi`, `bcrypt` and `passlib` are all rejected, and nothing is added
to `requirements.txt`.** `hashlib.scrypt` is stdlib, OpenSSL-backed, and a
legitimate memory-hard password KDF — not a hand-rolled construction, which
would be the thing to refuse.

**Measured on this machine today** (Mac mini, Python 3.14.5), `r=8, p=1,
dklen=32`:

| `n` | time |
|---|---|
| 2^14 (16384) | 27.4 ms |
| 2^15 (32768) | 47.2 ms |
| 2^16 (65536) | **92.4 ms** |

**Proposed: `n = 2^16`, `r = 8`, `p = 1`, `dklen = 32`, 16-byte salt from
`secrets.token_bytes`.** ~92 ms is a cost paid once per login, not per request,
and logins are rare by design (A5). Comparison uses `hmac.compare_digest`.

**Implementation gotcha, found by running it rather than by reading the docs:**
Python's default `maxmem` is too small for `n ≥ 2^15` — `hashlib.scrypt` raises
`ValueError: [digital envelope routines] memory limit exceeded` at both 2^15 and
2^16, while 2^14 succeeds. `maxmem` must be passed explicitly (memory used is
roughly `128 · n · r` ≈ **67 MB** at the proposed parameters). Recording it here
because the failure appears only at the parameters we are proposing, and a
implementer who tests at 2^14 will not see it.

---

## A5 — Session lifetime: 30 days, absolute — **JUDGMENT, flagged**

`auth.session_lifetime_days = 30`. Same standing as `CHUNK_MAX_TURNS` and
`SOUL_MAX_CHARS`: **nothing measured this**, and it should not be read as if
something had.

**Absolute, not sliding.** A sliding window renews on every request, so a stolen
token stays valid indefinitely as long as it is used — the property you least
want. Absolute expiry means every credential has a known death date. The cost is
a login roughly monthly per device, which for household devices is noise.

The reasoning behind *30* rather than 7 or 90: short enough that a lost device's
access ends without intervention, long enough that people are not typing
passwords on phones weekly. That is a judgment about household friction, not a
measurement, and it is flagged as such.

---

## A6 — Producing the `Actor`: this feeds the existing system, changes nothing in it

```
request → Bearer token → verify signature + expiry → user_id
        → db.get_actor(user_id) → Actor(user_id, name, role)   [or 401]
        → passed to everything already taking an actor
```

**Confirmed: no change to `program/settings/permissions.py`, and no change to
`CAPABILITIES`.** Checked against the live module rather than assumed —
`Actor` is a frozen dataclass of exactly `user_id`, `name`, `role`; `get_actor`
already returns `None` for an unknown id, which becomes a 401; `require()`
already raises `TypeError` rather than proceeding when handed a non-`Actor`.

This satisfies `BUILD_PLAN.md` task 2.2's obligation (c) — *"Construct a real
`Actor` from the request rather than reaching for `Actor.operator()`"* — by
making the real actor available. **`Actor.operator()` must remain unreachable
from HTTP**: it is the shell/script sentinel, and no request path may construct
it. A test should assert that, because the sentinel is precisely the
always-allowed path 2.2's note warns becomes dangerous once untrusted callers
exist.

**No `chat.send` capability is proposed**, deliberately. Both roles may chat, so
a capability that never denies anything would be decoration — and
`permissions.py` deliberately raises on unregistered names rather than defaulting
permissive, so the registry's value comes from every entry being real. Chat
gating, if it is ever wanted, registers its own capability then.

**`last_seen_at` is updated on login only, never per request.** A per-request
write would put a database write on the hot path of every chat turn, against
the contention the retry decorator exists to survive. Login is rare; that is the
right place for it.

---

## A7 — One credential per person; one token per login

**Per person, not per device.** Each of Lyle and Jodie has one password. Logging
in on a phone and on a laptop produces two independent tokens, so devices are
already separated at the token level without a second credential to provision.

Per-device *secrets* were considered and rejected: they multiply the
provisioning problem by the number of devices while buying revocation
granularity this design structurally cannot act on anyway — stateless tokens
(A1.2) cannot be revoked individually regardless of how many secrets exist.
Per-device revocation and a sessions table are the same decision, and both are
deferred together.

---

## A8 — Failure shapes: fail closed, and say nothing useful

- **Unknown user and wrong password return the identical 401**, same body, same
  timing. The KDF runs against a dummy hash for an unknown user, so response
  time does not reveal whether a name exists. In a two-person household name
  enumeration is nearly free anyway — the cost of doing this right is one dummy
  hash, so there is no reason to be sloppy about it.
- **Missing, malformed, expired or badly-signed tokens all return 401**, never
  403 and never a specific reason. The client's response to all of them is the
  same: send the user to the login form.
- **A token for a user who no longer exists fails** — `get_actor()` returns
  `None`, which is a 401, not a crash.
- **Unset `auth.session_secret` prevents startup** (A1.3), rather than serving
  chat unauthenticated.

**Login rate limiting is an open question (5), not a decision.** scrypt at ~92 ms
already caps guessing at roughly ten attempts per second per core, which is a
real but weak brake. A minimal in-process per-name counter would help and is
honest about its limits — process-local, resets on restart, not a defence
against a distributed attacker (which is not in the threat model anyway).

---

## A9 — Setting passwords: an operator CLI, because there is no admin panel

The admin panel is a later task and out of scope here, so passwords are set by
`scripts/set_password.py <name>`, prompting via `getpass` (never an argv
argument — argv lands in shell history and `ps`). It runs as
`Actor.operator()`, which is exactly `GUIDANCE.md`'s carve-out: a human directly
driving the action.

This is also the **password reset path**. There is no self-service reset flow,
no security questions, no email — the operator sets a new password. For two
people sharing a house, that is the whole feature.

Note the asymmetry with role, and that it is intentional: `role` is fixed at
creation with no `set_role()` (a tested property of `db`), while a password is
mutable by design.

---

## A10 — What this deliberately does not build

- **TLS.** Login posts a password over plain HTTP on the LAN, and the token
  travels the same way. Anyone able to capture LAN traffic can replay both. On a
  switched network that means ARP spoofing rather than passive sniffing, but a
  household member has the wifi key by assumption, so **this is inside the
  stated threat model and is being accepted, not solved.** The upgrade path if
  it stops being acceptable: a self-signed certificate, or a WireGuard/Tailscale
  overlay. Recorded, not proposed. **Open question 6.**
- **Per-session revocation** (A1.2) — the upgrade is a sessions table.
- **Guest or third-party accounts.** Two users, both known.
- **Account lockout, MFA, password complexity rules, breach checks.**
- **Authentication for any channel other than the chat endpoint.** iMessage is
  deferred entirely; the admin panel gets loopback plus this same token check
  when it lands.

---

## Flagged constants — judgment, not derived

| Setting | Proposed | Status |
|---|---|---|
| `auth.session_secret` | **unset** | bootstrap-only; startup fails if missing (A1.3) |
| `auth.session_lifetime_days` | 30 | **JUDGMENT** (A5) |
| `auth.scrypt_n` | 65536 (2^16) | measured 92.4 ms here; the *target cost* is judgment (A4) |
| `auth.scrypt_r` / `auth.scrypt_p` | 8 / 1 | standard scrypt parameters |
| `auth.login_max_attempts_per_minute` | 5 | **JUDGMENT**, and only if open question 5 says build it |

All new keys need explicit entries in `config.py`'s env map — the `ANAM_*`
mapping is a registry, not a naming convention, so an unregistered key is
silently unreadable from the environment.

---

## Test plan

Against real rows and a real store, in the style of `tests/test_db_contention.py`
(assert outcomes, not the presence of a check):

1. **Correct password logs in; wrong password does not** — and the two responses
   are byte-identical apart from the token.
2. **Unknown user and wrong password are indistinguishable** — same status, same
   body.
3. **A `NULL` `password_hash` can never log in**, even with an empty password.
4. **A tampered token fails** — flip one bit of the signature, expect 401.
5. **An expired token fails**, with expiry frozen rather than slept.
6. **Rotating `auth.session_secret` invalidates existing tokens.**
7. **A token whose user was deleted fails** rather than raising.
8. **The token is only accepted from the header** — the same token as a query
   parameter is rejected (this is the log-leak rule, and it should be a test,
   not a comment).
9. **The produced `Actor` matches the database row**, role included.
10. **A role changed in the database takes effect without a new login** — the
    A1.2 property that role is not in the token.
11. **`Actor.operator()` is unreachable over HTTP** — no request produces one.
12. **Stored-parameter verification** — a hash written at `n=2^14` still
    verifies after config moves to `2^16` (A3).
13. **Missing `auth.session_secret` fails startup** rather than serving chat.

---

## Open questions for approval

1. **A1 — mechanism.** Password login + token (recommended) vs. a static
   per-user secret. This is the real decision; everything else follows from it.
   The recommendation rests on the recovery path, not the happy path.
2. **A1.2 — stateless token vs. a sessions table.** Stateless needs no
   migration and no write on the request path, at the cost of per-device
   revocation. A sessions table is a Tier 3 schema pass. Confirm stateless is
   the right trade at two users.
3. **A1.3 — where the signing secret lives.** Bootstrap-only config, with
   startup failing when unset, versus a settings-table row. I recommend
   bootstrap-only; a panel-editable signing key can be rotated by accident.
4. **A5 — 30-day absolute lifetime.** Pure judgment. 7 days is more cautious,
   90 is friendlier; nothing measured any of them.
5. **A8 — login rate limiting: build now or defer?** A minimal per-name
   in-process counter is maybe twenty lines and is honest about being
   process-local. Deferring is defensible given scrypt's cost. I lean to
   building it, but it is not required by anything.
6. **A10 — accepting plaintext-over-LAN.** Confirm explicitly that no TLS is
   wanted for this build, since the password crosses the LAN in the clear and a
   household member is inside the threat model. If that is not acceptable, this
   design needs a TLS section and the scope grows.
7. **A6 — no `chat.send` capability.** Confirm chat needs no registered
   capability, on the grounds that both roles may chat and a never-denying
   capability is decoration.
