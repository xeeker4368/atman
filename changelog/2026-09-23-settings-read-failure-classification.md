# Settings reads: an unreadable table is no longer "no row"

Date: 2026-09-23 · merged-queue item 1 · confirmed from the external review's finding 3,
verified against code before implementing

## What was wrong

Three parts, and the third is the one that would have misled whoever investigated.

1. **`_load_table()` caught every `sqlite3.OperationalError` and returned `{}`**, with a
   comment naming only the intended case (*"no settings table yet"*). That class is also what
   SQLite raises for `database is locked`, `unable to open database file` and `disk I/O error`.
2. **`_table()` cached that `{}` unconditionally**, and `invalidate()` is called only by
   `set()`/`clear()`. So **one transient lock reverted every settings-backed value to its
   config seed for the lifetime of the process** — an operator-set chat model or temperature
   silently stopped being the one in use, with a single DEBUG line as the only trace.
3. **`describe()` then reported `source="config"`**, positively asserting that no row existed
   — a provenance the code had not established. The diagnostic built to answer *"is the read
   path really settings-first?"* answered it wrongly, in the one situation where it mattered.

The read path also had no retry, while `set()` and `clear()` in the same module both carry
`@retry_on_locked` — so the contended path was the unprotected one.

Reachable, not theoretical: `db.connection()` runs `PRAGMA journal_mode = DELETE` plus an
`ATTACH` before any query, and the recorded concurrent writers are the post-response idle
sweep and `backup.py`'s cross-store read lock.

## What changed

**Classify instead of flatten.** `no such table` stays benign — `{}`, cached, fresh-store
behaviour preserved and tested. Anything else raises the new `SettingsUnavailableError`.

**Only a successful load is cached.** A failed read leaves the cache empty, so the next call
retries rather than inheriting the failure forever.

**`_read_rows()` is split out and carries `@retry_on_locked`.** A test found that the first
version put the classification *inside* the retried function, so the `except` caught the lock
error before the decorator could ever see one — **the retry was dead code.** The retry has to
sit under the classification, and the split is what makes that true rather than incidental.

**The two callers diverge deliberately**, mirroring the split this module already draws:

- `resolve()` — machine-facing, runs inside a turn — **degrades**: logs at **WARNING**, returns
  the seed for that call only, caches nothing. `prompt.py`'s recorded criterion: degrade when
  nothing can be corrupted and a person is waiting; and retrying is free here because the
  failure is not cached.
- `get()` — person-facing — **raises**. Answering "what is this set to" with the seed, when the
  row could not be read, is a wrong answer to the exact question asked, and nothing waits on it.
- `describe()` gains a third `source` value, **`"unavailable"`**, carrying the seed as a
  fallback rather than as provenance. `describe_all()` still lists every setting instead of
  failing whole because one read lost a race.

## Tested

6 new tests, **each proven to bite by breaking the fix and re-running**:

| break | what failed |
|---|---|
| flatten every `OperationalError` to `{}` (the original defect) | 3 tests |
| swallow-and-cache the failure | 3 tests |
| cache `{}` **and** re-raise (the exact original shape) | `assert 'gemma4:26b' == 'operator-chosen-model'` |
| drop `@retry_on_locked` from the read | the retry test |

Failures are injected at `db.connection` — the real seam — rather than by patching
`_load_table`, which would skip the classification under test. The unavailable-path tests use
a non-lock error so they do not spend the retry deadline; the lock path has its own test.

Preserved and pinned: a missing settings table is still the benign seed case, and a corrupt or
mistyped row still raises `SettingTypeError` (unchanged).

Full suite **1170 passed, 2 skipped** (was 1164/2), `ruff check .` clean.

## Known limitations

- **`resolve()` still runs a turn on seed values when the table is unreadable.** That is the
  chosen degradation, not an oversight — but it means a turn can use a different temperature
  than the operator set, now at WARNING and for one call rather than silently and forever.
- **Nothing counts the WARNING.** A store under sustained contention would degrade repeatedly
  with visibility only in the log — the same gap recorded against `turn._after_durable()`.
- **No admin panel consumes `source="unavailable"` yet**, because there is no admin panel. The
  value exists for diagnostics and for whatever builds that surface.

## Scope note, flagged rather than decided

This touches lock-error handling, which `AGENTS.md` makes a Tier 3 checkpoint **for
`program/memory/db.py`**. This change is in `program/settings/store.py`, adds no new locking
semantics, and reuses `db.retry_on_locked` exactly as the two writers in this module already
do. Treated as in scope; raise it if it should have gone up as its own checkpoint.
