# 2026-11-24: note pass v2.4, measured and not merged (docs only)

**Result** (v2.3 = main `09c498f`, v2.4 = `cc/note-pass-v2-4` `e6c5768`; they differ only in
`program/tools/note_texts.py`; run 6's states rebuilt on copies, attempts out of five; descriptive,
one reader):

| row | v2.3 | v2.4 |
|---|---|---|
| C1 Tom ADD proposed / text keeps "sells" | 5/5 / 5/5 | 5/5 / **0/5** |
| C1 shed ADD proposed / with progress (walls, roof) / with permit | 5/5 / 5/5 / 0/5 | 5/5 / 5/5 / 0/5 |
| C5 pottery ADD proposed | 5/5 | 5/5 |
| C7 Tom revise proposed / keeps "sells occasionally" / keeps "setting up shop" | 5/5 / 0/5 / 5/5 | 5/5 / 0/5 / 5/5 |
| C8 shed revise proposed / with schedule / keeps the permit | 5/5 / 5/5 / 0/5 | 5/5 / 5/5 / 0/5 |
| expected none (C2, C3, C4, C6, C9, C10), proposals | 0 | 0 |

No over-suppression, and no improvement on the permit and "sells occasionally" drops. The only change
is Tom's "sells" at C1 (5/5 to 0/5, "mostly as a craft"), which reads as a fidelity change.

**Determinism.** The pass runs at `classifier_temperature` 0.0 with no seed set. Every repeated
attempt on the same input gave byte-identical output, so one pass per case is a complete measurement.

**Input check.** The pass sees each note's full text, not just the line, for N, P and D entries
(`program/memory/note_pass.py`). Nothing in the parser or the pass requires a REVISE to keep anything
from the old text; Lyle's approval is the only control.

**Decision.** v2.4 is not merged. `cc/note-pass-v2-4` at `e6c5768` is kept unmerged as evidence for
the measurement report. Decision #40 is not on main, so `CLAUDE.md`'s decision count stays 39.

**Correction.** The run 5 and run 6 reviews called one pass per conversation a weak measurement. That
was wrong: the pass is deterministic per input. The archives under `runs/` are sealed and read-only
and are not edited; the correction lives here only.

**Open follow-ups.**
- `scripts.note review`: show a diff of the old and new text for a revise.
- The header piece, including the stale "word for word" comment above `PASS_INSTRUCTION`.
- The speed investigation.
