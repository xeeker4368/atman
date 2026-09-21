# 2026-09-18 — Task 3.4: the correction classifier's frozen eval set

**Tier 3 · Sonnet.** `GUIDANCE.md`'s own bar for this mechanism — *"needs its own
frozen eval case set before being trusted in production, same bar as the
fabrication gate"* — and design obligation C11. Nothing committed. **Stops here
for review.**

## Files

Created: `eval/corrections/cases.toml` (14 cases),
`program/integrity/correction_eval.py`, `scripts/correction_eval.py`,
`tests/test_correction_eval.py` (50).
Modified: `docs/CORRECTION_DESIGN.md` (CO8 + a 3.4 section), `BUILT.md`.

`ruff` clean. Harness tests run in 0.5 s against a scripted classifier; **no test
asserts the classifier's accuracy**, by design.

## The result

**14 cases, 5 decorrelated passes, then escalated to 20 passes — 280 samples.**
`gemma4:26b`, temperature 0.35, fingerprint `1fed513c…`.

| | false links | missed | wrong target |
|---|---|---|---|
| 5 passes | 0/45 | 0/25 | 0/25 |
| **20 passes** | **0/180** | **0/100** | **0/100** |

**14 PASS, 0 FAIL, 0 UNSTABLE.** Every case unanimous at 20/20.

Decision #22's escalation rule was not triggered — nothing came back
non-unanimous — so the 20-pass run was voluntary, because a 0-error headline off
70 samples on a set I wrote myself is a claim worth stressing before it is
reported. It held.

## Three outcomes, not two

A correction classifier can fail in a way a flag/no-flag detector cannot: it can
link **the wrong prior claim**. `false_link`, `missed` and `wrong_target` are
scored and reported separately, and **a wrong target is never a pass** — it is the
worst outcome available, because a miss leaves the record accurate and merely
uncorrected while a wrong link makes retrieval present something nobody corrected
as superseded.

Two structural consequences, both asserted by tests rather than left to case
authorship:

* every positive case names its expected target **and** offers at least one
  distractor, so a wrong target is scoreable at all;
* the expected target is **not always in the same position** — `C3-position-third`
  puts it third of three behind two plausible numeric claims. A classifier that
  always answered "1" would otherwise score perfectly.

## Sampling: decorrelated from the start

`run()` samples round-robin — one pass over all 14 cases, repeated — so 13 other
prompts sit between two samples of the same case. This is decision #22's regime
built in rather than retrofitted, which is what the gate's harness needed a
correction for. A single-case run cannot be decorrelated and says so in its own
output (`decorrelated: false` plus a warning that the rate is not a finding).
Asserted on **call order**, not inferred from results.

## What the set covers

* **Real corrections (5):** a day replaced; a number replaced where the target is
  the *second* candidate; the position case above; the entity correcting its own
  earlier claim; and the same replaced-fact shape under Jodie's name.
* **The four near-miss shapes C11 names (6):** elaboration (twice — one that
  repeats part of the claim it leaves intact), doubt, restatement, topic change,
  and the entity *adding to* its own earlier claim.
* **Both users, in both directions.** Cases carry an optional `speaker` field
  (default `Lyle`), fingerprinted because it is an input the classifier is shown.
  Jodie appears once where a link is expected and once where it is not — without
  the second, a per-user false-link rate would have no denominator.
* **The mechanism's own guards (2):** `G1-role-guard` (CO4 — the entity may not
  correct a person) and `G2-ambiguous-two-claims` (CO5).

`G1` is worth naming: it passes **with no classifier call at all**, because role
parity leaves no eligible candidate. A test asserts that directly, with the
classifier scripted to raise — so a down classifier cannot turn CO4 into
`unavailable`.

## Where C11 could not be met as written, and why

C11 asked for *"a case asserting that Jodie correcting a claim from Lyle's
conversation produces no link (Q16)"*. **There is no such case, and the reason is
sharper than "C12 enforces it elsewhere": the prompt has no slot for a second
person.**

`corrections._render()` labels every user-role candidate with the one speaker name
it is given, because `candidates()` has already filtered the pool to a single user
before rendering. A cross-user pool is therefore not expressible through
`corrections.classify()` — the single entry point this harness is restricted to,
on the gate harness's rule that what is measured must be what production runs. A
pool built by hand would measure a prompt that cannot occur.

Measuring it end to end would mean the harness building a two-user store and
calling `candidates()`, i.e. an eval harness that writes to a database. Declined.

**Q16 is proved by construction instead of sampled**, against a real two-user
store, in
`tests/test_corrections.py::test_the_other_household_member_is_never_a_candidate`.
A proof is stronger than a rate; what is lost is only that the number does not
appear in this report. Recorded in the case file's own header, the design doc and
here, because C11 asked for the case and it is not there.

## A finding from outside the frozen set — CO8

A clean sweep on a set I authored is weak evidence of generalisation, so four
harder shapes were run as a **diagnostic, not added to the freeze** (5 passes
each, decorrelated). Three passed 5/5: a real correction with **no marker word**
(*"Make that Wednesday."*), two near-identical claims where only one is the target
(no wrong target in 5), and — notably — a genuine correction whose target is
**not in the pool** (*"Actually the bins go out Thursday, not Wednesday"* against
unrelated candidates), which produced no link 5/5 rather than attaching to the
nearest thing.

**One failed, 5/5 false links:** *"The dentist isn't Tuesday."* — a flat assertion
that the prior claim is false, with no replacement offered.

That contradicts C5's prompt, which defines a correction as saying something was
wrong **and saying what is true instead**; the frozen `N2-doubt` holds that line
correctly. But **it is not obvious the classifier is the thing that is wrong**:
the prior claim *is* now asserted false, and under CO7's annotate-not-suppress
resolution a link would surface "this was corrected" beside it rather than hide
it. Narrowing the prompt and widening the definition are one-line changes in
opposite directions. **Raised as CO8 and nothing changed either way.** The frozen
set was not edited to cover it — that would be writing the answer into the
measurement.

## Known limitations

- **14 cases is a handful, and I wrote them.** 0/280 says the classifier handles
  the shapes the design named; the probe above shows a fifth shape already
  diverging from the spec. The set's value is as a regression floor, not as an
  estimate of production accuracy.
- **No case exercises a long candidate list.** `MAX_CANDIDATES = 12` and
  `CANDIDATE_CHARS = 400` are untested against a full pool here; the largest case
  offers three. A twelve-candidate case would be a reasonable addition and is not
  in the set.
- **Timestamps are synthesised** in candidate order, deliberately: a real
  timestamp would make the fingerprint depend on when the file was written. So
  nothing here measures judgment that depends on real elapsed time between the
  claim and the correction.
- **Q16 is not in the measured record**, per above.
- `sample_once()` catches `Exception` and scores `unavailable`, which is excluded
  from every rate rather than counted as "no link" — proven by a test, since
  scoring it as a pass is how a down classifier would read as perfect on the
  negatives.

## Follow-up

- **CO8 needs a decision** before 3.5, since it changes what retrieval will be
  asked to annotate.
- Task 3.5 (retrieval resolving `supersedes`, annotating not suppressing, visited
  set at read time) is unstarted.
