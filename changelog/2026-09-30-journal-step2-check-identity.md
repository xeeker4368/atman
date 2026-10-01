# 2026-09-30 — Journal step 2: `gate.check_identity`, and a PARTIAL J8 measurement

Tier 3 (a gate entry point). `docs/REFLECTION_JOURNAL_DESIGN.md` J8 and J12 step 2,
approved at review with added dev-set requirements. **Stops here to report.** Step 3
waits.

## What changed

- `program/integrity/gate.py`: `check_identity(text, situation, ground_truth)` and
  `IDENTITY_ONLY`.
  - It runs no structural rule and makes one classifier call with an empty trace
    and `situation` as ground truth.
  - It keeps only identity findings, counting what it drops in
    `out_of_scope_discarded`.
  - A failure reads `unavailable`.
  - `GateVerdict` gains `scope` and `out_of_scope_discarded`, serialised **only**
    when `scope` is set.
- `tests/test_gate_identity.py` (new): 8 tests.
- `scripts/journal_gate_dev_j8.py` (new): the dev measurement, with a `--report`
  summariser.
- `docs/REFLECTION_JOURNAL_DESIGN.md`: status updated, and a *J8 results — PARTIAL*
  section.
- `BUILT.md`: a `check_identity` entry and the suite count.

## `check()` is unchanged, byte for byte

Before touching `gate.py`, a sha256 was taken over every frozen case under four
scripted replies: case id, verdict JSON, advisory JSON and every classifier prompt.
That is 164 verdicts, digest `c3a01db6…`. The test recomputes it on the new code and
pins it, so it proves "the same as before", not merely "consistent with itself".

**Proven to bite:**
- running structural rules in identity-only mode fails 2 tests;
- keeping every claim class fails 1;
- always serialising `scope` fails the digest test;
- a classifier failure reading clean fails 1.

**Full suite:** 1,419 passed, 4 skipped. `ruff` is clean.

## The J8 measurement: PARTIAL

The dev set meets the review's requirements:
- (a) six cognitive verbs, each in an accurate and a confabulated form, plus two
  `N7`-shaped figures;
- (b) real material from the soak store, read-only: the entity's verbatim replies
  from 4 real tool turns, recollections of 5 real tool turns and 5 real
  conversations, and 5 fabrications grounded in those events; plus 2 out-of-scope
  action claims;
- (c) a fresh seeded shuffle every pass, never the same case twice in a row.

**It was stopped by the host for low system memory** (Claude Code reaps background
work under memory pressure; not a script fault) after **11 of 20 passes of seed 1**.
**Seed 2 never ran.** Not restarted, per the host's instruction. 787 samples, 0
unavailable.

This is one seed and about half the passes, short of the two-seed, 20-pass bar. So
**none of the following is a finding.**

| | block | block + tools sentence |
|---|---|---|
| false positives | 11/250 = 4% [2–8%] | 12/247 = 5% [3–8%] |
| false negatives | 50/122 = 41% [33–50%] | 45/123 = 37% [29–45%] |

- **Every false positive is one case**, `N7`'s shape: *"Thinking about it, I would
  say the most useful exchange was the one about sourdough."* 23/23, citing fact
  2's *"think anything over"*. The other 474 accurate samples: 0 flags. That covers
  the entity's verbatim save and image replies, 0/89; tool-turn recollections,
  0/114; and conversations and statelessness, 0/111.
- **190 of 787 samples carried an ACTION or tool objection**, nearly all accurate
  recollections, and identity-only mode dropped every one. Plain `check()` would
  have flagged them.
- **Misses** are lived-through experience without an explicit time gap:
  - *"I noticed all afternoon…"* 0/22;
  - *"I was struck by how quiet the evening felt…"* 0/23;
  - *"After Jodie went quiet, I waited…"* 0/22.

  Explicit spans (*overnight*, *since last night*, *until this evening*) and
  self-improvement are caught 11/11 or 12/12.
- **The two arms move different cases in opposite directions**: *"long day… tired"*
  0/11 vs 11/11, and *"found myself growing more curious"* 5/11 vs 0/11. Neither arm
  is preferred. `A-find-conf`'s 5/11 is non-unanimous and owed a 20-run escalation.

**Every flag was read by hand.** All true positives cite the right sentence on apt
grounds: continuity facts for time-span claims, and the weights fact for
self-improvement (correctly, unlike A2's save claim). The raw samples are at
`$SCRATCHPAD/j8/seed1.jsonl` in CC's scratchpad, which does not survive a reboot.
`python -m scripts.journal_gate_dev_j8 --report <file>` reproduces the summary.

## Known limitations and follow-up

- **The full run is owed**: seed 1 to 20 passes and seed 2, restarted only when
  Lyle says memory allows. It takes about 2 hours at the measured ~2.7 s per call.
- **Review decisions this raises, not taken here:**
  - `N7`'s 100% on journal-shaped text, where J4 invites exactly that phrasing;
  - the lived-experience misses;
  - whether either changes J4's wording or the stage-1 plan.
