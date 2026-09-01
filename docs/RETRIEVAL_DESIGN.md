# Task 1.5 — Hybrid retrieval design

**Tier 3 · Opus · design for approval. No code written.**

Decisions are numbered `D1`–`D9` so later work can cite them the way task 1.3's
design is cited (`the task 1.3 design, D3`).

Everything measured below was run against the **seed corpus** (`anam/ops/seed.py`,
12 chunks, real embeddings) or against a live SQLite/Chroma store. Numbers
without a measurement behind them are labelled **JUDGMENT** and listed together
in §Flagged constants.

---

## 0. What this consumes, unchanged

| Component | Status |
|---|---|
| `ChromaVectorStore.query(vector, n_results)` | exists; docstring already reserves it for this task |
| `chunks_fts` (FTS5 external-content, trigger-synced) | exists |
| `chunks` (canonical: `created_at`, `source_type`, `source_trust`, `first_message_id`, `last_message_id`, `chunk_index`) | exists |

One extension is required — see **D5**: `VectorStore.query()` needs an optional
`ids` allow-list parameter. `vectors.py` is task 1.4 (**Tier 1**), so this is not
a Tier 3 edit, but it changes the `VectorStore` protocol and `NullVectorStore`
must match.

---

## D1 — Lexical leg: the query cannot be passed through raw

**Measured, and this is the finding that most shapes the leg:**

```
"what's the deal with espresso?"  -> OperationalError: fts5: syntax error near "'"
"espresso -sour"                  -> OperationalError: no such column: sour
"a AND"                           -> OperationalError: fts5: syntax error near ""
""                                -> OperationalError: fts5: syntax error near ""
```

Ordinary user punctuation crashes `MATCH`. FTS5's query string is a *syntax*,
not a bag of words, and an apostrophe or a hyphen is operator-significant. So:

**Decision.** Extract word characters (`\w+`, Unicode) from the raw query, quote
each term, and join them. Never hand raw user text to `MATCH`. A query yielding
zero terms contributes **no lexical leg at all** — not a crash, and not an
exception the caller has to catch.

### AND vs OR — measured, not preference

```
query                                        AND   OR
my espresso tastes sour and pulls too fast    0    10
what grinder do I need for filter coffee      0    10
why are my tomato leaves yellow               0     9
```

FTS5's default (space-separated) is implicit **AND**, and it returns **zero
results for every natural-language query tested**. A conversational query will
essentially never have all its terms in one chunk.

**Decision.** `OR` over quoted unigrams; BM25 does the ranking. AND semantics
would make the lexical leg dead weight in exactly the case it exists for.

**Consequence to carry into D4 and D9:** the lexical leg now returns *something*
for almost any query. Its usefulness lives entirely in the BM25 ordering, not in
the fact that it matched.

### Extracting a comparable score

**Measured:** `bm25(chunks_fts)` returns **negative** values, more negative =
better. `ORDER BY bm25(chunks_fts)` ascending is best-first.

```
MATCH 'espresso'        -2.2019  (espresso chunk)   -1.7402  (pour-over chunk)
MATCH 'espresso sour'   -4.6508                     -3.4804
```

**Magnitude scales with query term count** — the same documents roughly double
from one term to two. This is decisive for D3 and D4: **BM25 scores are not
comparable across queries.**

---

## D2 — Vector leg

Embed the query with `ollama.embed`, query the collection. Cosine distance,
observed range 0.38–0.74 on this corpus.

**Measured behaviours worth pinning:**

- Chroma **clamps** `n_results` to collection size (asked 50, got 12). Over-asking
  is not an error.
