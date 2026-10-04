# 2026-10-03: scripts never migrate a store (Tier 3, db.py; stopped for review)

**Why.** Demonstrated on a scratch store first:
- A v6 store was built `_store_at`-style.
- `python -m scripts.write_journal --show-records 2026-10-01` printed "No messages on 2026-10-01." and exited 0.
- The store was then at schema version 8, with rows `(7, 'artifact_integrity_check')` and `(8, 'notes')`.
- `write_journal.py:188` and `program/ops/seed.py:423` call `db.init_databases()`, which runs every pending migration.
- The real store is at version 6 until the server's first startup, so either script would have migrated it.

**What.**
- `db.require_store_not_migrating()` and `db.StoreWouldMigrateError` (`program/memory/db.py`, before `init_databases`):
  - `working.db` absent: returns, so a fresh directory may be created.
  - Present: opened read-only with `sqlite3.connect(f"file:{working}?mode=ro", uri=True)` (note_admin's pattern).
    `MAX(version)` is read from `schema_version`.
  - **Only `no such table` reads as version 0.** Any other `OperationalError` (locked, unable to open, disk I/O) raises
    `StoreWouldMigrateError("cannot read the schema version of …: <message>")` and claims no version.
  - The store is in DELETE journal mode, not WAL (`db.py:81-82`, `PRAGMA journal_mode = DELETE`). On a scratch store
    the read-only connection read `journal_mode` = `delete` and `MAX(version)` = 8, and left no side files (the
    directory listing was `['archive.db', 'working.db']` before and after).
  - Any version other than the latest raises. The latest is `max(INITIAL_VERSION, every migration's version)`. The
    message says the command never migrates and migrations run only when the server starts.
- **Above the latest is refused too.** `init_databases()` would migrate nothing there, but the store is newer than the
  code. Its schema is one this code does not know, so writing to it with older code could break invariants the newer
  migrations added. Refusing is the safe direction for a command that is not the server.
- Called first in `scripts/write_journal.py` `main()`, for every subcommand: on refusal it prints `refused: <reason>`
  to stderr and returns 2. Called first in `program/ops/seed.py` `seed()`, where the error propagates;
  `scripts/seed_dataset.py` catches it next to `SeedError` and prints one `refused: <reason>` line to stderr, exit 2.
- **Unchanged:** `program/api/app.py` (the server still migrates at startup) and `note_admin.require_schema`.

**Tests** (`tests/test_store_not_migrating.py`, 9):

| test | checks |
|---|---|
| write_journal on v6, `--show-records` and `--dry-run` | each returns 2 with "never migrates", and the version is still 6 (read-only) |
| `seed.seed()` on v6 | raises `StoreWouldMigrateError`, and the version is still 6 |
| `seed.seed()` on a fresh directory | creates the store and writes messages (embedding stubbed) |
| latest version | the guard passes, and `write_journal --show-records` returns 0 with the version unchanged |
| version 999 | raises "above this code's version" |
| `seed_dataset` on v6 | exactly one stderr line, `refused: …never migrates…`, exit 2, version still 6 |
| a `working.db` with no `schema_version` table | raises "schema version 0, below" |
| another read error (`sqlite3.connect` patched so the query raises `disk I/O error`) | raises with "disk I/O error" and no "is at schema version" |

**Mutations** (`PYTHONDONTWRITEBYTECODE=1`, each restored from a copy):

| mutation | result |
|---|---|
| `write_journal` call removed | 2 tests fail |
| `seed` call removed | 1 fails |
| `version != latest` changed to `<` | the above-latest test fails |
| missing store refused | the fresh-seed test fails |
| always refuse | the latest-version test fails |
| blanket `except` (every error read as 0) | the other-read-error test fails |
| `no such table` also raises | the version-0 test fails |
| `seed_dataset` catch removed | the `seed_dataset` test fails |

**Also in this change** (test only): `tests/test_history.py::test_window_carries_the_breakdown_for_inspection` now
checks `reserved + history == context` with a non-zero `tool_schema_chars` (6,000). Leaving `tool_schema_tokens` out of
`BudgetBreakdown.reserved_tokens` fails it.

**Full suite:** 1,982 passed, 4 skipped; `ruff` clean.

**Not changed:**
- No AST test over `init_databases` call sites, as instructed.
