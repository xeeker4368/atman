# 2026-09-01 — Rename the `anam/` package to `program/`

**Mechanical rename. No logic changes, no behaviour changes.** Isolated commit,
nothing else bundled.

## Why

The package name collided with two other things called Anam: the ongoing
project's own name ("Project Anam"), and the prior implementation's repository
(`xeeker4368/Anam`, kept at `reference/old-anam/`).

That collision caused real confusion **in this session's own history** about
whether `anam/` was reused code carried over from the prior build. It was not —
every file in it was written fresh for this repo. The old build's package was
`tir/`, and nothing from it was copied. This rename removes the ambiguity at the
source rather than continuing to answer the question in prose.

`program/` deliberately carries no meaning: the package is the substrate's
plumbing, and a neutral name cannot be mistaken for the project, the prior repo,
or the entity.

## What changed

- `anam/` → `program/` via **`git mv`**, so git records true renames rather than
  delete+add pairs. All eight subpackages moved with it: `api/`, `api/routes/`,
  `memory/`, `engine/`, `integrity/`, `tools/`, `settings/`, `ops/`,
  `artifacts/`.
- **93 package references** rewritten across 37 Python/SQL files — every
  `from anam …` / `import anam …`, plus package paths in docstrings and
  comments. `grep -rE '\b(from|import) anam\b'` now returns **nothing**,
  repo-wide.
- **35 package references** rewritten across the seven enumerated docs:
  `BUILD_PLAN.md`, `BUILT.md`, `AGENTS.md`, `docs/DB_SCHEMA.md`,
  `docs/RETRIEVAL_DESIGN.md`, `docs/SOUL_AND_PROMPT_DESIGN.md`. (`CLAUDE.md`
  needed none — its only `anam` hits were `reference/old-anam/`.)
- `run_server.py`: `from anam import config` and the uvicorn target
  `"anam.api.app:app"` → `program.*`.

**Tooling needed no changes.** `pyproject.toml` and `pytest.ini` contain no
`anam` reference at all, and `.gitignore` has none either — verified rather than
assumed.

## Item 4: path constants — confirmed, not assumed

Both hardcode nothing. Read directly:

```python
# program/memory/db.py
SCHEMA_DIR = Path(__file__).resolve().parent / "schema"

# program/engine/prompt.py
SOUL_PATH = Path(__file__).resolve().parent.parent / "integrity" / "soul.md"
```

Pure relative `Path` arithmetic — no `"anam"` literal anywhere in either
resolution chain — so **neither needed changing**, and both resolve correctly
under the new directory. Proven by the suite and by a live `load_soul()`.

## Item 6: soul.md's substrate sentence — untouched, verified

> The system you run on is called Anam; that is the name of the substrate, not
> of you.

Still present and unmodified. `soul.md` is **byte-for-byte identical** to the
committed version — `git show dac6f7f:anam/integrity/soul.md | diff -` against
the new path reports no difference. Still 3,401 characters, and
`test_soul_md_char_count_matches_the_design_document` still passes. It contains
no lowercase `anam`, so the case-sensitive replace could not have reached it
regardless.

## One sentence that needed judgment, not substitution

`BUILD_PLAN.md`'s Phase 0 row read:

> package named `anam/` (**the project's own codename**, same role `tir/` played
> in the reference build …)

A mechanical swap would have produced *"package named `program/` (the project's
own codename…)"* — which is **false**; `program` is not the project's codename,
and the parenthetical's entire rationale was that the package took the project's
name. Rather than leave a false statement or silently delete the reasoning, the
row now records the original name, the rename, and why, while keeping the
original point that neither name conflicts with the entity staying unnamed.

**This is the one place prose was edited beyond a path swap.** Flagged for
wording review.

## Deliberately NOT renamed

Four categories contain a lowercase `anam` that is **not** a package reference.
Renaming any of them would be a behaviour change, which this is not:

| Kept | Count | Why |
|---|---|---|
| `ANAM_*` env vars | 84 | The application's env namespace (`ANAM_DATA_DIR`, `ANAM_API_PORT`, …). Renaming would break every existing `local.toml`/shell env — a behaviour change, and it names the project, not the package. |
| `anam.log` | 2 | Runtime log filename in `run_server.py`. Renaming moves the log file. |
| `anam-backup-<stamp>` | 6 | Runtime backup directory prefix in `program/ops/backup.py`. Renaming changes where backups land and orphans existing ones. |
| `/tmp/anam-test-data` | 2 | An arbitrary temp-path string inside one config test. |

`ANAM_*` is uppercase, so the case-sensitive replace never touched it; the other
three were explicitly protected during the substitution.

## Changelogs deliberately left alone

