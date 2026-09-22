"""Storing an uploaded file and putting its text into memory. Task 2.6.

Design of record: ``docs/INGESTION_DESIGN.md``. This module is the orchestration
— store the bytes, extract, record the artifact, chunk and index — with the HTTP
shape in ``program/api/routes/upload.py``, the same split the chat endpoint has.

The chunking path is its own, and why
-------------------------------------
Extracted text lands in the **same ``chunks`` table**, indexed the same way, and
retrieval needs no change for it (I4). What does not transfer is
``chunking.py``: its entry points are turn-shaped — turn-preserving boundaries,
an 8-turn coherence cap, ``first_message_id``/``last_message_id`` ranges. A
document has no turns, and pushing one through that path would mean inventing
messages that were never said.

So the pieces are reused rather than the pipeline: ``splitting.py`` for
boundaries (already generic — paragraph, then line, then sentence, then
whitespace, hard cut last, all in ``str`` space so multi-byte characters
survive), ``chunking.target_chars`` for size so a document chunk and a
conversation chunk are comparable when they compete for the same retrieval slot,
and ``db.insert_chunk(conversation_id=None, artifact_id=...)``.

**Embed before write**, matching ``chunking.py``: a chunk that cannot be
embedded is not written at all, so a failure leaves the store untouched rather
than half-indexed.

Two security properties, held here
----------------------------------
* **The client's filename never becomes a path.** ``storage_path`` is built from
  a generated id; ``filename`` is recorded for display only. A file called
  ``../../program/integrity/soul.md`` is inert here because nothing joins it to
  a directory.
* **The content type is detected from the bytes**, not read from the upload
  header, which whoever is uploading controls. Same reasoning as ``web_fetch``
  validating a resolved address rather than a hostname.

The governance blocklist is its own module, ``blocklist.py``, and is checked
here before a single byte is written. It matches on **content**, because an
upload arrives as bytes with a name the client chose — see that module for why a
path check cannot work at this seam and how the directory rule still drives it.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field

from program import config
from program.artifacts import blocklist, indexing, kinds
from program.artifacts import extract as extraction
from program.artifacts.extract import ExtractionStatus
from program.memory import db

logger = logging.getLogger(__name__)

#: Provenance for a chunk that came from an uploaded file (INGESTION_DESIGN O1).
#:
#: ``working.sql`` names ``program/memory/provenance.py`` as the vocabulary's
#: owner and that module does not exist yet — task 1.7 has not landed — so these
#: two constants sit here beside ``chunking.py``'s ``conversation``/``firsthand``
#: pair, in the same shape, for 1.7 to collect when it arrives.
#:
#: ``secondhand`` rather than ``firsthand`` because an uploaded document is not
#: the entity's own experience: someone handed it over, and what it says is the
#: document's claim, not a thing that happened here. Nothing reads the value —
#: `test_source_trust_does_not_change_ranking` rewrites every chunk's trust and
#: asserts ranking is byte-identical — so this is a record, not a lever.
#: Phase 4 P0 moved the authority for these to ``kinds.py``, where the storage
#: root lives too, so one kind cannot have its vocabulary in one file and its
#: directory in another. They stay exported here because callers and tests refer
#: to them by these names; the values are the registry's.
SOURCE_TYPE = kinds.kind("upload").source_type
SOURCE_TRUST = kinds.kind("upload").source_trust

#: What kind of artifact an upload is. Decision #10 uses the same column for
#: ``creative_writing``; Phase 4 adds generated images.
ARTIFACT_TYPE = "upload"


class IngestionError(RuntimeError):
    """The file could not be ingested."""


class FileTooLargeError(IngestionError):
    """Over ``ingestion.max_upload_bytes``."""


@dataclass
class IngestResult:
    """What ingesting one file did."""

    artifact_id: str
    filename: str
    content_type: str
    size_bytes: int
    extraction_status: str
    extraction_note: str | None = None
    chars_extracted: int = 0
    truncated: bool = False
    chunks_written: int = 0
    chunk_ids: list[str] = field(default_factory=list)
    duplicate_of: str | None = None

    @property
    def indexed(self) -> bool:
        return self.chunks_written > 0


def _storage_path(artifact_id: str, artifact_type: str = ARTIFACT_TYPE) -> tuple[str, object]:
    """``(relative_path, absolute_path)`` for a new artifact.

    Delegates to ``kinds.storage_path``, which owns the sharding and the
    per-kind root (Phase 4 Q1). The sharding is unchanged — two hex characters of
    the generated id, never anything the client sent — and for ``upload`` the root
    is still ``config.artifact_dir()``, so nothing about this path moved.
    """
    return kinds.storage_path(artifact_type, artifact_id)


def _index_text(artifact_id: str, user_id: str, text: str) -> tuple[int, list[str]]:
    """Chunk, embed and store an upload's extracted text.

    Delegates to ``indexing.index_text``, which Phase 4 A3 extracted from this
    function so generated images could use the same pipeline with their own
    provenance instead of a second copy of it. The behaviour for an upload is
    unchanged: ``kinds`` supplies the same ``file``/``secondhand`` pair this module
    hardcoded before.
    """
    return indexing.index_text(artifact_id, user_id, text, ARTIFACT_TYPE)


def ingest(
    data: bytes,
    filename: str,
    user_id: str,
    *,
    artifact_type: str = ARTIFACT_TYPE,
) -> IngestResult:
    """Store one uploaded file, extract what can be read, index what was read.

    ``filename`` is recorded and never used as a path. The content type is
    detected from ``data``; any type the caller was told is ignored.
    """
    limit = config.ingestion_max_upload_bytes()
    if len(data) > limit:
        raise FileTooLargeError(
            f"{filename!r} is {len(data):,} bytes, over the {limit:,}-byte "
            f"limit. That limit bounds extraction time and the number of "
            f"embedding calls one upload can cost."
        )
    if not data:
        raise IngestionError(f"{filename!r} is empty.")

    digest = hashlib.sha256(data).hexdigest()
    # Before anything is written, and before the duplicate check: a governance
    # file must not reach the disk, the artifacts table, or the chunk store even
    # transiently. Raises GovernanceFileError, which is deliberately not an
    # IngestionError subclass — the route maps it to its own fixed response.
    blocklist.check(data, digest)

    existing = db.get_artifact_by_hash(digest, user_id)
    if existing is not None:
        # Byte-identical re-upload. Recorded rather than re-indexed: embedding
        # the same text twice would put two copies in competition for the same
        # retrieval slots.
        logger.info("artifact %s re-uploaded; returning the existing row",
                    existing["id"])
        return IngestResult(
            artifact_id=existing["id"],
            filename=existing["filename"],
            content_type=existing["content_type"],
            size_bytes=existing["size_bytes"],
            extraction_status=existing["extraction_status"],
            extraction_note=existing["extraction_note"],
            chars_extracted=len(existing["extracted_text"] or ""),
            chunks_written=len(db.get_artifact_chunks(existing["id"])),
            duplicate_of=existing["id"],
        )

    artifact_id = uuid.uuid4().hex
    content_type = extraction.detect_content_type(data, filename)
    relative, absolute = _storage_path(artifact_id, artifact_type)
    absolute.write_bytes(data)

    result = extraction.extract(data, content_type)

    db.insert_artifact(
        artifact_id=artifact_id,
        user_id=user_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        sha256=digest,
        storage_path=relative,
        artifact_type=artifact_type,
        extraction_status=result.status.value,
        extracted_text=result.text or None,
        extraction_note=result.note,
    )

    written, chunk_ids = 0, []
    if result.status is ExtractionStatus.EXTRACTED and result.text.strip():
        written, chunk_ids = _index_text(artifact_id, user_id, result.text)

    logger.info(
        "ingested %s (%s, %d bytes) as %s: %s, %d chunk(s)",
        filename, content_type, len(data), artifact_id[:8],
        result.status.value, written,
    )
    return IngestResult(
        artifact_id=artifact_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        extraction_status=result.status.value,
        extraction_note=result.note,
        chars_extracted=len(result.text),
        truncated=result.truncated,
        chunks_written=written,
        chunk_ids=chunk_ids,
    )
