# 2026-09-08 — Task 2.6: file/artifact ingestion

**Schema portion Tier 3** per `AGENTS.md`'s stop-and-verify list. Design of
record `docs/INGESTION_DESIGN.md`, written and reviewed before any code.
Nothing committed.

## Files changed

Created: `program/artifacts/extract.py`, `program/artifacts/ingest.py`,
`program/api/routes/upload.py`, `tests/test_ingestion.py` (25),
`tests/test_upload_route.py` (7).
Modified: `program/memory/migrations.py` (migration 2), `program/memory/db.py`,
`program/config.py`, `config/defaults.toml`, `program/ops/backup.py`,
`program/api/app.py`, `requirements.txt`, `tests/conftest.py`,
`tests/test_db.py`, `tests/test_migrations.py`, `tests/test_backup.py` (+2),
`NOW.md`, `BUILT.md`.

610 tests pass (was 576); `ruff check` clean. One new dependency: **pypdf**.

## O6: the size limits, derived

**Two limits, because one number cannot bound this.** Measured: the text a file
yields per byte differs by ~55x between formats — the 2.2 MB test PDF gave
39,510 characters (**1.8%** of its bytes), while a plain text file gives ~100%.
A byte limit sized for PDFs would let a 10 MB `.txt` produce ten million
characters.

Measured inputs, all on this machine:

| | |
|---|---|
| pypdf extraction | 2.2 MB in 3.33 s = **0.66 MB/s**, peak 29.7 MB (13.5x file size) |
| embed one 2500-char chunk | **0.075 s** median over 6 runs (0.373 s cold) |
| insert one chunk row | **0.0006 s** (60 inserts in 0.03 s) |

Embedding dominates; writes are noise.

**`max_extracted_chars = 1,000,000`** — the binding limit.
`1,000,000 / 2,500 = 400 chunks`; `400 × 0.075 s = 30.0 s` embedding, plus
`400 × 0.0006 s = 0.2 s` writing.

**`max_upload_bytes = 10,000,000`** — bounds extraction, not indexing.
`10 MB / 0.66 MB/s = ~15 s`; `10 MB × 13.5 = ~135 MB` peak, acceptable beside a
17 GB resident model.

**Worst case: 15 + 30 + 0.2 ≈ 46 s**, inside a ~60 s target for a synchronous
upload someone is waiting on.

Over the character limit, extraction **truncates and records that it did**
rather than refusing: a book-length PDF should be partly ingested with the
truncation on the record, not rejected.

## The capability question: none, and why

**No capability is registered.** `permissions.py`'s rule is that only
capabilities something actually enforces get registered, and an unregistered
name raises rather than defaulting permissive.

There is nothing here to gate. `PROJECT.md` gives Jodie chat, image generation
and creative-space access; decision #17 enumerates what she may *not* do —
settings, research triggering — and uploads are not on it. Both household users
may upload, so `role` draws no line and `artifacts.upload` would be a capability
that always returns True: a gate mounted on nothing, which this build has
refused to build before (the unmounted loopback gate, the placeholder tool).

**Ownership is what is enforced**, the axis `turn.py` uses: the uploader is the
authenticated actor from the token, never anything in the request. A test posts
a file as Jodie and asserts the row is hers.

**Retrieval over the resulting chunks stays unfiltered by actor**, per decision
#20 — an ingested file is memory like any other, and the judgment about
disclosing it sits with the entity at the point of response. No filter was
added and no `artifacts.read` capability registered; both would have answered a
settled question the other way.

**Nothing assumes `source_trust` is firsthand-only** — checked, since the design
introduces `secondhand`. Every consumer was traced: `db.py` stores it,
`reconcile.py` copies it into vector metadata, `retrieval.py` returns it and
never scores it, and `test_source_trust_does_not_change_ranking` rewrites every
chunk's trust and asserts ranking is byte-identical. There is no CHECK
constraint on the column. It is a record, not a lever.

## Migration 2

`artifacts` exactly as I3 specified, plus `chunks.artifact_id` (O2).

**Not added to `working.sql`.** That file is the version 1 definition and stays
that way: a change made in both places would apply twice on a fresh store, and
`ALTER TABLE ADD COLUMN` is not idempotent. `init_databases()` runs `working.sql`
and then the migrations, so a brand-new database and one created yesterday reach
the same schema by the same path — which also means **the migration is exercised
on every fresh store, including every test run**, rather than once in production.
A test asserts `artifacts` never appears in `working.sql`, so the two cannot
both grow it.

`extraction_status` is CHECK-constrained to `extracted | metadata_only | failed`.
That column is the point of the table for task 3.1: "was this file read?" has to
be answerable structurally, not inferred from whether `extracted_text` happens to
be empty — the same distinction `ToolResult` draws between `TIMEOUT` and
`TOOL_ERROR`.

## Eight tests moved, deliberately