`changelog/` was not in the scope list, and it should not have been. Those are
**dated audit records** — `2026-08-31-task-1-3` genuinely created
`anam/memory/chunking.py`, because that is where the file was that day.
Rewriting them to say `program/` would misrepresent the record.

This project treats provenance as sacred and refuses to edit history to make it
tidy; the changelogs are the build's own provenance, and the same rule applies
to them. Anyone tracing a path from an old changelog finds it under `program/`
now, and this entry is what explains the gap.

## Verification

- **Full suite: 326 passed** — identical to the pre-rename count, no tests
  added, removed or skipped.
- **`ruff check .` clean.**
- **Live boot, not just the suite.** `python run_server.py --port 8137`, then
  `GET /api/health` → **HTTP 200 `{"status": "ok"}`**, ready in 0.5s, with clean
  shutdown. This exercises the uvicorn import string `program.api.app:app`,
  which no test covers.
- **Import smoke test**: every subpackage imported under `program.*`, and
  `prompt.load_soul()` returned 3,400 stripped characters.
- Stale `__pycache__` removed before testing, so nothing resolved through
  leftover bytecode.

## `grep -rn anam` — full accounting

Excluding `reference/`, `.git/`, `venv/`, `__pycache__/`. Every remaining hit,
by category, with its justification:

**Zero package references remain.** `grep -rE '\b(from|import) anam\b'` →
nothing. The only `anam/` path outside `changelog/` is the deliberate historical
note in `BUILD_PLAN.md:35` described above.

| Category | Hits | Where | Correct to remain because |
|---|---|---|---|
| `reference/old-anam/` | 14 | `CLAUDE.md`, `AGENTS.md`, `PROJECT.md`, `NOW.md`, `BUILD_PLAN.md` | The historical repo path. Renaming it would break the reference-folder rule and point at nothing. |
| `ANAM_*` env vars | 84 | `program/config.py`, `config/*.toml`, `start.sh`, `tests/*` | Env namespace, not a package name. See table above. |
| `Project Anam` | 23 | `run_server.py`, `start.sh`, `program/api/app.py` (`title=`), `CLAUDE.md`, changelog headings | The project's name — explicitly out of scope. |
| `Anam` as substrate | ~20 | `program/integrity/soul.md`, `program/engine/prompt.py`, `tests/test_prompt.py`, `docs/SOUL_AND_PROMPT_DESIGN.md` | The substrate name, and **load-bearing**: `prompt.py`'s naming regexes literally match on `Anam`, and the tests assert both that entity-framing raises and that substrate-framing does not. Renaming would break the constraint it enforces. |
| `anam.log` / `anam-backup` / `anam-test-data` | 10 | `run_server.py`, `program/ops/backup.py`, `scripts/backup.py`, `tests/test_config.py`, `BUILT.md` | Runtime artifact names. See table above. |
| `anam/` package paths | ~40 | `changelog/` only | Dated historical records. See section above. |

## Known limitations

- **No functional change was intended or made.** If anything behaves
  differently, this rename is the wrong place to look — but the identical test
  count, clean ruff, and live health check are the evidence offered.
- **Existing runtime artifacts keep their old names** (`logs/anam.log`,
  `backups/anam-backup-*`). Deliberate; changing them is a behaviour change and
  a separate decision if wanted.
- **`BUILD_PLAN.md:35`'s prose** was edited beyond a path swap, as flagged.

## Project Anam alignment check

1. Entity named? **No** — `program/` names a Python package and is further from
   any entity reading than `anam/` was.
2. Anam-or-Tír leakage? **No.** `Tír` appears nowhere in this build; `Anam`
   remains only as the project and substrate name, which is correct.
3. Personality assigned? **N/A** — `soul.md` byte-identical.
4. Preserve raw experience? **Yes** — no data touched. Changelogs deliberately
   not rewritten, for the same reason.
5–9. **N/A** — no behaviour changed.
10. Anam/entity distinction preserved? **Yes, and improved** — this rename
    removes a third collision the distinction had to survive.
11. Migration required? **No** — no schema change; database paths resolve from
    config, not from the package name.
12. Tests? **Same 326, all passing**, plus a live boot check.
13. Core substrate changed unnecessarily? **No** — content changes were import
    paths and doc references only.
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **Unaffected.** `soul.md` remains package
    content at `program/integrity/soul.md`, still outside `data_dir` and
    `workspace_dir`, and still covered by the Phase 2 blocklist's
    resolved-directory rule.
16. Casual legacy renaming avoided? **Yes** — this is the opposite: a rename
    that *removes* a legacy-name collision, with the old build's `tir/` still
    untouched in `reference/`.
