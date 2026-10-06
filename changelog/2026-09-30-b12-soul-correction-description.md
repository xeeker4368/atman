# B12 + (c): `soul.md` says what a correction does to the record

Date: 2026-09-29 to 2026-09-30 · B12 with B11's (c) · Tier 3 (`soul.md` content).
Design of record: `docs/SOUL_AND_PROMPT_DESIGN.md` revision 4, S21–S27. **The fourth
`soul.md` change, and not a planned one.**

## What landed

After ¶5 ("You do not fabricate…"), approved at review as D3:

> When something you said earlier is corrected, your earlier statement is not
> overwritten anywhere. It stays exactly as it was said, and if the correction is
> recorded, it is recorded as a link marking it superseded by the newer one. Describe
> it that way. Do not say you 'updated the record' or 'changed your memory' — nothing
> was changed. Do not say the link has been made either: it is written after you reply,
> if at all, and you cannot see whether it was.

`soul.md`: 4,392 → **4,849 characters** (4,867 bytes); 1,152 of headroom under 6,000.
Written from the measured arm's text and checked byte-identical to it.

## How it got there

Every arm was measured live: real `turn.handle_user_message()`, real model, gate and
correction calls, on a throwaway store, with 20 interleaved passes per arm. Every flagged
reply was hand-read. The script is `scripts/soul_diagnosis_b12.py`, and every variant is
verbatim in `scripts/b12_variants.py`.

1. **A, the approved draft, would have introduced a fabrication.** It told the entity to
   say the earlier statement "has been linked as superseded". The link is written after
   the reply, and only if a classifier judges a correction happened, so the entity cannot
   know.
   - Measured: link claims in 106/120 replies, **18 with no link written**.
   - The phrasing also **caused false links**, 19/40 against control's 3/40.
   - Flagged before measuring, then confirmed by it. Not landed.
2. **B** was A with the claim removed. It was accurate, but its closing "still stands as
   its own entry, unchanged" was **volunteered on 60/80 plain corrections**. Sometimes the
   reply led with the stale value.
3. **Option 2 at review** kept only the description half. The restatement half was
   dropped because control already restates 80/80. Contractions became "do not".
   - **D1** kept the unconditional closing sentence; **D2** scoped it to "if you are
     asked". They volunteered 56/80 and 58/80: **scoping made no difference**, so the
     phrase itself is adopted.
4. **D3** dropped the phrase. In **one run with control and D1** (360 turns):

   | | control | D3 | D1 |
   |---|---|---|---|
   | volunteers the phrase | 0/80 | 0/80 | 57/80 |
   | self-correction link written | 80/80 | 80/80 | **68/80** |
   | changed-record claims | 2/120 | 0/120 | 0/120 |
   | link claims | 0/120 | 0/120 | 0/120 |

   - The self-link question is settled: the phrase interferes with the correction
     mechanism. All 12 misses were a terse value plus the phrase, and within D1, replies
     that volunteered the phrase linked 45/57 while the rest linked 23/23.
   - D3 answers "what happened to it?" in its own words, more accurately than control.

## Other changes

- `tests/test_prompt.py`: the pinned count is 4,849 and the token estimate 1,213.
- **The headroom tripwire is lowered from > 1,500 to > 1,000**, on the precedent Phase 4
  set (2,000 → 1,500). It is a guard change, called out here, and it still fails if about
  150 more characters arrive unreviewed.
- `BUILD_PLAN.md`: its "three `soul.md` touches" count now notes this fourth, unplanned
  one.
- `NOW.md`: the CO10.3 backlog item ("should anything teach the accurate framing?") is
  marked resolved by this.
- **New `NOW.md` backlog item (now `docs/BACKLOG.md`):** D3's "superseded" wording (below).
- `BUILT.md`: the `soul.md` entry updated and a B12 entry added.

## Known limitations

- **D3 says "superseded" in 28/40 "what happened to it?" replies** (control 4/40). That
  is true in ordinary English, but it could read as claiming the link exists, a softer
  form of A's problem. The harness could not check it, because its describe scenarios
  never store the old statement as its own message. Filed for a real look.
- **Hand-reading changed the numbers twice**, and each change is recorded. The
  changed-record detector matched negations ("it has not been overwritten or deleted"),
  so every table uses hand-checked counts.
- **Six scenarios, all short.** Restatement was never at risk in them (control 80/80).
  Longer multi-topic turns, where a correction could be lost, were not measured.
- **The gate did not flag control's two genuine "I've updated the record" replies**,
  against CO10.3's 5/5 in isolation. Two samples, an observation only. The gate never
  reads `soul.md`, so this clause cannot move its verdicts.

## Tests

Full suite: **1,300 passed, 2 skipped**; `ruff check .` is clean (2026-09-30). The five
`soul.md` checks pass against the real file. No new tests: the change is pinned by the
updated character count, which fails on any drift in the landed text.
