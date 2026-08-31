# 2026-08-29 — Phase 0: Scaffolding

## Summary

Empty-but-runnable skeleton. Repository initialised, `anam/` package created,
layered configuration implemented, FastAPI app with a health endpoint, and
`start.sh`. No memory, no model calls, no tools — the shape of the app only.

All six Phase 0 tasks are Tier 0 / Sonnet, which permits a whole-milestone run.
The phase gate was verified before writing this entry.

## Files changed

Created:

- `.gitignore` — written **before** `git init`, covering `__pycache__/`,
  `.pytest_cache/`, `.DS_Store`, and `reference/`.
- `pyproject.toml` — ruff config, line length 100, rules E/F/I.
- `pytest.ini` — `testpaths = tests`; `reference/` and `venv/` excluded.
- `requirements.txt` — Phase 0–1 dependencies only.
- `anam/__init__.py` and empty subpackages: `api/`, `api/routes/`, `memory/`,
  `engine/`, `tools/`, `integrity/`, `settings/`, `ops/`, `artifacts/`.
- `anam/config.py` — layered configuration with call-time accessors.
- `anam/api/app.py` — application factory, no route bodies.
- `anam/api/routes/health.py` — `GET /api/health`.
- `run_server.py` — entry point with `--debug` and `--port`.
- `start.sh` — local by default, `--lan` to bind all interfaces.
- `config/defaults.toml`, `config/local.example.toml`.
- `tests/conftest.py` — store-isolation guard skeleton.
- `tests/test_config.py` (10 tests), `tests/test_health.py` (4 tests).
- Directory skeleton: `workspace/{generated,uploads,writing,research,journals}/`,
  `changelog/`, `docs/`, `eval/`, `scripts/`.

Modified: `BUILT.md`.

## Behaviour changed

New application where none existed. Nothing pre-existing was altered.

## Why these choices

**`.gitignore` precedes `git init`.** Per the updated Phase 0 sequencing —
otherwise unwanted files already have history to clean up. Verified with
`git check-ignore` that `reference/`, `venv/` and `.DS_Store` are excluded;
git sees 8 files at init, exactly the docs plus `.gitignore`.

**Config resolves at call time; there are no module-level constants.** This is
the single load-bearing property of `anam/config.py`. The reference build
exposed constants, so `from config import X` bound a separate name that test
patching could never reach, and its suite wrote into the production store for
weeks with nothing failing. `test_values_resolve_at_call_time_not_import_time`
pins the property.

**Routers split by domain from the first commit,** even though only `health`
exists. The reference build's single 1,824-line routes module is the outcome
being designed against.

**The store-isolation guard exists in Phase 0,** before there is a store to
protect. Adding it alongside the first store is too late. `StoreIsolationViolation`
derives from `BaseException` so that `except Exception` blocks downstream cannot
swallow it. It is armed against real stores in task 1.4.

**OpenAPI and docs endpoints disabled.** Nothing is public-facing yet and an
always-on API description is a capability nobody asked for.

## Tests run

- `ruff check .` — clean.
- `python -m pytest` — **14 passed**.
- `bash -n start.sh` — syntax clean; `--help` renders.
- Live gate, not mocked: server started, `GET /api/health` returned
  `{"status":"ok"}`, unknown route returned 404.
- Live env-override proof: `ANAM_API_PORT=8123 python run_server.py` bound 8123
  and answered there, while 8000 was correctly not listening.
- Ctrl+C path verified under a default SIGINT disposition (`os.setsid` +
  `SIG_DFL`, signal to the process group): trap fires, backend shuts down
  gracefully, exit code 130, no orphaned processes, port released.

## Bug found and fixed during the phase

**`.gitignore` excluded the `workspace/` subdirectories themselves,** which
meant their `.gitkeep` markers could not be tracked and the directory structure
would not have survived a clone — while the comment directly above the rule
claimed the structure *was* tracked. Caught when `git add` refused the marker
files. Replaced with `workspace/*/*` plus a `!workspace/*/.gitkeep` negation.
Verified both halves: the markers stage, and a file dropped into
`workspace/generated/` is still ignored.

Worth recording because it is the same failure shape as the reverted `set +m`
above — a comment asserting behaviour the code did not implement.

## Known limitations

- **`start.sh` prints a job-control notice on Ctrl+C.** A normal shutdown emits
  `./start.sh: line 77: <pid> Terminated: 15 ( cd ... )` from bash's job
  control before `Stopped.`. Functionally correct but reads like an error. A
  `set +m` in `cleanup()` was tried and did not suppress it, so it was reverted
  rather than left in place claiming an effect it did not have. Cosmetic;
  follow-up.
- **Health check is liveness only.** It reports that the process is up and
  nothing more, deliberately — there are no dependencies to report on yet.
- **`fastapi.testclient` emits a `StarletteDeprecationWarning`** recommending
  `httpx2`. Harmless now; revisit if it becomes an error.
- **`target-version = "py313"` in ruff config** while the venv is Python 3.14.5.
  Ruff's `target-version` has no `py314` value in the installed release; 313 is
  the closest available and affects only version-gated lint rules.
- Phase 0 builds no memory, model, or tool code. Everything under Phase 1
  onward is absent by design.

## Follow-up

- Task 1.4 arms the isolation guard against the real vector store and working DB.
- Task 1.11's settings store makes `config.py` bootstrap-only in practice; the
  scope comment in `config.py` and `defaults.toml` documents that boundary now.
- The cosmetic `start.sh` job-control notice.

## Project Anam alignment check

1. Assign the entity a name? **No.** `anam/` names the package; the naming
   rationale is in BUILD_PLAN's own task text.
2. Call the entity Anam or Tír? **No.** No entity-facing text exists yet.
3. Assign personality? **No.** No `soul.md` in this phase — that is task 1.8.
4. Preserve raw experience? **N/A** — no store yet.
5. Traceable derived artifacts? **N/A.**
6. Tool calls recorded? **N/A** — no tools.
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **N/A.**
9. Autonomy more cumulative? **N/A.**
10. Anam/entity distinction preserved? **Yes** — package naming only.
11. Migration required? **No** — no schema yet.
12. Tests? **Yes**, 14, plus live verification.
13. Core substrate changed unnecessarily? **No** — nothing existed.
14. External dependencies added? **Yes**, listed in `requirements.txt` with the
    phase they serve. All standard, all local.
15. Workspace vs. self-modification distinction? **Preserved** — `workspace/`
    exists as directory skeleton; no self-mod seam anywhere (decision #15).
16. Casual legacy renaming avoided? **Yes** — no `tir/` reference in new code.

## Not done — requires Lyle

**The first commit.** `git init` was run and files are staged, but CC never
commits. Phase 0's second task reads "git init, first commit"; the commit half
is yours. `git status` is clean of unrelated files — `reference/`, `venv/` and
`.DS_Store` are all ignored.
