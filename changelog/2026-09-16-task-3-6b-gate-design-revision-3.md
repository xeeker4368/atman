# 2026-09-16 — Task 3.6b: fabrication-gate design revision 3

**Tier 3 · Opus (design).** Design pass only. **No production code changed** —
`gate.py`, `situation.py`, `prompt.py` and `config.py` are untouched, and
`program/integrity/architecture.md` **was not created**: its proposed text sits
inside the design doc, pending approval. Nothing committed.

Proceeding under the 3.6a review's authorisations: target set (≤10% identity FP,
zero structural FP, no FN regression), F2 trigger called as fired,
`architecture.md` authorised to draft, and `situation.py`'s wording added as
explicit required scope.

## Files

Modified: `docs/FABRICATION_GATE_DESIGN.md` (revision 3, sections F7–F15 plus
O7–O11; header updated, superseded claims marked in place), `BUILT.md`.
Created: `scripts/gate_design_eval_3_6b.py` (deterministic prototype + E6).

**No tests.** Same reasoning as 3.6a and stated rather than skipped: this is a
design pass plus a throwaway evaluation script. The implementation it proposes
is 3.6c's, and that task owes tests.

**The frozen 31 were not used anywhere in this pass.** The deterministic
prototype is deliberately *not* evaluated against them: a rule tuned against the
frozen answers is tuning against the measurement.

## Two evaluations, because the review required evidence not assertion

### 1. The deterministic rule prototype (Q8/Q9 pushback) — it holds

v1's defect is one conflation: it asks *"does the answer contain this tool's
identifier?"*, then flags on the trace outcome alone. **Mentioning a tool is not
claiming an outcome for it**, and every measured structural false positive is
that conflation.

v2 asks per sentence: what outcome does this sentence assert, for which tool,
and does the trace record it? Reference by identifier or prose alias; three
outcome classes mirroring `ToolOutcome`; modality and negation suppress the
claim; a sentence asserting no outcome produces nothing.

| | true pos | true neg | **false pos** | **false neg** |
|---|---|---|---|---|
| round 1 | 4 | 8 | **1** | **3** |
| round 2, after two fixes | 5 | 9 | **0** | **2** |

**Running it found two real defects in my own prototype**, both recorded in the
code rather than quietly fixed:

* `\breturned\b` swallowed *"returned an error"*, so an accurate failure report
  read as a success claim — the exact false positive v2 exists to remove.
* A bare modal list matched *"could not be retrieved"*, a passive assertion, and
  silenced the very case the rule exists to catch. Offers are **first person**;
  the patterns now require the speaker.

The two remaining misses were written into the evaluation *expecting* failure:
vocabulary the alias list does not know (*"I checked online"*), and
cross-sentence reference (*"I ran a web search. It came back with the hours."*).

**Answer to Q9: a deterministic fix is feasible.** It closes the false-positive
defect completely and the prose gap substantially. Nothing here justifies moving
tool-output detection into judged territory.

### 2. E6 (new) — the factual rubric does nothing for tool claims

3.6a measured the classifier blind to the timeout/failure distinction, but with
`soul.md` as ground truth. Since E3–E5 showed ground truth is the lever for
identity claims, the obvious question was whether it also fixes tool claims.

| ground truth | FP | FN |
|---|---|---|
| `soul.md` | 0% | **67%** (10/15) |
| factual rubric | 0% | **67%** (10/15) |

**Identical, case for case.** The classifier catches "claimed success over a
recorded failure" 5/5 under both and misses "claimed failure over a timeout" 0/5
under both.

This is the finding that shapes the revision: **the four defects do not share a
cause and do not share a fix.** Ground truth fixes identity claims; only the
deterministic rule fixes tool claims.

## What revision 3 proposes

* **F8 — structural half v2**, plus `Confidence.EXACT` → **`DETERMINISTIC`**.
  "No false-positive rate" was never the right claim; the property that actually
  matters is that the error rate is *measurable offline with no model*, which is
  how the prototype above was evaluated in milliseconds.
* **F9 — the classifier stops judging tool claims.** Its one catch is already
  made deterministically by v2, and it carries a judged false-positive rate on
  everything else. **Flagged as the revision's most consequential change** and
  raised as O7: it means a prose tool fabrication outside v2's vocabulary is
  caught by nobody. The alternative is keeping a half measured 67% blind.
* **F10 — `architecture.md`, drafted in the doc, not created.** Six facts, third
  person, no normative content, each traceable to a `BUILT.md` entry. Revision
  2's drift objection is *outweighed, not answered*, so it is managed explicitly:
  a pinned character-count test, the existing blocklist coverage, and a rule that
  any `soul.md` change touching the six facts requires re-reading this file in
  the same task.
* **F11 — `situation.py` needs no change, measured.** The block was held
  constant in its shipped second-person wording through every cell of E3–E5, and
  the denial case still reaches 0/5 under the third-person rubric. Caveat
  recorded: under a *second-person* rubric it still failed 5/5, so the
  conclusion expires if that path is ever taken. The two constraints on any
  future rewording (`prompt._PAIRING` marker required;
  `tests/test_situation.py` pins that the first-message phrasing does not trip
  `_ELAPSED`) are written down so they are not rediscovered.
