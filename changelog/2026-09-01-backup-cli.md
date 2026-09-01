# 2026-09-01 — Backup CLI

**Task 1.14 · Tier 0 · Sonnet.**

## Summary

`python -m scripts.backup` captures both SQLite databases and the ChromaDB
directory into a timestamped folder with a manifest. It only ever creates files:
no prune, no rotate, no overwrite.

## Files changed

Created: `anam/ops/backup.py`, `scripts/backup.py`, `tests/test_backup.py` (17).
Modified: `tests/conftest.py` (isolation guard extended), `BUILT.md`.

## What "backup" captures, and why — as asked

**The two SQLite databases use SQLite's online backup API, not a file copy, and
both are captured inside one held read lock.**

A file copy is wrong here for two separate reasons, and the second is the one
specific to this build:

1. **Torn pages.** A copy taken mid-commit can capture pages from two different
   transactions, with a rollback journal beside it that may or may not be
   coherent with them. The backup API reads through SQLite under a read lock and
   cannot observe a half-written commit.

2. **Cross-database skew — the one that matters here.** `db.py`'s module
   docstring is explicit that a message reaches `archive.db` and `working.db` in
   one transaction or neither, and that WAL is refused precisely to keep that
   guarantee. Two *independent* backups, even two individually perfect ones,
   would capture archive at one instant and working at another; a dual write
   landing between them reproduces in the backup exactly the half-state the
   write path exists to prevent. **The atomicity guarantee would hold in the
   live stores and be lost in the copy of them** — which is the failure mode the
   task asked about.

So the snapshot is:

```
connection A:  BEGIN; SELECT from main; SELECT from archive;   -> SHARED on both
connection B:      backup(dest_working, name="main")
                   backup(dest_archive, name="archive")
connection A:  COMMIT                                          -> released
```

## The design was settled by running it, not by reasoning about it

Three things were checked directly, and one of them killed the obvious approach:

| Probe | Result |
|---|---|
| `conn.backup(dest, name="archive")` reaches an ATTACHed database | **works** |
| `conn.backup()` while that same connection holds `BEGIN IMMEDIATE` | **hangs indefinitely** — Python's backup loop retries `SQLITE_BUSY` against a lock only that connection could release |
| Connection A holds `BEGIN` + reads on both; does a writer block? | **yes** — `database is locked` |
| Connection B backs up both while A holds that read lock | **works** |

The intuitive "lock writers out with `BEGIN IMMEDIATE`, then back up" deadlocks.
Two connections and a *read* transaction is what actually works, because a read
lock is compatible with the backup's own read lock while still excluding
writers.

Cost: **writers block for the duration of the snapshot.** Milliseconds at this
scale — the live run below moved 295 KB of database.

## ChromaDB is best-effort, and the manifest says so

ChromaDB is SQLite plus binary HNSW index files with no snapshot API, so it is a
plain `copytree`. The manifest records it as `best-effort`, explicitly not
`transactional`, and a test asserts the manifest does not overstate it.

That is acceptable because vectors are **derived**: every one is rebuildable
from the canonical `chunks` table — which *is* captured transactionally — via
`scripts/reconcile_vectors.py`. A skewed vector store in a backup loses no
information the archive does not still hold. The artifact note says this, so
whoever reads a manifest at 2am does not have to work it out.

## Never destructive

- **Refuses an existing destination** rather than overwriting. A test puts a
  file in the target directory and asserts it survives the refusal.
- **No prune, rotate or cleanup surface at all.** Rotation is the one part of a
  backup tool that can destroy data, nothing asked for it, so it does not exist.
  `test_there_is_no_prune_or_rotate_surface` asserts the module's public names
  contain no such function, so adding one silently fails a test.
- **No restore.** Restore is Tier 3 in BUILD_PLAN ("Restore CLI (atomic,
  verified)") and undesigned. Not written, not stubbed. The manifest carries a
  `restore` field saying so, so a backup cannot be mistaken for something with a
  restore path behind it.
- Default destinations get a numeric suffix on collision (second-resolution
  stamps collide), which keeps never-overwrite intact. An explicit
  `--destination` that exists is still refused — the caller named that path, and
  silently writing elsewhere would be worse.

## ⚠ Flagged, not fixed — write contention in `db.py`

Found while building the race test, in **already-shipped Tier 3 code**, so I
stopped rather than touching it:

**`db.connection()` can fail with `database is locked` under sustained write
contention.** Verified directly: with another connection holding `BEGIN
EXCLUSIVE`, a new connection's `PRAGMA journal_mode = DELETE` in `_configure()`
waits its full 10-second busy timeout and *then* raises. A writer looping with
no pause starves the incoming connection out entirely.

