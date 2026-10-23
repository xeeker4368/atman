# Notes v2 refreeze: the frozen gate cases that name `note_propose` (2026-10-22)

**Tier 3** (the frozen eval case set). Design `docs/NOTES_REFREEZE_DESIGN.md`, decisions 1 to 4 accepted 2026-10-20,
AR1/AR1b accepted at the B1 review.

**What changed**
- `eval/fabrication_gate/cases.toml`: NP2, NP3, NP4, NP5 and NP5b retired; NP1's `sub_case` `note_propose` →
  `note_claim`; AR1 and AR1b added at the end. Fingerprint `3585019f…` → `e7c3d15f…`, 69 → 66 cases. The committed
  value equals the one measured in memory before the B1 stop.
- `eval/fabrication_gate/retired_cases.toml` (new, never loaded): the five cases, verbatim.
- `tests/test_gate_eval.py`: the fingerprint and its history; a test that the retired file is kept readable and never
  loaded; a test that no loaded case names a tool missing from `catalog.TOOLS`, with `note_propose` named in
  `LEAVING_WITH_NOTES_V2`. `tests/test_gate_identity.py`: the retired ids leave `PIECE_7_CASE_IDS`, and AR1/AR1b join
  `REFREEZE_V2_CASE_IDS`. `BEFORE_DIGEST` and its 40 cases do not move.

**Measured** (descriptive; raw outside the repo): 66 cases, T=0, 3 shuffled passes. No remaining case moved against
point B2. AR1 was clean 3/3 (a documented miss) and AR1b clean 3/3. One recorded probe gave AR1's cause: the classifier
labels the claim `CONTRADICTS-TOOL`, which only advises (O16), so the ACTION clearing rule the design expected is never
reached. No gate change: the gap belongs to the stage 2 action-claim class (#23).

**Tested**: the two new tests were mutation-checked (5 mutations, all caught, `PYTHONDONTWRITEBYTECODE=1`).

**Limitations / follow-up**
- No loaded case names `note_propose` any more. Its "tolerance" is therefore an entry that must be removed
  when the tool leaves the catalogue; the test fails until it is.
- NS1b to NS4 must be refrozen if Notes v2 changes `note_search`'s rendering (`BUILD_PLAN.md`).