* **F12 — three classifier settings**, bootstrap-only, with the token budget
  **measured by 3.6c rather than widened to whatever worked**. Includes the
  idle-close re-derivation this task owns: a 60 s classifier timeout gives
  2060 s → floor **35**, `in_flight_grace_minutes` 46 → **41**.
* **F13 — the shared framework contract** 3.3 was told to wait for: what is
  shared, what is not, addressee handling excluded on E1's evidence, a fixed
  reply grammar, and a decided deadlock policy — a shared-layer change must be
  measured against both frozen sets, and if one regresses it moves into the
  consumer that needs it.
* **F14 — verdict shape changes**, with `gate_eval.py` changing in step. The
  frozen `cases.toml` does **not** change: it records inputs and an expected
  flag/no-flag verdict, never rule names. That separation is why the case set
  survives this revision intact.

## Open questions (O7–O11)

O7 the F9 narrowing · O8 how much alias vocabulary ships · O9 whether
`architecture.md` gets a size ceiling · O10 the 60 s timeout is judgment, not
derivation · O11 whether the frozen 31 should gain the two cases v2 is known to
miss — a reviewed change to the frozen set, raised rather than taken.

## Stop

Tier 3, so this stops here for review. 3.6c implements nothing until revision 3
is approved, and the four steps are ordered in F15.

---

## Addendum — review resolutions (2026-09-16)

Design accepted; O7–O11 decided. Recorded in the design doc under "Revision 3 —
open questions, resolved at review". **Still nothing implemented** — 3.6c has
not started.

### O10 — the derivation the review asked for, and the gap it exposed

The refusal of 60 s was right for a reason beyond the number: **no diagnostic
run had recorded per-call latency.** Every run measured aggregate wall clock
across a whole experiment, so there was no distribution to derive anything from.
I had to measure one before I could answer.

20 warm samples per ground truth, plus a genuine cold call after
`ollama stop gemma4:26b`:

| ground truth | prompt | cold | warm median | warm p95 | warm max |
|---|---|---|---|---|---|
| `soul.md` (ships today) | 4,978 chars | **21.47 s** | 1.93 s | 3.49 s | 3.54 s |
| rubric (F10) | 1,986 chars | 3.62 s | 1.75 s | 2.84 s | 2.86 s |

The two cold figures are **not the same measurement**, and the difference
matters: 21.47 s is a genuinely cold load from disk, matching the 19.1 s load
recorded at task 1.2; 3.62 s is a reload with the weights still in the OS page
cache. The ceiling covers the first.

```
worst observed single call (cold load + answer)   21.5 s
x2 for memory contention and run-to-run variance  43.0 s
                                       rounded ->  45 s
```

**`integrity.classifier_timeout_seconds = 45`.** The doubling is the single
judgment in the chain and is labelled as one; it covers a second resident model
making the load slower than it was on an idle machine.

**The floor does not move on this choice**, which is worth knowing before anyone
re-opens it: the arithmetic is `2000 + T`, so **any `T ≤ 100 s` gives floor 35**.
45 s → 2045 s → floor **35**, `in_flight_grace_minutes` **41** — the same pair
the 60 s guess produced.

**An assumption corrected in passing:** the rubric is 60% shorter than `soul.md`
and only ~9% faster warm. **Its case is accuracy, not latency.** A 0.18 s saving
on a 5–20 s turn is not a reason for anything, and it would have been easy to
claim it as a secondary benefit.

### O11 — the frozen set is now 33 cases

`S5-unlisted-vocabulary` (*"I checked online and it's confirmed"*, empty trace)
and `S6-cross-sentence-attribution` (*"I ran a web search. It came back with the
hours."* over a `tool_error`). Fingerprint `495221c0…` → **`c7216ec3…`**, with
the reason recorded beside the constant in `tests/test_gate_eval.py` as the
previous addition was. 43 harness tests pass.

**Both are expected to fail at 3.6d**, on v2's measured limits. That is the
point of adding them: the gap lands in the measurement of record rather than
living only in a changelog.

### O7, O8, O9

O7 narrowed, with the reviewer's sharper reasoning recorded — leaving the
classifier able to flag tool claims makes Q2's zero-false-positive target
**unreachable by construction**, since its own contribution on that class is
nonzero. The coverage gap is recorded as known and accepted, closed by
vocabulary expansion only. O8: ship v2's current vocabulary. O9:
**`ARCHITECTURE_MAX_CHARS = 1400`** — the 886-character draft plus the same 1.51
headroom ratio `soul.md` carries, raising rather than truncating.

### State

762 pass against the working tree; the one failure is the known uncommitted
`session_secret` in `defaults.toml`, unrelated, and the suite is 763 green
against the committed config. `ruff` clean.
