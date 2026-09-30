# B18: a user name is capped at creation, matching login

Date: 2026-09-30 · queue item B18 · small, contained. Plan approved at review, with empty
and whitespace-only names kept explicitly out of scope.

## What was wrong

B6b bounded login names at `auth.MAX_NAME_CHARS` (128). `db.create_user` enforced
nothing, so a user could be created with a name login would always refuse, an account
that could never be used.

## What changed

- **`db.USER_NAME_MAX_CHARS = 128`**, and `db.create_user` raises `ValueError` for a
  longer name **before anything is written**, in either store.
- **`auth.MAX_NAME_CHARS = db.USER_NAME_MAX_CHARS`**: one constant. It lives in `db`
  because `auth` imports `db` and not the reverse.
- **Enforced in code, not by a `CHECK` constraint.** A constraint on `users.name` needs
  a table-recreating migration (Tier 3). This follows the precedent of the role being
  fixed at creation, which is also code-level.
- No existing caller is affected: the seed users, the entity's `__entity__` row
  (10 characters) and the B12 script all use short names.

## Explicitly out of scope, and recorded

**Empty and whitespace-only names are still accepted.** B18 closes only the length
mismatch with login. A test pins that both are accepted today, so the gap is a checked
property rather than silence, and refusing them later has to change that test on
purpose.

## Tests (`tests/test_db.py`: 4 new functions, 5 items)

- 129 characters is refused, and neither store gains a row.
- Exactly 128 is accepted.
- Creation and login share one limit.
- Empty and blank names are out of scope and still accepted (parametrised).

## Proven to bite

- Removing the length check fails the refusal test.
- **Giving `auth` its own literal `128` did NOT fail the first version of the
  shared-constant test.** That version asserted `auth.MAX_NAME_CHARS is
  db.USER_NAME_MAX_CHARS`, and Python caches small integers, so two independent `128`
  literals are the same object. The test now changes `db`'s value in a fresh interpreter
  before `auth` is imported and asserts `auth` follows it. With the copy in place, it
  fails.

**Full suite:** 1,323 passed and 2 skipped (1,318 before); `ruff` is clean.
