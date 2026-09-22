"""Storing a generated image as an artifact. Phase 4, task A2's storage half.

Design of record: ``docs/MEDIA_AND_CREATIVE_DESIGN.md`` (Q1, Q13, Q14).

Follows ``ingest.py`` rather than paralleling it: the same sharded path from a
generated id, the same sha256 of the stored bytes, the same
bytes-then-row-then-index ordering, and the storage root from
``kinds.root_for()`` so an image lands in ``workspace/`` (Q1) without this module
naming a directory.

**Indexing landed at A3** and is here now, through the same
``indexing.index_text`` that uploads use. What gets indexed is the **prompt** (Q14):
an image has no text of its own, and the prompt is what makes it findable by what was
asked for.

Why the row is written at all in A2
===================================
The alternatives both leave a half-state. A tool that generates and discards has
nothing to return — Q13 requires a path and an id — and would be the placeholder
that ``catalog.py`` refuses: *"a placeholder tool would read as built while being
nothing."* A file written with no row is the orphan ``backup.py`` warns about from
the other direction. Storing bytes and row together leaves neither.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from program.artifacts import indexing, kinds
from program.media.comfyui import GeneratedImage
from program.memory import db

logger = logging.getLogger(__name__)

ARTIFACT_TYPE = "generated_image"

#: What an image's ``extraction_status`` is, and it is not a shrug. ``metadata_only``
#: means *stored but not read*, which is exactly true: there is no text in a PNG to
#: extract. The precedent is task 2.6's scanned PDF — recorded as ``metadata_only``
#: rather than ``extracted`` with an empty string, because a file that was never read
#: must not look read. ``extracted`` here would claim the opposite of the truth.
EXTRACTION_STATUS = "metadata_only"

#: Characters of the prompt used to build a human-readable filename.
FILENAME_PROMPT_CHARS = 60

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class StoredImage:
    """Where a generated image landed, and what a caller can say about it."""

    artifact_id: str
    filename: str
    #: Relative to ``kinds.root_for("generated_image")`` — never absolute. An
    #: absolute path breaks the moment the store moves, and a backup is restored
    #: elsewhere; the same reason ``artifacts.storage_path`` is relative.
    storage_path: str
    absolute_path: Path
    size_bytes: int
    sha256: str
    width: int
    height: int
    chunks_written: int = 0
    chunk_ids: tuple[str, ...] = ()

    @property
    def indexed(self) -> bool:
        return self.chunks_written > 0


def _filename_for(prompt: str, artifact_id: str) -> str:
    """A readable filename, derived from the prompt.

    **Never used to build the path** — that comes from ``artifact_id`` sharding, as
    it does for an upload. The difference from an upload is only whose string it is:
    a client filename is untrusted input, this one is ours. It is still sanitised,
    because a prompt is model- or person-supplied text and a filename that reached a
    shell or a path would be the same hazard either way.
    """
    slug = _SLUG_STRIP.sub("-", prompt.lower()).strip("-")[:FILENAME_PROMPT_CHARS]
    slug = slug.strip("-") or "image"
    return f"{slug}-{artifact_id[:8]}.png"


def store(image: GeneratedImage, user_id: str) -> StoredImage:
    """Write the bytes and record the row. Returns where it went.

    ``user_id`` comes from the turn's ``AttributionContext`` — the person present in
    a live turn (Q2b), the entity's own row when nobody is (Q2c). It is not an
    authorization value and nothing here reads a role.
    """
    if not image.image_bytes:
        raise ValueError("refusing to store an empty image")

    artifact_id = uuid.uuid4().hex
    filename = _filename_for(image.prompt, artifact_id)
    relative, absolute = kinds.storage_path(ARTIFACT_TYPE, artifact_id)
    digest = hashlib.sha256(image.image_bytes).hexdigest()

    # Bytes first, then the row — `ingest.py`'s order, and the asymmetry is the
    # point: a file with no row is an orphan that wastes space and is detectable by
    # walking the directory, while a row with no file is a record pointing at
    # nothing, which is what `backup.py` calls out as the failure that matters.
    absolute.write_bytes(image.image_bytes)

    db.insert_artifact(
        artifact_id=artifact_id,
        user_id=user_id,
        filename=filename,
        content_type=image.content_type,
        size_bytes=image.size_bytes,
        sha256=digest,
        storage_path=relative,
        artifact_type=ARTIFACT_TYPE,
        extraction_status=EXTRACTION_STATUS,
        # Q14: an image carries no text, so the PROMPT is its indexable content —
        # what makes it findable by what was asked for. Stored in the column A3 will
        # read, which is the same column `ingest.py` indexes from for a document.
        extracted_text=image.prompt,
        # How it was made, so a later reader can tell a 4-step Lightning image from a
        # 20-step one. JSON in a note column rather than new columns: these are
        # provenance for one artifact kind, and a column per generator parameter
        # would be a migration every time a sampler setting changes.
        extraction_note=json.dumps(image.to_metadata(), sort_keys=True),
    )

    # After the row, as `ingest()` does: the row is the record, the chunks are
    # derived from it and rebuildable by re-indexing. Indexing first would risk
    # chunks pointing at an artifact_id no row claims.
    written, chunk_ids = indexing.index_text(
        artifact_id, user_id, image.prompt, ARTIFACT_TYPE
    )

    logger.info(
        "stored generated image %s (%d bytes, seed %d) at %s, %d chunk(s)",
        artifact_id[:8], image.size_bytes, image.seed, relative, written,
    )
    return StoredImage(
        artifact_id=artifact_id,
        filename=filename,
        storage_path=relative,
        absolute_path=absolute,
        size_bytes=image.size_bytes,
        sha256=digest,
        width=image.width,
        height=image.height,
        chunks_written=written,
        chunk_ids=tuple(chunk_ids),
    )
