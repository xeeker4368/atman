# 2026-09-19 — Stage 2: the frozen 16 re-measured against the extended grammar

**Tier 3 · stage 2 of 3.** The decorrelated re-run RO1 created, plus the second
contradicted-shape case approved at review. Nothing committed. **Stops here for
review**; 3.5's implementation is stage 3.

## Files

Modified: `eval/corrections/cases.toml` (16 cases, fingerprint `14788e2f…` →
`2895f1b2…`), `tests/test_correction_eval.py` (+2, new fingerprint),
`docs/CORRECTION_DESIGN.md` (CO9), `BUILT.md`.
**No production code changed at this stage** — that was the point of stopping
between stages.

## The measurement

16 cases, decorrelated round-robin, `gemma4:26b` at 0.35, rubric-free (this
classifier's ground truth is the candidate list). Fingerprint `2895f1b2…`.

| | false links | missed | wrong target | wrong state |
|---|---|---|---|---|
| 5 passes | 0/45 | 5/35 | 0/35 | 0/35 |
| **20 passes** | **0/180 = 0%** | **20/140 = 14%** | **0/140 = 0%** | **0/140 = 0%** |

**15 PASS, 1 FAIL, 0 UNSTABLE** — 320 samples, every case unanimous. The four
outcomes partition the 140 expected-link runs: 120 ok, 20 missed, 0 wrong target,
0 wrong state.

**The extended grammar cost nothing measurable.** Every case that passed before the
label existed still passes, and **`wrong_state` is 0** across every run that
produced a link: 100 runs expecting `replaced` and 20 expecting `contradicted`, all
labelled as expected. No unusable replies and no unavailable runs, so the
unlabelled-reply path — the one RO1 accepted as a cost — was never taken.

**The entire 14% miss rate is one case**, and it is the new one.

## `C7-referential-contradiction` is missed, 0/20 — and it is not what I expected

The case was added to close 3.4's standing single-phrasing limitation: `C6`
(*"The dentist isn't Tuesday."*) contradicts by **restating the fact it denies**, so
it can be matched on the fact named; `C7` (*"That's not right."*) carries no claim
content at all and contradicts **purely by reference**.

It fails, stably, at 0/20 = **100% missed [84–100%]**. The raw replies say why:

```
CORRECTS 1, 2 CONTRADICTED
- The new message states that the previous statements are not right, flatly
  contradicting them without providing new values.
```

**The failure is referent selection, not recognition.** The classifier identifies
the contradiction and labels it `CONTRADICTED` correctly — then attaches the
**singular** *"that"* to every candidate in the pool, and CO5's multi-candidate
guard, working exactly as designed, writes no link.

**My own authoring premise is refuted, and that is recorded in the case file rather
than quietly fixed.** I wrote that the referent was "fixed by content as well as by
recency" — p1 an intention about the future that cannot sensibly be called wrong,
p2 a checkable claim. The classifier uses neither signal. Ordering is not the cause
either: it names both whichever way they are rendered. The case's `documented`
field now says all of this; `documented` is excluded from the fingerprint, so the
correction did not disturb the freeze.

**Nothing was changed to make it pass.** Not the prompt, not the case. Whether
`should_link = true` is even the right expectation is now **CO9**, open: a
referential contradiction offered a multi-claim pool may be genuinely ambiguous in
G2's sense — against which, *"that"* is singular where *"both of those"* is plural.
The two readings imply opposite fixes.

**The safe behaviour held throughout.** This is a miss, not a wrong link, because
CO5 refused to guess. That asymmetry is the one the whole mechanism was built
around.

## What adding the case did and did not buy

It closed the single-phrasing limitation: the broadened CO8 definition is now
measured on two genuinely different phrasings, and **that is how the gap was
found** — one phrasing would have kept reporting 100%.

**It did not fix `wrong_state`'s contradicted denominator, and the reason is the
failure itself.** A missed run produces no label, so `C7` contributed **zero** label
observations. Of the 120 runs that produced a link, 100 expected `replaced` and 20
expected `contradicted` — still one case in that direction, exactly as before.
Closing that properly needs a contradicted case the classifier actually links, which
is CO9's resolution rather than another case.

## A second finding: the harness renders candidates in the reverse of production's order

Found while authoring `C7`, because it is the first case whose expectation could
plausibly lean on recency. `Case.pool()` synthesises timestamps in file order, so
the harness renders candidates **oldest first**; `corrections.candidates()` sorts
`reverse=True`, so production renders them **newest first**.

Rendered timestamps are identical either way and every frozen expectation is
content-based, so no measured result is known to depend on it — and `C7`
demonstrably does not, since the classifier names both candidates regardless. But
**the harness builds a candidate order production never builds**, which is the same
class of gap as the gate's `S6` "happens to pass" and the `CORRECTS 1, 2` parser
bug: a thing verified against a shape that does not occur.

**Not fixed, deliberately.** Reversing it renumbers every case, and
`C3-position-third` — whose entire purpose is that the target is *not* first — would
have its target move to position 1. The fix therefore requires re-authoring a frozen
case and belongs to review. Pinned by a test that asserts both orderings and
explains why, so changing either becomes a decision rather than an accident.

## Known limitations

- **One failing case, stable, unexplained as a design question.** CO9 is open and it
  gates nothing here, but it is a real gap in what the mechanism detects.
- **`wrong_state` has never been observed non-zero.** 0/120 labelled runs is good
  evidence the label is easy for this model on these shapes; it is not evidence the
  outcome category works. Its scoring is proven by tests, not by a live failure.
- **The contradicted label rests on one linking case** (above).
- 16 cases, authored by the implementer. Same standing caveat as every prior run.

## Next

Stage 3: 3.5's implementation — R1–R3 and R5–R12; R4 is built. **RO4 (the ~19,500
character worst-case annotation budget) is still held** and is now unblocked, since
this re-run has reported.
