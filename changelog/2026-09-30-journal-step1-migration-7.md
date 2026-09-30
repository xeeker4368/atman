# 2026-09-30 — Reflection journal step 1: migration 7, `artifacts.integrity_check`

Tier 3 (schema). `docs/REFLECTION_JOURNAL_DESIGN.md` J7 and J12 step 1, approved
at review 2026-09-30. **Stops here for schema review**; steps 2 and 3 wait.

## What changed

- `program/memory/migrations.py`: migration 7 adds `artifacts.integrity_check TEXT`,
  nullable with no default. It is one statement run inside the runner's
  transaction.
- `program/memory/db.py`: `insert_artifact(..., integrity_check=None)` writes the
  verdict in the same `INSERT` as the row. Every existing caller is unchanged and
  writes NULL.
- `tests/test_migrations.py`: 5 new tests (listed below).
- `docs/DB_SCHEMA.md`: a new `artifacts` section. The table had none.
- `docs/REFLECTION_JOURNAL_DESIGN.md` revision 3, with the review's changes:
  - J7 now says what the operator can and cannot do with a flagged entry.
  - J7 states that at stage 1 a flagged entry is indexed and retrieved like a
    clean one, and that the revisit trigger is the first true-positive flag.
  - J8's dev set now requires the cognitive verbs the prompt elicits, real
    soak-store material and real tool-use turns, and a fresh shuffle every pass
    with at least two seeds.
- `NOW.md` backlog: *`messages.integrity_check` has no reader under `program/`*
  (requested at review; it was not already there).
- `BUILT.md`: a migration 7 entry and the suite count.

## Why

The journal's gate verdict has to be persisted (review decision 3). A log-only
verdict on the most confabulation-prone text in the system is the unmounted-gate
shape. The column goes on `artifacts` under `messages`' name and semantics, so
the two are queried the same way.

## Tests

- The column is `TEXT`, nullable, with no default, and the version is ≥ 7.
- It is not in `working.sql`.
- **Forced-failure rollback:** a failure injected *after* the `ALTER` ran, in
  the same migration, leaves the column gone, the version at 6 and a version-6
  artifact row intact. A retry applies, and the old row reads NULL.
- `insert_artifact` stores the verdict when given one, and NULL when not.
- `writing.store` (an existing writer) leaves it NULL.

**Proven to bite, one mutation at a time, each restored afterwards:**
- migration 7 calling `conn.commit()` after its `ALTER` fails the rollback
  test;
- removing migration 7 fails 4 tests;
- `insert_artifact` dropping the value fails the same-row test.

**Full suite:** 1,354 passed, 2 skipped, 1 failed. `ruff check` is clean.

**The one failure is not from this change.**
`test_blocklist.py::test_config_local_toml_is_covered_by_the_rule_even_though_it_does_not_exist`
asserts `config/local.toml` does not exist. It now exists, because it holds the
Moltbook key, and the test was written to fail at that moment so its premise
gets rechecked. The rewrite ships with the Moltbook read tools, since that is
why the file exists.

## Known limitations

- No writer and no reader yet. Steps 2 and 3 add both. J7 records who reads it
  and what the operator can do.
- `db.get_artifact` returns the column through `SELECT *`; there is no typed
  accessor, because nothing needs one yet.

## Follow-up

- Schema review of this step. Then step 2: `gate.check_identity` and the J8
  dev measurement, stopping to report the numbers.
