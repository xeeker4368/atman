# Retrieval and supersession — task 3.5 design

**Tier 3.** `AGENTS.md` lists *"retrieval changes that implement the
supersedes/correction link"* as a stop-and-verify category. This is design only:
**nothing here is implemented**, and one item (R4) is a schema change that
`AGENTS.md` requires be raised *before* it is coded, not disclosed afterwards.

Design of record for the classifier that writes these links:
`docs/CORRECTION_DESIGN.md` (C1–C13, CO1–CO8). This document is its read side.

Sections R1–R12 are the proposal; open questions are RO1–RO6 at the end.

---

## What is already decided, and is therefore not a question here

* **Annotate, never suppress** (CO7). The corrected content still surfaces; the
  correction surfaces beside it. *"Transparency is the reason, not cost."*
* **Raw experience is never edited** (`PROJECT.md`, `GUIDANCE.md`). Nothing here
  writes to `chunks`, `messages`, FTS5 or the vector store.
* **Two renderable states, not one** (CO8, decided 2026-09-18): *corrected,
  replacement known* and *corrected, no replacement given*. A `supersedes` link
  does **not** imply a new value exists to display.
* **A visited set at read time** (`docs/DB_SCHEMA.md`). The schema's cycle
  triggers preserve correctness; the read-side check preserves *termination*, and
  retrieval hanging mid-turn is severe enough that it is not optional.
