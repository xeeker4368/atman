# 2026-09-02 — `db.py` write contention: bounded retry

**Tier 3 · Sonnet · design approved before implementation.**
Design of record: `docs/DB_CONTENTION_DESIGN.md`, C1–C8.

## Summary

`db.connection()` could raise `database is locked` when another connection held
a lock longer than the busy timeout — the shape `ops/backup.py` creates while
holding a snapshot. Writes now retry against a **wall-clock deadline**.

## Files changed

Created: `tests/test_db_contention.py` (13), `docs/DB_CONTENTION_DESIGN.md`.
Modified: `program/memory/db.py` (`retry_on_locked`, configurable timeout, six
decorated writes), `program/settings/store.py` (two decorated writes),
`program/config.py`, `config/defaults.toml`, `BUILT.md`.

No schema change.

## The measurement that chose the fix

| Scenario | Failures |
|---|---|
| 8 writers × 40 writes, no held lock | **0 / 320** |
| 4 writers, lock held **2s** (< timeout) | **0 / 48** |
| 4 writers, lock held **11s** (> timeout) | **4 / 48 (8%)** |

**The trigger is a lock held longer than `busy_timeout`, not writer-vs-writer
contention** — the timeout absorbs that completely. Failures landed at
`db.py:275` (inside `save_message`'s body) and `db.py:122` (`COMMIT`), *not*
during connection setup, which ruled out retrying the handshake.

## Before / after, same scenario, production settings

```
WITH FIX (deadline=30, timeout=10)
  save_message hit lock contention (attempt 1, 10.4s elapsed); retrying in 0.03s
  ... x4
  attempted=48  ok=48  FAILED=0 (0%)     archive=48 working=48

FIX DISABLED (deadline=0)
  save_message gave up after 10.4s of lock contention (0 retries)
  attempted=48  ok=45  FAILED=3 (6%)     archive=45 working=45
    2x at db.py:384 conn.execute(...)      1x at db.py:130 conn.execute("COMMIT")
```

Note the retries fire ~10.4s in — the busy timeout expiring — and succeed within
~0.05s, because by then the holder has released.

## Why a deadline, not an attempt count

Approved as such, and it is the right shape: the worst case is expressible in
seconds rather than in "however long N attempts happen to take."

**Stated precisely, because the name could mislead:** the worst case is
`write_retry_deadline_seconds + busy_timeout_seconds` ≈ **40s**, not 30s. The
deadline gates whether a new attempt may *start*; an attempt beginning just
under it can still wait a full busy timeout. That is written into
`defaults.toml` beside the value.

Retry costs nothing when there is no contention — `busy_timeout` returns the
moment a lock frees, so the sleeps only accrue when a lock is genuinely held.

## Why it wraps functions rather than living in `transaction()`

**A `@contextmanager` cannot replay its caller's body.** By the time `__exit__`
sees the exception, the body has already run. Combined with the measurement that
failures occur *inside* the body and at `COMMIT`, there is no earlier point to
retry from — so retry wraps whole units of work.

Eight functions decorated, each already a self-contained `with transaction()`
block, so no body changed:

`create_user`, `start_conversation`, `end_conversation`, `save_message`,
`insert_chunk`, `mark_conversation_chunked`, `store.set`, `store.clear`.

## What is deliberately not retried

- **`migrations.run_working_migrations()`** — found by walking the AST rather
  than reading. Its transaction body calls arbitrary `migration.apply(conn)` and
  mutates Python state (`applied.append`, `known.add`) that a SQL rollback
  cannot undo. It also runs once, single-threaded, at startup. A test asserts it
  carries no `__wrapped__`.
- **`sqlite3.IntegrityError`** — `insert_chunk` relies on a duplicate-index
  violation as the arbiter between two concurrent writers, so retrying would
  spin on a genuine conflict. Tested.
- **Non-lock `OperationalError`** (`no such table`) — permanent. Tested.

Every other `transaction()` body was confirmed pure SQL plus `now_iso()`, and
`chunking.py` contains no `db.transaction()` block at all — so a retry
re-running an embedding call is structurally impossible, which was the specific
worry worth checking.

## The atomicity guarantee — verified, not assumed

`AGENTS.md`: *"a fix verified only against the lock-timeout symptom can weaken
the guarantee without failing anything."*

**No interaction, and here is why that is a conclusion:**

1. Retry runs entirely outside `transaction()` — it sees the exception only
   after the context manager committed or rolled back and `connection()`'s
   `finally` closed the connection. It cannot observe or resume a half-open
   transaction.
2. Each attempt opens a **fresh** connection that re-`ATTACH`es the archive and
   re-applies both journal-mode pragmas. Attempt *n+1* shares no state with *n*.
3. No statement, isolation level, or lock ordering changed. A retried write is
   byte-identical to a first attempt.
4. `DELETE` journaling is untouched — the reason for it (SQLite guarantees
   atomic cross-attachment commit only when no participant is in WAL) is
   unaffected by how many times a transaction is attempted.
5. The guarantee is a property of *one* transaction. Retry changes how many are
   attempted, never what one does.

**The mode that would weaken it — a partial commit a retry duplicates — is
closed and now tested rather than argued.** A `COMMIT` raising `SQLITE_BUSY` in
rollback-journal mode did not commit: it failed to take the exclusive lock, the
transaction stays open, and `transaction()` rolls it back.
`test_no_write_is_duplicated_by_a_retry` asserts no duplicate content survives a
contended run, and both contention runs above show `archive == working`.

## The reproduction test, both directions

`busy_timeout` is now configurable, which lets the test set it to 0 so a ~1s
hold reproduces what an 11s hold does against the shipped 10s timeout — a
fiftieth of the wall time, and deterministic.

- `test_writes_survive_a_lock_held_longer_than_the_busy_timeout` — zero errors,
  all writes land.
- **`test_without_retry_contention_actually_fails`** — sets the deadline to 0
  (pre-fix behaviour) and **requires** `database is locked`. This is what makes
  the suite evidence rather than decoration: a test that only passes after a fix
  cannot distinguish "fixed" from "never reproduced".
- `test_even_unretried_failures_leave_the_stores_consistent` — losing the race
  was always an availability problem, never an integrity one.

13 tests, stable across 4 consecutive runs.

## The previously flaky test

`test_a_write_during_the_snapshot_cannot_land_in_one_store_only` **asserts
exactly what it asserted before** — the fix changes no SQL semantics, so
"consistent" means what it meant. Only its reliability changed: 8 consecutive
runs, all passing (it previously failed roughly 1 full-suite run in 6).

Its 5ms writer pacing was left in place. It was added to stop a zero-pause
writer starving the snapshot's own connection; removing it would change what the
test exercises, which is a separate question from this fix.

## Judgment values, flagged

| Setting | Value | Basis |
|---|---|---|
| `database.busy_timeout_seconds` | 10 | unchanged; now configurable |
| `database.write_retry_deadline_seconds` | 30.0 | **JUDGMENT** — tolerates a ~30s snapshot hold. Nothing has measured how long a large-store snapshot takes. |
| `database.write_retry_base_delay_seconds` | 0.05 | **JUDGMENT** — doubling, ±50% jitter, capped at 1s |

## Known limitations

- **The root cause is backup's lock-hold duration**, which scales with database
  size. Retry raises tolerance to ~40s; it does not shorten the hold. A
  large enough store still exceeds it — a backup-side concern, out of scope.
- **Worst case is ~40s of blocking** before a caller raises. Long for a chat
  turn, and task 2.2 should know it when reasoning about turn latency.
- **A genuinely stuck lock still fails**, correctly, with the same clean
  `OperationalError` and nothing written.
- **Cross-process contention is unchanged in kind** — `scripts/backup.py` runs
  as its own process; retry makes losing survivable, not impossible.
- **The 30s deadline is untested against a real large-store snapshot**, because
  no large store exists.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes** — and strengthened: writes that previously
   failed under contention now land. No write is altered or duplicated, asserted
   by test.
5. Traceable derived artifacts? **N/A.**
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **N/A.**
9. Autonomy more cumulative? **Neutral.**
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No.**
12. Tests? **Yes**, 13, including a test that proves the bug still exists with
    the fix disabled.
13. Core substrate changed unnecessarily? **No.** `transaction()`,
    `connection()` and `_configure()` keep their semantics; the only change
    inside them is that the hardcoded `timeout=10` became a config read.
14. External dependencies added? **None** — stdlib `functools`, `random`, `time`.
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** The reference build was not
    consulted; this task does not point at it.
