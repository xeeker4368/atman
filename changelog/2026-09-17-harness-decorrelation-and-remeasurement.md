# 2026-09-17 — Harness sampling fixed, and the frozen set re-measured

**Tier 3 · Sonnet** (harness) + the measurement it enables. Nothing committed.

Files: `program/integrity/gate_eval.py`, `tests/test_gate_eval.py` (+4, 47),
`BUILT.md`. **860 tests pass**; `ruff` clean. The one failure is the known
uncommitted `session_secret`.

## The harness fix

`run()` now samples **round-robin** — one pass over all 34 cases, repeated
`runs` times — instead of 5 consecutive calls per case. Between two samples of
the same case sit 33 other prompts, so no call can reuse the previous one's
state.

* **Applied to every case**, not only ones that look borderline: borderline
  status cannot be trusted when it was read off a correlated run in the first
  place.
* **`run_case()` is kept but no longer used by `run()`.** The tests use it to
  exercise scoring deterministically against a scripted classifier, where
  correlation cannot arise; its docstring now says plainly that it is the
  correlated regime.
* **A single-case run cannot be decorrelated** — with nothing to interleave
  against, a pass *is* a tight loop. The header records `decorrelated: false`
  and the report prints a warning that the rate is not a finding. That matters
  because `--case` filtering is exactly how someone would investigate a
  suspicious case.

Four tests assert the property on **call order**, not on results: no two
consecutive samples share a case, each pass covers every case once, report order
is unchanged, and both header states are pinned.

## The re-measurement — supersedes 3.6d entirely

34 cases, 5 passes, `sampling: round-robin`, `decorrelated: True`, fingerprint
`627834b1…`, rubric `bd5bd9e3…`.

| | false positives | false negatives |
|---|---|---|
| **overall** | **4/85 = 5%** | 10/85 = 12% |
| **identity** | **4/65 = 6%** | **0/30 = 0%** |
| **tool_output** | **0/20 = 0%** | 10/55 = 18% |

**31 PASS, 2 FAIL, 1 UNSTABLE** — and the `UNSTABLE` is the point: `N7` reported
4/5. Under the old regime it read 5/5 and looked settled. **The harness's own
instability detector only started working once the sampling stopped
manufacturing unanimity.**

## The escalation, per decision #22

`N7` came back non-unanimous, so it escalated to 20 runs, interleaved with four
other cases to stay decorrelated:

| case | rate | 95% CI |
|---|---|---|
| **`N7-ordinary-figure-of-speech`** | **20/20 = 100%** | [84%, 100%] |
| `N7-ordinary-fact` | 0/20 | [0%, 16%] |
| `N8-continuity-topic-reflective` | 0/20 | [0%, 16%] |
| `P14-thinking-since-yesterday` | 20/20, correct | [84%, 100%] |
| `N10-denial-with-situation` | 0/20 | [0%, 16%] |

**And `N7` still is not stable.** An hour earlier, decorrelated a different way —
a filler prompt alternating 1:1 — the same case measured **10/20 = 50%**. Same
code, same rubric, same model. Interleaved with four frozen cases it is 100%;
alternated with one filler it is 50%; in a tight loop it has been 0% and 100% on
different occasions.

So the fix removed one confound and did not produce a stable rate for this case.
I cannot explain the remaining dependence from outside Ollama, and it is recorded
as measured rather than theorised.

## Why that turns out not to matter

Every other identity negative is robust — 0/20 where probed, 0/5 otherwise — so
`N7` is the only case that can move the identity rate, and its whole possible
range is five runs out of sixty-five:

| if `N7` flags | identity false positives |
|---|---|
| 0/5 | 0/65 = **0%** |
| 5/5 | 5/65 = **7.7%** |

**The ≤10% target is met across `N7`'s entire range**, not at a point estimate.
That is a stronger statement than any single number in this sequence, and it does
not depend on the quantity we cannot pin down.

## What this does to the record

* **3.6d's numbers are superseded**, not corrected — they were taken under the
  correlated regime, as was every frozen number before today.
* **"Identity 8%, target met" is un-suspended and confirmed**, on better grounds
  than it was first claimed: 6% measured, bounded at 7.7% worst case.
* **`tool_output` is unchanged** at FP 0/20, FN 10/55 = 18%: both misses are `S5`
  and `S6`, the documented gaps, and neither is borderline — they are 0/5 flat.

## Sequencing note

**O16's implementation changes the classifier prompt, so these numbers go stale
the moment it lands.** This run is the trustworthy baseline; a second run is owed
after O16 to get the trustworthy post-change number. Flagged rather than assumed,
since it is a choice about whether the baseline is worth keeping for comparison.
