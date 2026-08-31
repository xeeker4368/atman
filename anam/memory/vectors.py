"""The vector-store boundary.

The chunking pipeline needs somewhere to put an embedding. It must not need to
know *where*. This module is that seam: a narrow protocol, and a null
implementation that does nothing.

Task 1.4 adds ``ChromaVectorStore`` here and makes it the default. Nothing in
the pipeline changes when it does, because the pipeline never knew which store
it was talking to.

**The null store is deliberately loud about being a null store.** A no-op that
reports success is the failure pattern this project keeps designing against, so
selecting it is explicit rather than a fallback, and callers can ask
``store.indexes_vectors`` to find out whether anything was actually indexed. The
chunking pipeline reports that in its result, so "the chunk was written" and
"the chunk is vector-retrievable" stay separate claims.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


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


class NullVectorStore:
    """Accepts vectors and keeps none of them.

    The default until task 1.4 lands. Chunks written through it are present in
    SQLite and lexically retrievable via FTS5, and are **not** vector
    retrievable. That is the degraded-but-detectable state the design sanctions:
    task 1.4's reconciliation finds chunk rows with no vector and embeds them.

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


_default_store: VectorStore = NullVectorStore()


def get_vector_store() -> VectorStore:
    """The store the pipeline writes through."""
    return _default_store


def set_vector_store(store: VectorStore) -> None:
    """Replace the store. Task 1.4 uses this; tests use it to inject a fake."""
    global _default_store
    _default_store = store
