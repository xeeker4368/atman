"""Storing a reflection-journal entry. Journal step 3.

Design of record: ``docs/REFLECTION_JOURNAL_DESIGN.md`` J2, J5, J7.

Follows ``writing.py``: a sharded path from a generated id, a sha256 of the stored
bytes, the root from ``kinds.root_for()`` (``workspace/journals/``), and **bytes
before the row**. Two differences, both from the design:

* **The gate's verdict is written in the same insert as the row** (``integrity_check``,
  migration 7), so a verdict cannot be lost between the row and a later update.
  ``None`` is the "no verdict recorded" state and is never clean.
* **It does not index.** An entry enters memory only when the operator has read it and
  runs ``--index`` (``indexing.index_existing``, J7, approved 2026-10-01). A stored
  entry therefore has no chunks until then.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from program import config
from program.artifacts import kinds
from program.memory import db

logger = logging.getLogger(__name__)

ARTIFACT_TYPE = "reflection_journal"
EXTRACTION_STATUS = "extracted"
CONTENT_TYPE = "text/markdown"


@dataclass(frozen=True)
class StoredEntry:
    artifact_id: str
    covered_date: str
    filename: str
    #: Relative to ``kinds.root_for("reflection_journal")``, never absolute.
    storage_path: str
    absolute_path: Path
    characters: int
    sha256: str


def filename_for(covered: date, artifact_id: str) -> str:
    """Readable, and **never used to build the path**: that is the id's job."""
    return f"journal-{covered.isoformat()}-{artifact_id[:8]}.md"


def find_entry(covered: date):
    """The stored entry for this covered date, or ``None``. J1's idempotency check.

    A query on the date held in ``extraction_note``, not a filename convention.
    """
    with db.connection() as conn:
        return conn.execute(
            "SELECT * FROM artifacts WHERE artifact_type = ? "
            "AND json_extract(extraction_note, '$.covered_date') = ? "
            "ORDER BY created_at LIMIT 1",
            (ARTIFACT_TYPE, covered.isoformat()),
        ).fetchone()


def list_unindexed():
    """Entries with no chunks, oldest first. Held and declined look the same (J7)."""
    with db.connection() as conn:
        return conn.execute(
            "SELECT a.* FROM artifacts a WHERE a.artifact_type = ? "
            "AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.artifact_id = a.id) "
            "ORDER BY a.created_at",
            (ARTIFACT_TYPE,),
        ).fetchall()


def store(
    text: str,
    covered: date,
    note: dict[str, Any],
    verdict_json: str | None,
    user_id: str,
) -> StoredEntry:
    """Write the entry's bytes, then its row (with the verdict). Indexes nothing."""
    body = (text or "").strip()
    if not body:
        raise ValueError("refusing to store an empty journal entry")
    limit = config.ingestion_max_extracted_chars()
    if len(body) > limit:
        raise ValueError(
            f"this entry is {len(body):,} characters, over the {limit:,}-character "
            f"ceiling on one artifact (ingestion.max_extracted_chars)"
        )

    artifact_id = uuid.uuid4().hex
    filename = filename_for(covered, artifact_id)
    relative, absolute = kinds.storage_path(ARTIFACT_TYPE, artifact_id)
    encoded = body.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()

    # Bytes, then the row: a file with no row is a walkable orphan, a row with no
    # file points at nothing (`ingest.py`'s order).
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
        extracted_text=body,
        extraction_note=json.dumps({**note, "covered_date": covered.isoformat()}),
        integrity_check=verdict_json,
    )
    logger.info("stored journal entry %s for %s (%d chars)", artifact_id[:8], covered, len(body))
    return StoredEntry(artifact_id, covered.isoformat(), filename, relative, absolute,
                       len(body), digest)
