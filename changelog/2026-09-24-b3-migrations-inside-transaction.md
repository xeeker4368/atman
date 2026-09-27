# B3: migrations 5 and 6 now run inside their transaction

Date: 2026-09-24 · merged-queue item 8 · plan B3 · Tier 3 (schema migration). Plan
approved before implementation. The review asked specifically for a deterministic
forced-failure test, not only a clean-path one.

## What was wrong

- Migrations 5 and 6 ran their multi-statement SQL with `conn.executescript()`.
  `executescript` issues an implicit `COMMIT` of any open transaction before it runs.
  Inside `run_working_migrations()`'s transaction, that meant three things:
  - every earlier migration in the run, plus the migration's own
    `DROP TABLE supersedes`, was committed at once;
  - the rest of the script ran outside any transaction;
  - after a failure, `db.transaction()`'s `ROLLBACK` raised "cannot rollback - no
    transaction is active", which replaced the real error.
- Migration 6 drops and recreates `supersedes` before creating its two cycle-guard
  triggers. A failure between those steps would leave the table with no cycle guards
  and no version recorded, with nothing reporting it.
- `migrations.py`'s module docstring promised the opposite.
- The existing `test_failed_migration_records_no_version` passed throughout, because its
  test migration used `conn.execute` and never touched the broken path.

## What changed

All in `program/memory/migrations.py`:

- **New `_statements(script)`.** It splits a script into complete statements with
  `sqlite3.complete_statement`, so a trigger's `BEGIN … END;` body is not cut at its
  inner semicolons. It raises on an incomplete trailing statement.
- **New `_execute_script(conn, script)`.** It runs each statement through `conn.execute`
  inside the caller's transaction, and raises if the transaction is somehow gone.
- **Both migrations call `_execute_script`** where they called `executescript`. The SQL
  text is unchanged.
- **The module docstring** now says a multi-statement migration must never use
  `executescript`.

`db.init_databases()` still uses `executescript` for `archive.sql` and `working.sql`.
Those run on fresh connections with no open transaction, so they don't have this
problem. They are out of scope.

## Tested

Four new tests in `tests/test_migrations.py`:

- **Migration 6 failing before its triggers.**
  - Setup: a store at version 5, with a version-5 `supersedes` link seeded.
  - A failing statement is injected immediately before `CREATE TRIGGER
    supersedes_no_cycle_insert`, which is after the table recreate.
  - Asserted:
    - the injected error is the one raised;
    - the version is still 5;
    - the table's columns, both triggers and the seeded row are unchanged.
  - The injection is then removed and the migrations re-run. The test asserts the store
    reaches the latest version with the `replacement` column and both triggers.
- **Migration 5**, the same injection from version 4. It asserts the injected error, the
  version still at 4, and the chunk-level `supersedes` table unchanged.
- **The statement splitter keeps a trigger body whole.**
- **No `executescript` call in `migrations.py`.** An AST check, so a mention in a
  docstring doesn't trip it.

**Proof they bite:** I made `_execute_script` run the same statements through
`executescript`. Both forced-failure tests then fail with "cannot rollback - no
transaction is active" (the original defect, reproduced exactly), and the AST test
fails.

**Caught in my own first draft.** The recovery half of the migration-6 test called
`monkeypatch.undo()`. That fixture is shared with `isolated_data_dir`, so the undo sent
the retry against the real store. The real store is already at version 6, so the check
passed without testing anything. It was read-only: the real `working.db` and
`archive.db` mtimes are unchanged (still 2026-09-22). The test now restores only the
patched function and asserts it is still on the temporary store.

Full suite **1198 passed, 2 skipped** (was 1194/2). `ruff check .` clean.

## Known limitations

- **The splitter rejects a script with an incomplete final statement.** It does not
  validate SQL beyond that.
- **Nothing in the test isolation guard catches a read from the real store.** It only
  fingerprints writes, which is why the `undo()` mistake above passed silently.
