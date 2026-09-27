"""The file upload endpoint. Task 2.6.

The HTTP shape around ``program/artifacts/ingest.py``, which holds the
substance — the same split ``routes/chat.py`` has against ``engine/turn.py``.

**No capability is registered for uploading**, and that is a decision rather than
an omission. ``permissions.py``'s rule is that only capabilities something
actually enforces get registered, and an unregistered name raises rather than
defaulting permissive. There is nothing here to gate: `PROJECT.md` gives Jodie
chat, image generation and creative-space access, and decision #17 enumerates
what she may not do — settings, research triggering — without excluding uploads.
Both household users may upload, so `role` draws no line, and registering
`artifacts.upload` would create a capability that always returns True: a gate
mounted on nothing, which this build has repeatedly refused to build.

What *is* enforced is **ownership**, the same axis `turn.py` uses: the uploader
is the authenticated actor, taken from the token and never from the request
body. Retrieval over the resulting chunks stays unfiltered by actor, per
`NOW.md` decision #20 — an ingested file is memory like any other, and the
judgment about disclosing it sits with the entity at the point of response.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from program import config
from program.api.routes.auth import CurrentActor
from program.artifacts import blocklist, ingest
from program.engine import ollama
from program.settings.permissions import Actor

logger = logging.getLogger(__name__)

router = APIRouter()

#: How much of the upload is pulled into memory per read.
_READ_SIZE = 1024 * 1024


async def _read_capped(file: UploadFile, limit: int) -> bytes | None:
    """Read the upload, or ``None`` as soon as it is known to exceed ``limit``.

    The size limit used to apply only after ``await file.read()`` had pulled the
    whole body into memory, so it bounded extraction and embedding but not what
    one request could make this process hold. Reading in pieces and stopping at
    ``limit + 1`` bytes means an oversize upload costs at most one read beyond the
    limit, however large it is. ``ingest()`` keeps its own check for callers that
    do not come through this route.
    """
    parts: list[bytes] = []
    total = 0
    while True:
        part = await file.read(_READ_SIZE)
        if not part:
            return b"".join(parts)
        total += len(part)
        if total > limit:
            return None
        parts.append(part)


@router.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    actor: Actor = CurrentActor,
) -> dict:
    """Accept one file, store it, and index whatever text it holds."""
    limit = config.ingestion_max_upload_bytes()
    data = await _read_capped(file, limit)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"{file.filename or 'unnamed'!r} is over the {limit:,}-byte limit. "
                f"That limit bounds extraction time and the number of embedding "
                f"calls one upload can cost."
            ),
        )

    try:
        result = ingest.ingest(data, file.filename or "unnamed", actor.user_id)
    except blocklist.GovernanceFileError as exc:
        # 400, not 403: 403 reads as "you may not", which invites "perhaps
        # someone else may". Nobody may — this is not a permission question, so
        # it is not a permission status.
        #
        # The exception's message is fixed and names no path, no filename and no
        # matched rule. Jodie can upload, and a response saying *which* internal
        # file matched would confirm internal structure to a caller who should
        # not learn it. The detail is logged at WARNING by blocklist.check(),
        # where the operator can see it and an unprivileged uploader cannot —
        # the same split as AUTH_DESIGN's single 401.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from None
    except ingest.FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
        ) from None
    except ingest.IngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from None
    except ollama.OllamaError as exc:
        # The file is stored and its row is written; only indexing failed. Said
        # plainly rather than reported as a failed upload, because re-uploading
        # would produce a duplicate rather than fix anything.
        logger.error("ingestion stored %r but could not index it: %s",
                     file.filename, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"the file was stored but could not be indexed into memory: "
                f"{exc}"
            ),
        ) from None

    return {
        "artifact_id": result.artifact_id,
        "filename": result.filename,
        "content_type": result.content_type,
        "size_bytes": result.size_bytes,
        "extraction_status": result.extraction_status,
        "extraction_note": result.extraction_note,
        "chars_extracted": result.chars_extracted,
        "truncated": result.truncated,
        "chunks_indexed": result.chunks_written,
        "duplicate_of": result.duplicate_of,
    }
