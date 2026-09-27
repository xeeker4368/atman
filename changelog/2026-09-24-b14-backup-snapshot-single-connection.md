# B14: backup snapshot on one connection

Date: 2026-09-24 · plan B14, Option 2 · Tier 3 (locking behind the two-database
atomicity guarantee). Diagnosed, reported, and the option chosen at review before
implementation.

## What was wrong

A writer that started committing while a backup snapshot was in progress stalled the
snapshot for 10 s or more, and could fail its own commit.

The cycle:

- The snapshot's holder connection runs `BEGIN` plus reads, so it holds SHARED on both
  databases.
- The writer, at COMMIT, takes PENDING and waits for the holder to release.
- The snapshot's second connection needs a **new** SHARED lock to run `backup()`.
  SQLite refuses new SHARED locks while PENDING is held.
- Nothing moves until the writer's 10 s busy timeout lapses.

In production, that is a chat turn arriving during a backup. The two-connection design
came from finding that `backup()` hangs on a connection holding `BEGIN IMMEDIATE`. A
connection holding only a read transaction was never tried.

**How it surfaced:** Group A item 19 moved the manifest counts off the live source. That
removed the source reads that used to run before the snapshot, and the race test went
from 6/6 passing to 4/6 failing. Putting one throwaway read back restored 6/6, so the old
code had only avoided the window by timing. The test was marked `xfail(strict=False)`
citing B14 until this landed.

## Diagnosis (scratch scripts, not in the repo)

- **Per-statement lock-timing probe.** It logged every SQLite call by thread. The writer's
  COMMIT was blocked 10.8 s, and `backup(main)` spent 10.8 s in a single step.
- **Deterministic reproduction.** The writer was forced to be waiting on COMMIT when the
  snapshot began.
  - Two-connection shape: 10.5 s stall and a failed writer, 3/3.
  - Holder running `backup()` itself: 5 ms, writer committed, copies consistent, 3/3.
- **`BEGIN IMMEDIATE` holder (rejected).**
  - It takes RESERVED on `main` then `archive`, while `save_message` writes `archive`
    then `main`. That is a cross-database lock-ordering deadlock.
  - Measured over 6 runs: 3 clean, 3 stalled 10.8 s, and 2 of those failed the backup
    itself.

## What changed

- **`program/ops/backup.py` `_snapshot_databases()`:** the holder connection runs
  `backup()` for `main`, then `archive`, itself, inside its read transaction, and then
  commits. The second connection is gone.
- **Docstrings:** the module docstring's "Why two connections" section is rewritten as
  "Why one connection, and why it used to be two", with the rejected option recorded. The
  function docstring is updated.
- **Unchanged:** nothing in `program/memory/db.py`. No lock order, retry or busy-timeout
  change.

## Tested

- **New deterministic test,
  `test_a_writer_committing_during_the_snapshot_neither_stalls_it_nor_fails`.**
  - It hooks the first `sqlite3.connect` to a backup destination. That point is after the
    holder's reads and before the first `backup()`.
  - There it starts a raw writer's COMMIT and waits 0.3 s, so the writer is holding
    PENDING.
  - It asserts that the hook fired (so the collision was really set up), that the
    snapshot took under 2 s, that the writer committed, and that the in-flight write is
    absent from both copies.
  - Against the old `backup.py`, it fails with "the snapshot stalled for 10.8s".
- **xfail removed** from `test_a_write_during_the_snapshot_cannot_land_in_one_store_only`.
  - 20 runs in isolation: 20/20 passed. 18 took 0.22 s and two took 0.43 s and 0.46 s.
  - Two full-suite runs: 0.18 s and 0.17 s.
- **Full suite:** 1194 passed, 2 skipped, twice. `ruff check .` clean.

## Known limitations

- **Writers still wait for the snapshot.** That is milliseconds at this size. It grows
  with the databases, because `backup()` copies every page in one step while holding the
  lock.
- **A writer already waiting on COMMIT before the snapshot begins delays the snapshot.**
  The snapshot's own first reads wait for that commit. There is no cycle, just ordinary
  queueing.
- **Two hazards beyond this fix are unchanged:**
  - The lock-ordering asymmetry found here (holders `main`→`archive`, `save_message`
    `archive`→`main`) still matters to any future code that takes write locks on both
    databases.
  - The general `db.py` contention note in BUILT.md still stands.
