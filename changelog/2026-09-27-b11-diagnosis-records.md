# B11 diagnosis: CO10.2's cause corrected, scripts committed, harness-configuration rule

Date: 2026-09-27 · queue item B11, both parts diagnosed and reported · documentation and
diagnostic scripts only. **No production code, prompt, frozen case or measurement of
record changed.** The fixes are planned separately: part 1's harness fix and part 2's
option (b) both change the measurement of record.

## Part 1: CO10.2 reproduced, and its recorded cause was wrong

`docs/CORRECTION_DESIGN.md` recorded CO10.2's false link (*"I do not find any mention of
descaling"* superseded by a general-knowledge explanation of descaling) as a
**context-size** trigger: a larger pool and a longer message. **Refuted.**

- Measured on the pool production's `candidates()` builds for that turn after B2: 11 of
  the entity's messages, with the claim first because candidates are newest first.
- Same pool, same message: the claim **first 20/20** linked, **last 0/20**. The
  intervals do not overlap.
- Six candidates with the claim first: 20/20. One candidate: 0/20. Two or three: 0/5.
- The classifier's rationale drops the claim's "in my records" scope. An answer that
  opens *"None of this comes from the records"* still links 5/5 in the 11-candidate pool.
- Controls hold on both sides: a genuine correction links in every pool, and an
  unrelated answer and a same-topic answer that corrects nothing never do.
- **Consequence:** the correction eval lists candidates oldest first, the reverse of
  production. That difference was recorded as "not known to matter". This defect depends
  on it, so the frozen set cannot see this defect as built.

**Recorded in:**
- `docs/CORRECTION_DESIGN.md`: a new "CO10.2 corrected" subsection. The original reading
  is kept, since the doc keeps superseded readings visible, and CO11 question 2 is
  answered.
- `BUILT.md`: the ordering-gap entry now says it demonstrably matters, and two lines
  that stated the refuted cause are annotated.
- `changelog/2026-09-24-b2-correction-candidate-pool.md` still states the old cause and
  is left alone, as a dated record.

## Part 2: the measurement the decision was taken against

Links are same-speaker only (CO4 as resolved), so a stale entity claim is superseded only
if the entity's own reply self-corrects it. That happens 5/5 when the reply restates the
value, and 5/5 (contradicted) when it apologises. It happens **0/5** when the reply only
acknowledges ("Got it") or moves on, and then the stale claim resurfaces unannotated. The
decision was option (b), a judged person-to-entity link, with (c), a prompted
restatement, as a complement. The plan is submitted separately.

## Scripts (diagnosis only, `scripts/gate_diagnosis_*.py` convention)

- `scripts/correction_diagnosis_co10_2.py`: all 15 cells of the part 1 diagnosis,
  sampled round-robin.
  - It **embeds the entity-side pool**, because the soak store is disposable and is
    wiped before go-live. Candidates are stored at `CANDIDATE_CHARS` (400), which is all
    the classifier ever sees.
  - `--rebuild-pool` regenerates the pool from the soak store, working on a temporary
    copy and never on the store itself.
  - The embedded pool and answer were generated from the rebuilt data and verified
    byte-identical to it. `--rebuild-pool` confirms the answer still matches the store.
- `scripts/correction_diagnosis_acknowledgement.py`: the part 2 table.

**Both re-run from the committed files and reproduce every cell** (5 passes each, all
unanimous and matching the diagnosis).

## AGENTS.md: "A harness must build what production builds"

A new subsection under "Verification discipline". An eval harness has to hand the
function under test what production hands it: ordering, defaults, pool composition and
the registry or settings in force. Otherwise it measures a configuration nobody runs, and
returns clean, stable numbers while doing it. The occurrence cited is this one.

The reviewer recalled an earlier gate/registry instance of the same class. **No record of
it could be found** in the changelogs, `BUILT.md` or `gate_eval.py`, so it is not cited
rather than cited from memory. Add it when its record is identified.

## Tests

No test changes: nothing under `program/` changed. `ruff check .` is clean.

## Known limitations

- The rebuilt pool approximates what retrieval returned at the time. It takes the top
  10, of chunks that existed then, from a 200-result search. Removing later chunks can
  shift RRF ranks slightly. The finding does not depend on the exact pool: position
  decides within the same pool.
- `BUILT.md`'s C8 line ("`2000 + 45 + 45 = 2090 s` → 35") counts the correction calls
  toward the in-flight grace floor. They run after the reply is saved, when the grace
  window no longer applies, and `tests/test_idle.py` correctly leaves them out
  (2,045 s). The floor is 35 either way. This is corrected with the part 2 work, which
  adds a call and has to state the arithmetic anyway.
