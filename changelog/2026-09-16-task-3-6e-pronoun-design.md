# 2026-09-16 — Pronoun resolution for defect (d): design (revision 4)

**Tier 3 · Opus (design).** Design only. **No implementation** — `gate.py`,
`classifier.py`, `architecture.md`, `cases.toml` and `turn.py` are all
untouched. Nothing committed. Stops here for review.

Modified: `docs/FABRICATION_GATE_DESIGN.md` (revision 4: F16–F22, O12–O15),
`scripts/gate_diagnosis_pronoun.py` (the throwaway harness gained a
`--referent` flag for the one measurement below), `BUILT.md`.

## One measurement was taken during the design pass, and it decided the shape

Flagged plainly because the brief said design only: **no production code was
written.** The existing throwaway diagnostic harness was re-run with one
constant changed, because the design's central question could not be argued
honestly without the answer.

The diagnosis rewrote second person to a **real name** — which is the only
reason the actor-boundary question exists. Nobody had tested a **neutral
placeholder**:

| second person rewritten to | false positives | false negatives |
|---|---|---|
| `"Lyle"` (the diagnosis) | 0/50 = 0% | 0/20 = 0% |
| `"the user"` (this pass) | **0/50 = 0%** | **0/20 = 0%** |

Identical, case for case, including both hazard cases and the quoted
fabrication that the raw text hides.

## What follows from it

**The gate never needs to know who is speaking.** No `turn.py` argument, no
`actor.name`, no new plumbing at all.

**And the decisive practical consequence: the frozen 33 exercise the fix
unchanged at 3.6d.** Under the named variant they could not — the case file has
no speaker field, so no rewrite would happen and `N5-user-continuity` and
`T-neg-user-improved` would fail exactly as they do today, making the
re-measurement unable to see the fix it is measuring.

## The two questions the review posed

**1. Speaker name at the no-actor boundary.** The design writes out the
argument *and* the counterargument, as asked, rather than recording the working
view as settled. The review's reasoning is correct as far as it goes: a name
used only to resolve a referent is not an input to judgment. The counterargument
is stronger than it looks — the *text the classifier sees* would differ by
speaker, and nothing constrains a model to judge "Lyle said…" and "Jodie said…"
identically, so the guarantee would rest on hoped-for model behaviour rather
than a property of the system. That is the same weakness decision #20 is
explicit about elsewhere. **F17 resolves it by making it moot**: a placeholder
is name-free, so the judged text is byte-identical whoever speaks, and the
property is structural.

A related recommendation: `test_the_gate_takes_no_actor` should stop being a
parameter-name blacklist and assert the property instead. A check that passes on
spelling while the property it protects weakens is worse than no check.

**2. Transformed text versus the judged record.** F18 specifies it as a table of
every surface, and the mapping back rather than just the intent:

* the rewrite runs sentence-wise, keeping `(original, rewritten)` pairs;
* a finding's quoted phrase is located among the rewritten sentences, and
  `Finding.evidence` is set to the **corresponding original sentence**;
* if nothing matches — a paraphrase, or a quote spanning a boundary — evidence
  is `None`. Never a best guess, never the rewritten text.
* **`messages.integrity_check` never stores rewritten text**, confirmed as
  asked. DEBUG logging may carry it for diagnosis; the record may not.
* A test asserts the property, not the intention: a token unique to the rewrite
  must appear nowhere in the serialised verdict.

## The three measured weaknesses

* **`you'd` guessed between *had* and *would*** — ships as a documented limit on
  O7's precedent. Closing it needs a lexicon, and a partial one is the kind of
  uncalibrated guess this project keeps declining. The measured cost is
  legibility, not accuracy: ungrammatical output changed no verdict in 14 cases.
  **Recommend the eval set gain a "you'd like" case** so the limit sits in the
  measurement of record (O15 — an addition to the frozen set, so the reviewer's
  call).
* **Generic "you"** — documented; measured harmless.
* **Quoted second person** — deliberately *not* settled (O14). Leaving quotations
  untouched looks safer, but D11 passes *because* the rewrite reaches inside the
  quotation, so closing this could reintroduce a false positive. One run of the
  existing harness decides it; guessing would be the wrong move.

## Two traps the design names

* **The structural half keeps reading the original.** Rewriting could turn
  *"you fetched the page"* into a sentence attributing a tool call to a person,
  changing what the deterministic rules see.
* **The situation block is never rewritten.** Its "you" is the **entity** —
  *"You were not running during that time."* Rewriting it would invert the one
  piece of turn-local ground truth into a claim about the person.

## Shared framework, or local

**Local, with the seam.** F13's own deadlock policy says the shared layer holds
what genuinely does not differ. 3.3 has a "who said this" problem but not *this*
one: it judges a pair of messages whose speakers are already known
structurally, so it can label them in its prompt without touching either
message's text — a different solution to a different problem, and rewriting
inside 3.3 would carry F18's provenance cost for no measured gain.
`program/integrity/pronouns.py` as a pure function, imported by `gate.py` alone;
3.3 imports it if it ever measures a need.

## Cost

No new setting, no new model call, no schema change, no change to the frozen 33.
Implementation is one module, one call path in `semantic_findings()`, the
mapping-back step, and four tests.

## Open questions

**O12** placeholder wording (`"the user"` measured; `"the person"` may fit the
rubric's own vocabulary — one run decides, and I did not guess) · **O13** both
household members become "the user", which is the property F17 wants but means
one answer addressing Jodie while quoting Lyle is indistinguishable; unmeasured
and not in the frozen set · **O14** quote-preserving variant · **O15** the
`you'd like` eval case.

## Stop

Tier 3. Nothing is implemented until revision 4 is approved, and 3.6d stays on
hold behind it.
