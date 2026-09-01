#!/usr/bin/env python3
"""Re-embed and store vectors for chunks that have none.

    python -m scripts.reconcile_vectors [--dry-run] [--limit N]

The repair path for chunk rows written without a vector: the crash window
between the row insert and the vector upsert, and the backlog of chunks written
before ChromaDB landed. Idempotent — a second run finds nothing.
"""

from __future__ import annotations

import argparse
import sys

from program.memory import db, reconcile, vectors


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile chunk vectors")
    parser.add_argument("--dry-run", action="store_true", help="Report, change nothing")
    parser.add_argument("--limit", type=int, default=None, help="Repair at most N")
    args = parser.parse_args()

    store = vectors.get_vector_store()
    print(f"data dir     : {db.working_path().parent}")
    print(f"vector store : {vectors.chroma_path()}")
    print(f"indexes vectors: {store.indexes_vectors}")
    if hasattr(store, "count"):
        print(f"vectors before : {store.count()}")

    result = reconcile.reconcile_vectors(limit=args.limit, dry_run=args.dry_run)

    print(f"chunks checked : {result.checked}")
    print(f"already present: {result.already_present}")
    print(f"missing vectors: {result.missing}")
    print(f"repaired       : {result.repaired}{' (dry run)' if result.dry_run else ''}")
    if hasattr(store, "count"):
        print(f"vectors after  : {store.count()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
