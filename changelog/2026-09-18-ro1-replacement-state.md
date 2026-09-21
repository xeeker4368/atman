# 2026-09-18 — RO1: the correction grammar carries REPLACED/CONTRADICTED, and migration 6 stores it

**Tier 3 · stage 1 of 3.** Approved at review: extend the grammar (R4), migration 6
on `working.db` only, and accept that it reopens task 3.4. **The frozen
re-measurement has NOT been run** — that is stage 2, and this stops for review
first, on 3.6c/3.6d's precedent. Nothing committed.

## Files

Modified: `program/memory/migrations.py` (migration 6),
`program/memory/db.py` (`create_supersedes_link`, `REPLACEMENT_STATES`),
`program/integrity/corrections.py` (prompt, `_CORRECTS`, `_parse`, `Correction`),
`program/integrity/correction_eval.py` (`wrong_state`, `unusable` counting),
`eval/corrections/cases.toml` (fingerprint `b2ba7658…` → `14788e2f…`),
`tests/test_migrations.py`, `tests/test_db.py`, `tests/test_corrections.py`,
`tests/test_correction_eval.py`, `docs/CORRECTION_DESIGN.md`,
`docs/RETRIEVAL_SUPERSESSION_DESIGN.md`, `docs/DB_SCHEMA.md`, `BUILT.md`.

**981 tests pass**, `ruff` clean. One standing failure, unrelated: the
`session_secret` one.

## What landed

**The grammar.** `CORRECTS <n> REPLACED` / `CORRECTS <n> CONTRADICTED`, with the
prompt explaining the distinction in the model's own terms. Parsed locally (O18).
Case-insensitive.

**Migration 6** recreates `supersedes` with `replacement TEXT NOT NULL` and
`CHECK (replacement IN ('replaced', 'contradicted'))`.

*Recreated rather than ALTERed for a reason that is SQLite's, not taste:*
`ALTER TABLE ADD COLUMN` with `NOT NULL` **requires** a non-null default, which is
exactly the failure the column exists to prevent — a writer omitting the label
would silently get whichever state is cheaper to render. The alternative, a
nullable column plus a `BEFORE INSERT` trigger enforcing both the NOT NULL and the
vocabulary, puts one constraint in two mechanisms and leaves the schema not saying
what it means. Destructive on migration 5's own grounds: disposable data, and
decision #16 wipes before go-live. **Cost, stated: any `supersedes` rows in a dev
store written since 3.3 landed are dropped.**

**An unlabelled `CORRECTS <n>` is unusable, not defaulted.** Choosing a state on
the model's behalf would make "the classifier did not say" indistinguishable from
"the classifier said contradicted" — the mistake `Actor.operator()` and the unset
retrieval floors exist to avoid. **The cost is real and is not hidden: a correction
the classifier did identify becomes a miss.** Safe direction, deliberate trade, and
a test says so in those words.

**`wrong_state` is the harness's fourth outcome** — the right candidate under the
wrong label. Never a pass, and **scored after `wrong_target`**, because when both
are wrong at once the worse one must be reported; calling a wrong link a labelling
problem would understate it. A test drives exactly that case.

Every should-link case now names its expected label. Five expect `replaced`; `C6`
expects `contradicted`.

## Two defects found while doing it, both the same shape

Neither was in the work being asked for, and both are the *"passes for the wrong
reason"* family this project keeps meeting.

**1. The harness would have hidden a systematic label failure.** `sample_once()`
caught every exception as `unavailable`, which is **excluded from all rates**. An
unlabelled reply raises — so a model that never emitted the label would have
produced *no links at all* while the report read a clean 0% with some runs quietly
dropped. Found by writing the test for the unlabelled path and watching it come
back `unavailable` instead of `missed`.

Fixed by splitting the two cases, which were never the same thing:

* **the classifier could not be reached** → `unavailable`, excluded, because no
  judgment was made;
* **the classifier answered and the answer was unusable** → scored exactly as
  production behaves (no link → `missed` where a link was expected), with
  `unusable_replies` counted beside the rate so the *cause* stays visible.

**2. `test_migration_four_...` passed with migration 4 deleted.** It called
`db.init_databases()` without the `store` fixture, so it ran against whatever store
the ambient data directory already held — already at version 4, so nothing migrated
and the assertions inspected a schema built by earlier code. **Verified by deleting
the `ALTER TABLE` and re-running: it passed, in 0.01 s.** Migration 6's new test had
inherited the same shape from it. Both now take `store`, and both were re-verified
by breaking the migration they cover.

That second one matters beyond these two tests: the pattern is *"a test that
constructs no state and asserts about state"*, and `grep -n "db.init_databases()"
tests/*.py` shows ~20 call sites worth a look. Not done here — it is not this
task's scope, and it deserves to be a deliberate pass rather than a drive-by.

## Verification

- **The cycle guards survived the recreate**, proven by breaking it: deleting
  migration 6's `supersedes_no_cycle_update` fails
  `test_cycle_guard_also_covers_updates` **and** the new migration test. A
  recursive-CTE trigger is exactly the SQL that looks right when transcribed
  wrongly, and this session already produced one such transcription error.
- **The NOT NULL and CHECK are the schema's, not the writer's** —
  `test_the_replacement_state_is_required_and_constrained` inserts raw SQL with a
  missing label and with `'probably'`, and both are refused.
- **Smoke test, live, 1 pass over the frozen 15** (`gemma4:26b`): all 15 correct,
  all six positives labelled as expected including `C6`'s `contradicted`, no
  unusable replies. **This is a smoke test, not a measurement** — one run per case,
  and decision #22 governs rates. It says the grammar works end to end; it says
  nothing about the label's accuracy.

## Known limitations

- **No rate is claimed.** Stage 2 is the decorrelated re-run.
- **`wrong_state`'s `contradicted` denominator is one case.** Five positives expect
  `replaced` and only `C6` expects `contradicted`, so the label's failure rate in
  that direction moves in whole-case steps — the same defect the gate's 18%
  tool-output figure has (*"moves in 9-point steps"*). **A second
  `contradicted` case would fix it**, and would also address 3.4's standing
  single-phrasing limit. **Not added** — the brief scoped this to re-running the
  15, and adding frozen cases is reviewed separately. Recommended.
- Separators `,`, `and`, `&` are accepted in the number list; `CORRECTS 1/2` would
  still parse as a single verdict for 1.
- The label adds ~1 token to a reply whose worst measured length was 51 against
  `classifier_num_predict = 120`, so **no budget or idle-close floor changes**:
  `2000 + 45 + 45 = 2090 s` → floor 35, flat for any total ≤ 100 s. Checked, not
  assumed.

## Next

Stage 2: decorrelated re-run of the frozen 15 (5 passes, escalating anything
non-unanimous per decision #22), reporting `wrong_state` alongside the other three.
Stage 3: 3.5's implementation (R1–R3, R5–R12), with RO4's global annotation budget
still open and held until stage 2 reports.
