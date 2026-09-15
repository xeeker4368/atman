# INGESTION_DESIGN.md — file/artifact ingestion (task 2.6)

**Status: PROPOSAL. Nothing here is built.** The schema portion is Tier 3 by
`AGENTS.md`'s stop-and-verify list ("Database schema... and any migration"),
which the list says is kept in sync with BUILD_PLAN's tiers regardless of what
that row's own tier column says. Design first, review, then code — the pattern
`docs/AUTH_DESIGN.md` followed.

Seven questions are marked **OPEN** and need answers before implementation. Two
of them (O1, O2) block the schema itself.

---

## I1. The table does not exist, and no shape is specified anywhere

Verified against the live store rather than the docs — `working.db` holds
`chunks`, `chunks_fts*`, `conversations`, `messages`, `schema_version`,
`settings`, `supersedes`, `users`. No `artifacts`. `archive.db` holds `messages`
and `users` only.

`docs/DB_SCHEMA.md` is explicit that the shape was deliberately left undesigned:

> **`artifacts`** — belongs to Phase 2 (file/PDF ingestion) and Phase 4
> (generated images). It was in an earlier draft of this schema and was removed
> at the Phase 1 checkpoint: nothing in Phase 1 depends on it, and its shape
> should be driven by a real ingestion design rather than guessed a phase ahead.

