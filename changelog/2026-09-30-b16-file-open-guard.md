# B16: the isolation guard stops plain file opens, not only store opens

Date: 2026-09-30 · queue item B16 · test infrastructure (how the guard works), reviewed
like B15. Plan approved at review, including `os.open`. **No `program/` change.**

## What was wrong

B15 wrapped `sqlite3.connect` and `chromadb.PersistentClient`, so opening a real store
fails at the open. A test that lost its isolation could still read a real *file*
directly (an uploaded artifact under `data/artifacts/`, a piece under `workspace/`)
with `open()` or `Path.read_bytes()`, and pass without testing anything. The
end-of-run fingerprint sees only what changed, so a read is invisible to it.

## What changed

- **`tests/conftest.py`** wraps three names for the session, each through B15's
  `_refuse()`, so there is one real-directory check and one violation record:
  - **`builtins.open`**: bare `open()`, `shutil`, `json.load(open(…))`.
  - **`io.open`**. On Python 3.14 `builtins.open is io.open`, **checked on this
    interpreter**, but `pathlib`'s `Path.open`, `read_bytes`, `read_text` and `write_*`
    look it up as `io.open`. A `builtins`-only wrapper would have missed every `Path`
    read.
  - **`os.open`**, the low-level route.
- A `bytes` path is decoded before the check. A file descriptor is ignored.
- The refusal happens before anything is opened, so a write into a real directory never
  creates the file.

## Known scope limit, recorded in BUILT.md

Not intercepted:
- `io.FileIO` constructed directly;
- files opened by C extensions (SQLite and Chroma stay guarded at their Python entry
  points);
- `mmap`;
- subprocesses;
- `os.open` with `dir_fd`, whose path is relative to a directory descriptor the guard
  cannot resolve portably (`shutil.rmtree` uses this form).

A *write* through any of these is still caught by the end-of-run fingerprint. A *read*
through them is as invisible as every read was before this change.

## Tests (`tests/test_isolation_guard.py`: 8 new test functions, 10 test items; B15's shape)

Each test loses isolation on purpose and resolves the path through `config`, the way a
leaking test would reach it:
- a bare `open()` of a real workspace file fails **at the open**;
- `Path.read_bytes()` and `read_text()` fail at the open (the `io.open` route);
- `os.open()` fails;
- the real `working.db` opened as a plain file is refused;
- a violation swallowed by `except BaseException` is still recorded;
- ordinary reads still work: `soul.md`, a `tmp_path` file, every route;
- **a decoy-directory case for each wrapper**, the mutation proof that does not depend
  on real data;
- a write into a decoy "real" directory is refused before the file exists.

## Proven to bite

Each wrapper was removed in turn, and restored after:

| wrapper removed | tests that fail |
|---|---|
| `builtins.open` | 3, including its decoy case |
| `io.open` | 4, including its decoy case |
| `os.open` | 2, including its decoy case |

*Honest detail:* with a wrapper removed, the real-path tests did open their targets for
reading, a tracked empty `workspace/*/.gitkeep` and `working.db`, read-only. Nothing
was written. The decoy cases are the proof that does not rely on that.

**Full suite:** 1,310 passed and 2 skipped (1,300 before), unchanged apart from the new
tests, so no existing test was reading the real directories. `ruff` is clean.