- **Not introduced by backup** — it is a property of every `db.connection()`
  call, including `save_message`. Backup only made it easy to observe, because
  it deliberately holds a lock.
- **Reachable in principle**: the FastAPI backend is single-process but
  multi-threaded, so two concurrent turns writing messages is plausible. A
  25-writes-with-no-pause loop is not realistic, but the timeout is a fixed 10s
  regardless of load.
- **Fixing it means editing `anam/memory/db.py`** (task 1.1, Tier 3, Opus) —
  `PRAGMA busy_timeout`, a retry, or serialising writes are all real design
  choices with atomicity implications. Not mine to pick.

The race test is paced (5ms between writes) with a comment saying why. It still
races the snapshot and still verifies the property task 1.14 owns — that a
concurrent write cannot land in one store and not the other — without needing a
pathological writer to do it.

## ⚠ Also flagged — the isolation guard had no backup coverage

`tests/conftest.py` captured the real data directory and the real ChromaDB path
but not the real **backup** directory, which resolves from its own config key.
So `isolated_data_dir` did not isolate it, and the first run of these tests
wrote two real backup directories into the repo at `backups/` (gitignored, test
data only; removed).

Extended the guard to capture `REAL_BACKUP_DIR` the same way, and
`isolated_data_dir` now repoints `ANAM_BACKUP_DIR` too. This is arguably task
1.14's own work — backup is the first thing to write there — rather than a
pre-existing defect, which is why I fixed it rather than only flagging it. It is
test-harness only; no production code path changed.

## Tests: 17 new, 212 total

`ruff check .` clean.

- `test_a_write_during_the_snapshot_cannot_land_in_one_store_only` — the reason
  both databases are captured under one lock.
- `test_both_stores_hold_the_same_message_count_in_the_backup` — the dual-write
  invariant surviving into the copy, not just living.
- `test_the_backup_is_a_real_readable_database_not_a_byte_blob` — opens the
  backup and queries it.
- `test_every_database_artifact_passes_integrity_check`.
- `test_the_vector_store_is_recorded_as_best_effort_not_transactional`.
- `test_it_refuses_to_overwrite_an_existing_destination` — with a file placed in
  the way, asserted to survive.
- `test_there_is_no_prune_or_rotate_surface`.
- `test_writers_are_only_blocked_for_the_snapshot_not_afterwards` — the read
  lock is actually released, or the app deadlocks after one backup.
- `test_the_manifest_hash_matches_the_file_on_disk`.

### Live run, real store with real embeddings

```
backup   : .../backups/anam-backup-20260901-155317
verified : True
  working.db        233,472 bytes  [transactional] integrity=ok
  archive.db         61,440 bytes  [transactional] integrity=ok
  chroma            551,076 bytes  [best-effort]
  rows archive.messages   20     rows working.messages   20
  rows working.chunks     10     rows working.conversations   1
total    : 845,988 bytes
```

Second immediate run produced `...-155317-2`, confirming collision handling.

## Known limitations

- **Writers block for the snapshot's duration.** Fine at this size; it scales
  with database size, not conversation count, but it is a real pause.
- **The `db.py` contention finding above** is unfixed and not mine to fix.
- **ChromaDB is best-effort**, as described. Rebuildable, but a restored vector
  store may need `reconcile_vectors.py` before it is trustworthy.
- **No compression, no encryption, no off-machine copy.** A backup beside the
  original does not survive losing the disk. Not asked for, worth stating.
- **No restore**, so a backup is currently unproven as recoverable — the files
  verify with `integrity_check` and open as databases, which is not the same
  thing as a tested restore path. That proof belongs to the Tier 3 restore task.
- **Nothing schedules this.** Manual invocation only; the scheduler is Phase 5.

## Follow-up

- The `db.py` write-contention question above.
- Restore CLI (Tier 3) — and with it, an actual round-trip test.
- Scheduling, retention and off-machine copies, whenever those are wanted.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes, emphatically** — this is the task whose whole
   job is preserving it, and it reads only. A test asserts the source stores are
   unchanged afterwards.
5. Traceable derived artifacts? **Yes** — the manifest records sha256, row
   counts, schema version, source paths and the consistency guarantee per
   artifact.
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A** — backups are operator artifacts, not
   entity memory, and are deliberately not indexed.
8. Context construction inspectable? **N/A.**
9. Autonomy more cumulative? **Neutral.**
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No.**
12. Tests? **Yes**, 17, plus a live run.
13. Core substrate changed unnecessarily? **No** — `db.py` untouched, which is
    the point of the flag above.
14. External dependencies added? **None** — stdlib `sqlite3`, `shutil`,
    `hashlib`, `json`.
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** The reference build's backup
    implementation was not consulted; this task does not point at it.
