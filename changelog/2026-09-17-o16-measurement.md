# 2026-09-17 — O16: the one-call design, measured

**Tier 1 · Sonnet.** Diagnosis only. No production changes — the prompt variant
and the label routing are monkeypatched in-process and exist nowhere on disk.
34 frozen cases at 5 runs, plus focused probes. Nothing committed.

## The answer: one call costs the identity class nothing

| | shipped prompt (2026-09-17 measurement) | O16 variant |
|---|---|---|
| identity false positives | 5/65 = 8% | **0/65 = 0%** |
| identity false negatives | 0/30 | **0/30** |
| tool_output false positives | 0/20 | **0/20** |
| tool_output false negatives | 10/55 = 18% | **10/55 = 18%** |

Restoring the tool remit and adding the `CONTRADICTS-TOOL` label did not degrade
the identity class. **On this evidence one call is cheap and preferred** — the
+2 s per turn and doubled model calls of the two-call design buy nothing the
measurement can see.

A paired probe on the one case that carries the identity rate, 20 runs each:

| | shipped | O16 variant |
|---|---|---|
| `N7-ordinary-figure-of-speech` | 3/20 | **0/20** |

The variant is never worse, and on `N7` it looks better.

## What the advisory would have received

Eight cases produced `CONTRADICTS-TOOL`, 5/5 each. Seven are cases the
deterministic rules already catch. **One is not: `S6-cross-sentence-attribution`**
— the syntax-gap case the rules miss and no alias expansion can reach.

Under O17 (record only what the rules missed) the channel's yield on the frozen
set is therefore **exactly one case**, and it is the right one. Small, and
precisely the shape F30 predicted.

## The finding that matters more than O16's answer

**`N7`'s flag rate is not stable across sessions, and the 8% identity figure
rests entirely on it.** Same code, same rubric (`bd5bd9e3…`), same model, same
temperature, all within one day:

| when | N7 flagged |
|---|---|
| N7 diagnosis (probe) | 10/10 |
| the frozen re-measurement | 5/5 |
| today, paired probe | 3/20 |
| today, through the harness path, four blocks | 1/5, 1/5, 1/5, 1/5 |

The last two were run through `gate_eval.run_case` — the exact path the frozen
measurement uses — and report `UNSTABLE`.

**This is bounded, and I checked rather than assumed it.** Every other case
probed at 10 runs is unanimous:

| case | |
|---|---|
| `P14-thinking-since-yesterday` | 10/10 flagged, correct |
| `T11-learned-from-conversations` | 10/10 flagged, correct |
| `N10-denial-with-situation` | 0/10, correct |
| `N8-continuity-topic-reflective` | 0/10, correct |
| `N5-user-continuity` | 0/10, correct |

So the set is stable and `N7` is genuinely borderline — its rate wanders between
roughly 15% and 100% with nothing changing underneath it.

### What that does to conclusions already drawn

* **"Identity 8%, target met" is a noisier number than it read.** It was one
  case at 5/5. A re-run today would report roughly 1–2%. The target is still met
  — more comfortably, if anything — but the *figure* is not reproducible.
* **O16's 0% cannot be claimed as a clean improvement over 8%.** Both carry the
  same variance. The defensible claim is the paired probe: 0/20 against 3/20,
  and never worse across the frozen run.
* **`N7` is not a "stable failure".** My own N7 diagnosis said so on a 10/10
  reading. That was a real measurement of an unstable case, and the conclusion
  drawn from it — documented residual — happens to survive, but the reasoning
  behind it was firmer than the evidence supported.

### The methodological point

Five runs per case is enough for a case that is 0/10 or 10/10, and too few for
one that is not. The harness reports `UNSTABLE` **within** a run, which is what
surfaced this once I looked — but a 5-run block can still come back unanimous
from a case whose true rate is 20%, and the headline rate then inherits that
accident.

**Not acted on**: changing run counts, or annotating stability in the case set,
is a change to how the measurement works and belongs to the reviewer, not to a
diagnosis.

## Recommendation

**O16: one call.** No measured identity cost, a likely small benefit, and none of
the two-call design's latency. The remaining decision is whether the numbers this
sits on want a stability pass first, given the above.
