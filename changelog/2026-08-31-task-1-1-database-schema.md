# 2026-08-31 — Task 1.1: Database schema

**Tier 3 · Opus · Checkpoint — stop here for review before task 1.2.**

## Summary

Initial schema for both SQLite stores, the connection and dual-write layer, and
a versioned migration runner. No memory is produced or retrieved yet; this is
the shape everything in Phase 1 writes into.

Direct cross-check against `reference/old-anam/tir/memory/db.py` was authorised
for this task specifically (BUILD_PLAN Phase 1 notes). What was taken from it,
and what was deliberately not, is set out below.

## Files changed

Created:

- `anam/memory/schema/archive.sql` — frozen two-table durable record.
- `anam/memory/schema/working.sql` — operational store, 9 tables + FTS5 + triggers.
- `anam/memory/db.py` — connections, `ATTACH`, explicit transactions, dual write.
- `anam/memory/migrations.py` — versioned forward-only runner.
- `tests/test_db.py` — 31 tests.
- `tests/test_migrations.py` — 8 tests.
- `docs/DB_SCHEMA.md` — the reviewable narrative.

Modified: `BUILT.md`.

## Design decisions, with reasoning

**Writes are atomic across both stores.** A message goes to `archive.messages`
and `messages` in one transaction over a connection with the archive attached.
The failure prevented is silent and asymmetric: archive-only is a memory
retrieval cannot see; working-only is a memory the durable record does not have.
Neither raises anything at the time.

**DELETE journaling, not WAL.** SQLite guarantees cross-database atomicity only
when no participating database is in WAL. WAL would be faster and is
deliberately declined. Both `main` and `archive` are pinned, and a test asserts
it rather than trusting the pragma was set.

**A canonical `chunks` table.** This is the main departure from the reference
build, which had no relational chunk row — chunks existed only inside Chroma and
FTS. That is what made orphaned index entries possible there and required a
dedicated purge tool. Here `chunks` is the source of truth and both indexes are
derived and rebuildable from it, which turns that bug class into a foreign-key
violation.

**Provenance is `NOT NULL`.** Task 1.7 requires that no chunk can be written
without provenance; a constraint holds where a convention does not. The
permitted *values* stay a Python-owned vocabulary rather than a CHECK, so adding
a source type does not need a migration.

**FTS5 as an external-content table with triggers.** Text is not duplicated, and
insert/delete/update triggers make desync structurally impossible rather than
merely unlikely.

**`supersedes` ships now**, though its classifier is Phase 3, because schema is
the most expensive thing to change late and supersession is committed work.
`CHECK (superseding <> superseded)` prevents a self-link, and two `BEFORE`
triggers prevent longer loops — see the addendum below.

## Two reference claims checked rather than inherited

1. **"executescript can't mix FTS5 virtual-table DDL with regular DDL."** The
   reference build's comment. **Does not reproduce** on SQLite 3.53.1 — verified
   directly before writing the schema. FTS5 is declared inline, so each database
   is one readable file.
2. **WAL vs. cross-database atomicity.** Confirmed both databases accept and
   report `delete` under `ATTACH`, and that the attached database needs its
   journal mode set explicitly — a WAL archive would silently break the
   guarantee.

## Tests run

- `ruff check .` — clean.
- `python -m pytest` — **53 passed** (14 from Phase 0, 39 new).
- Live smoke against a real on-disk store outside the suite: both databases
  initialise, a user and conversation are created, two messages round-trip with
  archive and working counts equal, journal modes report `delete`/`delete`, and
  `schema_version` holds exactly version 1.
- Isolation guard confirmed working: no `data/` directory exists in the repo
  after a full test run.

Tests that earn their place specifically:

- `test_failed_write_leaves_neither_store_touched` — forces a failure between
  the two inserts and proves the atomicity claim instead of asserting it.
- `test_archive_has_exactly_two_tables` — pins the frozen scope.
- `test_deferred_features_have_no_tables` — asserts the absence of review-queue
  and self-modification tables, because a seam is easiest to add by accident.
- `test_fts_index_follows_chunk_delete` / `_update` — the index cannot outlive
  what it indexed.
- `test_failed_migration_records_no_version` — a half-applied migration must not
  leave the database claiming a schema it does not have.

## Known limitations

- **No `chunks` writer yet.** The table and its constraints exist; the pipeline
  that fills it is task 1.3. Tests insert rows directly.
- **`source_type` / `source_trust` values are unconstrained at the schema
  level** until task 1.7 defines the vocabulary. Deliberate, but it means a
  typo'd source type is currently accepted.
- **No down-migrations.** Reversing a schema change on a store holding real
  memory is a restore-from-backup operation, not a routine one.
- `MIGRATIONS` is empty — correct for an initial schema, but the runner's
  apply-path is exercised only by tests until a real migration exists.

## Follow-up

- Task 1.3 writes chunks through this schema; the `text_sha256` column is there
  for it to populate.
- Task 1.7 adds the provenance vocabulary and the ranking-independence guard.
- Task 1.11 fills `settings`; the precedence rule is documented in `config.py`.
- Task 1.12 uses `users.role` and `password_hash`.

## Project Anam alignment check

