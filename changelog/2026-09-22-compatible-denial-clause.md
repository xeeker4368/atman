# The correction classifier no longer links a compatible denial

2026-09-22 · CO13/V3 · authorised after a diagnosis-only design pass

## What was wrong

During the 3-hour soak, 2 of the 13 `supersedes` links written on the production store were
false, both entity self-corrections. One of them linked these:

```
superseded : "Nothing. I have not been running, so I have not been doing anything."
superseding: "No. I have not been thinking about our last conversation. I was not
              running, so I have not been thinking about anything."
label      : contradicted
```

**The two statements agree.** Not thinking is included in not doing anything. Linked
**5/5**, and a second phrasing differing only by *"I was not running"* → *"I have not been
running"* also linked 5/5.

The consequence is specific and is why this ranked where it did: **task 3.5 annotates a
superseded record as contradicted whenever it surfaces**, so the honest statelessness denial
— the exemplar answer `BUILT.md` records as the fix for the prior build's confabulation, and
which this same soak produced *correctly* after a real 21-minute gap — would be presented to
the model as having been contradicted, with no replacement.

**Two earlier characterisations of mine were wrong and were corrected by testing.** It is
not the pronoun/addressee family: the text contains no second person at all and
`pronouns.rewrite_sentences()` changes neither message. And it is not a general
scope-narrowing defect: two paraphrases with the same topic *and* the same three-clause
structure never reproduced (0/5). It was a small set of exact strings — the same shape `N7`
turned out to be for the gate.

## What changed

One bullet added to `corrections._PROMPT`'s list of non-corrections:

> *denying a specific thing after denying everything: "I have not been doing anything"
> followed by "I have not been thinking about it" are compatible — the second is included
> in the first — so that is NOT a correction, and neither is any restatement of the same
> denial with a different detail named.*

**Three formulations were tested before this one was chosen**, and the frozen-set comparison
is what decided it:

| formulation | fixes the defect | frozen set |
|---|---|---|
| a mild "both could be true" bullet | **no** — 5/5 unchanged | — |
| an "incompatible-first" paragraph | yes — 0/5 | **regressed**: false links 1/45, `G2-ambiguous-two-claims` UNSTABLE |
| **the shipped wording** | yes — 0/5 | **identical to base on every cell** |

The middle one is worth keeping on record: a clause aimed at one shape destabilised CO5's
multi-candidate guard, which is evidence that this class of change is not locally safe by
default and that the narrow test alone would not have caught it.

## The frozen case ships with it

`N8-compatible-denial` — the exact production shape, `should_link = false`, two candidates
so the pool is not degenerate. Its `documented` field records the pre-fix 5/5, the second
failing phrasing, the two paraphrases that never reproduced, and the formulation that
regressed.

**Fingerprint `2895f1b2…` → `39ce8e41…`**, 16 → 17 cases, with the history comment in
`tests/test_correction_eval.py` extended to say why. A test also pins the clause's presence
and asserts the case exists alongside it: **a fix with no case is a fix nothing will notice
regressing**, which is the role `S5`/`S6` play for the gate's known gaps.

**Why the old set could not see this.** `self_correction` reported false links **0/20** and
missed **0/20** with both its cases perfect — while **both** false links produced in
production were self-corrections. The set said the area was clean.

## The measurement of record, re-run in full

Changing `_PROMPT` invalidates every prior number by construction, so the full 20-pass
decorrelated re-measurement was run, matching O16's standard rather than relying on the
5-pass screen.

**17 cases x 20 passes = 340 calls**, fingerprint `39ce8e41…`, `gemma4:26b` at 0.35:

| | before (16 cases) | after (17 cases) |
|---|---|---|
| false links | 0/180 = 0% | **0/200 = 0%** |
| missed | 20/140 = 14% | **20/140 = 14%** |
| wrong target | 0/140 = 0% | **0/140 = 0%** |
| wrong state | 0/140 = 0% | **0/140 = 0%** |
| `self_correction` | false links 0/20, missed 0/20 (2 cases) | **false links 0/40, missed 0/20 (3 cases)** |
| case states | 15 PASS, 1 FAIL | **16 PASS, 1 FAIL, 0 UNSTABLE** |

**`N8-compatible-denial`: 20/20 correct.** Every case unanimous. `C7` remains the single
known failure at 0/20, unchanged. Full suite 1153+ passing, `ruff` clean.

## Known limitations

- **The shipped wording names the shape it fixes**, so the `N7`-trap concern is bounded
  rather than eliminated: it closes both failing strings found, and only two were findable.
  A third phrasing appearing later would be evidence the boundary is still open rather than
  closed.
- **One unusable classifier reply in 340 calls** (`unusable replies (scored) 1`), scored as
  production behaves rather than excluded, per the harness's own rule. It landed on a
  no-link case, so `missed` is unchanged at exactly `C7`'s 20.
- The other false link from the soak — the *"not in my records"* one, which needs a
  6-candidate pool **and** a long message to reproduce — is untouched here and stays open as
  CO10.2.
