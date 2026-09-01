"""The vector-store boundary, and the ChromaDB implementation behind it.

The chunking pipeline needs somewhere to put an embedding. It must not need to
know *where*. This module is that seam: a narrow protocol, a real store, and a
null store that keeps nothing.

**The null store is deliberately loud about being a null store.** A no-op that
reports success is the failure pattern this project keeps designing against, so
``indexes_vectors`` is part of the protocol and the chunking pipeline reports it,
keeping "the chunk was written" and "the chunk is vector-retrievable" separate
claims.

**Paths resolve at call time**, and stores are cached per resolved path rather
than globally. A module-level client bound at import is unreachable by test
patching — the mechanism that had the reference build's suite writing into its
production store for weeks.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from anam import config

#: One collection, named once. The go-live wipe and any rebuild both key on it.
COLLECTION_NAME = "chunks"


@runtime_checkable
class VectorStore(Protocol):
    """What the chunking pipeline needs from a vector store."""

    #: Whether this store actually persists vectors. False for the null store.
    indexes_vectors: bool

    def upsert(self, chunk_id: str, vector: list[float], metadata: dict[str, Any]) -> None:
        """Store a vector under ``chunk_id``, replacing any existing one."""
        ...

    def delete(self, chunk_id: str) -> None:
        """Remove a vector. Absent ids are not an error."""
        ...

    def has(self, chunk_id: str) -> bool:
        """Whether a vector exists for this id. Used by reconciliation."""
        ...


class VectorDimensionError(RuntimeError):
    """A vector was the wrong width for the configured store.

    Distinct from ``ollama.EmbeddingDimensionError``, which catches a bad vector
    as it leaves the model. This catches one as it reaches the store — the last
    point before it would define, or violate, the collection's width.
    """


class NullVectorStore:
    """Accepts vectors and keeps none of them.

    Retained after ChromaDB landed because tests want a store that provably
    holds nothing, and because it makes "no vectors were written" expressible
    rather than merely observed.

    ``has()`` returns False for everything, which is honest — it holds nothing —
    and is exactly what makes reconciliation pick every chunk up.
    """

    indexes_vectors = False

    def upsert(self, chunk_id: str, vector: list[float], metadata: dict[str, Any]) -> None:
        return None

    def delete(self, chunk_id: str) -> None:
        return None

    def has(self, chunk_id: str) -> bool:
        return False


class ChromaVectorStore:
    """Vectors in a local, on-disk ChromaDB collection.

    **Dimension is checked here, before Chroma sees the vector.** Chroma infers a
    collection's width from the first vector written to it and then enforces that
    width — so a wrong-width vector arriving first would silently define the
    collection wrongly, and every correct vector afterwards would be rejected
    against it. Checking against the configured dimension first means the
    collection can only ever be defined by a vector of the expected width.

    That is a second line: ``ollama.embed()`` already asserts the width as the
    vector leaves the model. This one exists because a vector can reach a store
    from somewhere other than a fresh embed call — a reconciliation pass, a
    future backfill — and the collection's width is defined exactly once, by
    whatever arrives first.

    Cosine distance, set at creation. It is a property of the collection, not of
    a query, so changing it later means rebuilding.
    """

    indexes_vectors = True

    def __init__(self, path: str, collection_name: str = COLLECTION_NAME):
        import chromadb

        self.path = path
        self.collection_name = collection_name
        self._client = chromadb.PersistentClient(path=path)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            # We supply our own vectors from Ollama. Leaving this unset would
            # have Chroma silently embed text with its own bundled model.
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )

    def _check_dimension(self, chunk_id: str, vector: list[float]) -> None:
        expected = config.expected_embedding_dimension()
        if len(vector) != expected:
            raise VectorDimensionError(
                f"refusing to store a {len(vector)}-dimension vector for chunk "
                f"{chunk_id}: the collection is for {expected} dimensions "
                f"(embedding.expected_dimension). Either the embedding model "
                f"changed or that setting is wrong; storing this would define "
                f"or corrupt the collection's width."
            )

    def upsert(self, chunk_id: str, vector: list[float], metadata: dict[str, Any]) -> None:
        self._check_dimension(chunk_id, vector)
        # Chroma metadata values must be scalars; drop Nones rather than sending
        # them, since a None user_id is absence, not a value to index on.
        clean = {k: v for k, v in metadata.items() if v is not None}
        # An *empty* dict is rejected outright ("Expected metadata to be a
        # non-empty dict") while omitting it is fine — verified against the
        # library. A chunk whose metadata is entirely absent is legitimate, so
        # this must not be a crash.
        self._collection.upsert(
            ids=[chunk_id],
            embeddings=[vector],
            metadatas=[clean] if clean else None,
        )

    def delete(self, chunk_id: str) -> None:
        self._collection.delete(ids=[chunk_id])

    def has(self, chunk_id: str) -> bool:
        return bool(self._collection.get(ids=[chunk_id])["ids"])

    def count(self) -> int:
        """How many vectors the collection holds. For reporting and reconciliation."""
        return self._collection.count()

    def query(self, vector: list[float], n_results: int = 10) -> dict[str, Any]:
        """Nearest neighbours. Task 1.5 consumes this; nothing else should."""
        return self._collection.query(query_embeddings=[vector], n_results=n_results)


# ---------------------------------------------------------------------------
# Store selection
# ---------------------------------------------------------------------------


def chroma_path() -> str:
    """Where the collection lives. Resolved at call time, never cached."""
    return str(config.data_dir() / "chromadb")


_override: VectorStore | None = None
_cache: dict[str, ChromaVectorStore] = {}


def get_vector_store() -> VectorStore:
    """The store the pipeline writes through.

    Defaults to ChromaDB at the configured data directory, built on first use
    and cached **per resolved path** — so redirecting the data directory, as
    tests do, produces a different store rather than reusing one pointed at the
    real one.
    """
    if _override is not None:
        return _override
    path = chroma_path()
    if path not in _cache:
        _cache[path] = ChromaVectorStore(path)
    return _cache[path]


def set_vector_store(store: VectorStore | None) -> None:
    """Force a specific store, or pass None to fall back to the default."""
    global _override
    _override = store


def reset_vector_store() -> None:
    """Drop the override and every cached client. Used between tests."""
    global _override
    _override = None
    _cache.clear()
