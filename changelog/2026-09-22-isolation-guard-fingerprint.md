# Four of the five isolation checks could no longer fire

2026-09-22 · finding #11 of the diagnostic pass · authorised individually

## What was wrong

`tests/conftest.py` asked, per directory, **"was this created during the run?"**, guarded
by a `_PREEXISTED` flag captured at import. That is answerable only while the directory
does not exist. Verified this session — all five exist:

| directory | exists | could its check fire? |
|---|---|---|
| `data/` | yes | **no** |
| `data/chromadb` | yes | **no** |
| `backups/` | yes | **no** |
| `data/artifacts/` | yes | **no** |
| `workspace/` | yes | n/a — file-set snapshot, live |

So a test that forgot `isolated_data_dir` and wrote rows into the real `working.db`, or a
real backup into `backups/`, or an uploaded file into `data/artifacts/`, **passed
silently**. `BUILT.md` cited the evidence that had gone stale: *"Confirmed after a full
run: no `data/` directory in the repo."*

`workspace/` had already needed a different check at Phase 4 B0, because it is part of
the tracked skeleton. **That insight was right and was applied to one directory.**

`record_violation()` had **zero callers**, and its contract — record now, re-report at
session end so a swallowed violation still fails visibly — described a resolve-time trap
that was never built.

## What changed

**One mechanism for all five.** `_fingerprint()` records `{path: (size, mtime_ns)}` for
every file under every entry in the new `REAL_DIRS`, at import, and the session guard
compares at the end, reporting files **created** and files **modified** separately.

**Why not a file-set snapshot, which is what `workspace/` used:** writing rows into an
existing database creates no new file. A path-set comparison cannot see the single most
important case — a test writing into the real `working.db`. Size and mtime catch creation
and modification with one mechanism, so the five directories are no longer watched two
ways with a gap between them.

`.DS_Store` is excluded: the OS writes it, and Finder touching one mid-run would
otherwise fail the session.

**`record_violation()` removed**, and the module docstring rewritten to describe the
mechanism that exists. Keeping a live-but-cosmetic version would have preserved the
misleading story — `ROLE_GATING_DESIGN` R2's own argument about an unmounted gate.
`StoreIsolationViolation` stays, still deriving from `BaseException`, and is still named
by `registry.dispatch` and `turn._after_durable`.

**`tests/test_directories.py` no longer passes on spelling.** Three checks grepped
`inspect.getsource(...)` for `config.<name>()` — satisfied by a mention in a comment, a
docstring, or the backup manifest's `source` dict as readily as by real coverage. They now
assert properties:

- **isolation** — resolve every accessor *from inside the fixture* and require the answer
  under the temporary path;
- **guard coverage** — compare resolved paths against `REAL_DIRS` as data, treating a
  directory nested inside a watched one as watched;
- **backup** — plant a canary in each non-exempt directory, take a **real backup**, and
  require the canary to come out the other side.

A fourth test drives `_fingerprint()` directly and asserts the **modification** case is
seen, since that is the one a file-set snapshot missed.

**Documentation corrected in the same change**, both claims being false as written:
`AGENTS.md`'s *"For a directory the suite creates, 'was it created during the run?' is
enough"*, and `BUILT.md`'s stale confirmation.

## What was tested

Full suite **1148 passed, 2 skipped**, `ruff` clean — and that is itself the result worth
noting: the guard now watches for *modifications* to the real store, and 1,148 tests ran
without tripping it.

**Proven to bite, each by breaking it:**

| break | result |
|---|---|
| `backup.py` stops copying `workspace/` | new backup property test fails |
| `isolated_data_dir` stops repointing `ANAM_WORKSPACE_DIR` | isolation test fails **and the guard itself fires** |
| `workspace_dir` dropped from `REAL_DIRS` | coverage test fails |
| a throwaway `reflection_dir()` accessor is added | **3** tests fail |
| a throwaway test writes into the real `workspace/` | `StoreIsolationViolation` raised, naming the file |

**Dropping `artifact_dir` from `REAL_DIRS` does *not* fail — and that is correct, not a
gap.** It sits inside `data_dir`, whose walk still covers it. Worth recording because my
first attempt treated that as a broken test rather than checking why it passed.

## Known limitations

- **The guard cannot tell the suite's writes from another process's**, and extending it
  from creation to modification widens that: running the suite while anything else uses
  the real store now fails the session. Right direction — a foreign write is
  indistinguishable from a leak — but the suite and a soak run cannot overlap. Stated in
  the docstring rather than left to be discovered.
- **A created-but-empty directory is not reported.** Nothing leaked, so this is
  deliberate, but it is a behaviour change from the old check.
- `mtime_ns` is the load-bearing half for databases; a write that somehow preserved both
  size and mtime would be missed. No such write is known.
