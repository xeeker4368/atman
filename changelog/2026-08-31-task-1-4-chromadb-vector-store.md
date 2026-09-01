# 2026-08-31 — Task 1.4: ChromaDB vector store + reconciliation

**Tier 1 · Sonnet · not gated.** Scope as narrowed: FTS5 shipped in task 1.1, so
this is `ChromaVectorStore` plus reconciliation.

## Summary

Vectors are now stored for real. `ChromaVectorStore` implements the protocol
task 1.3 defined, it is the default, and reconciliation repairs any chunk that
has a row but no vector — including every chunk written under `NullVectorStore`.

## Files changed

Created: `anam/memory/reconcile.py`, `scripts/reconcile_vectors.py`,
`tests/test_vectors.py` (24).
Modified: `anam/memory/vectors.py` (ChromaVectorStore), `anam/api/app.py`
(startup warm), `tests/conftest.py` (guard armed), `requirements.txt`,
`BUILT.md`.

**New dependency: `chromadb` 1.5.9.** It was not installed — Phase 0's
`requirements.txt` was deliberately scoped to what Phase 0–1 needed at the time,
and Chroma wasn't in it. Local and on-disk; no server process.

## How it was made the default — a deviation, stated

The task said to call `vectors.set_vector_store()` with a real instance at
startup. **I did something slightly different and want it on the record.**

`get_vector_store()` now *constructs* ChromaDB on first use and caches it per
resolved path. Startup wiring alone would mean any entry point that forgot to
call it — a script, a CLI command, a background pass — silently got
`NullVectorStore` and wrote no vectors while reporting success. That is the
precise failure this project keeps designing against, and making it depend on
every future caller remembering a bootstrap call seemed like the wrong place to
put the guarantee.

`set_vector_store()` still exists and is still how an override happens; tests
use it, and `None` restores the default.

The app *also* warms the store at startup — but via FastAPI's `lifespan`, not
`on_event`, which is deprecated in this version (checked; it emits a
`DeprecationWarning`). The warm is not what makes Chroma the default; it exists
so a broken or unwritable store fails at boot rather than partway through a
conversation.

**Caching is per resolved path, not global.** A single cached client would have
been reused across tests pointing at different data directories — the same class
of bug as a module-level constant.

## Dimension handling (item 4)

Verified directly against the library rather than assumed. **Chroma has no
explicit dimension setting**: a collection infers its width from the first
vector written and enforces it thereafter.

```
mismatch raises: InvalidArgumentError
  Collection expecting embedding with dimension of 768, got 512
```

So it fails loudly — it does not truncate or silently drop. But the inference
behaviour has a sharp edge: a wrong-width vector arriving *first* would define
the collection wrongly, and every correct vector afterwards would be rejected
against it.

`ChromaVectorStore.upsert()` therefore checks the width against
`embedding.expected_dimension` **before** Chroma sees the vector, so the
collection can only ever be defined by a vector of the expected width. That is a
second line behind `ollama.embed()`'s existing assertion, and it earns its place
because a vector can reach the store from somewhere other than a fresh embed
call — a reconciliation pass, a future backfill.

Both are tested: ours raises `VectorDimensionError` naming got/expected/the
config key, and a separate test bypasses our guard to confirm Chroma itself
still refuses.

## Two bugs found during the work

**A real one in my code.** Chroma rejects an *empty* metadata dict —
`ValueError: Expected metadata to be a non-empty dict` — while omitting metadata
entirely is fine. My `upsert()` stripped `None` values and could hand Chroma
`{}`. Four tests hit it. Verified the distinction against the library, then fixed
it to pass `None` when nothing survives the strip. A chunk with no metadata is
legitimate and must not crash.

**One in my test fixture, which had been hiding a worse problem.** The
`seeded_with_null_store` fixture called `monkeypatch.undo()`. `monkeypatch` is
shared across every fixture in a test, so that also reverted the `fake_embed`
patches — meaning the reconciliation tests were quietly calling the **real**
embedding model, and the call-count assertions were measuring nothing. They
passed because Ollama happened to be running. Replaced with
`set_vector_store()` / `set_vector_store(None)`, which touches only what it
means to.

Worth recording: a test that passes for the wrong reason is worse than one that
fails, and this one would have kept passing indefinitely.

## Reconciliation, run for real (item 6)

**There is no `working.db` on this machine.** No `data/` directory exists — the
isolation guard has been working, so every development chunk to date has lived
in a temp directory and been discarded. There is no pre-existing backlog to
reconcile, and saying otherwise would be inventing one.

So I built the equivalent: chunks written through the real pipeline under
`NullVectorStore`, which is exactly the shape of the backlog this task inherits.
Real script, real `nomic-embed-text` embeddings.