1. Assign the entity a name? **No.**
2. Call the entity Anam or Tír? **No.**
3. Assign personality? **No.**
4. Preserve raw experience? **Yes** — that is the archive's whole purpose;
   append-only, frozen, and corrections layer via `supersedes` rather than edits.
5. Traceable derived artifacts? **Yes** — `first_message_id`/`last_message_id`
   trace a chunk to its raw turns; `artifacts.source_*` traces an artifact.
6. Tool calls recorded? **Yes** — `tool_trace` in both stores.
7. Created artifacts remembered? **Yes** — `artifacts` table.
8. Context construction inspectable? **N/A** — task 1.8.
9. Autonomy more cumulative? **N/A.**
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **This task is the migration system.** Archive is never
    migrated by design.
12. Tests? **Yes**, 39 for this task, plus a live smoke test.
13. Core substrate changed unnecessarily? **No** — nothing existed.
14. External dependencies added? **None.** `sqlite3` is stdlib.
15. Workspace vs. self-modification? **Preserved** — `artifacts.path` points
    into `workspace/`; no self-mod table or column anywhere, asserted by test.
16. Casual legacy renaming avoided? **Yes** — no code copied from `tir/`.

## Addendum — supersedes cycle guard (added during review)

**Defect found in review, not by me.** The `CHECK` stopped a chunk superseding
itself, but `UNIQUE (superseding, superseded)` treats `(A,B)` and `(B,A)` as
different tuples, so a two-row loop was representable — as were longer ones
(A→B→C→A). Since resolution follows links forward, any loop means resolution
never terminates. Task 3.5 as specified would have bounced between them: my plan
for it described chain resolution and a two-correction test, and never mentioned
a cycle guard.

**Fix: both a write-time guard and a read-time requirement, because they solve
different problems.**

- *Write-time, added now.* Two `BEFORE` triggers (INSERT, and UPDATE of either
  link column) walk forward from the proposed superseding chunk with a recursive
  CTE and abort if the proposed superseded chunk is already reachable — exactly
  the condition "this link closes a loop". Placed in the schema rather than in
  the classifier because the writer is not always the classifier: a restored
  store, a hand-edited row, or a future bug can all produce a link.
- *Read-time, now a documented requirement on task 3.5.* Forward resolution must
  carry a visited set and stop on revisit rather than trusting the data.

Neither replaces the other. The trigger preserves correctness — without it a
wrong classifier judgment quietly makes the store self-contradictory. The
visited set preserves termination — a cycle from a restore must not hang
retrieval mid-conversation. If only one could exist the trigger is the more
valuable, but hanging in a live turn is severe enough that the read-side check
is not optional. Recorded in `docs/DB_SCHEMA.md` under the `supersedes` section.

The trigger rejects a **link**, never a chunk, so no raw experience is touched.

**Verified before implementing:** SQLite accepts a recursive CTE inside a
trigger `WHEN` clause, and the guard rejects both 2-cycles and 4-hop cycles
while still allowing legitimate structure (one chunk superseding several things,
and two chains converging).

**One test of mine was wrong and the trigger was right.** My first
update-guard test repointed a link to produce `C→A→B`, which terminates and is
legitimately allowed; the trigger correctly permitted it and the test failed for
claiming otherwise. Fixed the test, and pinned the non-cyclic repoint as its own
case so the distinction stays explicit.

Six new tests: 2-cycle rejected, 4-hop cycle rejected, non-cycle links still
allowed, update path guarded, non-cyclic update still allowed, rejected cycle
leaves chunks and the existing link untouched. Plus one asserting forward
resolution terminates on a clean chain — the invariant 3.5 depends on.

## Also documented in this pass

**Go-live wipe procedure (Phase 10, decision #16)**, now written plainly in
`docs/DB_SCHEMA.md` so Phase 10 does not rediscover it: delete both database
files, recreate the schema fresh and empty. No special archive handling — frozen
and append-only describe normal operation, not protection from deliberate
deletion. No partial preservation and no carve-out. Nothing else needs clearing,
because Chroma and FTS are both derived from `chunks` — a property of the
canonical-chunks design that would not hold if chunks lived only in the indexes.

**Schema-change routes**, also in `docs/DB_SCHEMA.md`: new objects go in
`working.sql` (every statement is `IF NOT EXISTS` and the file re-runs on every
startup); anything that alters or backfills existing data needs a migration.
Worth stating because the overlap is otherwise easy to get wrong in either
direction.

## For review — three things I want a decision on

1. **No `channel` column in the frozen archive.** Omitted as speculative: one
   channel, iMessage deferred, `user_id` already resolves to a person. But the
   archive is frozen, so this is cheap now and awkward later. Say if you want it.
2. **`research_candidates` has no consumer until Phase 5.** It was in the
   approved plan so it is here, but it is the one table I would argue for
   deferring — Phase 5 will know its shape better than Phase 1 does, and the
   migration runner exists to add it then. Flagging rather than dropping it
   silently.
3. **`chunks` as a canonical table is a real departure from the reference
   build.** I believe it is the right call and the reasoning is above, but it is
   the largest structural difference in this schema and worth an explicit yes.
