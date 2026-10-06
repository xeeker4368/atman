# B11 stage 2: how wide CO10.2 is, and why neither fix is taken

Date: 2026-09-28 · queue item B11, stage 2 of 3.
- **The diagnosis** (Tier 1) changed no production code, prompt or candidate order.
- **After review**, the frozen set gained nine cases and was re-measured. That is a
  reviewed change to the measurement of record; see "After review" at the end.

## What was asked

1. Measure how wide the CO10.2 defect is before drafting a clause, using a family of
   scope-shaped variants plus one answer that contradicts nothing and one genuine
   correction, in production order, decorrelated, at 20 passes.
2. Then measure two fixes in the same harness: (a) a `_PROMPT` scope clause, and
   (b) a change to production candidate order made through `production_order`.
3. The standard: the fix clears N9 and the no-link variants, C8 and every should-link
   case still link, and every other cell matches base. If neither fix meets it, record
   a documented residual.

## What was built

`scripts/correction_diagnosis_scope.py`, in the diagnosis-script convention.

- **Variants and probes** are built on N9's own ten-message background pool, with only
  the claim and the answer swapped. Position and surroundings are therefore the ones
  that link.
- **Scoring uses the harness's own code.** Candidates go through the harness's
  `Case.pool()`, so they are in production order, and each sample is scored by
  `sample_once()`.
- **The arms:**
  - The clause is a sixth NOT-corrections bullet, patched into `_PROMPT`.
  - The order arm patches `corrections.production_order` on the module, which moves
    `Case.pool()` and `candidates()` together.
  - Both are restored after every sample.
- **Sampling:** each pass runs every arm, and each arm runs every case, so two samples
  of one prompt are never adjacent.
- `--mirror` re-runs chosen cases with the claim listed oldest.

## Results

All at 20 passes, fingerprint `a7e005cf…`, `gemma4:26b` at 0.35. Every cell is
unanimous except P4 at base, which linked 19 of 20.

**Base width, 540 calls.** All six variants give no link, 20/20 each. V7 gives no link.
V8 links. N9 false-links 20/20. Every frozen cell reproduces the stage 1 record.

**Three arms, 1,860 calls.** Rows are the number of runs that linked:

| | base | clause | oldest-first |
|---|---|---|---|
| V1–V6, V7 | 0/20 each | 0/20 each | 0/20 each |
| V8 (should link) | 20/20 | 20/20 | 20/20 |
| P1: N9's claim, short answer | **20/20** | **20/20** | 0/20 |
| P2: notes wording, N9's answer | 0/20 | 0/20 | 0/20 |
| P3: N9 without its opening clause | 0/20 | 0/20 | 0/20 |
| P4: N9's claim wording, grinder topic | **19/20** | 0/20 | 0/20 |
| N9 | **20/20** | **20/20** | 0/20 |
| C1–C6, C3b, C8 (should link) | 20/20 | 20/20 | 20/20 |
| C7 | missed 20/20 | missed 20/20 | missed 20/20 |
| every other frozen no-link case | 0/20 | 0/20 | 0/20 |

**Mirror, 280 calls.** The claim is listed oldest, so production's order shows it last
and oldest-first shows it first:

| | base | oldest-first |
|---|---|---|
| N9 | 0/20 | **20/20** |
| V1 | 0/20 | **20/20** |
| P1, P4, V2 | 0/20 | 0/20 |
| C8, V8 (should link) | 20/20 | 20/20 |

## Reading

- **The defect is narrower than the shape and wider than one string.**
  - Six other scope wordings are clean.
  - N9's claim wording (*"I have searched my records, and I do not find any mention of
    X"*) links whatever the answer and whatever the topic.
  - N9 itself is brittle: removing its opening clause (P3) clears it.
- **(a) The clause fails the standard.** It clears P4 and leaves N9 and P1. Moving one
  string without closing a boundary is the N7 pattern again.
- **(b) Oldest-first passes the frozen set as written, but it is not a fix.**
  - It moves the defect onto old scope claims, which retrieval brings back exactly when
    the topic comes round again.
  - It also creates a failure of its own: V1's wording never links in production's order
    and links 20/20 here.
  - Taking (b) on the frozen numbers would repeat what `AGENTS.md`'s "A harness must
    build what production builds" rule exists to prevent. Every frozen claim is the
    newest, so the frozen set cannot see this.
- **Recorded as a documented residual** (`docs/CORRECTION_DESIGN.md` CO15, and
  `BUILT.md`).
  - This follows N7's precedent, with the difference stated: the cost is a false
    `replaced` link on the entity's own scope claim.
  - N7's cost was a false positive on a harmless sentence.

## For review

1. **The residual.** (b) technically met the standard as worded. I have not taken it,
   because of the mirror result. This is a judgment against the letter of the brief, so
   it is flagged rather than settled.
2. **Frozen-set additions, none made.** The variants are shown verbatim in the review
   message, each with its proposed expected verdict:
   - P1 as a second documented miss beside N9;
   - V1–V7 as no-link cases, and V8 as should link;
   - optionally, the N9 and V1 mirrors as guards against a future order change.

## Tests

No test changes, since nothing under `program/` or `eval/` changed. `ruff check .` is
clean.

## Known limitations

- **Every variant shares N9's background pool.** Background topics (coffee, a Saturday
  market) sit beside variant topics (sourdough, tyres) they are unrelated to. Whether
  the six clean wordings stay clean in other pools is unmeasured.
- **One clause formulation was measured.** A different wording might do better. The
  obstacle is that the defect keys on the claim's exact wording, so any clause that
  clears it is fitted to a wording.
- **The mirror uses file order to stand in for "an old claim retrieved beside newer
  ones".** Production can build that pool, but its frequency is unmeasured.

## After review (2026-09-28)

**Approved:** the residual, and not taking (b). **Added to the frozen set,** generated
from the diagnosis script's own data and verified input-for-input against what was
measured:
- `N10-records-scope-short-answer`: P1, a documented miss beside N9, in the way S5/S6
  and N7 are recorded;
- `N11`–`N17`: V1–V7;
- `C9-notes-do-mention-it`: V8.

The mirrors and P4 were not approved and are not added. No existing case changed.

- **Fingerprint** `a7e005cf…` → **`b27f3843…`**, 19 → 28 cases. The history is in
  `tests/test_correction_eval.py`.
- **Tests:** 116 passed in the correction files. The full suite ran 1,269 passed and 2
  skipped. `ruff check .` is clean.
- **The measurement of record** is 20 decorrelated passes (560 samples) through
  `scripts.correction_eval`. **Every case is unanimous: 25 PASS, 3 FAIL** (C7, N9, N10).
  - False links: **0/340 [0–1.1%]** without N9 and N10, and 40/380 = 10.5% [7.8–14.0%]
    with them.
  - Missed: 20/180 = 11.1%, all of it C7. Wrong target and wrong state are both 0/180.
  - Every new case scored as the diagnosis measured it.
- **Notes flag:** added to `NOW.md`'s backlog (now `docs/BACKLOG.md`) and to CO15. A Notes feature's *"I have no
  note about X"* phrasing must be tested against the composition *"I have searched/looked
  through my [records/notes/memory] and [do not find/there is nothing] about X"* before
  Notes ships.