The migration broke exactly the guards that exist to notice a schema change, and
each was fixed to assert the *property* rather than the old constant, so
migration 3 does not break them again:

* `test_later_phase_tables_are_absent` — `artifacts` is now expected to exist
  (`research_candidates` still absent). This is the guard doing its job.
* `test_working_has_the_expected_tables` — `artifacts` added.
* `test_init_is_idempotent` — asserted `schema_version` held exactly 1 row; now
  asserts the count does not *grow* across repeated inits, which is what
  idempotence actually means.
* `test_initial_schema_is_recorded_as_version_1` — `>=` rather than `==`: the
  test is about version 1 being recorded, not about it being the latest.
* Three migration-runner tests hard-coded versions 2 and 3 for their fake
  migrations. Those **silently stopped applying** once a real version 2 existed
  (the runner skips a recorded version, so the tests asserted on empty results).
  They now derive `next_free_version()` from the real list.
* `test_the_manifest_records_counts_hashes_and_guarantees` — schema version read
  from `migrations.current_version()` rather than pinned at 1.

## Extraction

pypdf for PDFs, decode for text, metadata-only for everything else (decision
#11). **A PDF with no text layer lands as `metadata_only`, never as `extracted`
with an empty string** — a scan is an image of a page, OCR is out of scope, and
recording it as extracted-but-empty would make a file that was never read look
read. The note names the case and says OCR is what would be needed. Tested with
a PDF built in the test file rather than downloaded, so "has a text layer" and
"has none" are exactly what they claim.

`pypdf` justification is in `requirements.txt` and INGESTION_DESIGN I7: measured
6,022 words against pdfplumber's 2,033 on the same document, because
pdfplumber's default extraction collapses inter-word spacing — and extracted text
feeds FTS5 and the embedder, both of which tokenise on words. BSD-3, 4.1 MB, one
transitive dependency. PyMuPDF excluded on licensing (AGPL-3.0 against a public
repository) before quality was reached.

## Chunking (I4)

Same `chunks` table, same indexes, **its own path**. `chunking.py`'s entry points
are turn-shaped; a document has no turns. So `splitting.py` (already generic) does
boundaries, packing goes to `chunking.target_chars`, and
`db.insert_chunk(conversation_id=None, artifact_id=...)` stores it, with
**embed-before-write** so an unreachable model leaves nothing half-indexed — a
test kills the embedder and asserts zero chunks.

One correction during the build: chunks were initially split to
`embedding.max_input_chars` (5000), producing document chunks twice the size of
conversation chunks that compete for the same retrieval slots — a bigger lexical
target and a more diluted embedding. Now split to the 2500 target, with the
embedding budget as the ceiling neither may cross. A test pins it.

**Retrieval needed no change**, as the design predicted and a test confirms:
`_attach_siblings` already guards on the message-id columns, so file chunks are
skipped rather than mishandled.

## O5: backup now covers uploaded files

`ops/backup.py` captures the artifact directory. Recorded as `best-effort` in
the manifest, honestly — it is a directory copy taken outside the databases'
read lock — but with a note the vector store's does not carry: **this one is not
rebuildable.** Vectors regenerate from the chunks table; an uploaded file exists
nowhere else, so its absence would leave an `artifacts` row pointing at nothing.
Each row records a sha256, which makes that detectable rather than silent.

`tests/conftest.py`'s isolation guard was extended to the artifact directory —
**the same trap the backup directory sprang at task 1.14**: it resolves from its
own config key, so repointing `ANAM_DATA_DIR` does not move it even though its
default sits inside `data/`.

## Security (I6)

* **The client's filename never becomes a path.** `storage_path` is built from a
  generated id; `filename` is display only. A test uploads
  `../../program/integrity/soul.md` and asserts the stored file lands under the
  artifact directory with no `..` in its path.
* **Content type is detected from the bytes**, never the upload header. A test
  uploads a real PDF named `photo.png` declared as `image/png` and asserts it is
  treated as a PDF.

Neither is the governance blocklist — that is its own BUILD_PLAN row, must match
by resolved directory rather than filename, and is **not built here**.

## Verified live

Real server on port 8124, real login, real upload, real embeddings:

```
POST /api/upload  ->  extracted, 96 chars, 1 chunk indexed
POST /api/chat    ->  "The espresso machine needs descaling every two months,
                       as the water in your location is hard."
retrieval          ->  source_type=file  trust=secondhand  conversation_id=None
```

The entity answered from a file it had been handed, with the provenance recorded
correctly. That is the whole path — upload, extract, chunk, embed, index,
retrieve, answer — exercised end to end.

## Left open

`extracted_text` is stored on the row (O3, as directed). O4 is recorded in
`NOW.md`'s backlog: ingested files have no archive presence, so "provenance is
sacred" holds more weakly for them than for conversation messages. O7 (whether
ingestion should also be model-callable) stays endpoint-only for this task.
