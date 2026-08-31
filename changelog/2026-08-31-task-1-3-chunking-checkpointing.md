# 2026-08-31 — Task 1.3: Chunking + checkpointing pipeline

**Tier 3 · Opus · Design approved before implementation.**

## Summary

Conversation messages become chunk rows. Two public functions, sealing-based
checkpointing, boundary-preferring splitting, and a vector-store seam that lets
task 1.4 land without touching this code.

Design approved with three resolutions (K1–K3 in the design document); D3 and D4
stood as written. All three are implemented as agreed.

## Files changed

Created: `anam/memory/chunking.py`, `anam/memory/splitting.py`,
`anam/memory/vectors.py`, `tests/test_chunking.py` (32), `tests/test_splitting.py` (14).
Modified: `anam/memory/db.py` (chunk helpers), `anam/config.py`,
`config/defaults.toml`, `BUILD_PLAN.md`, `BUILT.md`.

## The three resolutions, as built

**K1 — D1's replacement is task 1.5's, and it is recorded.** Chunk text carries
speaker and content only; no timestamps. Added to `BUILD_PLAN.md`'s Phase 1
notes as an explicit obligation on 1.5 to provide a structured time filter, so
it is a task in the plan of record rather than a promise in a design document.

**K2 — idle-close is a numbered Phase 1 task, Tier 2 / Sonnet.** Added to
`BUILD_PLAN.md` after the chunking row it depends on, with the spec question
called out: the idle window's *floor* is a correctness constraint, not a
preference — it must exceed a worst-case in-flight turn or the janitor closes a
conversation mid-generation. Tier 2 because that value wants approving before it
is coded. Tier 3 count stays 13, so `AGENTS.md`'s sync rule is not triggered —
checked, not assumed.

**K3 — the vector write goes through a protocol.** `anam/memory/vectors.py`
defines `VectorStore` (upsert/delete/has) and `NullVectorStore`. Task 1.4 adds
`ChromaVectorStore` to the same module and makes it default; nothing in the
pipeline changes when it does. The null store is deliberately not a silent
success: `ChunkingResult.vectors_indexed` is reported separately from
`chunks_written`, so "written" and "vector-retrievable" stay distinct claims.

## Failure modes designed against, and the mechanism for each

