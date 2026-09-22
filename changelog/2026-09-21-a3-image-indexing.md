# 2026-09-21 — A3: generated images are findable by retrieval, plus the standing directory item

**Tier 1.** Nothing committed. **Stops here for review**; B4 is next and deliberately
not batched into this diff.

## Files

Created: `program/artifacts/indexing.py`, `tests/test_directories.py` (6).
Modified: `AGENTS.md`, `program/artifacts/ingest.py`, `program/artifacts/generated.py`,
`program/engine/prompt.py`, `tests/test_image_generate.py` (+6, now 32),
`tests/test_ingestion.py`, `tests/test_blocklist.py`, `tests/test_upload_route.py`,
`BUILT.md`.

**1,108 tests pass** (was 1,096), 2 skipped, `ruff` clean.

## The standing item, and a guard so it is not needed a fourth time

`AGENTS.md` gains **"Adding a runtime directory"**: any new directory resolved from its
own config key gets `backup.py` coverage and test-isolation coverage **in the same task
that introduces it**. It names the three occurrences and, more usefully, the mechanism
they share — *a new directory starts outside every existing guard by default*, because
each resolves from its own key, so repointing `ANAM_DATA_DIR` does not move it.

**Beyond the ask, because a doc line is exactly what the last three occurrences already
had in spirit:** `tests/test_directories.py` enumerates every zero-argument `config`
accessor returning a `Path` and fails on one that is neither covered nor **explicitly
exempted with a written reason** — the `test_every_write_in_db_carries_the_retry`
pattern applied to directories. Exemptions are real answers (`backup_dir` is the
destination; `config_dir` holds the signing key and must never be copied; `data_dir` is
covered by its contents through SQLite's backup API rather than as a directory), and a
test asserts each exemption names a live accessor and gives a reason of more than a
few words.

**Proven by adding a fourth directory.** A throwaway `reflection_dir()` accessor with
no coverage fails three of the six tests, with the message naming the accessor, the
expected env var, the file to edit and the `AGENTS.md` section:

```
runtime directories not isolated:
    reflection_dir() — expected ANAM_REFLECTION_DIR in isolated_data_dir
Add the setenv/delenv pair in tests/conftest.py, or add an entry to
ISOLATION_EXEMPT here saying why it needs none. See AGENTS.md, 'Adding a
runtime directory'.
```

One guard on the guard: a test asserts the enumeration found at least five
directories, because an enumeration that silently finds nothing makes every test above
it pass by vacuity — which is the failure mode this file exists to prevent.

## A3 itself

**One indexing path, not two.** `ingest._index_text` was correct and
upload-specific — it hardcoded `file`/`secondhand`. Extracted to
`program/artifacts/indexing.py`, parameterised by artifact kind, and **`ingest.py` now
calls it too**, which is what the Cluster A brief asked for ("the same
`_index_text`-style path") rather than a second implementation. `source_type` and
`source_trust` come from the kind registry, so an artifact cannot be indexed under
provenance that disagrees with its own row — a test asserts that directly.

The refactor is behaviour-preserving for uploads: 25 ingestion tests pass unchanged.
*It did move where the embedding call lives, so four test files patched
`ingest.ollama.embed` and now patch `indexing.ollama.embed`. That is a real
consequence of the extraction, not a cosmetic rename — the patch has to sit where the
call is made.*

**The prompt is what gets indexed** (Q14), chunked and embedded after the row is
written — `ingest()`'s order, because the row is the record and chunks are derived and
rebuildable by re-indexing, while chunks pointing at an artifact_id no row claims are
not. `StoredImage` now reports `chunks_written` and `chunk_ids`.

**Embedding still precedes every chunk write**, so an unreachable model leaves the
image stored and unindexed rather than half-indexed. A test kills the embedder and
asserts exactly that: file and row survive, zero chunks.

## The rendering question A3 raises, and the answer

An image chunk's text is the prompt. Rendered as it stood, a retrieved image looked
like this:

```
[record 1 · 2026-09-21T16:57:00]
a copper kettle on a slate worktop, morning light
```

**Which reads as something someone said.** The model would be handed a description
with no way to know it describes a picture — and it has no vision, so treating that
text as an observation is precisely the fabrication the gate exists to catch.

Fixed at **presentation**, on task 1.3's rule and 3.5's precedent — putting a label in
the indexed body would feed "generated image" into the embedding and the BM25 index,
and every image would match a query mentioning images:

```
[record 1 · generated image, prompt only · 2026-09-21T16:57:00]
a copper kettle on a slate worktop, morning light
```

`prompt only` is the load-bearing half: it says the text **is** the prompt, not a
description of how the image looks.

**Conversation chunks render byte-identically** — no label — so nothing any prior turn
or test saw has shifted. A test pins that for both `"conversation"` and `None`, and
another asserts the label never enters `chunks.text`. **Proven to bite:** neutering the
label fails 2 tests.

`creative_writing` and `file` get labels too, so B4 inherits it and an uploaded
document stops being indistinguishable from a conversation.

## Verified

A generated image is **retrievable by its prompt** through real FTS5, real RRF and the
real renderer (only the embedding substituted): `search("copper kettle slate")`
returns the image chunk with `source_type == "generated_image"`, and the rendered
block carries the label.

## Known limitations

- **One chunk per image in practice.** A prompt is short, so splitting and packing
  never trigger. They are inherited rather than exercised.
- **No re-indexing tool.** If the embedding model changes, conversation chunks are
  rebuildable via `scripts/reconcile_vectors.py`; an artifact's chunks are equally
  rebuildable in principle, but nothing offers the command.
- **The label vocabulary is a fixed dict**, so a future `source_type` renders
  unlabelled and silently reads as a conversation. Task 1.7 owns that vocabulary and
  has still not landed; when it does, the two should be reconciled.
- **Nothing dedupes identical prompts** — two images of the same prompt produce two
  artifacts and two near-identical chunks competing for the same retrieval slots.
  Deliberate (they are different images) but worth watching.

## Next

B4 — the creative-writing tool. It inherits `workspace/writing/`, the isolation guard,
`indexing.index_text`, and the `creative_writing` label.
