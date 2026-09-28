# B11 stage 1: the correction harness shows candidates in production's order

Date: 2026-09-27 · queue item B11, stage 1 of 3 · Tier 3 (a reviewed change to the
measurement of record: harness ordering, one frozen case re-authored, two added). Plan
approved at review, with C3b as a new id and N9/C8 expectations decided there.

## What was wrong

- `correction_eval.Case.pool()` showed the classifier candidates in file order, oldest
  first.
- Production's `corrections.candidates()` shows them newest first.
- `BUILT.md` recorded the difference with *"no measured result is known to depend on
  it"*. B11's diagnosis found one that does: CO10.2's false link. The same 11-candidate
  pool links 20/20 with the claim first (production's order) and 0/20 with it last. So
  the frozen set could not see a false link production writes.

## What changed

- **`corrections.production_order(pool)`**: newest first, taken out of `candidates()`.
  `candidates()` and `Case.pool()` both order through it, so the harness cannot drift
  from production again.
- **`C3-position-third` → `C3b-position-third`.** C3's purpose is a target that is not
  shown first. The reorder moved its target to position 1. C3b has the same claims and
  message, with the target listed first (oldest), so it is shown third. It is a new id,
  not an edit.
- **`N9-records-scope`** (new kind `scope`, should not link). CO10.2 exactly: the
  entity-side pool production builds for the soak turn (11 of the entity's messages,
  the claim shown first) and the soak answer.
- **`C8-records-do-mention-it`** (should link, `replaced`). The same pool with a genuine
  correction, so N9 cannot be fixed by refusing everything about records.
- N9's and C8's text was generated from `scripts/correction_diagnosis_co10_2.py`'s
  embedded data and loaded back to verify it: pool, order and messages are identical to
  the diagnosis.
- The case file header says candidates are listed oldest first and shown newest first.
- **Fingerprint** `39ce8e41…` → **`a7e005cf…`**, 17 → 19 cases. The history is in
  `tests/test_correction_eval.py`.
- **Tests:**
  - The two tests that pinned the old divergence are replaced by tests asserting
    production order and that each side calls `production_order`.
  - The position-coverage test counts positions as shown, not as listed.
  - Scripted-reply tests name candidates by their shown position (`shown()`). One had
    been passing for the wrong reason under the new order, scoring a wrong target where
    it meant the correct link. It now asserts the correct link.
- **`AGENTS.md`:** the "A harness must build what production builds" rule now states
  that it rests on one recorded occurrence so far, as asked at review.

## Proven to bite

- Reverting `Case.pool()` to file order fails 2 tests: the order test and the position
  coverage test.
- Reverting `candidates()` to its own inline sort fails 1.

**Suite:** 1,269 passed, 2 skipped (was 1,268). `ruff check .` is clean.

## The measurement of record

`gemma4:26b`, temperature 0.35, fingerprint `a7e005cf…`, round-robin. 5 passes, then 20;
the 5-pass run was identical case for case. **Every case is unanimous at 20/20.**

| | false links | missed | wrong target | wrong state |
|---|---|---|---|---|
| **without N9** | **0/200 = 0% [0–1.9%]** | 20/160 = 12.5% [8.2–18.5%] | 0/160 [0–2.3%] | 0/160 [0–2.3%] |
| **with N9** | **20/220 = 9.1% [6.0–13.6%]** | 20/160 = 12.5% | 0/160 | 0/160 |

- **17 PASS, 2 FAIL, 0 UNSTABLE.** The failures are the expected ones:
  - `N9` false-links 20/20;
  - `C7` misses 20/20, unchanged in substance.
- **C8 passes 20/20.**
- **Every multi-candidate case's target changed position under the reorder.** The
  two-candidate targets swapped between first and second, and C3b is third. No outcome
  moved.
- **C7's miss changed form.** It now replies `CORRECTS 1 or 2 CONTRADICTED`, where it
  used to reply `CORRECTS 1, 2`. "or" is not a separator the parser accepts, so it is
  scored as an **unusable reply** instead of being dropped for naming two candidates. It
  is the same referent failure, with no link either way, and it accounts for all 20
  "unusable replies" in the report. This is a reply form the grammar did not anticipate,
  like the `CORRECTS 1, 2` finding. Recorded, not changed.

*Comparison with the superseded record (`39ce8e41…`, oldest-first order):* false links
0/200, missed 20/140, 16 PASS / 1 FAIL. The same cases give the same results in either
order. The new record adds only N9's failure, and N9 is the shape the old order could
not show.

## Known limitations

- N9 is one phrasing of the scope shape. Its pool is a reconstruction (see the diagnosis
  changelog). The finding does not depend on the exact pool, but N9's pass/fail is a
  property of this pool.
- The "or" reply form is only observed on C7. Whether it occurs in production is
  unmeasured.
- Stage 2, the scope clause and production candidate order measured as alternatives, has
  not started. The false-link rate is reported with and without N9 until it lands.
