# 2026-09-18 — `db.py`'s write contention: measured, and a real gap closed

**Asked for before task 3.4:** a real read on what closing `BUILT.md`'s recorded
`database is locked` issue would take, and whether it is cheap enough to do now
rather than after the correction path sees traffic. The honest answer has two
halves, and only one of them was cheap.

Nothing committed.

## Why it was asked again

Task 3.3's design review corrected C9: *"db.py's contention fix, recommended not
blocking"* rested on a **"no new concurrent writer"** claim which the
self-correction path makes false. The entity's own correction is written from the
same turn that is already writing messages, so the recommendation had to be
re-tested rather than inherited.

## What was measured, not reasoned about

Two lock regimes, run directly against real databases:

**Under `BEGIN EXCLUSIVE` held by another connection** — everything blocks,
including connection setup:

| operation on a new connection | result |
|---|---|
| `PRAGMA journal_mode = DELETE` (what `_configure()` runs) | blocked 2.10 s → `database is locked` |
| `PRAGMA journal_mode` — **read-only** | blocked 2.10 s → `database is locked` |
| `SELECT` | blocked → `database is locked` |

That second row **kills the fix I was going to propose.** The plan was to have
`_configure()` *verify* the journal mode instead of setting it, so a reader would
not need a write lock to open a connection. Reading the pragma takes the same lock.
The hypothesis was wrong and measurement is what said so; it is recorded here
rather than quietly dropped.

**Under the lock `backup.py` actually holds** — `BEGIN` plus a `SELECT` on both
stores, which is SHARED, not EXCLUSIVE:

| operation on a new connection | result |
|---|---|
| `PRAGMA journal_mode = DELETE` | 0.00 s, fine |
| `SELECT` | 0.00 s, fine |
| `INSERT` + `COMMIT` | blocks for the snapshot, then `database is locked` |

So the failure the suite intermittently sees is **a blocked write, not a blocked
connection** — and a blocked write is exactly what `retry_on_locked` already
retries. No production path holds `EXCLUSIVE`.

## The actionable defect, which was not the one recorded

Enumerating every function in `db.py` that opens a write transaction against the
ones carrying `@retry_on_locked` returned:

```
write functions WITHOUT @retry_on_locked: ['create_supersedes_link',
                                           'set_message_integrity_advisory']
```

Both were added **in this session** — the advisory channel's writer and the
correction classifier's. The newest write paths, including the one C9 was about,
were the only unprotected ones. Two decorators, applied.

`tests/test_db_contention.py::test_every_write_in_db_carries_the_retry` now
enumerates the module the same way, so the next writer cannot ship bare. It is a
structural guard for the same reason O7's enforcement is: this gap was invisible
to reading and took an enumeration to find. 47 tests in
`test_db.py` + `test_db_contention.py` pass.

## The read, plainly

**Not cheap, and not needed.** Closing the recorded issue properly means one of
`busy_timeout` tuning, write serialisation, or WAL — and WAL is off the table
because cross-database atomicity depends on `DELETE` journaling. Serialisation is
a lock discipline over the whole module and is Tier 3 on `AGENTS.md`'s list for a
reason: a fix verified only against the lock-timeout symptom can weaken the
atomicity guarantee without failing anything.

**But the symptom that motivated urgency is already covered.** The correction
writer is not a new class of risk now that it carries the retry; it is an ordinary
writer competing with the backup snapshot for milliseconds. The residual is
unchanged from what `BUILT.md` records: a genuinely saturated writer can still
exhaust the retry deadline and raise, with nothing written.

**Recommendation: the Tier 3 task stays open and stays unscheduled.** What
changed is that it is no longer standing behind an unprotected writer.

## Known limitations

- The retry covers whole functions, from outside `transaction()` — it cannot
  retry *within* a transaction, and does not try to.
- Nothing here measures the retry under real concurrent load; the evidence is the
  lock-behaviour table above plus the existing tests.
- `test_a_write_during_the_snapshot_cannot_land_in_one_store_only` remains
  intermittently flaky for the recorded reason. This work does not claim to fix
  it.