* **Retrieval is not filtered by actor** (decision #20) and **links never cross
  users** (#21/Q16), so annotation inherits both without a new rule.
* **Provenance is returned but never scored** (D6). Supersession is provenance.

---

## R1. Where resolution happens: after fusion, never inside ranking

**Proposal: resolve after fusion, attach to `RetrievedChunk`, render in
`prompt.render_retrieved()`.** Exactly the shape D7 gave split siblings —
*attached after fusion, never ranked* — and for the same reason: a chunk's rank
must not depend on whether something in it was later corrected.

Two consequences worth stating rather than discovering:

* **The ranking path is untouched.** `_lexical_leg`, `_vector_leg`, `_apply_floor`
  and `reciprocal_rank_fusion` do not learn about links. A test on D6's own
  pattern proves it: write links over every result and assert ranks **and** RRF
  scores are byte-identical.
* **`memory_search` gets annotations for free**, because it reuses
  `prompt.render_retrieved()` and a test already pins that its output is
  *identical* to the passive rendering for the same query. That identity is a
  feature — one chunk must not read two ways depending on how it was retrieved —
  so the test should keep passing unchanged rather than be relaxed.

**Rejected: suppressing or reordering superseded chunks.** CO7 forbids the first,
and the second is a scoring change that would need its own RRF review.

**Rejected: annotating inside `chunks.text`.** That is editing the record, and it
would put the annotation into FTS5 and the embedding on the next rebuild.

## R2. Chunk → message, in one query rather than N+1

A link names *messages*; retrieval returns *chunks*. The mapping already exists —
`db.get_messages_in_chunk()` (a timestamp-window join, C3/CO2) — but calling it
per chunk and then `get_supersedes_links()` per message is up to
`top_k (10) x max_turns (8) = 80` link queries inside a turn, on a database whose
lock contention is a recorded issue.

**Proposal: one new read in `db.py`** — `get_supersedes_for_chunks(chunk_ids)` —
joining chunks → messages (the same window join) → `supersedes`, returning one row
per (chunk, superseded message, superseding message) with the superseding
message's `content`, `timestamp` and `role`. One query per turn regardless of
`top_k`. Read-only, so no `@retry_on_locked` question arises; it is nevertheless
subject to the same lock waits as any read.

**Cost must be measured, not assumed** (R11).

## R3. Follow the chain to its tip, with a visited set

A corrected claim can be corrected again: `A ← B ← C`. The **current** statement
is `C`, so surfacing `B` would annotate a record with a superseded correction.

**Proposal: follow forward to the tip.**

* A `visited: set[str]` of message ids, seeded with the starting message.
* Stop on revisit, and **record that it happened** rather than only breaking —
  a cycle reaching retrieval means the schema trigger was bypassed (a restore, a
  direct `sqlite3` write, a future bug), which is an operator-visible fact.
* A hard depth bound as well as the visited set, because the two fail differently:
  the visited set stops a *loop*, the bound stops a pathologically long *chain*
  from spending the turn. `SUPERSESSION_MAX_DEPTH = 10`, a flagged judgment value.
  Reaching it is recorded and the deepest resolved message is used.
* **A branch is possible**: two messages may supersede the same claim (the schema's
  `UNIQUE` is on the pair, not on the superseded side). Proposal: take **all**
  tips, ordered by timestamp, and render them; do not pick one. Picking would be
  an unrecorded judgment, and the case is rare enough that showing both is
  cheaper than deciding.

## R4. The two states need a column — and this is the decision to take first

CO8 requires *corrected, replacement known* and *corrected, no replacement given*
to render differently. **Nothing in the store can currently tell them apart.**
`supersedes` carries `rationale` (free text from the classifier) and `confidence`
(always `NULL`); neither is a structural answer, and parsing the rationale at read
time would make the rendering depend on prose wording.

**Proposal, in two halves:**

1. **The classifier says which**, because it is the only place that knows. The
   reply grammar extends from `CORRECTS <n>` to `CORRECTS <n> REPLACED` /
   `CORRECTS <n> CONTRADICTED`, parsed in `corrections.py` (O18: the grammar is
   local to this task). An unlabelled `CORRECTS <n>` is **not** defaulted to
   either — it is an unusable reply, on this module's existing rule, because
   guessing would silently manufacture whichever state is cheaper to render.
2. **One new column, `supersedes.replacement` TEXT**, CHECK-constrained to
   `replaced | contradicted`, `NOT NULL`. Migration 6 on `working.db` only — the
   archive's shape is frozen and `supersedes` has never lived there.

**Why a column rather than deriving it:** the alternative is re-judging at read
time, which means a model call inside the retrieval path. That is not acceptable
at any latency.

**Why `NOT NULL` rather than nullable:** a nullable column would make "the
classifier did not say" a third, unrendered state that looks like a missing
feature rather than a failure. Every row is written by one writer
(`corrections.record()`), which can always supply it.

**This column is the item `AGENTS.md` requires be approved before it is coded**
— *"on a frozen table the cost of a wrong column is permanent and
one-directional"*, and while `supersedes` is not the archive, migration 5 already
recreated it destructively once and a second such migration is not free. **Nothing
in R4 is written until it is approved.** RO1 states the alternative.

## R5. What the annotation actually says

Rendered as a short block **after** the chunk it annotates, indented so it reads
as a footnote rather than as content:

```
[record 2 · 2026-09-14T10:02]
The dentist appointment is on Tuesday at 3.
We need oat milk and coffee beans.

  Later corrected. "The dentist appointment is on Tuesday at 3." was superseded
  on 2026-09-15 by: "Actually the dentist is Wednesday, not Tuesday."
```

and, for CO8's second state:

```
  Later contradicted, with no replacement given. "The dentist appointment is on
  Tuesday at 3." was contradicted on 2026-09-15 by: "The dentist isn't Tuesday."
```

Four deliberate choices:

* **The superseded message is quoted, not described by position.** A chunk packs
  up to eight turns into one opaque block; "the third message" is not something a
  reader can resolve against the text it is looking at.
* **The superseding message is quoted in full** up to a cap (R10), not summarised.
  Summarising would put a generated paraphrase where the person's own words
  belong, in a mechanism whose whole purpose is fidelity to what was said.
* **The rationale is NOT rendered.** It is classifier output about a judgment, not
  something either party said, and the prompt is not the place to argue for the
  link. It stays queryable in the row.
* **"with no replacement given" is stated positively**, the same rule the gate's
  *"No tools were used this turn"* follows: an absent clause reads as no
  information.

## R6. The superseding message may itself be a ranked result

Then it appears twice — once as its own record, once quoted in an annotation. This
is the duplication problem D7 solved for siblings by never letting a sibling be
both ranked and attached.

**Proposal: allow the duplication, deliberately.** The two occurrences say
different things — one is a retrieved record, the other is evidence attached to an
older claim — and suppressing the annotation when the corrector happens to rank
would make the annotation's presence depend on ranking, which is exactly the
coupling R1 exists to prevent. The cost is a few duplicated characters, bounded by
R10.

## R7. Failure policy: degrade, and say so

On `prompt.py`'s own recorded criterion — *abort when a failure could corrupt
something or when retrying is free; degrade when nothing can be corrupted and a
person is waiting* — a failed annotation lookup degrades: results are returned
unannotated, with `RetrievalResult.supersession` recording the failure the way
`LegReport.skip_reason` records a downed leg.

**But the degraded state is not benign, and the design should not pretend it is.**
Unannotated results present a corrected claim as current, which is the exact
outcome this mechanism exists to prevent. That is the gate's *"unavailable is
never clean"* shape, and it is why the state must be **inspectable** rather than
only logged. Whether it should be surfaced *to the model* as well ("some records
could not be checked for corrections") is RO3.

## R8. No actor, anywhere

Links exist only within one user's own claims (#21/Q16), so annotation needs no
actor to be correct, and decision #20 forbids scoping retrieval by who is asking.
A test asserts no function on this path takes `actor`, `user_id`, `role` or
`user` — the same enforcement `gate.py` carries, and for the same reason: once an
actor parameter exists, someone will filter on it.

## R9. Where the state lives on the result

```python
@dataclass
class Supersession:          # attached to RetrievedChunk
    superseded_message_id: str
    superseded_text: str
    superseding_message_id: str
    superseding_text: str
    superseding_timestamp: str
    replacement: str          # "replaced" | "contradicted"
    depth: int = 1            # how many links were followed to reach this tip
```

`RetrievedChunk.supersessions: list[Supersession]`, empty for the ordinary case —
so every existing consumer is unaffected and the annotation is additive, the same
way `siblings` was.

`RetrievalResult.supersession: SupersessionReport` carrying `resolved: bool`,
`links_followed`, `cycles_detected`, `depth_limit_hit` and `skip_reason`. Task
1.6's precedent: *structured* access, not inference from result counts.

## R10. Bounds, because annotations spend the context window

`render_retrieved()`'s output is measured by `assemble_turn()` and charged against
the history budget, so an unbounded annotation silently evicts conversation
history.

Proposed, all flagged as judgment values:

* `SUPERSESSION_QUOTE_CHARS = 300` per quoted message (so ~650 characters per
  annotation with scaffolding). 300 is `web_search`'s own content cap, chosen for
  the same reason.
* `SUPERSESSION_MAX_PER_CHUNK = 3`, with a count stated when more exist
  (*"and 2 further corrections"*) rather than silently dropping them — the
  `unresponsive_engines` pattern.
* Worst case is therefore bounded by construction:
  `top_k (10) x 3 x ~650 ≈ 19,500` characters, which is **too large** — it would
  dominate a 32K-token window. **This needs a global cap as well**, and that is
  RO4 rather than a number I should pick unilaterally.

## R11. What must be measured before this is trusted

Nothing in this document is a measurement, and three claims in it are empirical:

* **The extra query's cost**, cold and warm, against a store with real links —
  compared against the 0.66 s cold / 0.04 s warm `retrieval.search()` already
  measured. If it is material it belongs inside the in-flight-grace arithmetic.
* **The realised annotation size** on real chunks, against R10's bound.
* **That ranking is byte-identical** with and without links (D6's pattern).

The correction *classifier's* accuracy is already measured (task 3.4). This task
adds no model call, so it needs no frozen eval set of its own — its correctness is
testable deterministically, which is worth saying explicitly rather than leaving
the reader to wonder whether a harness was skipped.

## R12. Deliberately not built

* **No suppression, no reordering, no relevance penalty** for superseded content.
* **No backfill.** Links only exist going forward; moot under decision #16's wipe.
* **No annotation of history**, only of retrieved records. The windowed history is
  the raw conversation, and annotating it would put generated text into the
  transcript the model reads as what was said.
* **No admin surface.** Phase 9 owns any view of the link table.

---

## Open questions

**RO1 — DECIDED at review, 2026-09-18: yes, extend the grammar and add the
column.** Built: the prompt emits `CORRECTS <n> REPLACED|CONTRADICTED`,
`supersedes.replacement` is `NOT NULL` and CHECK-constrained by migration 6, and
3.4's harness gained a fourth outcome, `wrong_state`. The reviewer accepted the
cost explicitly — the two-state requirement has no real workaround. R4 above is
therefore no longer a proposal. **R1–R3 and R5–R12 remain unimplemented.** The
question as raised follows.

**RO1 as raised — the `replacement` column (R4), the decision that gated coding.**
The alternative to a column is rendering **one** state for every link — quoting the
superseding message and letting the reader see for themselves that no replacement
value appears in it. That is strictly cheaper (no migration, no grammar change, no
re-measurement of the frozen 15) and it loses something real: the distinction is
then implicit in prose the model must interpret, rather than stated. CO8's wording
— *"two distinct renderable outcomes, not assume every correction link carries a
new value"* — reads as asking for the column. **I recommend the column**, and I am
raising it rather than coding it because it is a schema change on a table that has
already been recreated destructively once.

**RO2 — does extending the grammar re-open task 3.4's measurement?** It does:
`CORRECTS <n> REPLACED` changes the classifier's prompt, so the 15-case frozen set
must be re-run, and the label is a *new* thing to get wrong — a correction with a
replacement labelled `CONTRADICTED` would render the weaker annotation. That is a
new failure mode the current outcome vocabulary (`false_link`, `missed`,
`wrong_target`) cannot score, so 3.4 would need a fourth outcome,
`wrong_state`. Cheap to add, but it is scope this task creates for another and
should be visible before RO1 is answered rather than after.

**RO3 — should a failed annotation lookup be visible to the model?** R7 makes it
inspectable in code. Telling the model *"some records could not be checked for
corrections"* follows `memory_search`'s precedent (nothing found with a leg down is
a different claim from nothing found) and cuts the other way here: it invites the
entity to hedge on every record in a turn where one lookup failed. I lean to
**recording it without telling the model**, which is the opposite of what I argued
for `memory_search`, and the asymmetry deserves your judgment rather than my
consistency.

**RO4 — the global annotation budget (R10).** The per-chunk bound leaves a
worst case near 19,500 characters. A global cap is needed; where it should come
from is genuinely unclear to me. Candidates: a fraction of the retrieved-chunk
budget (self-scaling, but a new kind of constant), a flat character ceiling
(simple, arbitrary), or annotating only the **top N ranked** chunks (bounded and
principled — the most relevant records are the ones whose staleness matters most —
but it makes annotation depend on rank, which R1 argues against everywhere else).

**RO5 — branches (R3).** Rendering every tip when two messages supersede the same
claim is my proposal. The alternative is newest-wins, which is one line of code and
hides a disagreement between two things the same person said. Confirming that
showing both is wanted, rather than assumed, matters because it is the only place
this design shows more than one correction for one claim.

**RO6 — the quoted superseded text (R5).** The annotation quotes the superseded
message so a reader can locate it inside an eight-turn chunk. For a long message
that quote is truncated at `SUPERSESSION_QUOTE_CHARS`, and a truncated quote may
not uniquely identify the line it points at. An alternative is quoting the
message's first sentence rather than its first 300 characters. Minor, but it is the
kind of detail that reads fine in a design and badly in a real prompt.

---

## Built at stage 3 (2026-09-19)

`program/memory/supersession.py`, two batched readers in `db.py`, attachment in
`retrieval.search()`, rendering and the budget in `prompt.py`,
`tests/test_supersession.py` (25). **R1–R3 and R5–R12 implemented; R4 was already
built.** 1,005 tests pass.

### RO4 — resolved, and the answer is a shape rather than a number

None of the three candidates I listed was right on its own. The one that matters is
an ordering, not a cap: **shorten before dropping.** A dropped annotation presents a
corrected claim as current — the failure this whole mechanism exists to prevent — so
the correction's *quote* is the first thing to go and the annotation's *existence*
is the last.

Two bounds, protecting different things:

* `SUPERSEDING_QUOTE_BUDGET_CHARS = 2000`, spent on correction quotes in rank order.
  Once gone, annotations still render with their locator quote and their state, just
  without the correction quoted. **The essential signal is never budgeted.**
* `SUPERSESSION_MAX_ANNOTATIONS = 12` overall and `MAX_PER_CHUNK = 3`, with whatever
  is withheld **counted** in a closing line rather than vanishing — the
  `unresponsive_engines` pattern.

Worst case by construction ≈ **4,600 characters**, against
`agent.max_tool_result_chars`'s 4,000 as the nearest precedent for how much one
auxiliary thing may add to a turn. A test asserts the bound on a deliberately
pathological input (10 chunks × 8 corrections × 900-character messages) rather than
leaving the arithmetic in a comment.

**The honest cost:** the global cap does let a low-ranked record's annotation be
replaced by an aggregate count, so in that case the model is told *"corrections
apply to N of the records above"* without being told which. That is a real
degradation and it is the only place annotation presence depends on rank.

### Three open questions I answered by implementing my own stated lean

None of these was decided at review. Each is implemented as the design proposed,
and each is **reversible** — flagged here so they do not become settled by silence.

* **RO3 — REVERSED at review, 2026-09-19: the failure IS surfaced to the model.**
  My lean was to record it silently, fearing that telling the model would invite
  hedging on every record in a turn where one lookup failed. The reviewer's argument
  is better and it is `memory_search`'s own: *nothing found with the vector leg down
  is a different claim from nothing found.* The absence of annotations carries no
  information when the check did not run, so `prompt._SUPERSESSION_UNRESOLVED` says
  so — **before** the records, per this module's own ordering rule, and worded about
  the *check* rather than the records' truth so there is nothing in it to generalise
  into doubt about the memory. It is authored text and the naming/trait tripwires
  cover it. No note is emitted when there are no results: nothing to qualify.
* **RO5 — a branch renders every tip.** Two messages superseding the same claim both
  appear, ordered by timestamp. Newest-wins would be one line of code and would hide
  a disagreement between two things the same person said.
* **RO6 — the locator is truncated at 120 characters**, not at the first sentence.
  A truncated quote may not uniquely identify the line it points at; first-sentence
  quoting was the alternative.

### What was verified live, not just in tests

Real store, real embeddings, real model, both states in one prompt:

* `replaced` — *"What day is my dentist appointment?"* → **"Your dentist appointment
  is on Wednesday."** The record still says Tuesday; the annotation carried the
  correction.
* `contradicted` — *"What should the boiler pressure be?"* → **"Lyle previously
  mentioned that the boiler pressure should sit around 1.4 bar, but then stated,
  'That's not right.' No replacement value was provided in the records."**

That second answer is what CO8's two-state requirement was for, and it is the first
evidence the distinction survives all the way to the model rather than only to the
renderer.

### Guards proven by breaking them

Each was deliberately broken and the suite re-run, then restored:

| broken | what failed |
|---|---|
| the visited set | the cycle test — and the depth bound caught the loop instead, so the two stops really are independent |
| chain state taken from the first link, not the last | the `replaced → contradicted` chain test |
| drop annotations instead of shortening them | the degradation-order test |
| the cycle branch's own counter | `cycles_detected` assertion |

### Known limitations

- **Chain resolution costs one query per level.** Depth 1 — the expected case — is
  two queries total. A deep chain is one per level, not one per message, but it is
  not a single recursive CTE; a CTE could not report *which* branch hit a cycle,
  which R3 requires.
- **A branch multiplies annotations.** Two tips on one claim render two annotations
  and spend two slots of the global cap.
- **The superseding message may also be a ranked record**, so its text can appear
  twice (R6, accepted deliberately — suppressing the annotation when the corrector
  happens to rank would make annotation presence depend on ranking).
- **Nothing measures the extra query's latency yet.** R11 asks for it against the
  measured 0.66 s cold / 0.04 s warm `search()`; the tests establish correctness, not
  cost. It is one indexed query on `supersedes` plus one per chain level.
- **No real corpus has corrections in it.** Every annotation observed so far was
  written by a test or by the live check above.


---

## R11 measured (2026-09-19) — the last thing task 3.5 owed

Against `search()`'s recorded baseline (0.66 s first call, 0.04 s warm; task 2.3).
Real store, real embeddings, 12 conversations, 10 returned chunks, 21 samples each.

| | `resolve_for_chunks` | `search()` |
|---|---|---|
| no links | **0.55 ms** | 32.1 ms |
| 10 links, depth 1 | **0.96 ms** | 32.0 ms |
| + one depth-9 chain (19 links) | **2.44 ms** | 35.6 ms |
| **every chunk chained to `MAX_DEPTH`** — 100 links, 10 levels | **2.34 ms** (max 2.89) | 36.7 ms |

`db.get_supersedes_for_chunks` alone: **0.44 ms**. The last row is the ceiling the
code permits — ten levels, one query each, however corrupt the data.

**~1.7% of a warm search ordinarily, ~6% at the absolute bound.**

**The comparison is easy to get wrong and the correction matters:** the 0.66 s
baseline is dominated by the cold `nomic-embed-text` load, measured separately here
at **617 ms**. Resolution makes **no model call**, so it is pure SQLite and
invariant to model warmth — it has no cold figure to report, and claiming one would
describe a dependency it does not have.

**The in-flight-grace floor does not move** — `2000 + T` seconds, and this adds under
3 ms, so the floor stays 35. Checked, not assumed.

*Unmeasured, and stated as such: a large corpus (the index makes this a lookup on
matching links rather than on table size, but that is reasoning); lock contention
(these are uncontended reads); and rendering cost separately from resolution (string
work bounded by RO4 at ~4,600 characters).*
