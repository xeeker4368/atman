"""Repairing chunks that have a row but no vector.

SQLite is the source of truth; ChromaDB is derived. That choice makes exactly
one inconsistency possible — a chunk row whose vector is missing — and this is
the function that fixes it. The reverse case, a vector with no row, is the one
the ordering was chosen to make impossible, because it is invisible to SQLite
and needs a purge tool to find (which is what the reference build had to build).

Three ways a chunk ends up without a vector:

* **The crash window.** The row is inserted and committed, then the process dies
  before the vector upsert. Narrow, and real.
* **The null store.** Every chunk written before ChromaDB landed has a row and
  no vector by design.
* **A future backfill.** Any change that adds chunks without going through the
  chunking pipeline.

Failure policy matches the chunking pipeline: abort and propagate rather than
skipping. Every error the embedding client raises is systemic — a wrong
dimension, an unreachable model — so continuing would produce a long run of
identical failures and a report that looked like partial success. Because the
pass is additive and resumable, aborting costs only the work not yet done.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from program.engine import ollama
from program.memory import db, vectors


@dataclass
class ReconcileResult:
    """What a pass found and did.

    ``checked`` and ``missing`` are reported separately from ``repaired`` so a
    dry run says something meaningful, and so "nothing needed doing" is
    distinguishable from "nothing was done".
    """

    checked: int = 0
    missing: int = 0
    repaired: int = 0
    dry_run: bool = False
    repaired_ids: list[str] = field(default_factory=list)

    @property
    def already_present(self) -> int:
        return self.checked - self.missing


def find_chunks_without_vectors(store: vectors.VectorStore | None = None) -> list[str]:
    """Chunk ids that have a row but no vector, in insertion order."""
    store = store or vectors.get_vector_store()
    with db.connection() as conn:
        rows = conn.execute("SELECT id FROM chunks ORDER BY rowid").fetchall()
    return [row["id"] for row in rows if not store.has(row["id"])]


def reconcile_vectors(
    *,
    limit: int | None = None,
    dry_run: bool = False,
    store: vectors.VectorStore | None = None,
) -> ReconcileResult:
    """Re-embed and upsert every chunk that has no vector.

    Idempotent: a second run finds nothing. Resumable: an aborted run leaves the
    chunks it repaired repaired, and the next run continues from there.
    """
    store = store or vectors.get_vector_store()
    result = ReconcileResult(dry_run=dry_run)

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, text, conversation_id, user_id, chunk_index, "
            "source_type, source_trust FROM chunks ORDER BY rowid"
        ).fetchall()

    result.checked = len(rows)
    missing = [row for row in rows if not store.has(row["id"])]
    result.missing = len(missing)

    if dry_run:
        return result

    for row in missing[:limit] if limit is not None else missing:
        # No try/except: an embedding failure here is systemic, and swallowing it
        # would report a repair that did not happen.
        vector = ollama.embed(row["text"])
        store.upsert(
            row["id"],
            vector,
            {
                "conversation_id": row["conversation_id"],
                "user_id": row["user_id"],
                "chunk_index": row["chunk_index"],
                "source_type": row["source_type"],
                "source_trust": row["source_trust"],
            },
        )
        result.repaired += 1
        result.repaired_ids.append(row["id"])

    return result
