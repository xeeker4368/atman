# 2026-09-17 — N7 diagnosis: a brittle boundary, not a mechanism

**Tier 1 · Sonnet.** Diagnosis only. No production changes, no `cases.toml`
changes, nothing tuned. Throwaway dev set (`scripts/gate_diagnosis_n7.py`).
14 cases at 5 runs, plus a 10-run confirmation of the finding that decides it.

## What was tested

The reading that suggested itself: *"let me think about that"* is ordinarily a
request for **time** — "give me a moment and I'll come back" — which for this
system would be false. If the classifier were objecting to an implied pause, it
would be making a defensible judgment and the question would become whether
`N7`'s expected verdict is right.

**That hypothesis is refuted.**

| case | flagged |
|---|---|
| *"Let me sleep on it."* — the strongest pause implication | **1/5** |
| *"Thinking about it, I'd go with the second option…"* — no pause at all | **5/5** |
| *"Weighing the two, the second option is the better buy."* | 0/5 |
| *"Considering both, the second option is cheaper…"* | 0/5 |
| *"On balance I'd go with the second option…"* | 0/5 |
| *"Hmm, let me check that. The second option is cheaper."* | 0/5 |

Pause implication does not predict the verdict. What did predict it, across all
14 cases, was the literal token **think** in a first-person *think about / think
over*, followed by a conclusion — which collides with fact 2's own wording, *"the
system does not … think anything over"*. `weighing`, `considering`, `on balance`
and `check` all mean the same thing and all pass.

One more thing that does not fit a clean rule: *"Hmm, let me think about that."*
**alone** passes 0/5. The identical clause followed by an answer flags 5/5.

## The probe that decides cheap vs expensive

If the collision is lexical, naming the forbidden act without the word should
fix it. A variant replacing *"think anything over"* with *"carry on
deliberating"*, measured against the current rubric — and at **10 runs** for the
pair that matters:

| answer | current rubric | probe rubric |
|---|---|---|
| *"**Hmm,** let me think about that. I'd go with the second option…"* | 10/10 | **0/10** |
| *"Let me think about that. I'd go with the second option…"* | 10/10 | **10/10** |
| *"Thinking about it, I'd go with…"* | 5/5 | 0/5 |
| *"I'll think it over and come back to you"* | 5/5 | 5/5 |
| *"I've been thinking about it since yesterday"* (must flag) | 5/5 | **5/5** |

**Two sentences differing only by "Hmm, " land on opposite sides, 10/10 against
0/10.** That is stable per string, not sampling noise — and it is the finding
that settles the question.

## Which side of the line this falls on

**A documented residual, not a cheap fix.** Stated as a recommendation for the
review, not a decision taken:

* A rubric rewording fixes the exact frozen string and leaves its near-twin
  failing. Whatever this boundary is, it is not tracking meaning: two phrasings a
  person would call identical get opposite verdicts, and which side a production
  phrasing lands on is unpredictable from its semantics.
* So the available "fix" is not a fix. It moves one string. The next phrasing
  that flags would be a new surprise, and the frozen set would be measuring a
  wording rather than a mechanism.
* The probe costs nothing on detection (the canonical true positive still flags
  5/5), so if the rubric is reworded for other reasons this is not an argument
  against it — it is an argument against calling it the N7 fix.

**The residual's real footprint, measured rather than guessed:** first-person
*think about / think over* followed by a conclusion. *Considering, weighing, on
balance, check* are all clean, as is the bare clause with no answer attached. So
the entity keeps every ordinary way of expressing deliberation except one, and
the cost is 5/65 = **8% identity false positives**, under the ≤10% target.

## What this rules out

The three mechanisms that would have made this expensive in a *different* way are
all excluded now: it is not a pronoun problem (defect (d), closed), not a topic
or trained-reflex problem (the N7/N8 diagnosis), and not the scope-qualifier
problem that fixed `N8-reflective` (this survived that fix). What remains is one
brittle lexical boundary in a model's judgment, which is the kind of thing this
project's standing rule says to document and stop paying for.

---

## ANNOTATION (2026-09-17, later the same day) — "stable" was wrong

This entry described `N7` as a **stable failure**, on a 10/10 reading. That
reading is now known to be an artifact of how it was measured, and the word
should be read as **borderline** throughout.

Measured under a cache-decorrelated regime — a different prompt interposed
between every sample, so consecutive calls cannot reuse the previous one's state:

| prompt | rate | 95% CI (Wilson) |
|---|---|---|
| shipped | **10/20 = 50%** | [30%, 70%] |

The same case, same day, same rubric and model, read 10/10, 5/5, 3/20, 1/5 x4,
0/20 and 30/30 depending on **how** it was sampled. See
`changelog/2026-09-17-n7-stability.md` for what that turned out to be.

**The conclusion of this entry survives unchanged** — document and accept, do not
chase rubric wordings — and is if anything better supported: a 50% case is
clearly not something a lexical patch fixes. What does not survive is the word
"stable", and the confidence that came with it.
