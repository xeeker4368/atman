# Artifact indexing: embed everything, then write once

Date: 2026-09-24 · merged-queue item 2 · diagnostic finding #12 · Tier 3 (chunking
pipeline design + a new write path in `program/memory/db.py`), plan approved before
implementation

## What was wrong

`indexing.index_text()`, the one indexing path shared by uploads, generated images and
creative writing, looped **embed → `db.insert_chunk` → vector upsert per chunk**, and
each `insert_chunk` opened its own transaction. A failure at chunk N left chunks 0…N-1
committed. The `artifacts` row is written *before* indexing, with
`extraction_status = 'extracted'` and the full `extracted_text`, so the row claimed a
success the chunks did not deliver.

It could not recover:

- a re-upload hits the sha256 duplicate check, which returns the existing row and
  never re-indexes;
- `reconcile_vectors.py` repairs missing **vectors** for rows that exist, not rows
  that were never written;
- nothing compares `extracted_text` against `get_artifact_chunks()`.

The docstring promised the opposite: *"Embedding precedes every write, so an
unreachable model leaves nothing behind rather than a half-indexed artifact."* It is
reachable today: `creative_write` accepts up to 1,000,000 characters against a
2,500-character chunk target, so a 9,000-character piece is four chunks.

## What changed

- **`indexing.index_text()`** embeds every chunk first, then writes all rows in one
  transaction, then upserts the vectors.
- **`db.insert_chunks(rows)`**: new writer, one `transaction()`, `@retry_on_locked`,
  so the `test_every_write_in_db_carries_the_retry` enumeration covers it. An empty
  batch returns without opening a transaction.
- **`db._insert_chunk_row()`**: the INSERT, factored out so `insert_chunk` and
  `insert_chunks` share one statement. `insert_chunk` behaves exactly as before.
- **Docstrings corrected** in `indexing.py` and in `chunking.py`'s "Ordering and
  failure" section. That one said *"a raise leaves nothing written at all"*, which
  holds per chunk only. The chunking.py change is **docs only**. Last session's
  recommendation was to correct it here and not wait for a conversation-path fix.
  "Proceed" did not rule on that explicitly, so I took the recommendation. It is one
  paragraph and easy to revert.
- **BUILT.md**: three claims corrected (chunking pipeline, A3 image indexing, file
  ingestion) and a new entry under Artifacts.

## Why artifacts only

The partial unique index on `(conversation_id, chunk_index)` (`working.sql:143-145`,
`WHERE conversation_id IS NOT NULL AND chunk_index IS NOT NULL`) is the per-row arbiter
`insert_chunk` documents between two concurrent conversation writers. Artifact chunks
carry `conversation_id = NULL` and sit outside that index, so batching them changes no
arbiter. Batching conversation chunks would turn a one-row collision into a whole-batch
one. That changes a concurrency guarantee, so it needs its own review. The conversation
path's behaviour is unchanged.

## Tested

Five new tests in `tests/test_ingestion.py`, on a five-chunk fixture with a guard test
asserting it really is five chunks:

| test | failure injected | asserts |
|---|---|---|
| embedding failure mid-artifact | `embed` raises on call 3 | 0 chunk rows |
| write failure mid-artifact | `_insert_chunk_row` raises on row 3 | 0 chunk rows |
| vector-store failure | `upsert` raises on call 3 | 5 rows, 3 without vectors; `reconcile_vectors()` repairs all 3 |
| empty batch | `transaction` replaced by a raiser | no transaction opened |

**Proven to bite:** restoring the previous `indexing.py` from `HEAD` fails all three
failure tests (3 failed, 27 passed). The existing
`test_an_unreachable_embedder_leaves_no_chunks` could never catch this. It fails on the
first chunk, and both orderings leave nothing there.

Full suite **1175 passed, 2 skipped** (was 1170/2). `ruff check .` clean.

## Known limitations

- **The vector store is still not atomic with the rows.** Chroma cannot join a SQLite
  transaction, so a failed upsert after the commit leaves rows without vectors. That
  state is recoverable, and a test drives the repair. But nothing runs
  `reconcile_vectors` automatically. The fix turns the unrecoverable failure into the
  recoverable one; it does not remove failure.
- **An artifact whose indexing fails outright is still `extracted` with zero chunks**,
  and nothing re-indexes it, because the duplicate check still short-circuits a
  re-upload. No new `extraction_status` value was added: that is a schema change and
  would stack on queued item 8 (migrations 5/6 not atomic). No recovery queue was
  added either, since the conversation path's recovery queue already has nothing
  draining it.
- **Reconcile writes thinner metadata for artifact chunks.** `reconcile_vectors`
  upserts with `conversation_id`, `user_id`, `chunk_index`, `source_type` and
  `source_trust`, but not `artifact_id`. The repaired vector is findable, but its Chroma
  metadata differs from a first-time write. Retrieval reads provenance from the SQLite
  row, so I know of no behavioural effect, but I have not verified that.
- **Memory:** every vector is held until the write. At the 1,000,000-character ceiling
  that is about 400 × 768 floats, roughly 2.4 MB.

## Follow-up

- The conversation path's per-chunk transactions, if they should ever be batched, as
  their own Tier 3 change.
- Queued item 22a (`ingest()`'s dead `artifact_type` parameter) touches the same call
  chain (`ingest._index_text` → `indexing.index_text`) and was deliberately left alone
  here.
