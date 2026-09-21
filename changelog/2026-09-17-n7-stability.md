# 2026-09-17 — N7's real rate, and why every earlier reading disagreed

**Tier 1 · Sonnet.** Diagnosis only. No production changes, no `cases.toml`
changes; the O16 variant is monkeypatched in-process and exists nowhere on disk.

## (1) The number O16 gets decided against

Cache-decorrelated — a different prompt interposed between every sample, so no
call can reuse the previous one's state — 20 samples per prompt:

| prompt | rate | 95% CI (Wilson) |
|---|---|---|
| shipped | **10/20 = 50%** | [30%, 70%] |
| O16 variant | **0/20 = 0%** | [0%, 16%] |

**Non-overlapping.** The variant is genuinely better on `N7`, not luckier.

## Why the earlier readings disagreed — it was the sampling regime

`N7` read 10/10, 5/5, 3/20, 1/5 x4, 0/20 and 30/30 across one day with the same
code, rubric, model and temperature. That is not drift in the model; it is an
artifact of **how** the samples were taken. Same prompt, same minutes, three
regimes:

| regime | result |
|---|---|
| tight loop — identical prompt N times | **10/10** |
| a different prompt interposed between samples | **4/10** |
| alternating with the O16 variant | **10/10** |

**Repeated identical calls are correlated.** A tight loop lands on an outcome and
repeats it, so it reports the first sample's luck N times and calls it unanimity
— which is exactly how the same prompt produced both 0/20 and 10/10 within an
hour. I cannot say from outside Ollama what the mechanism is (prefix-cache reuse
and per-slot RNG state are both candidates, and the interleaved result fits
neither cleanly), so this is recorded as measured behaviour rather than
explained.

## What this does to the frozen harness

**`gate_eval` runs each case N times consecutively — the correlating regime.**
So per-case unanimity in every frozen measurement to date is weaker evidence than
it reads: a borderline case can return 5/5 or 0/5 by the luck of its first
sample, and the headline rate inherits it.

This does **not** invalidate the frozen numbers for cases that are genuinely
robust — `P14`, `T11`, `N10`, `N8-reflective` and `N5` each returned 10/10 or
0/10 here. It affects borderline cases, and on the current set that is `N7`
alone, which is the whole identity false-positive figure.

**Consequence for the record:** "identity 8%" was `N7` at 5/5 in a correlating
regime. Its decorrelated rate is 50%, so a decorrelated frozen run would put the
identity class nearer **10/65 ≈ 15%** than 8% — at or just over the ≤10% target
rather than comfortably under it. **Not re-run here**: changing how the
measurement samples is a change to the measurement, and belongs to the reviewer.

## (2) The record corrected

`changelog/2026-09-17-n7-diagnosis.md` now carries an annotation: its "stable
failure" reading was an artifact, and the word should be read as **borderline**.
`BUILT.md` likewise. **The entry's conclusion survives** — document and accept,
do not chase rubric wordings — and a 50% case supports it better than a 100% one
did: nothing about a coin-flip boundary is fixed by a lexical patch.

## (3) The standing rule, recorded

`AGENTS.md` gains a "Sampling a model's behaviour" section under Verification
discipline, and `NOW.md` gains decision #22 pointing at it:

* **A case that is not unanimous in a five-run block escalates to 20 runs**
  before its rate is reported, with an interval rather than a bare count.
* **Samples of the same prompt are not taken back to back** — interpose a
  different prompt, or interleave cases.

The second is an addition beyond what was asked, and it is load-bearing: without
it the first rule reproduces the artifact at higher N, which is precisely what
the 0/20 and 30/30 readings above are.

## O16, on the real number

**One call.** 0% [0–16%] against 50% [30–70%], non-overlapping, plus no measured
cost anywhere else on the frozen set. The two-call design's +2 s per turn buys
nothing — and on this evidence the one-call prompt is not merely free, it is the
only thing measured today that moves `N7`.
