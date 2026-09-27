# B15: every test isolated by default, plus an open-time store guard

Date: 2026-09-24 · queue item B15 (found during B3) · Tier 2 (test infrastructure; it
changes how the guard works). Plan approved, then option (B) chosen at review once the
first run found 77 offenders.

## What was wrong

The session guard fingerprinted the real runtime directories (size and mtime) and
compared them at the end of the run. It could only see changes, so a test that lost
its isolation and only read the real store passed silently.

- **Seen at B3.** A test draft called `monkeypatch.undo()`, which also reverted
  `isolated_data_dir`'s environment. It then ran migrations and assertions against the
  real `working.db`, which is already at the latest version. Nothing was written and
  the test passed without testing anything.
- **Found when the new layer first ran: 77 tests in 11 files.**
  - They call settings-backed `config` accessors (`chat_model()`, `model_options()`,
    `num_ctx`, the classifier settings) without asking for `isolated_data_dir`.
  - That path goes `settings.store.resolve()` → `db.connection()` → the real
    `data/working.db` settings table.
  - The table is empty today (0 rows, checked read-only), so nothing was affected yet.
  - **The first setting saved through the admin panel would have had those tests
    silently running against production configuration.** Nothing would have signalled
    it.

## What changed

All in `tests/conftest.py`, except where noted.

**`isolated_data_dir` is `autouse`.**

- Every test gets temporary data, backup, artifact and workspace directories.
- Tests that name the fixture get the same instance, unchanged.
- This is the same shape as B4: correct by default, not opt-in.

**Open-time layer.**

- `sqlite3.connect` and `chromadb.PersistentClient` are wrapped for the session. Opening
  a path under any real runtime directory raises `StoreIsolationViolation` at that
  point.
  - URIs are parsed, so a read-only `file:…?mode=ro` open counts.
  - `:memory:` and `mode=memory` URIs do not count.
- Chroma has its own wrapper, because Chroma 1.x opens SQLite from Rust and never calls
  the Python `sqlite3.connect`.
- Every violation is recorded before it is raised, and the session guard re-reports any
  recorded violation at the end of the run. So one swallowed by code that catches
  `BaseException` still fails the suite.
- The fingerprint layer is unchanged and stays as the backstop.

**Two tests that genuinely need the unisolated paths**, now explicit:

- `tests/test_config.py::test_relative_paths_resolve_against_project_root` removes
  `ANAM_DATA_DIR` to check the default `data_dir()`.
- `tests/test_directories.py::test_the_session_guard_watches_every_runtime_directory`:
  - It finds the directory accessors under isolation, then resolves only those with the
    isolation variables removed.
  - Calling every accessor unisolated would read the real settings table, and the new
    layer would, correctly, stop it.
- Both compute paths and open nothing.

## Tested

- **Suite under the new default, before touching those two tests:** 2 failed, 1201
  passed. The two failures were exactly those tests, confirmed by running rather than
  by inspection.
- **New `tests/test_isolation_guard.py`, 9 tests (6 at first, 3 decoy tests added at review):**
  - every test is isolated by default;
  - a read-only escape (unset env, then `config.chat_model()`) is stopped at the open;
  - the B3 shape (`monkeypatch.undo()`, then `db.connection()`) is stopped;
  - a violation swallowed by `except BaseException` is still recorded;
  - the Chroma client is guarded;
  - a read-only URI to the real store is refused while in-memory databases are allowed.

  Each test removes its own recorded violation, so the session stays green.
- **Mutation checks:**
  - With the `sqlite3` wrapper removed, 4 of the acceptance tests fail.
  - With the fixture opt-in again, the default-isolation test and all 4 `test_history`
    tests fail.
  - With `workspace_dir` dropped from `REAL_DIRS`, the adjusted `test_directories` guard
    test still fails, so the adjustment kept its teeth.
  - **Chroma wrapper, proven on a decoy** (added at review):
    - Removing the wrapper and running the real-path test would construct a real
      `PersistentClient` on the production Chroma directory, which can write on open.
    - So three decoy tests add a throwaway directory to `conftest._REAL_ROOTS`. That list
      is the guard's entire notion of "real", so the decoy is indistinguishable from
      production to the guard.
    - They assert that both wrappers refuse the decoy before anything is created there,
      and that the decoy is not the test's own isolated directory (so a pass cannot come
      from isolation instead).
    - Removing only the Chroma wrapper fails the Chroma decoy test ("DID NOT RAISE").
      Removing only the `sqlite3` wrapper fails the SQLite decoy test.
    - Both mutation runs used `-k decoy`, so the real-path test was deselected. The real
      `data/chromadb` and `chroma.sqlite3` mtimes are unchanged.
- **Full suite: 1212 passed, 2 skipped** after the decoy tests (1209 before them, run twice; was 1203/2). `ruff check .` clean.
- **Real store:** the mtimes of `data/working.db`, `data/archive.db` and
  `data/chromadb` are unchanged (2026-09-22). During the first mutation run, the
  unguarded tests did open the real `working.db` read-only. Nothing was written.

## Known limitations

- **Plain file reads are not intercepted.** `open()` or `Path.read_bytes()` under
  `workspace/` or `data/artifacts/` is not caught at open time. Autouse isolation means a
  test would have to remove its own isolation first.
- **The wrappers patch module attributes.** Code holding a reference to the original
  `sqlite3.connect` captured before `conftest.py` loads would go around the layer.
  Checked by grep: nothing in `program/`, `scripts/` or `run_server.py` holds one. The
  only captured reference in the repo is `tests/test_backup.py`'s B14 test. It captures
  at test time, after the patch, so it holds the guarded wrapper, and it puts that back.
- **Plain file reads are tracked as queue item B16** (low priority), not only as the
  paragraph above.
