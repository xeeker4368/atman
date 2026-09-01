# 2026-09-01 — Seeded dataset for the Phase 1 checkpoint

**Task 1.16 · Tier 1 · Sonnet.**

## Summary

A small, varied corpus — 8 conversations, 2 users, 53 messages, 12 chunks —
written through the real pipeline so the Phase 1 checkpoint has genuine material
to run real queries against once hybrid retrieval (task 1.5) exists.

## Files changed

Created: `anam/ops/seed.py`, `scripts/seed_dataset.py`, `tests/test_seed.py` (21).
Modified: `BUILT.md`.

## Everything goes through the real pipeline

`db.save_message()` then `chunking.finalise_conversation()`. **Nothing
hand-writes a `chunks` row.** Two reasons: the chunks carry genuine provenance
and genuine embeddings, so a query against them exercises what production does;
and the corpus cannot drift from the chunking rules, because it is produced by
them rather than describing them.

## Provenance: single vocabulary, deliberately

Every chunk is `source_type="conversation"` / `source_trust="firsthand"` —
the provisional convention `chunking.py` already uses. **Task 1.7 owns the
vocabulary and has not landed** (Tier 3).

As instructed, I used that convention and am saying so. I also deliberately did
**not** invent a second `source_type` to make the corpus look more varied:
that would be writing 1.7's vocabulary ahead of its design pass, which is
exactly the sort of quiet decision the task is gated for. A test pins the corpus
at one source type, so when 1.7 lands, the test fails and points at the place a
mixed-provenance corpus should be added.

## What the corpus is built to exercise

Retrieval quality is not "did it find the only matching document". The shapes
are chosen so a checkpoint can tell good ranking from lucky ranking:

| Conversation | User | What it exercises |
|---|---|---|
| `fans` | Lyle | short exchange, well under the chunk target |
| `espresso` | Lyle | **near neighbour A** |
| `pourover` | Jodie | **near neighbour B** — different user, adjacent topic |
| `tomatoes` | Jodie | short, distinct topic |
| `notebook` | Jodie | one message over the 5,000-char budget → **sub-chunk splitting** |
| `retrieval` | Lyle | long multi-turn → **multiple groups via the char target** |
| `checkin` | Jodie | nine short turns → **the 8-turn cap decides the boundary** |
| `open-thread` | Lyle | **left open** — trailing group stays unindexed |

The adjacent pair is the important one. A retrieval that cannot separate
espresso from pour-over still looks perfect on distinct topics alone.

## The first three attempts did not exercise what they claimed

Worth recording, because the failure was silent and only measurement caught it:

1. **The long paste was 2,449 characters** — under the 5,000 embedding budget,
   so the splitter never ran. `split chunks : none`.
2. **The "long multi-turn" conversation produced one chunk.** I had assumed
   `chunk_max_turns = 8` counted messages. It counts **turns**, and a turn is
   one or more user messages plus an assistant reply — so 10 messages is 5
   turns, under the cap, and 1,512 characters is under the 2,500 target. One
   group, one chunk. The pipeline was correct; my data was too small.
3. Extending the paste to 4,045 chars still was not enough: splitting applies to
   the packed *group* text, so the paste had to carry its whole turn past 5,000.

Fixed by measuring rather than assuming, and the tests now assert the properties
rather than the intent — `test_the_corpus_exercises_sub_chunk_splitting` fails
loudly with "the splitter is not being exercised at all" if the paste ever falls
back under the budget.

Final shape, verified in a live store:

```
notebook   4 chunks, 3 distinct first_message_id  -> siblings exist (split ran)
retrieval  2 chunks, 3,171 chars                  -> char target
checkin    2 chunks,   949 chars over 9 turns     -> only the turn cap can explain this
open-thread 0 chunks                              -> correctly unindexed
12 chunks total, all source_type=conversation
```

## Live retrieval sanity check, real embeddings

Not a calibration — floors stay unset per BUILD_PLAN, and this is an
observation of ranking only:

```
Q: my espresso shot tastes sour and pulls too fast
   0.244  espresso        <- correct
   0.484  pour-over       <- near neighbour, correctly second
Q: what grinder do I need for filter coffee
   0.380  pour-over       <- correct, and the pair FLIPS
   0.457  espresso
Q: why are my tomato leaves turning yellow
   0.248  tomatoes        <- correct
Q: how should relevance thresholds be calibrated
   0.447  retrieval design <- correct
```

The adjacent pair ranks correctly in *both* directions, which is the thing that
distinguishes a working ranking from a lucky one. That is what this corpus was
built to make answerable.

## Safety

- **Additive only.** Nothing deletes or overwrites.
- **Refuses a store that already holds conversations** unless `--allow-existing`,
  because seeding does not deduplicate. A test asserts the refusal changes
  nothing.
- **Re-seeding reuses existing users** rather than duplicating them.
- **No wipe, reset, clear, drop or truncate surface**, pinned by a test over the
  module's public names. Go-live wipe tooling is its own Tier 3 task and a
  development fixture is not the place for a second implementation of it.

## Tests: 21 new, 233 total

`ruff check .` clean. Embeddings are stubbed with a **content-derived** (not
random) vector so the corpus builds without Ollama; the tests are about the
corpus's shape, not retrieval quality, which is the checkpoint's job.

- `test_the_corpus_exercises_sub_chunk_splitting` — siblings sharing
  `first_message_id`.
- `test_closed_conversations_are_chunked_and_the_open_one_is_not`.
- `test_both_users_own_chunks_so_attribution_can_be_filtered`.
- `test_chunks_are_indexed_for_lexical_search` — FTS populated, or half of
  hybrid retrieval is dark.
- `test_every_chunk_carries_the_provisional_conversation_provenance`.
- `test_seeding_twice_is_refused_by_default` / `test_the_refusal_changes_nothing`.

## Known limitations

- **Eight conversations is a handful, not a corpus.** Enough to judge ranking on
  known pairs; nowhere near enough to calibrate a floor. NOW.md's backlog entry
  on floor calibration is unaffected by this and still needs real usage data.
- **Single source_type**, as above — so anything about provenance competing for
  retrieval slots cannot be exercised yet.
- **The content is invented.** It reads plausibly and the topics are separable,
  but it is not real household conversation and its vocabulary distribution is
  mine, not Lyle's or Jodie's. A ranking tuned to it is tuned to a fiction.
- **English prose only** — no code, no pasted logs, no symbol-dense text. That
  is also the content the history-window estimator is weakest on, so this corpus
  cannot help measure that gap either.
- **No time spread.** Everything is created at seed time, so the structured time
  filter task 1.5 owes has nothing meaningful to filter across.

## Follow-up

- Task 1.5: run the checkpoint queries against this and read the rankings.
- Task 1.7: revisit for a mixed-provenance corpus once the vocabulary exists.
- Consider back-dating timestamps so time filtering has something to bite on.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all. The assistant turns are
   written as plain, unnamed replies with no character or traits assigned.
4. Preserve raw experience? **Yes** — additive only; nothing is edited or
   removed, and re-seeding adds rather than replacing.
5. Traceable derived artifacts? **Yes** — chunks come from the real pipeline, so
   they trace to messages exactly as production chunks do.
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **N/A.**
9. Autonomy more cumulative? **Neutral** — this is disposable test data, which
   `PROJECT.md` says this build's database is throughout.
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No.**
12. Tests? **Yes**, 21, plus a live seed and live retrieval check.
13. Core substrate changed unnecessarily? **No** — `chunking.py` untouched; the
    corpus is produced by it, not alongside it.
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** The reference build's fixtures were
    not consulted; this task does not point at them.