`BUILD_PLAN.md` says only *"File/artifact ingestion: upload endpoint, text + PDF
content extraction (decision #11), other types metadata-only"*. So this document
is the "real ingestion design" that sentence defers to. `MIGRATIONS` in
`program/memory/migrations.py` is empty; this would be **version 2**.

## I2. Which database

**`working.db` only.** Not a proposal so much as a rule already written down —
`migrations.py`: *"The archive is never migrated. Its shape is frozen... If a
change seems to require altering the archive, that is a signal the field belongs
in working.db instead."*

The consequence is worth stating rather than leaving implied: **an artifact row
does not get the archive's append-only protection.** The durable original is the
file on disk, and the extracted text is reproducible from it. That is a weaker
guarantee than conversation messages have, and it is the price of not touching a
frozen schema. **O4** below asks whether that is acceptable.

## I3. Proposed `artifacts` table

```sql
CREATE TABLE IF NOT EXISTS artifacts (
    id                TEXT PRIMARY KEY,
    -- Who uploaded it. NOT NULL: an artifact with no uploader is not a state
    -- this system should be able to represent.
    user_id           TEXT NOT NULL,
    -- The name as supplied by the client. UNTRUSTED and never used to build a
    -- path — see I6. Kept because it is what the person will call the file.
    filename          TEXT NOT NULL,
    -- Detected from content, not taken from the client's Content-Type header,
    -- which is attacker-controlled. See I6.
    content_type      TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL,
    -- Of the stored bytes. Same role as chunks.text_sha256: detects a file
    -- changing underneath the rows derived from it, and makes re-upload of an
    -- identical file recognisable.
    sha256            TEXT NOT NULL,
    -- RELATIVE to config.artifact_dir(), never absolute — an absolute path
    -- breaks the moment the store moves, and backups are restored elsewhere.
    storage_path      TEXT NOT NULL,
    -- Decision #10 already uses this word for creative writing; Phase 4 adds
    -- generated images. Deliberately NOT a CHECK constraint, matching
    -- chunks.source_type's reasoning: adding a kind should not need a migration.
    artifact_type     TEXT NOT NULL,
    -- Whether the content was actually read. CHECK-constrained because the
    -- vocabulary is closed and small, the way users.role and
    -- settings.value_type are.
    extraction_status TEXT NOT NULL
        CHECK (extraction_status IN ('extracted', 'metadata_only', 'failed')),
    -- NULL unless extraction_status = 'extracted'. See O3.
    extracted_text    TEXT,
    -- Why, when status is 'failed' or 'metadata_only'. A file that was not read
    -- must be able to say why it was not read.
    extraction_note   TEXT,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_user   ON artifacts(user_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_sha256 ON artifacts(sha256);
CREATE INDEX IF NOT EXISTS idx_artifacts_type   ON artifacts(artifact_type);
```

**No `updated_at`.** An ingested file is immutable: re-uploading produces a new
row. `chunks` carries both because a chunk can be re-derived; an artifact cannot
be re-derived into something different without being a different artifact.
Flagged rather than assumed — say if you want it for symmetry.

**`extraction_status` is the point of the table for task 3.1.** "Was this file
read?" has to be answerable structurally, not inferred from whether
`extracted_text` happens to be empty — the same reason `ToolResult` distinguishes
`TIMEOUT` from `TOOL_ERROR`. An empty extraction and a refused extraction are
different claims.

## I4. Where extracted text feeds retrieval

**It is chunked into the existing `chunks` table and indexed exactly like
conversation text — but it needs its own chunking path.**

The store already anticipates this. `working.sql` on `chunks.conversation_id`:

> NULL for chunks that do not come from a conversation (an ingested file, a
> piece of creative writing). Conversation chunks always carry it.

So the destination needs no change. What does *not* transfer is the chunker:
`chunking.py`'s two entry points are turn-shaped — turn-preserving boundaries, an
8-turn coherence cap, `first_message_id`/`last_message_id` ranges. A document has
no turns, and forcing one through that path would mean inventing fake messages.

Proposed instead, reusing what already exists:

1. `splitting.py` — already generic (paragraph → line → sentence → whitespace,
   hard cut last, all in `str` space). It is the right tool unchanged.
2. Pack split pieces to `chunking.target_chars` (2500), the same target
   conversations use, so a document chunk and a conversation chunk are
   comparable at retrieval time.
3. `db.insert_chunk(conversation_id=None, ...)` — the signature already accepts
   it.
4. Embed and `vectors.upsert()` per chunk, mirroring `chunking.py`'s
   embed-before-write ordering so a failure leaves the store untouched.
5. FTS5 needs nothing: it is an external-content table kept in sync by triggers.

**Retrieval itself needs no change**, and this was checked rather than assumed:
`retrieval._attach_siblings()` already guards `first_message_id is not None and
conversation_id is not None`, so file chunks are skipped by the sibling logic
instead of misbehaving. Ranking, RRF, floors and the time filter all operate on
`chunks` and are indifferent to where a chunk came from.

## I5. Content-type routing (decision #11)

Decision #11: *"text files and PDFs get full content extraction and indexing.
Office documents, OCR, image, audio, video stay metadata-only / deferred."*

| Type | Treatment |
|---|---|
| `text/*`, `application/json`, `text/markdown`, `text/csv` | decode, extract in full |
| `application/pdf` | extract with pypdf (I7) |
| Office formats, images, audio, video, unknown | `metadata_only`, no text, `extraction_note` says why |

**A PDF with no text layer** (a scan) extracts to nothing. It must land as
`metadata_only` with a note naming the reason, not as `extracted` with an empty
string — OCR is explicitly out of scope, and silently recording an empty
extraction would make the file look read.

## I6. Two security properties, stated because they are cheap to get wrong

* **The client's filename never becomes a path.** `storage_path` is a generated
  id, and `filename` is kept only for display. `../../soul.md` as a filename must
  be inert, not sanitised-and-hoped.
* **`content_type` is detected from content, not trusted from the upload
  header**, which the client controls. This is the same reasoning as `web_fetch`
  validating the resolved address rather than the hostname.

Neither is the governance blocklist, which is **its own BUILD_PLAN row** (task
2.7, "`soul.md`, project docs can't be ingested as normal memory") and is not
designed here. Note BUILD_PLAN's own instruction that it match by **resolved
directory**, not an enumerated filename list.

## I7. PDF library: **pypdf**

Measured on this machine rather than argued from reputation. Test document:
*Attention Is All You Need* (arXiv 1706.03762), 2.2 MB, 15 pages, real
multi-column layout with tables.

| | pypdf 6.18.0 | pdfplumber 0.11.10 |
|---|---|---|
| time | **3.33 s** | 3.71 s |
| peak memory | **29.7 MB** | 94.5 MB |
| characters | 39,510 | 35,525 |
| **words** | **6,022** | **2,033** |
| installed footprint | **4.1 MB** | 53 MB |
| runtime deps | `typing_extensions` | `pdfminer.six`, `Pillow`, `pypdfium2`, `cryptography`, `cffi`, … (8 packages) |
| license | **BSD-3-Clause** | MIT (absent from PyPI metadata) |

**The word count is the decision, not the timing.** pdfplumber's default
extraction collapses inter-word spacing on this document:

> `Providedproperattributionisprovided,Googleherebygrantspermissionto`

against pypdf's:

> `Provided proper attribution is provided, Google hereby grants permission to`

That is disqualifying *for this project specifically*, because extracted text
goes to two consumers that both tokenise on words: FTS5/BM25, and the embedding
model. A probe for `"dominant sequence transduction"` is found in pypdf's output
and **not** in pdfplumber's. A document whose words are glued together is
lexically unsearchable and embeds poorly — it would look ingested and behave as
if it were not, which is this project's recurring failure shape.

pdfplumber's `x_tolerance` can be tuned to fix spacing, but that is a
per-document calibration this build has no basis to set, in exchange for 13x the
install footprint. Its strengths — table structure, word bounding boxes, visual
layout — are things nothing here consumes.

**PyMuPDF is excluded on licensing, not quality.** It is **AGPL-3.0 or
commercial**, and `github.com/xeeker4368/atman` is a **public repository**
(verified: the GitHub API returns 200 for it). Linking AGPL code into a publicly
distributed codebase imposes AGPL terms on the project. Its 26 MB wheel is a
distant second objection. It was not benchmarked, and this document does not
claim to know whether it extracts better — the licence question settles it before
quality is reached.

**Cost of the choice, stated plainly:** one new runtime dependency, 4.1 MB, one
transitive package, BSD-3. `requirements.txt`'s policy — *"Later phases add their
own, each with a note in the changelog entry saying why it was needed"* — is
satisfied by this section.

---

## Open questions

**O1 — `source_type` and `source_trust` values for ingested files. BLOCKING.**
`working.sql` says the vocabulary is *"owned by `program/memory/provenance.py`"*.
**That module does not exist** — verified. `chunking.py` hardcodes
`SOURCE_TYPE = "conversation"` / `SOURCE_TRUST = "firsthand"`, and `BUILT.md`
records that task 1.7 owns the vocabulary and has not landed. Ingestion needs a
second pair (`file`? `document`? and a trust that is plainly not `firsthand` —
an uploaded document is not the entity's own experience). Task 1.7 is **Tier 3,
Opus**. Either it lands first, or this task defines two values and 1.7 inherits
them. Not a call to make silently.

**O2 — `chunks.artifact_id`. BLOCKING, and a migration on an existing table.**
Nothing currently links a chunk to the file it came from: `conversation_id` is
NULL and the message-id columns are meaningless for a document. Without a link,
a retrieved document chunk cannot say which file it is from, which contradicts
provenance being returned (D6). The proposal is to add `artifact_id TEXT`
referencing `artifacts(id)`, symmetric with `conversation_id`. `AGENTS.md` is
explicit that this needs asking first: *"a column decision inside an
already-approved Tier 3 task still goes up before it is coded, not disclosed
afterward."*

**O3 — Store `extracted_text` on the artifact row, or only in `chunks`?**
Storing it duplicates text that also lives in chunks. Not storing it means the
canonical pre-chunk text exists only as a derived, split form — and "raw
experience is never edited" argues for keeping the original. Recommendation:
store it, treating it as the document's equivalent of `archive.messages`. The
duplication is bounded by the upload size limit.

**O4 — Is a working-db-only artifact acceptable?** I2's reasoning is sound but
the result is that ingested files sit outside the archive's guarantee. If not
acceptable, the alternative is a frozen-schema change, which `migrations.py`
says is a signal to reconsider rather than a thing to do.

**O5 — Uploaded files are not covered by backup.** Verified: `ops/backup.py`
captures `working.db`, `archive.db` and the ChromaDB directory, and nothing else.
An artifacts directory would not be captured, so a restore would produce rows
pointing at files that no longer exist. Either backup grows a fourth artifact, or
the gap is recorded deliberately. Related: `config.workspace_dir()` is configured
and **used by no code today**, so where uploads live is genuinely open —
`data/artifacts/`, `workspace/`, or its own `paths.artifact_dir`.

**O6 — Upload size limit.** None proposed yet; it should be derived rather than
guessed, the way the tool caps were. It bounds `extracted_text`, the number of
chunks one upload creates, and how long ingestion holds the write path.

**O7 — Is ingestion a tool, an endpoint, or both?** BUILD_PLAN says "upload
endpoint". The entity calling `web_fetch` on a PDF is a different path that
currently reports metadata only. Whether ingestion is also model-callable — and
whether a fetched PDF should become an artifact — is unresolved. Recommendation:
endpoint only for this task, since a person uploading is the case decision #11
describes.
