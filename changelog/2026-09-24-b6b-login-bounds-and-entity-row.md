# B6b: login input bounds, throttle memory, and the entity row

Date: 2026-09-24 · merged-queue items 16 (login half) and 24 · plan B6b · Tier 3
(`AGENTS.md`'s authentication checkpoint: credential verification, token acceptance, and
which `Actor` a request produces). Plan approved before implementation; split from B6a
at review.

## What was wrong

1. **Unbounded pre-auth input.** `LoginRequest.name` and `.password` had no length
   limit. Before anyone had authenticated:
   - the name became a key in the throttle's dict and went into the "login throttled"
     log line;
   - the password went into scrypt.
2. **Throttle memory grew without bound.** `LoginThrottle._prune` only tidied the name
   being asked about. A name that failed once and was never tried again stayed in the
   dict for the life of the process, so every distinct submitted name accumulated.
3. **The entity's reserved row could become an account** (`NOW.md` backlog).
   - `__entity__` is a real `users` row, kept inert only by a NULL `password_hash`.
   - `scripts/set_password.py` would set a hash for any name. After that the row could
     log in, and every route behind `require_actor` would accept it.
   - Nothing prevented it and nothing detected it.

## What changed

**Input bounds** (`program/auth.py`):

- `MAX_NAME_CHARS = 128` and `MAX_PASSWORD_CHARS = 1024`, with `within_bounds()`. These
  are judgment values, sized far above any real household credential.
- The check is the first thing in `auth.login()`, before the throttle. An over-long
  attempt:
  - returns `None`, so the route sends the same 401 bytes as every other failure (A8);
  - never touches the throttle's dict;
  - never reaches the KDF;
  - logs only the input lengths, never the name.

**Throttle** (`program/auth.py`):

- `record_failure()` first calls a new `_sweep(now)`, which drops every name whose
  failures have all left the one-minute window.

**The entity row**, refused at four points:

- `db.set_password_hash` raises `ValueError` for the reserved row, inside the same
  transaction as the update. This is the one write path every caller goes through.
- `scripts/set_password.py` refuses `__entity__` by name before prompting, with a plain
  explanation for the operator. It also refuses a password over `MAX_PASSWORD_CHARS`,
  so a password login would refuse cannot be set.
- `auth.login` refuses the name after running the same dummy verification an unknown
  name gets, so timing cannot single it out.
- `auth.actor_for_header` refuses a valid signed token whose id is the entity's row. A
  token for that id could only exist if something issued one around `login()`, and
  this makes sure no route behind `require_actor` ever runs as the entity.

`NOW.md`'s backlog item is marked closed.

## Tested

10 new tests in `tests/test_auth.py`:

- An over-long name gets the same 401 bytes as a wrong password. It is not added to the
  throttle's dict, it is absent from the log, and "over length" is logged.
- An over-long password is refused before `verify_password` (a scrypt stand-in that
  raises if called).
- Inputs exactly at the bounds go through the normal path and are counted by the
  throttle.
- 100 names that failed at t=0 are gone after a failure at t=61, leaving only the new
  name.
- A sweep keeps failures that are still within the window.
- `set_password_hash` on the entity raises, and the hash stays NULL.
- The entity cannot log in even with a hash written directly past the guard.
- A signed token for the entity's id is refused, both by `actor_for_header` and by a
  real route (401).
- `set_password.py` refuses the entity before prompting (the prompt raises if reached).
- `set_password.py` refuses an over-cap password, leaving the hash NULL.

**Six guards, six break tests.** Each guard was removed in turn, and each failed its own
test:

| guard removed | test that fails |
|---|---|
| bounds check | the over-long name and over-long password tests |
| throttle sweep | both throttle tests |
| entity login refusal | the "even if it had a password" test |
| entity token refusal | the entity token test |
| `db` entity guard | the `set_password_hash` test |
| script entity check | the script test |

Existing tests still pass, including `test_attribution.py`'s entity-row tests: `get_actor()`
still builds the Actor, and the refusal sits on the request path. Full suite **1231
passed, 2 skipped**. `ruff check .` clean.

## Known limitations

- **The throttle's stated scope is unchanged.** It is in-process (it resets on restart)
  and keyed by name (it can't slow guessing spread across names). The sweep bounds its
  memory to names that failed in the last minute, not to a fixed size.
- **`create_user` does not enforce the 128-character name bound.** A user created with a
  longer name could not log in. Every real name is far shorter.
- **Over-long attempts are not throttled.** They cost no KDF, so there is nothing to
  slow.
- **No live check against a running server.** The route behaviour is covered through
  TestClient.
