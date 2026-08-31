# 2026-08-31 — Task 1.1 revision: scope the schema to Phase 1

**Tier 3 · Opus · Checkpoint — revision of the same task, still awaiting sign-off.**

Follows `2026-08-31-task-1-1-database-schema.md`. Four review items, all
addressed here.

## Summary

Removed two tables that had no consumer in Phase 1, answered a process question
on the record, and verified the migration table name on both sides. No other
changes.

## Files changed

- `anam/memory/schema/working.sql` — removed `artifacts` and
  `research_candidates` with their indexes; extended the header's
  deliberately-absent list.
- `tests/test_db.py` — updated the expected-tables set; added
  `test_later_phase_tables_are_absent`.
- `docs/DB_SCHEMA.md` — removed both table sections; recorded both under
  "Deliberately absent".
- `BUILT.md`, this changelog.

Working store is now 7 tables: `schema_version`, `users`, `conversations`,
`messages`, `chunks`, `supersedes`, `settings` — plus `chunks_fts` and its
triggers. Archive unchanged at 2.

---

## 1. `research_candidates` removed

Gone entirely — table, both indexes, the section comment. Nothing referenced it:
no foreign keys pointed at it, no Python touched it, and the only test mentioning
it was the expected-tables list.

**Phase 5 designs it fresh.** The removed column shape is deliberately not
recorded in `docs/DB_SCHEMA.md` or anywhere else, and should not be treated as a
starting point. The self-flag tool and the mining pass will know what they need;
this schema was guessing four phases ahead.

## 2. The `channel` column — was the process followed?

**No. I decided it and flagged it afterwards. It was not discussed with you
beforehand.**

Plainly: I wrote `archive.sql` without a `channel` column, put the reasoning in
the file header, and then raised it in the changelog's review section as
something I wanted a decision on. The flag came after the file, not before it.
There was no prior conversation about it — I introduced the question myself,
having already resolved it in code.

On whether that broke a stated rule, the honest answer is that it did not break
the letter of one. The rule in `NOW.md` covers deviating from its decision log,
and omitting `channel` does not deviate from any entry there — it follows
decision #13 (iMessage deferred entirely). The approved plan specified the
archive as "two tables — users, messages — append-only, shape frozen" without
enumerating columns, so column choices sat inside the scope I had been given.

But that is a narrow defence and I do not want it to read as the whole answer.
The archive is frozen: the cost of getting a column wrong there is permanent and
one-directional, which is exactly the profile that should trigger a stop rather
than a note-to-self. Tier 3 exists for decisions whose cost compounds silently.
Making the call and then asking about it inverted the gate — you got the question
attached to an implementation that already assumed an answer, which is a weaker
form of review than being asked first. For a frozen structure, the flag should
have preceded the file.

Recording it as a miss. Applying to the rest of Phase 1: on anything frozen,
irreversible, or explicitly Tier 3, the question goes up before the code, not
alongside it.

The decision itself stands unchanged, per your instruction.

## 3. `artifacts` removed — option (a)

Checked for a hard dependency before choosing. There was none:

- The only inbound foreign key was `artifacts.revision_of → artifacts.id`, the
  table's own self-reference, which goes away with it.
- No other table in `working.sql` referenced it.
- No Python in `anam/` or `tests/` used it. The one grep hit in `config.py` is
  the word "artifacts" in a docstring describing the workspace directory, not a
  reference to the table.

So there is no reason to stand it up now, and it is removed. Phase 2 builds it
when the ingestion design drives its shape, and Phase 4 extends it for generated
images.

Both removals are pinned by `test_later_phase_tables_are_absent`, so re-adding
either is a deliberate act with a failing test attached rather than quiet drift.

## 4. Migration table name — verified on both sides

No mismatch. Actual names, not a summary:

| where | name used |
|---|---|
| `working.sql:31` | `CREATE TABLE IF NOT EXISTS schema_version` |
| `migrations.py:54` | `SELECT version FROM schema_version` |
| `migrations.py:60` | `INSERT INTO schema_version (version, name, applied_at)` |
| `migrations.py:69` | `SELECT MAX(version) AS v FROM schema_version` |

All singular. A repo-wide grep for the plural `schema_versions` returns nothing
in `*.py`, `*.sql` or `*.md` outside `reference/`.

Columns confirmed against a live database rather than the DDL: `version`,
`name`, `applied_at` — matching what the `INSERT` writes and the `SELECT`s read.
One row present after init: `(1, 'initial_schema', <timestamp>)`.

Worth adding one observation about the failure mode you described: a table-name
mismatch here would **not** in fact have been silent. `SELECT version FROM
schema_versions` raises `sqlite3.OperationalError: no such table`, and
`run_working_migrations()` is called by `init_databases()`, so it would fail at
startup on the first run. That is luck of the design rather than a guard, and it
would not hold for a mismatched *column* name inside an otherwise-valid
statement — `INSERT INTO schema_version (version, name, applied_at)` naming a
column the table lacks would also raise, but a silently unused extra column
would not. Nothing to change; noting it because the general concern is sound
even though this instance is safe.

---

## Tests run

- `python -m pytest` — **54 passed** (was 53; net +1 after removing nothing and
  adding `test_later_phase_tables_are_absent`).
- `ruff check .` — clean.
- Verified nothing depended on the removed tables *before* removing them, by
  grepping for inbound foreign keys and for Python references.
- Live check against a real on-disk store: working tables are exactly
  `chunks, conversations, messages, schema_version, settings, supersedes, users`;
  archive tables are exactly `users, messages`.

Nothing broke. The two removed tables had no dependents, as expected — checked
rather than assumed.

## Known limitations

Unchanged from the parent entry, minus the two that no longer apply. Still open:

- No `chunks` writer yet (task 1.3).
- `source_type` / `source_trust` values unconstrained at the schema level until
  task 1.7 defines the vocabulary.
- No down-migrations.
- `MIGRATIONS` is empty; the apply path is exercised only by tests.

## Follow-up

- **The published Phase 0–3 plan document is now out of date** in one respect:
  its task 1.1 file list still names `artifacts` and `research_candidates` as
  part of `working.sql`. Not touched in this patch. Say the word and I will
  update it — flagging rather than fixing, per the instruction to leave adjacent
  things alone.
- Phase 2 builds `artifacts`; Phase 5 builds its own research-candidate table
  from scratch.
- Task 3.5 must carry a visited set during forward resolution (recorded in
  `docs/DB_SCHEMA.md` under the cycle guard).

## Project Anam alignment check

Deltas from the parent entry only:

7. Created artifacts remembered? **Not yet** — the `artifacts` table moved to
   Phase 2. No behaviour regressed, because nothing created artifacts.
11. Migration required? **No.** No real database exists yet, and every statement
    in `working.sql` is `CREATE ... IF NOT EXISTS`, re-run at init. Removing a
    table from the file does not drop it from an existing database — worth
    knowing, but moot here since no database predates this change. Confirmed by
    creating a fresh store and listing its tables.
13. Core substrate changed unnecessarily? **No** — this removes scope rather
    than adding it.

All other answers stand as recorded in the parent entry.
