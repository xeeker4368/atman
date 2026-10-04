# 2026-10-03: scripts/_scratch.py, a scratch store before program is imported

**Why.** A measurement script imported `program.config` with the default directories. Config reads the settings table
first, so the script read the real `data/working.db` settings table. The table was empty, and the real `data/`
fingerprint is unchanged (`changelog/2026-10-03-notes-search-line-and-gap-remeasure.md`).

**What.** `scripts/_scratch.py`, `scratch_env(name: str, *, root: Path | None = None) -> Path`. Call it before any
`program` import.
- **Variables are discovered, not hardcoded.** It parses `program/config.py`'s env table with `ast` and takes every
  `ANAM_*` entry whose section is `paths`: today `ANAM_DATA_DIR`, `ANAM_WORKSPACE_DIR`, `ANAM_BACKUP_DIR`,
  `ANAM_ARTIFACT_DIR`. A new `[paths]` key is picked up with no change here.
- It sets each to `<root>/<name>/<key without _dir>`. The root defaults to `~/anam-measurements`.
- It refuses if any `program` module is already in `sys.modules`.
- It then imports `program.config`, reloads it, and requires every `*_dir()` accessor except `config_dir` (read-only
  repository config) to resolve under the scratch base. One that does not raises, naming the accessor. That is what
  catches a directory added outside `[paths]`.
- It creates no files or directories.

**Tests** (`tests/test_scratch_helper.py`, 4):

| test | what it checks | mutation that fails it |
|---|---|---|
| (i) normal call, subprocess | every discovered directory resolves under the scratch base; nothing exists under the root afterwards | helper writes a file under base (fails (i)); variables pointed at `base.parent` with the check skipped (fails (i) and (iii)) |
| (ii) already imported, subprocess | `import program.config` first, then `scratch_env` raises *"scratch_env must run before anything under program is imported"* | refusal replaced by `if False:` (fails (ii)) |
| (iii) uncovered directory, subprocess | `ANAM_WORKSPACE_DIR` dropped from the discovered set and the environment; raises *"config.workspace_dir() resolves to … outside the scratch root"* | resolution check replaced by `if False:` (fails (iii)) |
| scan | static scan of `scripts/*.py`: a script whose first `program` import precedes any `scratch_env(...)` call must be listed, either `OPERATOR` (6) or `NOT_YET_MIGRATED` (33; `checkpoint_queries` and `seed_dataset` moved there from `OPERATOR` on review); a listed script that becomes compliant fails | an unlisted script importing `program` fails; the same with `scratch_env` first passes; an import before the call fails; a compliant script added to the list fails |

- Mutations ran with `PYTHONDONTWRITEBYTECODE=1`, and the helper was restored from a copy (diff-checked).
- Full suite: 1,973 passed, 4 skipped; `ruff` clean.

**Not migrated.** None of the 31 listed measurement scripts was changed.

**Known limits:**
- The scan is static, so a script reaching `program` only through another script passes it.
- Scripts that set the directories by other means are listed, not judged.
- The two frozen evals read the real settings table today.

**Correction (2026-10-03, later the same day).** The "Why" paragraph above says the settings table "was empty". That
was **not observed** in these batches.
- The table was recorded empty on 2026-09-24 (B15, read-only).
- `working.db` has not been modified since 2026-09-22 (fingerprint mtime).
- So it was presumably still empty when the token script read it. That is an inference, not a reading.

`changelog/2026-10-03-notes-search-line-and-gap-remeasure.md` (committed, not edited) repeats the same claim.
