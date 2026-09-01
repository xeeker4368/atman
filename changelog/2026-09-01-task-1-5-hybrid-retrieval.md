# 2026-09-01 — Task 1.5: hybrid retrieval (BM25 + vector, RRF, floors)

**Tier 3 · Opus · design approved before implementation.**
Design of record: `docs/RETRIEVAL_DESIGN.md` (D1–D9).

## Summary

`anam/memory/retrieval.search()` runs a lexical leg (FTS5/BM25) and a vector leg
(Chroma cosine) over the same chunk store, fuses them with RRF, and returns
results carrying full score provenance. Relevance floors are built as a
mechanism and ship **unset**. A structured time filter pre-filters both legs.
Split siblings are attached after fusion, never ranked.

## Files changed

Created: `anam/memory/retrieval.py`, `tests/test_retrieval.py` (41).
Modified: `anam/memory/vectors.py` (`ids` allow-list — approved open question
#5), `anam/config.py`, `config/defaults.toml`, `docs/RETRIEVAL_DESIGN.md`
(D9 addition), `BUILT.md`.

No schema change.

## D9 addition, as requested

Added the sentence cross-referencing open question #4: the lexical leg returning
results for nearly any query (OR semantics) is **not** in tension with task 1.6's
first condition, because 1.6 counts *query terms*, not *result counts*. It is
also restated in `retrieval.py`'s module docstring, where a maintainer will
actually meet it, and pinned by
`test_term_counting_is_independent_of_lexical_result_counts`.

## Four measured behaviours the implementation is shaped by

All four were found by running things, not by reasoning about them.

**1. Raw user text crashes FTS5 (D1).** `"what's the deal with espresso?"` →
`fts5: syntax error near "'"`. So does a hyphen (`espresso -sour` → *no such
column: sour*), a trailing `AND`, and an empty string. `build_fts_query()`
extracts `\w+` terms and quotes each; nothing user-supplied reaches `MATCH` as
syntax. Seven hostile queries are parametrised as a regression test.

**2. AND semantics returns zero for natural language (D1).** Measured 0 rows vs
10 with OR across three real queries. Terms are OR-ed and `bm25()` ranks.

**3. `bm25()` is negative, more-negative-is-better.** Ordering is ascending and
the floor is an *upper* bound. Getting this backwards would silently invert the
lexical leg, so `test_bm25_is_negative_and_best_first` pins both the sign and
the ordering.

**4. Chroma's `ids=` allow-list has two hazards** — both handled in
`vectors.py`, both found by running it:
- **An id the collection does not hold raises `InternalError`**, not "no match".
  A chunk row can legitimately exist with no vector — precisely what
  `reconcile.py` repairs — so a SQL-derived allow-list can contain such ids.
  `get()` tolerates missing ids, so it sanitises the list first. Retrieval must
  degrade when a vector is missing, never crash.
- **`get(ids=[])` raises `ValueError`**, so an empty allow-list short-circuits
  before reaching Chroma. Separately confirmed that `query(ids=[])` means
  "nothing allowed" (0 results), not "unfiltered" — the ambiguity that would
  have made an empty time window return everything.

`NullVectorStore` had **no** `query()` at all and the `VectorStore` protocol
never declared one; both now do, so retrieval cannot `AttributeError` on the
null store.

## D4 — what "permissive" means, implemented

Floors ship as `None`, **not a low number**. The comparison is skipped entirely
and `LegReport.floor_applied` reports the state explicitly.

`test_a_set_floor_is_distinguishable_from_an_unset_one` is the point: with a
floor of 99.0 (which rejects nothing) and with no floor at all, the *outcome* is
identical — zero rejections — but the reported state differs. That is exactly the
confusion a low-but-set floor would make unanswerable.

The mechanism is proven to work despite shipping unset:
`test_the_floor_mechanism_works_when_a_threshold_is_set` sets 0.0 and asserts
every candidate is rejected.

**Why they cannot be calibrated, restated in `defaults.toml`:** the two existing
off-topic datapoints disagree — task 1.4's synthetic test saw 0.658, the seed
corpus's genuinely off-topic query saw 0.5567. A floor of 0.6 read from the
first would admit the second.

## D5 — time filter is a pre-filter on both legs

SQL resolves the window to an id set; the lexical leg adds `AND c.id IN (...)`,
the vector leg passes `ids=`. The window matches on **either** `chunks.created_at`
or any message timestamp in the chunk's range, because `created_at` is when the
chunk was *written* and a user asking about last Tuesday means the conversation.

`None` (no window) and `[]` (window matched nothing) are deliberately distinct
return values, tested — conflating them would make an empty window return
everything.

## D7 — siblings attached, never ranked

Verified live at three values of `top_k`:

```
top_k=1:  idx=1 ranks, sibling idx=2 ATTACHED
top_k=3:  idx=1 and idx=2 both rank independently, nothing attached
```

Both are correct. `test_no_sibling_is_ever_lost` asserts the real invariant
across `top_k` 1–10: every sibling of a ranked split hit is either ranked or
attached, and **never both** (which would duplicate it in the prompt).

## A test-design mistake worth recording

My first sibling test asserted that a specific chunk ranked first under the
stubbed embeddings. It passed alone and **failed in the full suite**. The stub is
a content-derived hash, so which chunk it favours is arbitrary — I was asserting
a property of a hash, not of retrieval.

Fixed by querying with the split chunk's own exact text, which makes its
distance 0 and its rank deterministic under the stub. The full suite now passes
repeatedly and `tests/test_retrieval.py` passes standalone.

## Live verification, real embeddings, real corpus

```
Q: my espresso tastes sour and pulls too fast
   1. rrf=0.03279 bm25=-13.55 dist=0.256 [lexical+vector] espresso
   2. rrf=0.03226 bm25= -3.48 dist=0.475 [lexical+vector] pour-over
Q: what grinder do I need for filter coffee
   1. rrf=0.03279 bm25= -7.72 dist=0.380 [lexical+vector] pour-over   <- flips
Q: what's the deal with yellow tomato leaves?      <- would previously crash
   1. rrf=0.03279 bm25= -8.14 dist=0.249 [lexical+vector] tomatoes
floors applied: lexical=False vector=False   (all queries)
```

The adjacency pair ranks correctly in both directions through the *fused*
ranking, not just the vector leg alone.

## Why the vector leg degrades where chunking and reconcile abort

`_vector_leg()` catches broadly and returns a lexical-only answer when the
embedder is unreachable. That is a deliberate divergence from the two modules
next to it, and since all three will be read side by side, the reasoning belongs
in writing rather than in whoever wrote it:

- `chunking.py` — "no exception is caught in the write loop. A failure aborts the
  run and propagates."
- `reconcile.py` — "failure policy matches the chunking pipeline: abort and
  propagate rather than skipping."

**Those two write, and nothing is waiting on them.** A partial write is worse
than no write. Both are additive and resumable, so aborting costs only the work
not yet done and the next checkpoint or pass picks it up with nothing lost.
Continuing past what is almost always a systemic error — a wrong embedding
dimension, an unreachable model — would produce a long run of identical failures
and a report that looked like partial success.

**Search reads, and a caller is waiting.** No data is at stake in either
direction, because retrieval never writes anything: there is no partial state to
leave behind and nothing to corrupt. Failing the whole query because one leg is
down converts a usable lexical-only answer into no answer, for a caller who
cannot meaningfully retry later — the turn is happening now. The failure is
recorded in `LegReport.skip_reason` rather than swallowed, so a degraded answer
is visibly degraded rather than quietly worse.

Stated as one rule rather than three policies: **abort when a failure could
corrupt something or when retrying is free; degrade when nothing can be
corrupted and a person is waiting.** `idle.py` sits between the two for the same
reason — it collects per-conversation failures and raises them together at the
end, because one unreachable model must not stop every other conversation
closing. Three different answers, one criterion.

The same paragraph is in `retrieval.py`'s module docstring, beside "what this
module does not do", so it is found by someone reading the code rather than only
by someone reading changelogs.

## ⚠ Flagged — OR semantics admits stopword-only matches

Visible in the live run above: the espresso query's third result is a storage
note with `bm25=-0.00` — it matched only on stopwords (`and`, `too`), carries
essentially no lexical signal, and still earns a rank and therefore an RRF
contribution.

This is a direct consequence of D1's OR semantics, which was approved
(open question #4) and is measured-necessary — AND returns nothing. **I have not
added stopword filtering**, because it was not in the approved design and it is a
real retrieval-policy decision, not an obvious cleanup: which words count as
stopwords is corpus-dependent, and dropping them changes what the degeneracy
term-count in task 1.6 sees.

It matters less as the corpus grows (better matches outrank noise), and the
relevance floor is the intended answer once calibrated. Flagging for a decision
rather than deciding.

## Judgment constants — flagged, not derived

Same standard as `CHUNK_MAX_TURNS`:

| Setting | Value | Basis |
|---|---|---|
| `retrieval.rrf_k` | 60 | **JUDGMENT.** Paper default. Swept 0→200 on real queries: ranks 1–2 identical throughout, only rank 3 moved. The corpus provably cannot discriminate. |
| `retrieval.candidates_per_leg` | 50 | **JUDGMENT.** |
| `retrieval.top_k` | 10 | **JUDGMENT.** Competes with task 1.10's token budget. |
| `retrieval.max_siblings_per_hit` | 3 | **JUDGMENT.** |
| `retrieval.vector_distance_floor` | **unset** | uncalibrated by design |
| `retrieval.lexical_score_floor` | **unset** | uncalibrated by design |

All raise `ConfigError` on nonsense (negative `rrf_k` would divide by zero at
rank `-k`).

## Tests: 41 new, 274 total

`ruff check .` clean. Suite verified order-independent across repeated runs.

Highlights beyond those above: provenance is returned but never scored —
`test_changing_source_trust_does_not_change_ranking` rewrites every chunk's
`source_trust` and asserts ranks and scores are byte-identical (D6);
`test_the_open_conversation_is_unreachable` confirms the deliberately unindexed
trailing group never surfaces; `test_a_vector_leg_failure_degrades_rather_than_
failing_retrieval` kills the embedder and asserts the lexical leg still answers.

## Known limitations

- **Stopword-only matches**, above.
- **Task 1.6 remains structurally untestable end-to-end** (D9). Pinned by
  `test_the_vector_leg_cannot_contribute_zero_while_floors_are_unset` — if that
  ever fails, floors have been calibrated and 1.6 became reachable, which is a
  real change rather than a broken test.
- **The time filter has no real data to bite on.** All 12 seed chunks were
  created inside 0.7 seconds, so it is tested for correctness against
  constructed windows only.
- **`ids=` allow-list scales with the window.** Fine here; the recorded fallback
  is `created_at` in Chroma metadata (Tier 3 — touches `chunking.py` — plus a
  re-index), per approved open question #2.
- **No relevance quality claim.** Unit tests use a content-hash stub embedding
  and assert plumbing, ordering and policy. Semantic quality is the Phase 1
  checkpoint's job, against the live run above.
- **Single `source_type` in the corpus**, so provenance competing for slots
  (D6's recorded future tension) cannot be exercised.

## Follow-up

- Phase 1 checkpoint: run real queries, read rankings, and state plainly that
  floor-rejection and degenerate-query behaviour were **not** exercised.
- Task 1.6 consumes `LegReport.floor_applied` and `query_terms()`.
- The stopword decision above.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes** — retrieval is read-only; no write path.
5. Traceable derived artifacts? **Yes** — every result carries bm25 rank/score,
   vector rank/distance, RRF score and which legs contributed.
6. Tool calls recorded? **N/A** — `memory_search` (Phase 2) will wrap this.
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Yes** — `RetrievalResult` reports per-leg
   candidates, kept, rejected, floor state, the FTS query issued, and the window.
9. Autonomy more cumulative? **Yes** — this is what makes accumulated memory
   reachable rather than merely stored.
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No.**
12. Tests? **Yes**, 41, plus live verification.
13. Core substrate changed unnecessarily? **No** — `chunking.py` and `db.py`
    untouched; `vectors.py` gained one optional parameter, as approved.
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** BUILD_PLAN permits consulting the
    reference build for this task specifically; its threshold constants were
    deliberately **not** carried over, per the Phase 1 note.
