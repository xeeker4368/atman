# O23 part 1: corrections to the ACTION class's recorded claims

Date: 2026-09-30 · documentation only, approved at review. No code, prompt, harness or
frozen-input change. Part 2 of O23 (the trigger's vocabulary-dependence) is separate and
not implemented.

## What was wrong

1. **F44.2's isolation check was described as existing, and it does not.** The design doc
   (F48) and `BUILT.md` both said an F44.2 check injects synthetic ACTION labels and confirms
   the `identity`/`tool_output` cells do not move. No test scripts `CONTRADICTS-ACTION` into
   `gate_eval`; the only such test covers `CONTRADICTS-TOOL`. The revision-9 changelog's
   *"confirmed twice"* was a before-and-after comparison of the live model on the 34 older
   cases.
2. **The check could not pass if written.** An ACTION finding is authoritative, and
   `gate_eval` scores whether a case flagged, not which class fired. Run as specified with a
   scripted classifier, over the frozen 41 cases for one pass: identity went from 0 false
   positives / 7 false negatives to **13 / 0**, and tool_output from 0 / 2 to **4 / 0**.
3. **The `A5` case note made three stale claims:** the production-observation trigger for
   G-C (F46 already showed nothing recorded can meet it), *"only one finding can appear"*
   (two items give two findings, both in the winning class), and *"the precedence rule makes
   it the action one"* (F47 measured `CONTRADICTS-SELF` 5/5 on this shape).

## What changed

- `docs/FABRICATION_GATE_DESIGN.md`: new **F49** stating what is guaranteed (routing only:
  an ACTION label never produces an identity or tool_output finding or an advisory note, it
  survives O7's enforcement, and the structural rules ignore the reply) and what is not.
  Pointers added at F44.2 and in F48, with the original text left in place.
- F49 also refines F46's *"the losing finding is never written"*: the fault is lost entirely
  only when a side-effect tool ran and the label was ACTION; otherwise it is written in the
  wrong class. That lost case sits in a queryable population (side-effect tool ran, verdict
  `clean`), so **a human read of that population is recorded as a design option, not
  authorised.**
- `BUILT.md`: the isolation entry and the accepted-limitations entry, corrected in place with
  dated notes.
- `eval/fabrication_gate/cases.toml`: `A5`'s `note` rewritten. `note` is not fingerprinted,
  so the frozen set stays **`a1a6baa8…`**.

## Tested

- `tests/test_gate_eval.py`: 48 passed, including the fingerprint pin, so the note edit did
  not touch a frozen input.
- The 13 / 4 figures came from a throwaway script in the session scratchpad, not a
  committed test. Committing the check would mean committing a test that fails on purpose,
  and it is not needed: F49 records why the property does not hold.

## Known limitations and follow-up

- The model's actual label choice on mixed answers remains protected only by undocumented
  behaviour (F47). This change records that more accurately; it does not fix it.
- O23 part 2 (whether the trigger can judge the claim rather than match words) is reported
  separately before any prompt change.
