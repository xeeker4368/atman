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


class StoredButNotIndexed(Exception):
    """The file and the ``artifacts`` row were written, and indexing then failed.

    Storage's own vocabulary for a partial write (O23, F50). Measured 2026-09-30:
    with the embedder down, ``creative_write`` came back ``tool_error`` while one
    row and one file existed, so "it failed" was false about the thing that
    mattered. This carries the id out, so the layer above can say what was kept.

    Deliberately not a tools-layer type: storage does not import the tools layer.
    """

    def __init__(self, artifact_id: str, cause: BaseException) -> None:
        super().__init__(
            f"artifact {artifact_id} was stored but could not be indexed: "
            f"{type(cause).__name__}: {cause}"
        )
        self.artifact_id = artifact_id
        self.cause = cause


def index_after_row(
    artifact_id: str, user_id: str, text: str, artifact_type: str
) -> tuple[int, list[str]]:
    """:func:`index_text` for a caller whose row is **already committed**.

    Any failure becomes :class:`StoredButNotIndexed`, so the caller's own error
    can never be read as "nothing was stored". Only ``Exception``: an interrupt or
    the test suite's isolation violation must propagate untouched.
    """
    try:
        return index_text(artifact_id, user_id, text, artifact_type)
    except Exception as exc:  # noqa: BLE001 - re-raised with the id attached
        raise StoredButNotIndexed(artifact_id, exc) from exc


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
    artifact_id: str,
    user_id: str,
    text: str,
    artifact_type: str,
    *,
    only_if_unindexed: bool = False,
) -> tuple[int, list[str]]:
    """Chunk, embed and store. Returns ``(count, chunk_ids)``.

    **Every chunk is embedded before any row is written, and the rows are written
    in one transaction** — so an embedding failure on any chunk, or a failed
    write, leaves no chunk rows at all rather than a half-indexed artifact.

    This used to be embed-then-insert *per chunk*, with each insert its own
    transaction, while this docstring claimed the whole-artifact property. A
    failure at chunk N left 0…N-1 committed under an artifacts row saying
    ``extracted`` — and permanently, since a re-upload hits the duplicate check
    and never re-indexes. Holding the vectors costs little: the character ceiling
    is ~400 chunks, ~2.4 MB of floats.

    **What this does not make atomic: the vector store.** Upserts run after the
    commit, because ChromaDB cannot join a SQLite transaction. A failure there
    leaves rows without vectors — the one inconsistency
    ``scripts/reconcile_vectors.py`` exists to repair, and a test drives that
    repair. So the unrecoverable failure became the recoverable one; failure as
    such was not eliminated.

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

    # Embed all first: nothing is written until every chunk has a vector.
    embedded = [(uuid.uuid4().hex, body, ollama.embed(body)) for body in chunks]

    db.insert_chunks(
        only_if_unindexed=artifact_id if only_if_unindexed else None,
        rows=[
            {
                "chunk_id": chunk_id,
                "conversation_id": None,
                "user_id": user_id,
                "text": body,
                "source_type": kind.source_type,
                "source_trust": kind.source_trust,
                "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "chunk_index": index,
                "artifact_id": artifact_id,
            }
            for index, (chunk_id, body, _) in enumerate(embedded)
        ]
    )

    store = vectors.get_vector_store()
    for index, (chunk_id, _, vector) in enumerate(embedded):
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
    written = [chunk_id for chunk_id, _, _ in embedded]
    logger.info(
        "indexed %s as %d %s chunk(s)", artifact_id[:8], len(written), kind.source_type
    )
    return len(written), written


# ---------------------------------------------------------------------------
# Indexing an artifact that is already stored (reflection journal, J7)
# ---------------------------------------------------------------------------

#: The only kind held back from memory until a person has read it. No other kind is
#: indexed late, so none may be indexed through this path: a second use of it would be
#: a new decision, not a reuse (`docs/REFLECTION_JOURNAL_DESIGN.md` J7).
HELD_KINDS = frozenset({"reflection_journal"})


class IndexRefused(Exception):
    """:func:`index_existing` will not index this artifact. The message says why."""


class AlreadyIndexed(IndexRefused):
    """The artifact has chunks already. Reported, never duplicated."""


def index_existing(artifact_id: str) -> tuple[int, list[str]]:
    """Index a stored journal entry into memory, after the operator has read it.

    Reads the row and indexes its ``extracted_text`` under its kind's provenance, with
    :func:`index_text`'s atomicity (every chunk embedded first, all rows in one
    transaction, vectors after). It **refuses**:

    * an id with no row;
    * any kind other than a held one (:data:`HELD_KINDS`);
    * a row with no extracted text;
    * a row that already has chunks. The check is made twice: here, for a clear
      message, and again **inside the insert's transaction**, so two concurrent
      ``--index`` runs cannot both write.

    An embedding or write failure raises with no chunks written, and a retry works.
    """
    row = db.get_artifact(artifact_id)
    if row is None:
        raise IndexRefused(f"no artifact has id {artifact_id}")
    if row["artifact_type"] not in HELD_KINDS:
        raise IndexRefused(
            f"artifact {artifact_id} is a {row['artifact_type']!r}, and only "
            f"{', '.join(sorted(HELD_KINDS))} entries are indexed late. Everything else "
            f"is indexed when it is written."
        )
    text = row["extracted_text"] or ""
    if row["extraction_status"] != "extracted" or not text.strip():
        raise IndexRefused(f"artifact {artifact_id} has no extracted text to index")
    if db.get_artifact_chunks(artifact_id):
        raise AlreadyIndexed(f"artifact {artifact_id} is already indexed")
    try:
        return index_text(
            artifact_id, row["user_id"], text, row["artifact_type"], only_if_unindexed=True
        )
    except db.ArtifactAlreadyIndexed as exc:
        raise AlreadyIndexed(f"artifact {artifact_id} is already indexed") from exc
