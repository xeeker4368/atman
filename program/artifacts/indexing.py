"""Chunking and embedding an artifact's text. Phase 4 A3.

Extracted from ``ingest.py``'s ``_index_text``, which was correct and
upload-specific: it hardcoded ``file``/``secondhand``. Generated images need the
same pipeline with their own provenance, and the Cluster A brief asked for
*"chunking/indexing via the same `_index_text`-style path"* rather than a second
one — so there is now one implementation, parameterised by artifact kind, and
``ingest.py`` calls it too.

What it does not do
===================
It does not decide **what** text to index. For a document that is the extracted
text; for a generated image it is the **prompt**, because an image carries no text
of its own and the prompt is what makes it findable by what was asked for (Q14).
That choice belongs to the caller who knows the kind.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from program import config
from program.artifacts import kinds
from program.engine import ollama
from program.memory import db, splitting, vectors

logger = logging.getLogger(__name__)


def pack(pieces: list[str], target: int) -> list[str]:
    """Greedily pack split pieces up to ``target`` characters.

    Mirrors ``chunking.py``'s packing, minus the turn cap that has no meaning for a
    document. A piece already over target — a single unbroken paragraph — stands as
    its own chunk rather than being cut again; ``splitting.py`` has already taken it
    as far as it goes.
    """
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 2 + len(piece) <= target:
            current = f"{current}\n\n{piece}"
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def index_text(
    artifact_id: str, user_id: str, text: str, artifact_type: str
) -> tuple[int, list[str]]:
    """Chunk, embed and store. Returns ``(count, chunk_ids)``.

    **Embedding precedes every write**, so an unreachable model leaves nothing behind
    rather than a half-indexed artifact — ``chunking.py``'s rule, for its reason.

    ``source_type`` and ``source_trust`` come from the kind registry rather than from
    a caller's argument, so an artifact cannot be indexed under provenance that
    disagrees with its own row.
    """
    kind = kinds.kind(artifact_type)
    target = config.chunk_target_chars()
    # Split to the CHUNK target, not to the embedding budget. Splitting at
    # embedding.max_input_chars (5000) would produce artifact chunks twice the size
    # of conversation chunks, which compete for the same retrieval slots — a bigger
    # chunk is a bigger lexical target and a more diluted embedding. The embedding
    # budget remains the hard ceiling neither may cross.
    pieces = splitting.split_text(text, min(target, config.embedding_max_input_chars()))
    chunks = pack(pieces, target)
    if not chunks:
        return 0, []

    store = vectors.get_vector_store()
    written: list[str] = []
    for index, body in enumerate(chunks):
        vector = ollama.embed(body)
        chunk_id = uuid.uuid4().hex
        db.insert_chunk(
            chunk_id=chunk_id,
            conversation_id=None,
            user_id=user_id,
            text=body,
            source_type=kind.source_type,
            source_trust=kind.source_trust,
            text_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            chunk_index=index,
            artifact_id=artifact_id,
        )
        store.upsert(
            chunk_id,
            vector,
            {
                "artifact_id": artifact_id,
                "user_id": user_id,
                "chunk_index": index,
                "source_type": kind.source_type,
                "source_trust": kind.source_trust,
            },
        )
        written.append(chunk_id)
    logger.info(
        "indexed %s as %d %s chunk(s)", artifact_id[:8], len(written), kind.source_type
    )
    return len(written), written