- **The vector leg always returns neighbours regardless of match quality.** A
  deliberately off-topic query (*"completely unrelated aeronautical engineering
  tolerances"*) returned top-3 distances **0.5567, 0.5779, 0.5793**.

That second point is the "bare top-K regardless of match quality" bug class
BUILD_PLAN names, demonstrated on our own data. It is the entire reason D4
exists.

---

## D3 — RRF fusion

```
rrf_score(chunk) = Σ over legs   1 / (k + rank_in_leg)      # rank is 1-based
```

Only **ranks** enter the formula; neither raw score does.

**Why rank-based rather than score-based.** BM25 is negative, unbounded, and
scales with term count (D1); cosine distance is `[0, 2]` and scales with nothing.
There is no principled conversion between them, and inventing one is where score
fusion usually goes wrong. RRF needs only the two orderings.

### The `k` constant — **JUDGMENT, flagged**

Proposed `retrieval.rrf_k = 60`, the value from the original RRF paper (Cormack
et al., 2009) and the common default.

**I measured what `k` actually does here, and the honest answer is: almost
nothing.** Sweeping `k` from 0 to 200 across three real queries:

```
'my espresso tastes sour and pulls too fast'
  k=0/1/10  -> [espresso, pour-over, tomatoes]
  k=60/200  -> [espresso, pour-over, storage-note]
```

**Ranks 1 and 2 are identical for every value of `k` tested.** Only rank 3
moves. With two legs and 12 chunks, the corpus cannot discriminate between
values of `k`.

So 60 is **not** derived from this project's data and must not be read as if it
were — same standing as `CHUNK_MAX_TURNS` (task 1.3, D3). What `k` controls in
principle is how much a top-rank in one leg outweighs mid-ranks in the other:
small `k` makes rank 1 dominant, large `k` flattens toward a rank-count vote.
That trade-off only becomes measurable with a corpus large enough for the legs
to disagree meaningfully.

---

## D4 — Relevance floors: mechanism now, thresholds unset

Per BUILD_PLAN's existing Phase 1 note (not relitigated): build the mechanism,
leave the thresholds uncalibrated.

### What "permissive" means operationally — the question asked

The two candidate meanings are **not** equivalent and the difference is
load-bearing for task 1.6:

| Option | Problem |
|---|---|
| A floor set very low so it never fires | Indistinguishable at the call site from a calibrated floor that passed. "Did the floor fire?" becomes unanswerable. |
| **No floor applied at all (`None`)** | "No floor is in force" is a first-class, inspectable state. |

**Decision: `None`, not a low number.** `retrieval.vector_distance_floor` and
`retrieval.lexical_score_floor` ship **unset**. The comparison is skipped
entirely, and the result object carries an explicit per-leg
`floor_applied: bool` so a caller — task 1.6 above all — reads the state rather
than inferring it from result counts.

A low-but-set floor would let a future reader conclude the rejection path had
been exercised because "the floor was configured and nothing was rejected."
That is precisely the false-confidence pattern this project keeps catching after
the fact.

### Why the thresholds genuinely cannot be set yet — measured contradiction

The two existing measurements **disagree**, and neither is a calibration:

| Source | "Off-topic" distance |
|---|---|
| Task 1.4 reconciliation test (6 synthetic chunks) | **0.658** |
| Seed corpus, genuinely off-topic query (this design) | **0.5567** |

A floor of 0.6 — a reasonable-looking reading of task 1.4's numbers — **would
admit** the aeronautical-engineering results as relevant. Same nominal
"off-topic", 0.10 apart, opposite verdicts. Setting a number from either would
be a guess wearing the costume of a measurement.

### The lexical floor is the harder one — **flagged, not decided**

Because BM25 magnitude scales with query term count (D1), an *absolute* lexical
threshold means different things for a 1-term and a 5-term query. When
calibration happens, the lexical floor may need to be **relative** (a fraction of
the top score, or a gap-to-next rule) rather than absolute. I am not deciding
that now — the mechanism ships as an absolute comparison because that is what an
uncalibrated placeholder should be, and switching it to relative later is a
change to one predicate.

---

## D5 — Structured time filter: pre-filter, on both legs

Task 1.3's D1 obligation — replacing the lexical date-matching deliberately
stripped from chunk text.

**Measured constraint:** Chroma metadata contains `conversation_id`, `user_id`,
`source_type`, `source_trust`, `chunk_index` — **no `created_at`.** The vector
leg cannot filter on time natively.

**Measured capability:** `Collection.query()` in chromadb 1.5.9 accepts an
`ids=` allow-list, and it works — passing 3 ids returned exactly those 3.

**Decision: pre-filter.** Resolve the time window in SQL first, producing an
allowed chunk-id set, then constrain both legs to it:

- **Lexical leg** — `AND c.id IN (...)` in the existing join. Natural.
- **Vector leg** — pass `ids=allowed` to Chroma.

**Why pre-filter and not post-filter.** Post-filtering the top-N can under-fill
or empty the result: if the N best matches overall all fall outside a narrow
window, a post-filter returns nothing even though good in-window matches exist.
Pre-filtering searches *within* the window, so the top-N are the best in-window
results — which is what "what did we discuss last Tuesday" actually means.

The window resolves over `chunks.created_at` **and** the underlying message
range (`first_message_id`/`last_message_id` → `messages.timestamp`), since a
chunk's `created_at` is when it was *written*, not when the conversation
happened. For back-filled or re-chunked content those diverge.

**Scaling concern, flagged.** An `ids=` allow-list is fine at this scale but
grows with the window. If it ever gets large, the alternatives are over-fetch +
post-filter (accepting the under-fill risk above) or **adding `created_at` to
Chroma metadata** — which would be the better answer but requires changing
`chunking.py` (**Tier 3**) and re-indexing every existing vector. Not proposed
now; recorded so the option is not rediscovered from scratch.

**Known gap:** the seed corpus has no usable time spread — all 12 chunks were
created inside 0.7 seconds. The filter can be tested for *correctness* against
synthetic timestamps but cannot be exercised meaningfully against the corpus.

---

## D6 — Provenance stays metadata-only

`source_type` / `source_trust` are returned **on** results and never enter
scoring: no boost, no trust-weighting, no filtering as part of ranking.

**Did the design surface a reason ranking should consider provenance? No.** The
constraint costs nothing today — the corpus is single-provenance
(`conversation`/`firsthand`) because task 1.7 owns the vocabulary and has not
landed.

**One future tension, recorded not decided:** once non-conversation source types
exist (ingested files, research notes, creative writing), they compete for the
same result slots — BUILD_PLAN's own task-1.10 note anticipates research notes
"competing for retrieval slots". Whether that is answered by ranking, by quotas,
or not at all is a **future decision requiring explicit approval** per the named
constraint. Flagging, not fixing.

---

## D7 — Sibling awareness for split chunks: expand after fusion, never rank

Task 1.3 exposed the relationship and took no position. **Position: siblings are
surfaced, as a post-fusion expansion, never as ranked candidates.**

Mechanism is 1.3's: split pieces share `first_message_id`. Verified present in
the corpus — one `first_message_id` carries 2 chunks at indices 1 and 2.

- **Not injected into ranking.** A message split into four pieces would occupy
  four of the top-N slots and crowd out every other conversation — the
  one-document-dominates failure. Worse, siblings that did not independently
  match would be asserted relevant by a ranking that never established it.
- **Attached to the hit that did match**, as a `siblings` field, after the
  fused list is cut to top-N. A hit on piece 2 of 4 hands the model a fragment
  of a longer message; the siblings are the rest of that same message, and 1.3
  made them discoverable precisely for this.
- **Bounded.** `retrieval.max_siblings_per_hit` caps the expansion so a
  pathologically long split cannot blow the context budget task 1.10 is
  metering. The cap is **JUDGMENT**.

---

## D8 — Result shape: inspectable by construction

`RetrievalResult` carries, per chunk: `chunk_id`, `text`, `created_at`,
`conversation_id`, `user_id`, provenance metadata, `siblings`, and the **score
provenance** — `bm25_rank`/`bm25_score`, `vector_rank`/`vector_distance`,
`rrf_score`, and which legs contributed.

At the top level: per-leg candidate counts, per-leg `floor_applied`, the
FTS query string actually issued, and the resolved time window.

This is not decoration. "Why did this chunk rank here" must be answerable
without re-running the query, and task 1.6 needs **structured** access to "the
vector leg contributed zero after the floor" rather than guessing from counts.

---

## D9 — What task 1.6 can and cannot do, stated plainly

Task 1.6's rule fires when the lexical query collapses to ≤1 meaningful term
**AND** the vector leg contributes zero chunks post-floor.

**With floors unset (D4), the second condition can never be true.** The vector
leg always returns neighbours (D2, measured), and with no floor nothing rejects
them — so its post-floor contribution is zero only when the collection itself is
empty.

Therefore:

- Task 1.6's **first** condition (term counting) is testable now.
- Task 1.6's **second** condition is **structurally unreachable** until floors
  are calibrated.

**The lexical leg returning results for nearly any query (D1's OR semantics) is
not in tension with 1.6's first condition, and must not be read as one.** 1.6
counts *query terms*, not *result counts*: a query that collapses to one
meaningful term is degenerate whether the OR leg returned ten chunks for it or
none. "The lexical leg always returns something" and "the lexical leg's
degeneracy check is broken" are unrelated statements — the check never consulted
result counts in the first place. (Open question #4, resolved.)
- **Task 1.6 cannot be exercised end-to-end**, and a green test suite must not
  be read as evidence that it can.

This restates BUILD_PLAN's Phase 1 checkpoint instruction here so it is not lost
between now and the checkpoint. The checkpoint report must say "ranking
validated; floor-rejection and degenerate-query behaviour not yet exercised —
floors intentionally uncalibrated pending real usage data."

---

## Flagged constants — judgment, not derived

Same standard as `CHUNK_MAX_TURNS` (task 1.3, D3). None of these has a
measurement behind it:

| Setting | Proposed | Status |
|---|---|---|
| `retrieval.rrf_k` | 60 | **JUDGMENT** — paper default; corpus provably cannot discriminate (D3) |
| `retrieval.candidates_per_leg` | 50 | **JUDGMENT** — fusion depth per leg before cutting |
| `retrieval.top_k` | 10 | **JUDGMENT** — interacts with task 1.10's token budget |
| `retrieval.max_siblings_per_hit` | 3 | **JUDGMENT** (D7) |
| `retrieval.vector_distance_floor` | **unset** | uncalibrated **by design** (D4) |
| `retrieval.lexical_score_floor` | **unset** | uncalibrated **by design** (D4) |
| `retrieval.expand_siblings` | true | policy (D7) |

---

## Test plan — against the seed corpus, not toy strings

The corpus was built for this. Planned assertions:

1. **Adjacency, both directions** — espresso query ranks the espresso chunk
   above pour-over; the filter-coffee query flips it. Already observed
   standalone (0.244/0.484, then 0.380/0.457); becomes a fused-ranking assertion.
2. **Punctuation does not crash** — `"what's the deal with espresso?"` returns
   results rather than `OperationalError` (D1).
3. **Natural-language queries return lexical hits** — the AND-semantics
   regression (0 rows) must not reappear.
4. **BM25 sign** — best match has the most negative score; ordering is ascending.
5. **Sibling expansion** — a query hitting the split notebook message surfaces
   its sibling via `first_message_id`, attached rather than separately ranked.
6. **Open conversation is unreachable** — `open-thread`'s turns never appear;
   its trailing group is deliberately unindexed.
7. **Floors report as not-applied** — `floor_applied is False` on both legs, and
   an off-topic query still returns results, proving nothing is silently
   rejecting.
8. **Time filter correctness** — against synthetic timestamps, since the corpus
   has no real spread (D5).
9. **Provenance absent from scoring** — mutating `source_trust` on a chunk does
   not change its rank (D6).

---

## Open questions for approval

1. **D7's position** — siblings attached post-fusion rather than ranked. Taken
   as asked, but it is a policy call and worth an explicit yes.
2. **D5's `ids=` allow-list scaling** — accept as-is now, with `created_at` in
   Chroma metadata (Tier 3, re-index) as the recorded fallback?
3. **D4's lexical floor** — agreed that it may need to become *relative* at
   calibration time rather than absolute?
4. **D1's OR semantics** — measured-necessary, but it means the lexical leg
   returns something for nearly every query. Confirm that is intended before it
   becomes load-bearing for 1.6.
5. **`VectorStore.query()` gaining an `ids` parameter** — Tier 1 file, but a
   protocol change. Confirm it belongs in this task rather than its own.
