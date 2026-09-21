# 2026-09-19 — Task 3.5: retrieval resolves `supersedes` and annotates rather than suppressing

**Tier 3 · stage 3 of 3.** Implements `docs/RETRIEVAL_SUPERSESSION_DESIGN.md`
R1–R3 and R5–R12; R4 landed at stage 1. Nothing committed. **Stops here for
review.**

## Files

Created: `program/memory/supersession.py`, `tests/test_supersession.py` (25).
Modified: `program/memory/db.py` (two batched readers),
`program/memory/retrieval.py` (attachment after fusion),
`program/engine/prompt.py` (rendering + the budget),
`docs/RETRIEVAL_SUPERSESSION_DESIGN.md`, `BUILT.md`.

**1,005 tests pass** (was 983), `ruff` clean. One standing failure, unrelated: the
`session_secret` one.

## What landed

**Resolution happens after fusion and cannot reach ranking.** `supersession.py`
takes chunk ids and returns data — it does not import `retrieval`, so there is no
path by which a link could influence a score. `retrieval.search()` maps the result
onto `RetrievedChunk.supersessions`, exactly the shape D7 gave split siblings.
**A test on D6's own pattern proves it**: the same query before and after a link is
written returns byte-identical chunk order, RRF scores and BM25 ranks, with the
annotation attached in the second run.

**Annotate, never suppress** (CO7). A test asserts the corrected claim still
surfaces in full, that `chunks.text` is unchanged — so nothing reaches FTS5 or the
embedding — and that the annotation is attached beside it.

**Two batched readers, not N+1.** `db.get_supersedes_for_chunks()` does the
chunk→message timestamp-window join (C3/CO2) for the whole result set in one query;
per-chunk-then-per-message would be up to `top_k (10) × max_turns (8)` queries
inside a turn on a database whose lock contention is a recorded issue. A test counts
the calls and asserts the ordinary no-corrections case costs exactly one.

**Chains resolve to the tip, with two independent stops** (R3). `A ← B ← C` surfaces
C, because surfacing B would annotate a record with a correction that has itself been
corrected. A **visited set per origin** stops a loop; a **depth bound** stops a
pathologically long chain from spending the turn. They fail differently and neither
implies the other — proven by deleting the visited set, at which point the depth
bound caught the loop instead and only the cycle *count* assertion failed.

**The chain's state comes from the last link.** For
`A ←(replaced) B ←(contradicted) C`, B supplied a value and C withdrew it, so there
is no current value — which is the reader's actual question. The first link's state
would answer one nobody asked.

**A branch renders every tip.** `UNIQUE` is on the pair, not the superseded side, so
two messages can supersede one claim. Both appear; newest-wins would hide a
disagreement between two things the same person said.

**Degrades rather than raising, and the degraded state is not treated as benign.** A
failed lookup returns results unannotated with `resolved=False` and a reason on the
report — the gate's *"unavailable is never clean"* shape, because unannotated
results present a corrected claim as current.

## RO4, answered: shorten before dropping

None of the three candidates I offered was right alone. The answer is an **ordering**:
a dropped annotation *is* the failure this mechanism prevents, so the correction's
quote goes first and the annotation's existence goes last.

* `SUPERSEDING_QUOTE_BUDGET_CHARS = 2000` — spent on correction quotes in rank order;
  once gone, annotations still render with their locator and their state.
* `SUPERSESSION_MAX_ANNOTATIONS = 12`, `MAX_PER_CHUNK = 3` — and whatever is
  withheld is **counted** in a closing line rather than vanishing.

Worst case ≈ **4,600 characters** by construction, against
`agent.max_tool_result_chars`'s 4,000 as the nearest precedent. **Asserted on a
pathological input** (10 chunks × 8 corrections × 900-character messages), not left
as arithmetic in a comment.

**The honest cost:** the global cap does let a low-ranked record's annotation become
an aggregate count, so the model is told corrections apply to N records without being
told which. That is the only place annotation presence depends on rank, and it is a
real degradation.

## Verified live, both states, real model

Real store, real embeddings, one prompt carrying both:

* **`replaced`** — *"What day is my dentist appointment?"* → **"Your dentist
  appointment is on Wednesday."** The record still says Tuesday; the annotation
  carried the correction.
* **`contradicted`** — *"What should the boiler pressure be?"* → **"Lyle previously
  mentioned that the boiler pressure should sit around 1.4 bar, but then stated,
  'That's not right.' No replacement value was provided in the records."**

That second answer is what CO8's two-state requirement was for, and it is the first
evidence the distinction survives to the model rather than only to the renderer.

The rationale is **not** rendered — classifier output about a judgment is not
something either party said — and a test writes a sentinel rationale and asserts it
appears nowhere in the prompt.

## Three open questions answered by my own stated lean, not by review

Flagged so silence does not settle them. Each is reversible.

- **RO3 — a failed lookup is recorded but not surfaced to the model.** Telling it
  *"some records could not be checked"* would invite hedging on every record in a
  turn where one lookup failed — the opposite of what I argued for `memory_search`'s
  downed leg. The asymmetry deserves your judgment rather than my consistency.
- **RO5 — branches render every tip** (above).
- **RO6 — the locator quote truncates at 120 characters**, not at the first sentence.
  A truncated quote may not uniquely identify the line it points at.

## Guards proven by breaking them

| broken | what failed |
|---|---|
| the visited set | the cycle test (the depth bound caught the loop) |
| chain state from the first link | the `replaced → contradicted` chain test |
| drop annotations instead of shortening | the degradation-order test |

## Known limitations

- **`memory_search` inherits annotations for free** and a test asserts its output
  still contains the passive rendering verbatim — one chunk must not read two ways.
- **Chain resolution is one query per level**, not a single recursive CTE: a CTE
  cannot report *which* branch hit a cycle, which R3 requires.
- **The extra query's latency is unmeasured** (R11) against the measured 0.66 s cold
  / 0.04 s warm `search()`. Correctness is tested; cost is not.
- **The superseding message may also be a ranked record**, so its text can appear
  twice (R6, accepted deliberately).
- **No real corpus has corrections in it.** Every annotation seen so far was written
  by a test or by the live check above.
- **CO9 is still open** and unaffected by this: a referential contradiction writes no
  link, so there is nothing here to render for it.
