"""Storing a piece of the entity's creative writing. Phase 4, task B4.

Design of record: ``docs/MEDIA_AND_CREATIVE_DESIGN.md``; decision #10 and
``GUIDANCE.md``'s creative-writing section.

Follows ``generated.py``, which follows ``ingest.py``: the same sharded path from a
generated id, the same sha256, the same bytes-then-row-then-index ordering, and the
storage root from ``kinds.root_for()`` — ``workspace/writing/`` — so this module names
no directory.

One structural difference from a generated image
===============================================
An image is produced by a separate program and the tool *fetches* it. **Creative
writing is produced by the entity itself**, so there is nothing to fetch: the model
composes the piece in its own output and calls the tool to keep it. That makes this a
*persist* path rather than a *generate* path, and it is why the tool's parameter is the
text rather than a prompt.

The consequence for provenance is that ``extracted_text`` and the bytes on disk are the
**same content**, unlike an image (where the indexed text is the prompt) or a PDF (where
it is what could be read out). ``extraction_status`` is therefore ``extracted`` and not
``metadata_only``: the content genuinely is available, trivially, because the system
wrote it.

**No gate** (decision #10). Creative writing is the lowest-risk category in the build —
no external effect, nothing irreversible — so nothing here inspects, scores or filters
what was written. That is a deliberate absence, not an omission.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from program import config
from program.artifacts import indexing, kinds
from program.memory import db

logger = logging.getLogger(__name__)

ARTIFACT_TYPE = "creative_writing"

#: The text is the content, and the system wrote it, so it is genuinely available.
#: Contrast ``generated.py``'s ``metadata_only``, where there is no text in a PNG to
#: read — the distinction task 2.6 drew for a scanned PDF and this build keeps.
EXTRACTION_STATUS = "extracted"

CONTENT_TYPE = "text/markdown"

#: Characters of the title kept in the filename.
FILENAME_TITLE_CHARS = 60

#: Words of the text used to derive a title when none was given.
DERIVED_TITLE_WORDS = 8

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class StoredWriting:
    """Where a piece landed, and what a caller can say about it."""

    artifact_id: str
    title: str
    filename: str
    #: Relative to ``kinds.root_for("creative_writing")``, never absolute.
    storage_path: str
    absolute_path: Path
    characters: int
    sha256: str
    chunks_written: int = 0
    chunk_ids: tuple[str, ...] = ()

    @property
    def indexed(self) -> bool:
        return self.chunks_written > 0


def _derive_title(text: str) -> str:
    """A title from the opening words, when the entity did not supply one.

    Deliberately dumb: the first few words, not a summary. Generating a title would
    mean a second model call to describe something the entity just wrote, and a
    paraphrase presented as its title is a small fabrication of exactly the kind this
    build keeps refusing.
    """
    words = text.strip().split()
    if not words:
        return "untitled"
    opening = " ".join(words[:DERIVED_TITLE_WORDS])
    return opening.rstrip(".,;:!?—-") or "untitled"


def _filename_for(title: str, artifact_id: str) -> str:
    """A readable filename. **Never used to build the path** — that is the id's job.

    The title comes from the model rather than from a person, which makes it the same
    kind of input a client filename is: not hostile by assumption, but not trusted
    into a path either.
    """
    slug = _SLUG_STRIP.sub("-", title.lower()).strip("-")[:FILENAME_TITLE_CHARS]
    slug = slug.strip("-") or "untitled"
    return f"{slug}-{artifact_id[:8]}.md"


def store(text: str, user_id: str, title: str | None = None) -> StoredWriting:
    """Write the piece, record the row, index it. Returns where it went.

    ``user_id`` comes from the turn's ``AttributionContext``: the person present in a
    live turn (Q2b), the entity's own row when nobody is (Q2c). **It records whose
    record this belongs to, not who wrote it** — the entity wrote it, which is what
    ``source_trust = firsthand`` says. The distinction matters more here than for an
    image, where the person at least asked for the thing.
    """
    body = (text or "").strip()
    if not body:
        raise ValueError("refusing to store an empty piece of writing")

    limit = config.ingestion_max_extracted_chars()
    if len(body) > limit:
        # Reusing ingestion's ceiling deliberately rather than inventing a second
        # one: what it bounds is the same work in both cases — characters to split,
        # pack and embed — and a separate constant would drift from it. Named for
        # ingestion because that is where it was first needed.
        raise ValueError(
            f"this piece is {len(body):,} characters, over the "
            f"{limit:,}-character ceiling that bounds how many chunks one artifact "
            f"embeds (ingestion.max_extracted_chars)"
        )

    resolved_title = (title or "").strip() or _derive_title(body)
    artifact_id = uuid.uuid4().hex
    filename = _filename_for(resolved_title, artifact_id)
    relative, absolute = kinds.storage_path(ARTIFACT_TYPE, artifact_id)
    encoded = body.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()

    # Bytes, row, then chunks — `ingest.py`'s order, for its reasons: a file with no
    # row is a walkable orphan, a row with no file points at nothing, and chunks are
    # derived from the row and rebuildable by re-indexing.
    absolute.write_bytes(encoded)

    db.insert_artifact(
        artifact_id=artifact_id,
        user_id=user_id,
        filename=filename,
        content_type=CONTENT_TYPE,
        size_bytes=len(encoded),
        sha256=digest,
        storage_path=relative,
        artifact_type=ARTIFACT_TYPE,
        extraction_status=EXTRACTION_STATUS,
        # The writing itself. Unlike an image, where the indexed text is the prompt,
        # here the content and the indexed text are the same thing.
        extracted_text=body,
        extraction_note=f"title: {resolved_title}",
    )

    written, chunk_ids = indexing.index_text(artifact_id, user_id, body, ARTIFACT_TYPE)

    logger.info(
        "stored creative writing %s (%d chars, %d chunk(s)) at %s",
        artifact_id[:8], len(body), written, relative,
    )
    return StoredWriting(
        artifact_id=artifact_id,
        title=resolved_title,
        filename=filename,
        storage_path=relative,
        absolute_path=absolute,
        characters=len(body),
        sha256=digest,
        chunks_written=written,
        chunk_ids=tuple(chunk_ids),
    )
