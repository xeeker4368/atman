# B11 stage 3: a person may correct the entity, built and shipped with the switch OFF

Date: 2026-09-28 to 2026-09-29 · queue item B11, stage 3 · Tier 3 (correction
classifier, prompt, the measurement of record). Design record:
`docs/CORRECTION_DESIGN.md` CO16, where the full tables live.

**Shipped with `corrections.person_corrects_entity` OFF, and it stays off.** The rest of
stage 3 lands. The switch is held by an open, unstable defect (`PN9`), ruled at review
2026-09-29: not a residual, because its rate depends on sampling context in a way that is
not understood, and a residual is a characterised, bounded case.

## What changed

- **The third classifier call** (D1), person against the entity's statements, gated on
  entity candidates **and** behind the switch (D6), which defaults off.
- **CO4 by construction, twice** (D3, wording confirmed at review 2026-09-29 and landed
  verbatim in the `corrections.py` docstring and CO16): `ALLOWED_PAIRS`; `classify`
  raises before any model call for the entity against a person; `record()` re-reads both
  ends of a link and raises `CorrectionScopeError` for a CO4 or cross-user link; the case
  file refuses the pairing at load. Live even with the switch off, since the entity's own
  call and any future caller go through them.
- **D5:** when the person's and the entity's calls supersede the same entity message, only
  the person's link is written and the dropped one is logged at INFO.
- **D6(ii)/D7:** two NOT-corrections bullets in `_PROMPT`. **These act on all three calls,
  including the two that run with the switch off** (see the coupling note below).
- **D6(i)/D8:** annotations name who corrected (*"Later corrected by Lyle."*,
  *"Later contradicted by the assistant, with no replacement given."*), at render time,
  never in chunk text. The entity's `__entity__` sentinel never renders.
- **Harness:** fingerprinted `candidate_role`, the `person_corrects_entity` kind, 16 new
  cases (28 to 44, fingerprint `c7760e49…`). D9's ordering test, and the `2090 s` floor
  lines in `BUILT.md` and CO8 corrected to 2,045 s (the correction calls are not a term).
- **Case-file notes only** (fingerprint unchanged): `C7` and `N10` now say they no longer
  fail; `PN9` says its rate is not stable.
- **Two diagnosis scripts**, wired into nothing: `scripts/correction_diagnosis_stage3.py`
  (the 5-pass prompt screen) and `scripts/correction_diagnosis_pn9.py` /
  `scripts/correction_validate_disclaim.py` (the width probe and the full-set validation).

## Measured

**D11: 20 decorrelated passes, 44 cases, 880 samples.** 43 PASS, 1 FAIL (`N9`). False
links 20/560, missed 0/320, wrong target 0/320, wrong state 0/320. `person_corrects_entity`
kind: 0/180 false links, 0/120 missed. D6 gate cases (PN6, PN7, PN8): 0 in 60.
**`PN9` reads 0/20 in that table and that is not evidence it is fixed**: see next.

**PN9, one prompt, one pool, six contexts:** 0/20 (harness, file order) · 20/20 (after
`PN8`) · 20/20 (unrelated cases between) · 1/3 (fresh 3-pass) · 18/20 (probe) · **14/20**
(shuffled full set with the probe family, CI 48-85%). Width: 2 of 7 wordings link
(`PV7` 13/20, `PV5` 20/20; five others 0/20). The bullets raise it (exact string 1/20
without, 13-18/20 with); stage 2's scope clause makes it worse.

**The disclaim clause was tried and rejected.** It closes `PN9`/`PV5`/`PV7` (0/20 under two
shuffles) and keeps every must-link control at 20/20, **but makes `PC2` link 40/40** ("Can
you look again? I'm sure we talked about it."), against 0/20 shipped. It trades one false
link for another on the doubt-vs-assertion line CO8 says must hold. Its probe-regime `PC2`
figure (1/20) was overturned by the full-set regime.

**Latency, live:** the third call is 1.7-4.5 s (median about 2.9 s), about **+2.9 s per
turn**, inside the request after the answer is saved. Applies only if the switch is ever
turned on.

## COUPLING TO WATCH: the D6/D7 bullets and C7 / N10 have NO CURRENT THEORY

The D6/D7 bullets fix two frozen cases that have nothing to do with what they say:

- `C7-referential-contradiction`: missed 20/20 before, correct 20/20 after.
- `N10-records-scope-short-answer`: false-linked 20/20 before, no link 20/20 after.

**There is no theory of why.** Neither case contains self-description or opinion content.
The earlier accounts of them (referent selection; the claim's wording) were not shown
wrong, only no longer expressed. **If those two bullets are ever touched again, for any
unrelated reason, `C7` and `N10` must be re-checked, not assumed stable.** Removing them
to chase `PN9` was considered and rejected for exactly this reason: it would give up two
known-good results without understanding why they held. `N9` beside `N10` still links
20/20 on the same pool, so the scope family was not fixed by them either.

## Known limitations and what is owed

- **`PN9` is open and unstable** (CO16). The switch stays off until it is understood.
  A better clause is its own later task, and needs a full-set validation under at least
  two shuffles before any number for `PN9` is trusted.
- **The frozen harness's own regime hid this.** It samples in file order every pass, so a
  case's neighbours never change; `PN9` read 0/20 there. Changing how the harness samples
  is a change to the measurement, and is not made here. The shuffled runner
  (`correction_validate_disclaim.py`) is a diagnosis script, not the measurement of record.
- **`PN9`'s harness PASS is misleading** and the case notes say so. Read the case as
  should-not-link, unstable, not as passing.
- **Notes** must still be tested against this pattern and `PN9`'s family before it ships
  (`NOW.md` backlog, now `docs/BACKLOG.md`).
- The person-corrects-entity path has therefore never run in production.
- ~~`GUIDANCE.md`'s wording is the reconciliation decision #21 asked for at 3.3; not
  done here.~~ Done before commit, at review's request: see "Added at review" below.

## Added at review (2026-09-29), before commit

- **`GUIDANCE.md` reconciled with the shipped scope.** Its corrections paragraph said
  *"when a human corrects something the entity said"*, which no longer describes the
  scope under CO4 as amended. It now lists who may correct what, says the
  person-corrects-entity mechanism is built but switched off pending `PN9`, and states
  that disagreeing with the entity's account of itself or its opinions is not a
  correction, and that every shown correction names who made it. Documentation only.
- **`known_unstable` in the case file, surfaced in the report.**
  - `PN9` carries `known_unstable = true`.
  - `correction_eval.render()` prints its state as `PASS*` or `FAIL*`, with a line
    directly underneath: *"KNOWN UNSTABLE: this result is not evidence either way"*.
  - The case-states summary lists known-unstable cases, and the JSON report carries
    the flag.
  - The reason: a future green run would otherwise show `PN9` passing, with nothing on
    the screen to say it isn't real.
  - **Not fingerprinted**, beside `note`/`documented`: it qualifies how a result is read,
    not what is measured, so the fingerprint stays `c7760e49…`.
  - Tests pin the flag on `PN9`, the non-fingerprinting, the marker beside the result,
    and a malformed value refused at load. Removing the marker from `render()` fails
    the report test.

## Tests

Full suite and `ruff`: see `BUILT.md`. Every new guard was broken and its test seen to
fail; the list is in CO16.