```
SEED (NullVectorStore active)
  conversation 0: chunks_written=3 vectors_indexed=0
  conversation 1: chunks_written=3 vectors_indexed=0
  total chunk rows: 6

DRY RUN
  vectors before : 0
  chunks checked : 6
  already present: 0
  missing vectors: 6
  repaired       : 0 (dry run)
  vectors after  : 0

REAL RUN
  vectors before : 0
  missing vectors: 6
  repaired       : 6
  vectors after  : 6

SECOND RUN (idempotence)
  vectors before : 6
  already present: 6
  missing vectors: 0
  repaired       : 0
  vectors after  : 6
```

Then a real semantic query against the reconciled store:

```
query 'small boats in the harbour' ->
  dist=0.3884  'Lyle: conv1 question 2 about the harbour detail...'
  dist=0.3906  'Lyle: conv0 question 2 about the harbour detail...'
  dist=0.3936  'Lyle: conv1 question 0 about the harbour detail...'
off-topic nearest distance: 0.6578
```

On-topic 0.388–0.394 against off-topic 0.658. **Not a calibration** — six chunks
of synthetic text is nowhere near enough, and BUILD_PLAN requires floors to stay
unset until there is real data. Recorded as an observation for whoever does
calibrate.

**When a real `data/` does exist, the command is the same:**
`python -m scripts.reconcile_vectors [--dry-run] [--limit N]`.

## The isolation guard is now armed (task 1.1's follow-up)

`tests/conftest.py` captures the real data directory *and* the real ChromaDB
path at import, records whether each already existed, and fails the session if
either was created while the suite ran. Checking pre-existence rather than mere
presence means an operator's real store is not mistaken for a leak.

`isolated_data_dir` now also resets the store cache on entry and exit.

Confirmed after a full run: no `data/` directory in the repo.

## Tests

`ruff check .` clean. Full suite **133 passed** (109 before, 24 new). All 24
ran — **0 skipped**, checked by grep rather than assumed.

Real ChromaDB on disk throughout; no mocked store anywhere. Notable:

- `test_live_chunking_writes_real_vectors_end_to_end` — real chunking, real
  embeddings, real Chroma, then a real semantic query whose top hit is one of
  the chunks just written, then a reconciliation dry run confirming nothing is
  outstanding. This is the test that proves the seam connects to something that
  works, rather than that the seam exists.
- `test_wrong_dimension_is_rejected_before_chroma_sees_it` and
  `test_chroma_itself_also_rejects_a_mismatch` — both layers.
- `test_persists_across_client_instances` — reopening finds the vector.
- `test_reconcile_is_idempotent`, `test_dry_run_reports_without_changing_anything`.
- `test_embedding_failure_aborts_rather_than_reporting_partial_success`.

Task 1.3's existing tests are unchanged and still pass, as intended — they inject
fakes and were never coupled to the implementation.

## Known limitations

- **Reconciliation is O(N) round trips.** `find_chunks_without_vectors()` calls
  `store.has()` per chunk. Fine against a local store at this scale; if chunk
  counts reach six figures it wants batching.
- **No reverse reconciliation.** Nothing detects a *vector* with no chunk row.
  By design — the write ordering makes it impossible — but if it ever happened it
  would be invisible.
- **Cosine distance is fixed at collection creation.** Changing it means
  rebuilding the collection.
- **`chromadb` emits a `DeprecationWarning`** about `asyncio.iscoroutinefunction`
  under Python 3.14. Library-internal; nothing to do but note it.
- **The startup warm only covers the FastAPI app.** Scripts construct the store
  lazily on first use, so a broken store surfaces at first write rather than at
  script start.
- **Still no caller in production.** Chunking is wired to nothing until task 2.2.

## Follow-up

- Task 1.5 consumes `ChromaVectorStore.query()` for the vector leg of hybrid
  retrieval, and owns floor calibration.
- Go-live wipe (Phase 10) must delete the Chroma directory alongside both
  databases — `COLLECTION_NAME` is the single constant it keys on.
- Batching for reconciliation if scale ever demands it.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes** — vectors are derived; nothing mutates chunks
   or messages.
5. Traceable derived artifacts? **Yes** — every vector is keyed by chunk id and
   carries conversation/user/index metadata.
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Partly** — `ReconcileResult` separates
   checked / already-present / missing / repaired.
9. Autonomy more cumulative? **N/A.**
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No** — no schema change.
12. Tests? **Yes**, 24, against a real store, plus a real end-to-end run.
13. Core substrate changed unnecessarily? **No** — task 1.3's pipeline is
    untouched, which was the point of the protocol.
14. External dependencies added? **Yes — `chromadb` 1.5.9**, planned in
    `PROJECT.md`'s stack, added here because this is the task that needs it.
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes** — the reference build's Chroma layer
    was not consulted; this task does not point at it.
