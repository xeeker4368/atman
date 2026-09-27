# B4: startup creates and migrates the databases

Date: 2026-09-24 · merged-queue item 12 · plan B4 · Tier 3 (it runs migrations at
every boot, and the go-live wipe relies on it). Plan approved before implementation;
sequenced after B3 so that the migrations it runs at boot are transactional.

## What was wrong

- Nothing called `db.init_databases()` at startup. The only production caller was the
  dev seed script.
- `docs/DB_SCHEMA.md` said it "re-runs the whole file on every startup", and its
  go-live wipe procedure is "delete both databases, then recreate via
  `init_databases()`".
- On a fresh data directory:
  - ChromaDB created the directory;
  - the first request's connection created an empty `working.db` with no tables;
  - the first login was an unhandled 500 (`no such table`).

## What changed

- **`program/api/app.py` `lifespan()`** calls `db.init_databases()`:
  - **after** `config.auth_session_secret()`, so an unconfigured server stops without
    creating anything on disk;
  - **before** `vectors.get_vector_store()`, so a failed migration stops startup before
    anything else is built.
  - The docstring records why.
- **`docs/DB_SCHEMA.md`:**
  - the "re-runs on every startup" sentence now names where that happens, and says it
    was false before this change;
  - the go-live wipe says starting the server now does the recreate.
- **`program/ops/backup.py`:** the "no databases" error hint now says "start the server
  once (it creates them) or run `init_databases()`".

## Tested

`tests/test_startup.py`, 4 new tests, all entering the real lifespan
(`with TestClient(...)`). Almost no other route test does.

- **Fresh directory.** The test first asserts that no `working.db` exists, then logs in:
  - the response is 401, not 500;
  - the schema is at the latest version;
  - the core tables exist.
- **Existing store is left alone.** The version is unchanged and a pre-existing user
  survives.
- **A failing migration stops startup.**
  - The error propagates.
  - The version is unchanged.
  - The failed migration's half-created table is absent. That rollback is B3's
    guarantee, now exercised on the boot path.
- **Missing secret.** Startup stops and neither database file is created. This proves
  the ordering.

**Proof it bites:** removing the call fails the fresh-directory test with
`assert 500 == 401` (the reported defect) and the failing-migration test with "DID NOT
RAISE". The other two pass either way; they cover ordering and idempotence.

**Checked live, not only with TestClient.** `run_server.py` on port 8765 with every data
path pointed at a throwaway directory:

- health came up;
- `POST /api/login` returned 401;
- `archive.db`, `working.db` and `chromadb/` were created, with schema version 6;
- 0 error or traceback lines in the server log;
- port 8765 was free after shutdown.

The real `data/working.db` mtime is unchanged (2026-09-22).

Full suite **1202 passed, 2 skipped** (was 1198/2). `ruff check .` clean.

## Known limitations

- **Migrations now run automatically at boot.** A pending migration applies the next
  time the server starts, with no separate step and no backup first.
  - That is what the design always assumed, but it now actually happens.
  - The go-live checklist's "backup before wipe" is a manual step, and nothing
    enforces a backup before a migration.
- **Startup costs the time `init_databases()` takes.** That is re-running the schema
  file plus a version check, measured as part of the 0.77 s for the 4-test file. It was
  not timed separately.
