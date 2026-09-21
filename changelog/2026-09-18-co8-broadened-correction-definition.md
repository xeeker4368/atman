# 2026-09-18 — CO8: a flat contradiction is a correction, and the parser bug that hid behind the old definition

**Tier 3 · decided at review.** CO8 was answered *broaden C5's definition rather
than treat the current behaviour as a bug*. Implementing it exposed a real defect
in task 3.3's parser. Nothing committed.

## Files

Modified: `program/integrity/corrections.py` (prompt + `_parse`),
`program/integrity/correction_eval.py` (kind vocabulary),
`eval/corrections/cases.toml` (15 cases, fingerprint `1fed513c…` → `b2ba7658…`),
`tests/test_corrections.py` (+4), `tests/test_correction_eval.py` (+2, new
fingerprint), `docs/CORRECTION_DESIGN.md` (C5 rewritten, CO8 resolved, a
second-order finding section), `BUILT.md`.

## What CO8 changed

C5's definition required a correction to say **what is true instead**. It now
admits a flat contradiction with no replacement value: *"The dentist isn't
Tuesday."* The reasoning recorded in C5 is CO7's rather than convenience — the
record should surface *"this was contradicted"* even when the correct value is
unknown, and **staying silent is the worse failure here specifically**, because
there is no replacement fact for a reader to lean on if no link fires. The narrow
definition withheld the annotation in exactly the case where the annotation is the
only thing a reader would have had.

The prompt now says so, and **the negative boundary is unchanged and now
load-bearing**: doubt is still not a correction. The line is between *asserting* a
claim false and *questioning* whether it holds — not between having a replacement
and not having one.

`C6-contradiction-no-replacement` joins the frozen set as a should-link case, so
the shape is measured going forward rather than recorded once as a diagnosis. Its
negative twin `N2-doubt` uses the **same prior claim**, so the expectation cannot
be explained by the candidate differing rather than the new message. A test pins
both sides, and another pins that the prompt still states both — the design and the
prompt must not drift when the definition is the thing that changed.

## Then `G2-ambiguous-two-claims` failed 20/20 — and it was not the classifier

The first re-measurement after the broadening: **14 PASS, 1 FAIL**, with
`G2-ambiguous-two-claims` (*"Both of those were wrong."* against two claims)
false-linking **5/5**, always to candidate 1. Escalated per decision #22,
decorrelated against four other cases: **0/20 correct, 100% false link, stable.**

It had passed 20/20 before, because under the narrow definition the model answered
`NONE` — no replacement value was offered. The broadening made the path reachable.

**The classifier was right and the parser was wrong.** Its actual replies:

```
CORRECTS 1, 2
- The dentist appointment time/day and Jodie's train arrival time are both
  invalidated | The message flatly contradicts both earlier statements…
```

It named **both** candidates — precisely what CO5 says must write no link.
`_parse()` did not notice: `_CORRECTS` captured one number per **match**, and
`CORRECTS 1, 2` is one line and therefore one match, so `len(numbers) == 1` read as
a confident single verdict and candidate 1 was linked. **Position bias in the
record, produced by my parser rather than by the model.**

**Why the original test passed.** It scripted
`"CORRECTS 1\n- a | b\nCORRECTS 3\n- c | d"` — two separate lines, the shape the
grammar *implies* rather than the shape the model *uses*. The guard was real and the
test was honest; both were written against an invented reply. **This is the gate's
`S6` "happens to pass" failure mode again: a constraint verified against a form that
does not occur is not verified.**

## The fix, and why it is a fix rather than a tune

`_CORRECTS` now captures the whole leading number list — `1, 2`, `1 and 2`, `2,1` —
and every digit in it is counted, so CO5's existing guard fires. The trailing
alternation stops at the first non-separator, so digits in a same-line rationale are
not swept in; a test covers that opposite failure, because sweeping them in would
turn a valid verdict into a miss.

**Proven to bite:** restoring the old single-number behaviour fails three tests.

It is a fix because CO5 was approved before 3.4 existed and the change is in
*reading what the model said*, not in what counts as a correction. **The frozen case
was not edited and `G2`'s expectation is what it always was** — which is the whole
point of having frozen it.

## The measurement after the fix

15 cases, decorrelated, fingerprint `b2ba7658…`, `gemma4:26b` at 0.35:

| | false links | missed | wrong target |
|---|---|---|---|
| 5 passes | 0/45 | 0/30 | 0/30 |
| **20 passes** | **0/180** | **0/120** | **0/120** |

**15 PASS, 0 FAIL, 0 UNSTABLE**, every case unanimous at 20/20 — 300 samples. `C6` links to the right candidate 5/5 and `N2`
still produces no link 5/5, so the broadened boundary holds from both sides.

## Known limitations

- **`G2` is the only ambiguity case in the set**, and it is now passing for a
  structural reason (the parser drops multi-candidate replies) rather than because
  the classifier declines to guess. Those are different guarantees. A case where the
  model names *one* candidate for a genuinely ambiguous message would test the
  second, and nothing in the set does.
- **The broadened definition is measured on one case.** `C6` is a single phrasing
  (*"X isn't Y."*); other flat-contradiction shapes — *"That's not right."*,
  *"Scratch that."* — are not in the set and were not probed.
- The parser now accepts `,`, `and` and `&` as separators. A model writing
  `CORRECTS 1 & 2` or `CORRECTS 1/2` was not observed; the first is handled, the
  second would parse as a single verdict for 1.

## Follow-up

- Task 3.5's design (`docs/RETRIEVAL_SUPERSESSION_DESIGN.md`) is written and
  carries CO8's two-state consequence as R4/RO1 — **a schema column that needs
  approval before it is coded.**