| # | Reference build defect | Mechanism here |
|---|---|---|
| F1 | Tail re-embedded every turn, up to 5× the same text | Only **sealed** groups are embedded; the open tail is never written |
| F2 | Delete-then-write destroyed retrievable content on a transient failure | Nothing is ever deleted; embed precedes any write |
| F3 | Per-sub-unit `except Exception` produced silently missing chunks | No exception caught in the write loop; abort and propagate |
| F4 | Split shape changed as the tail grew | Splitting happens once, at seal, on frozen content |
| F5 | A third entry point nothing called | Exactly two, pinned by a test on the public surface |
| F6 | Index rows with no canonical record | SQLite written first; Chroma derived (task 1.1's `chunks` table) |

## Boundary rule

Greedily pack whole turns until adding the next would exceed `target_chars`,
then seal. Turn boundaries preserved — a boundary never falls between a question
and its answer — but size decides where it goes.

- `chunking.target_chars = 2500` — ~540 tokens at the 4.63 chars/token measured
  in task 1.2, and half the 5000 embed ceiling, so splitting stays exceptional.
- `chunking.max_turns = 8` — coherence cap. Judgment, flagged as such in the
  design (D3) and in the config comment.

The property everything rests on: **greedy left-to-right packing is stable under
append**, so every group but the last is final the moment it forms.
`test_packing_is_stable_under_append` pins it.

## Tests: 46 new, 109 total

`ruff check .` clean. Full suite **109 passed**.

Tests that earn their place:

- `test_repeated_checkpoints_never_re_embed` — five further checkpoints after
  the first add zero embed calls. This is F1, measured rather than asserted.
- `test_checkpointing_does_not_change_total_work` — a heavily checkpointed
  conversation costs exactly as many embeddings as one closed cold.
- `test_embedding_failure_writes_nothing_and_propagates` — parametrised over
  dimension/unreachable/timeout; after each, zero chunk rows, zero vectors,
  `chunked` still 0. This is F2.
- `test_stored_text_mismatch_raises_rather_than_overwriting` — the `text_sha256`
  check firing.
- `test_hard_split_never_corrupts_multibyte_characters` — 200 emoji with no
  whitespace, forcing the hard-split path.
- `test_null_store_reports_no_vectors_indexed` — chunk written, lexically
  retrievable via FTS, `vectors_indexed == 0`.

**Live end-to-end run** outside the suite, with the real embedding model and a
9000-character message:

```
written: 2  vectors_indexed: 0  marked: True
rows: 2  indices: [0, 1]  sizes: [4873, 62]
all within 5000 budget: True
provenance on every row: True
no timestamps in text: True
FTS hits 'harbour': 1
default store indexes vectors: False (NullVectorStore until 1.4)
```

### A test bug worth recording

Three tests failed on first run. Diagnosing rather than adjusting expectations
showed **the pipeline was correct in all three cases and my test fixture was
wrong**: `big_turn()` generated identical text on every call, so
`len(set(embed_calls))` was 2 for reasons of test data rather than double
embedding. A trace confirmed 8 rows at indices 0–7 with 8 embed calls — exactly
once each. Helpers now emit distinct text per turn, which is what makes the
uniqueness assertion mean anything. A third failure searched FTS for a term that
was in the deliberately-unindexed open tail.

Worth writing down because a test asserting the right property on the wrong data
is the kind of thing that passes later for the wrong reason.

## Known limitations

- **No vectors are written yet.** `NullVectorStore` is the default until task
  1.4. Chunks are lexically retrievable only, and `vectors_indexed` reports 0.
  Task 1.4's reconciliation backfills them.
- **The embedding is computed and discarded** under the null store. Real waste,
  bounded — there is no chat endpoint until task 2.2, so the only chunks written
  in this window come from tests and task 1.15's seed corpus.
- **Reconciliation is specified but not built** (design §H). Its Chroma half
  does not exist yet; it lands with 1.4.
- **The open tail is unindexed until seal or close**, so idle-close is
  load-bearing and does not exist yet. That is K2's task.
- **The per-conversation lock is in-process only.** The unique index on
  `(conversation_id, chunk_index)` is the actual arbiter; the lock only avoids
  wasted embedding. Sufficient for a single-process backend, stated as an
  assumption rather than a guarantee.
- **`max_turns = 8` is unmeasured**, per D3.
- **No caller yet.** `checkpoint_conversation()` is wired to nothing until the
  chat route in task 2.2.

## Follow-up

- Task 1.4: `ChromaVectorStore`, make it default, build reconciliation.
- Task 1.5: the structured time filter that replaces lexical date matching.
- Task 1.7: validate `source_type="conversation"` / `source_trust="firsthand"`.
- Idle-close (new Phase 1 task): spec approval on the window and its floor.
- Task 2.2: call `checkpoint_conversation()` after each completed turn.

## Project Anam alignment check

1. Assign the entity a name? **No.**
2. Call the entity Anam or Tír? **No.**
3. Assign personality? **No.**
4. Preserve raw experience? **Yes** — chunks derive from messages and never
   modify them; nothing is deleted anywhere in this pipeline.
5. Traceable derived artifacts? **Yes** — `first_message_id`/`last_message_id`
   trace every chunk, including each piece of a split, to its raw messages.
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Partly** — `ChunkingResult` reports
   written/skipped/vectors separately rather than one opaque count.
9. Autonomy more cumulative? **N/A.**
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No** — no schema change. Split pieces take
    consecutive `chunk_index` values, which is why no `sub_index` column was
    needed.
12. Tests? **Yes**, 46, plus a live end-to-end run.
13. Core substrate changed unnecessarily? **No.**
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** The reference was consulted under
    this task's explicit authorisation; nothing was copied. The one thing
    carried across is a *constraint* — hard splits slice in `str` space, never
    bytes — because their comment on it was correct and hard-won.
