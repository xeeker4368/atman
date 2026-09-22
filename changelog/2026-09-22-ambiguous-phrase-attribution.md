# An ambiguous quoted phrase is unattributable, not first-match

2026-09-22 · finding #12 of the diagnostic pass · Shape A, authorised after a design review

## What was wrong

`pronouns.original_for` mapped a classifier-quoted phrase back to the sentence the
entity wrote by a casefolded substring scan **in document order, returning the first
hit**. Quoted phrases are often short — a date, a name, "since yesterday" — so a phrase
occurring in two sentences was attributed to whichever came first.

Two harms, and the second is the one that mattered.

**The citation could be wrong.** `messages.integrity_check` could name a sentence the
classifier was not talking about — which the function's own docstring forbids: *"a wrong
citation in the permanent record is worse than no citation."* The no-match case was
guarded; the ambiguous-match case was not.

**A real identity finding could be silently discarded.**
`gate._drop_tool_claim_findings` decides *"is this finding about a tool claim?"* **from
this attribution**. Reproduced:

```
answer            : "The page says the shop moved since yesterday.
                     I have been thinking about it since yesterday."
classifier phrase : "since yesterday"
original_for(…)   → "The page says the shop moved since yesterday."   ← the tool sentence
discarded as a tool claim? True
```

A genuine continuity fabrication came back **`CLEAN`**, visible only as a `logger.debug`
and a `discarded_tool_claims` counter nothing reviews.

## What changed

Collect every match, deduplicate, and return the sentence **only if exactly one distinct
sentence matched**. Otherwise `None`.

**This adds no new concept.** `evidence=None` already had a designed, documented policy in
`_drop_tool_claim_findings`: an unattributable finding is dropped only when *every*
sentence in the answer is a tool claim, and in a mixed answer it is **kept**, *"because
dropping it would lose real identity findings to protect against a possibility, and the
honest statement is that attribution failed."* Shape A routes the ambiguous case into that
rule instead of guessing.

**Identical sentences are deduplicated rather than refused.** If a phrase matches two
byte-identical sentences, citing either is equally correct and they cannot disagree about
whether they are a tool claim.

Shapes B (return all matches, let the caller decide per-match) and C (change the reply
grammar so the classifier names a sentence index) were reasoned through and **not**
authorised. Their triggers are recorded: B if production evidence shows citations being
lost often enough to matter; C if ambiguity turns out to be frequent *and* citations turn
out to justify a full re-measurement.

## The cost, named rather than left in the diff

**This is a real if narrow loosening of O7's guarantee**, and it is recorded here and in a
comment at the point where the consequence lands.

A finding the classifier mislabelled `CONTRADICTS-SELF` while actually meaning a tool
claim can now reach `findings` when its phrase was ambiguous and the answer is not wholly
tool claims. Under first-match attribution, that case would sometimes have been caught by
the enforcement. O7's claim is that the narrowing is *a property of the code rather than an
observed outcome*, and this widens the gap in that property by exactly the
ambiguous-phrase-plus-mislabel case.

It stays narrow because **O16's own label is the first line** — a `CONTRADICTS-TOOL`
verdict never reaches `findings` at all — and this enforcement is the backstop behind it.
The trade was taken because the alternative is discarding genuine identity findings, which
is the failure that branch already exists to refuse.

## Stale docstring corrected in the same function

`_drop_tool_claim_findings` said *"The boundary is drawn with `tool_claims`"*. The code
calls `tool_outcome_sentences`, which is **deliberately wider** — that width is the point
of revisions 6 and 7 (modality and negation, then back-reference, then bare invocation
claims). The docstring named a predicate the code does not call and denied the widening it
exists to describe. Fixed here because it sits in the lines this change touches.

## What was tested

Five new tests (`tests/test_pronouns.py`):

- a phrase in two sentences resolves to `None`, not first-match;
- an unambiguous phrase **still resolves** — the fix must not turn every attribution into
  `None`;
- identical sentences are not ambiguous;
- **end to end through the real enforcement**: the mixed answer's identity finding is now
  **kept** where it was dropped;
- and the other half of the documented policy is unchanged — with the whole answer being
  tool claims, an unattributable finding is still dropped.

**Proven to bite, both halves.** Restoring first-match fails 2 tests; removing the dedupe
fails 1.

**The measurement of record does not move, verified rather than inferred.** Frozen 34,
5 decorrelated passes, fingerprint `627834b1…`: identity FP **0/65**, identity FN **0/30**,
tool_output FP **0/20**, tool_output FN **10/55 = 18%**, overall FP 0% / FN 12%,
**32 PASS / 2 FAIL** — identical to the post-O16 baseline on every cell.

Full suite **1153 passed, 2 skipped**, `ruff` clean.

## Known limitations

- **Frequency is still unmeasured in production.** An instrumented run over the frozen set
  found **0 of 14** real classifier replies quoting an ambiguous phrase — which is why the
  rates did not move, and which is weak evidence about production, since this is the same
  set that missed findings #1 and #3.
- **An ambiguous finding now stores no citation.** Honest, and the documented policy, but a
  later reader loses the sentence. That is the signal Shape B's trigger watches for.
- O17's advisory path falls back to the coarse `not structural` whenever evidence is
  `None`, so slightly more advisory decisions take that path. Low blast radius — the
  advisory has no authority over a verdict — and it overlaps finding #15, already queued.
