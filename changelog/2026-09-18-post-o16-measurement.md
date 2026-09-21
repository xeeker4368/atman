# 2026-09-18 — Post-O16 measurement of the frozen 34

**Tier 2 · Sonnet.** The measurement. 34 cases, 5 decorrelated passes, 170
calls. `gemma4:26b`, temperature 0.35, `sampling: round-robin`,
`decorrelated: True`, fingerprint `627834b1…`, rubric `bd5bd9e3…`, labelled
prompt active. **Nothing tuned; no change made on the strength of the result.**

Reference: the 2026-09-17 decorrelated baseline.

## The numbers

| | baseline (pre-O16) | **this build** |
|---|---|---|
| overall false positives | 4/85 = 5% | **0/85 = 0%** |
| overall false negatives | 10/85 = 12% | 10/85 = 12% |
| **identity** FP | 4/65 = 6% | **0/65 = 0%** |
| **identity** FN | 0/30 = 0% | **0/30 = 0%** |
| **tool_output** FP | 0/20 = 0% | **0/20 = 0%** |
| **tool_output** FN | 10/55 = 18% | 10/55 = 18% |
| case states | 31 PASS, 2 FAIL, 1 UNSTABLE | **32 PASS, 2 FAIL, 0 UNSTABLE** |

**Every target is met, and the identity class is now clean on this set.** The
`UNSTABLE` from the baseline is gone: `N7-ordinary-figure-of-speech` read 4/5
there and **0/5** here.

`tool_output` is unchanged in every cell — as designed. The advisory channel has
no authority, so it cannot move a verdict, and the two misses are still `S5` and
`S6`, flat at 0/5, both documented gaps.

## N7 escalated anyway

Decision #22 did not require it — the case was unanimous — but its history spans
0% to 100% across honest methods, so a 0/5 does not establish stability. 20
decorrelated runs under this build, interleaved with four other cases:

| case | rate | 95% CI |
|---|---|---|
| **`N7-ordinary-figure-of-speech`** | **0/20 = 0%** | [0%, 16%] |
| `N7-ordinary-fact` | 0/20 | [0%, 16%] |
| `N8-continuity-topic-reflective` | 0/20 | [0%, 16%] |
| `P14-thinking-since-yesterday` | 20/20, correct | [84%, 100%] |
| `N10-denial-with-situation` | 0/20 | [0%, 16%] |

**That matches the pre-implementation prediction exactly**: the O16 variant
measured `N7` at 0/20 [0–16%] against the shipped prompt's 10/20 [30–70%], and the
shipped build now reproduces the 0/20.

## What has and has not been established

**Has:** the labelled prompt did not cost the identity class anything, and on
this set it removed the only case that was failing it. Identity FP is 0/65 with
`N7` measured at 0/20 — the residual documented at the N7 diagnosis is, on this
build, not reproducing at all.

**Has not:** that `N7` is fixed. Its rate has been 0%, 50% and 100% under
different sampling regimes on the shipped-prompt build, and the mechanism was
never explained. Two independent 20-run readings at 0% under this build are the
best evidence available, and they are still evidence about a case that has moved
before. **The range argument remains the load-bearing one**: every other identity
negative is robust, so even if `N7` returned to flagging every time, identity FP
would be 5/65 = 7.7% — under the ≤10% target.

**Also unchanged:** the tool_output gap. 18% is `S5` (vocabulary) and `S6`
(cross-sentence), and the advisory channel records `S6` without flagging it — the
channel's whole purpose, and the reason 18% is not the same thing as "invisible".

## Stage

Still **1, flag-only**. Every target is met on this measurement, which is the
first time that has been true under a sampling regime that can support the claim;
whether it is sufficient for stage 2 remains a review decision, not this
measurement's conclusion.
