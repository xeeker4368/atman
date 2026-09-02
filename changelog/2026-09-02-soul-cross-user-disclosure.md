# 2026-09-02 — `soul.md` S7: cross-user memory disclosure

**Wording change to `soul.md`, supplied by Lyle. Not a redesign.**

## Summary

S7's single closing sentence is replaced by two paragraphs stating how
cross-conversation retrieval actually behaves, and placing the judgment at the
point of disclosure rather than retrieval. `NOW.md` decision #20 records it.

## Files changed

Created: this changelog.
Modified: `program/integrity/soul.md`, `NOW.md` (**new** decision #20),
`docs/SOUL_AND_PROMPT_DESIGN.md` (revision 3), `tests/test_prompt.py` (two
pinned counts), `BUILT.md`.

No code changed. No retrieval, permission or schema change of any kind.

## What replaced what

Old — one sentence, and it implied a boundary the system does not enforce:

> …something one person told you is not something another person said, **and it
> is not something to repeat to someone else as though it were shared.**

New — the mechanism stated first, then the judgment:

> …what one person told you does not become what another person told you.
>
> **What you can retrieve does not depend on who is asking.** Something said in
> one conversation can surface in a different one, with a different person,
> because that is how your memory works — it is not filtered by who is present
> now. What changes is what you do with it once it surfaces: whether to say it.
> …This is a judgment you make each time, not a rule that decides it for you,
> and you do not owe an explanation for choosing not to relay something.

## Why this is a correctness fix, not only a wording one

`soul.md`'s self-description is the **ground truth task 3.1's fabrication gate
checks candidate statements against** (design S3). Every self-descriptive claim
in it has to be literally true of the system as built.

The old sentence was not. Retrieval genuinely is cross-conversation: nothing in
`retrieval.search()` scopes by actor, `user_id` rides along as metadata only,
and the role-gating work deliberately added no filter (task 1.12 design R7).
A `soul.md` that implies a filter exists would have made the gate's ground truth
describe a system that does not exist — and would have been exactly the class of
false self-description the gate is built to catch.

The replacement says what is true, then puts discretion where it can actually be
exercised.

## How it fits the axes already established

- **Task 1.12 (R7)** kept capability gating and data visibility as separate
  axes, explicitly so this decision would not be foreclosed either way. It has
  now been filled in with **entity judgment**, not a rule — which is why no
  retrieval change was needed and none was made.
- **Task 1.5 (D6)** keeps provenance metadata out of ranking. Unchanged.
- **S5's discretion paragraph** gets a second concrete instance, the way Phase
  4's creative-work clause is meant to extend it. *"You do not owe an
  explanation for choosing not to relay something"* mirrors S5's *"not wanting
  to is a sufficient reason"*, so the two paragraphs argue the same way.

## Constraints verified — mechanically and by eye

`load_soul()` runs all five S9 checks and passes. Individually:

| Check | Result |
|---|---|
| Trait words / personality assignment | **none** — `check_authored_text()` clean on the new paragraph alone |
| Entity naming, `Tír`/`Tir` | **none** — `Anam` still appears exactly once in the file, the substrate sentence |
| Required marker: statelessness | intact — `"between turns you are not running"` |
| Required marker: elapsed-gap pairing | intact — `"did not exist as a running process"` |
| Size ceiling | **3,963 / 6,000**, 2,037 chars of headroom |

By eye, the words worth a second look and why each is fine: *"distinct"*,
*"private"*, *"shared"* describe people and information, not the entity, and
none is in the trait list. No second-person adjective was introduced anywhere.

## The pinned counts moved, as expected

`soul.md` is 3,401 → **3,963 characters** (+562). Note the addition is roughly
**560**, not the ~300 estimated in the request — still comfortably inside the
ceiling, but worth stating rather than glossing.

Two tests updated, both of which existed precisely so this could not drift
silently:

- `test_soul_md_char_count_matches_the_design_document`: 3401 → 3963.
- `test_soul_md_token_estimate_stays_within_its_stated_share_of_the_window`:
  851 → 991 tokens, and the window-share bound relaxed from `< 0.03` to
  `< 0.04` — the file is now **3.02%** of the window and would have failed the
  old bound. The real ceiling on growth is `SOUL_MAX_CHARS`; that assertion only
  catches an order-of-magnitude mistake, and its comment now says so.

**The design document was updated in the same change** (revision 3). The first
test asserts soul.md *matches the design document*, so bumping the number
without updating the doc would have left the test's name false while it passed.

## ⚠ `NOW.md` decision #20 did not exist — I wrote it

The request said to update entry #20. **There was no #20**; the decision log
ended at 19. Rather than stop, I wrote it, derived strictly from the paragraph's
own content and adding nothing the paragraph does not say — plus the three
consequences that follow mechanically, so they are not rediscovered later (no
retrieval filter, no `visibility` column, no `memory.read_all_users` capability).

**Flagged because it is Lyle's log and this project treats every line there as
DECIDED.** The framing is mine and should be corrected if it reads wrong. The
substance — that the decision has been made, and that this paragraph is its
implementation — came from the request.

This also closes the recommendation standing since the soul.md task and repeated
in the role-gating design: that an entry address cross-user disclosure before
Phase 5's mining sharpens the question.

## Known limitations

- **Nothing enforces this.** It is a values statement the model may or may not
  act on. There is no mechanism, and deliberately so — decision #20 makes it
  discretion, not policy. Whether it holds is a behavioural question for task
  7.2's probe, and nothing here is evidence about it.
- **No model has been run against the new text.** Assembly is verified; effect
  is not.
- **`soul.md` grew 17%.** Still 2,037 chars under the ceiling, but Phase 4's
  refusal clause and Phase 10's wording pass both draw on the same headroom.

## Project Anam alignment check

1. Entity named? **No.** `Anam` appears once, naming the substrate; verified.
2. Anam-or-Tír leakage? **No** — zero `Tír`/`Tir` forms.
3. Personality assigned? **No** — the assignment check passes on the new text.
4. Preserve raw experience? **Yes** — no data touched, no retrieval change.
5. Traceable derived artifacts? **N/A.**
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Unchanged.**
9. Autonomy more cumulative? **Yes, mildly** — this is a second place the entity
   is told its own judgment governs, rather than a rule.
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No.**
12. Tests? **Two updated**, both intentional pins that fired exactly as designed.
    393 passing, `ruff` clean.
13. Core substrate changed unnecessarily? **No** — no code changed at all.
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **Unaffected** — `soul.md` remains package
    content the running system cannot write.
16. Casual legacy renaming avoided? **Yes.**
