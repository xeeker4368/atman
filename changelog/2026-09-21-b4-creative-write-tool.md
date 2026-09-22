# 2026-09-21 — B4: the creative-writing tool

**Tier 1.** Decision #10's mechanism, minus the two halves that are not a tool's to
give. Nothing committed. **Stops here for review.**

## Files

Created: `program/artifacts/writing.py`, `program/tools/creative_write.py`,
`tests/test_creative_write.py` (23). Modified: `program/tools/catalog.py`,
`tests/test_tools.py`, `BUILT.md`.

**1,131 tests pass** (was 1,108), 2 skipped, `ruff` clean.

## One structural difference from `image_generate`

**This keeps writing; it does not produce it.** An image is made by a separate program
and the tool fetches the result. Creative writing is made by the entity — the model
composes the piece in its own output and calls this to persist it. So:

* the parameter is the **text**, not a prompt;
* there is **no model call anywhere in this path** (the live run below took 0.05 s);
* `extracted_text` and the bytes on disk are the **same content**, unlike an image
  (indexed text is the prompt) or a PDF (indexed text is what could be read out);
* `extraction_status` is therefore **`extracted`**, not `metadata_only` — the content
  genuinely is available, trivially, because the system wrote it.

`title` is optional. A missing one is derived from the **opening words**, deliberately
dumb: generating a title would mean a second model call producing a paraphrase
presented as the work's own title, which is a small fabrication of the kind this build
keeps refusing.

## What decision #10 asks for, and what is deliberately absent

Three absences, each tested as an absence rather than left implicit:

* **No gate.** The lowest-risk category in the build — no external effect, nothing
  irreversible — so nothing inspects, scores or filters what was written. A test stores
  a piece a content check would plausibly object to and asserts it is kept verbatim.
* **No capability and no `enabled` flag.** Both household members may do this (#17), so
  `role` draws no line and a capability would always return True — the *"gate mounted on
  nothing"* `ingest.py` refused for uploads. And unlike image generation there is no
  external service to be unavailable, so an `enabled` flag would describe nothing. A
  test asserts `CAPABILITIES` is still exactly the two settings entries.
* **No retrieval-time exclusion.** Q9: "private" means *not proactively announced*, not
  *hidden*. Retrieval stays unfiltered per decision #20, and a test asserts a stored
  piece **is** indexed and findable — the opposite of what "private" might be
  mistaken for.

**Refusal is not here either**, and could not be: the entity being able to decline to
share a piece is a `soul.md` values statement (decision #10, `GUIDANCE.md`) and its own
Tier 3 thread. A tool cannot grant it and this one does not pretend to.

The result text says what the storage actually means rather than overclaiming:
*"It is kept, not published. Nothing shows it to anyone unless it comes up."*

## Reuse rather than reimplementation

`writing.py` follows `generated.py` following `ingest.py`: sharded path from the
generated id, sha256 of the stored bytes, bytes-then-row-then-chunks ordering, the root
from `kinds.root_for()` (`workspace/writing/`), and indexing through the shared
`indexing.index_text`. No new directory constant, no second indexing path, no second
sharding scheme.

The size ceiling **reuses `ingestion.max_extracted_chars`** rather than inventing a
second constant: what it bounds is the same work in both cases — characters to split,
pack and embed — and a separate value would drift from it. Named for ingestion because
that is where it was first needed, and the comment says so.

## `user_id` records whose record it is, not who wrote it

`source_trust = firsthand` says the entity wrote it. `user_id` is the person present
(Q2b). **That distinction is sharper here than for an image**, where the person at
least asked for the thing — a piece of writing is the entity's own work, filed in the
record of whoever was there. It is what Q2b decided and it is consistent, but it is the
kind of thing worth seeing stated before B5 makes the no-person case real. A test
asserts the attribution and names the distinction in its docstring.

## Verified live

A real turn: *"Write a very short piece of prose… and keep it."* The model wrote five
sentences, **titled it itself** (`The Unattended Kettle`), and called the tool, which
stored 418 bytes to `workspace/writing/ad/ad492a30…` as
`the-unattended-kettle-ad492a30.md`, `extraction_status = extracted`, one chunk.
`retrieval.search("kettle hob")` returned it, rendered as:

```
[record 1 · creative writing · 2026-09-21T23:51:06+00:00]
```

So A3's label is doing its job for a second artifact kind: without it the piece would
read as something said in conversation.

## Known limitations

- **The on-disk filename is the artifact id, not the readable title.** `storage_path`
  shards on the id — deliberately, since that is what makes paths collision-free and
  free of untrusted input — so a human browsing `workspace/writing/` sees hex, and the
  readable name lives in `artifacts.filename`. True of uploads and images too. Browsing
  is by row, not by directory listing; if that ever matters, a symlink tree or an index
  file would be the way, not a change to the path scheme.
- **No dedupe.** `ingest.py` refuses a byte-identical re-upload; saving the same piece
  twice here produces two artifacts. Arguably right (the entity meant to save it again)
  but it is not a decision anyone took.
- **No edit or delete path.** A piece can be written and never revised — corrections
  are `supersedes`' business and nothing links a rewrite to its predecessor. Worth
  knowing before the first time the entity wants to revise something.
- **Nothing prunes `workspace/`** — unchanged, and now two kinds write there.
- **B5 is not done.** This tool works inside a live turn only because that is the only
  caller that exists; nothing yet proves it runs with no conversation and no turn,
  which is B5's reduced scope.

## Next

B5 — prove the tool runs with no conversation and no live turn, as a tested property
rather than a wiring into a session mode that does not exist.
