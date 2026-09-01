#!/usr/bin/env python3
"""Back up both databases and the ChromaDB vector store.

    python -m scripts.backup [--destination PATH] [--no-vectors]

The two SQLite databases are captured with SQLite's online backup API under a
single read lock held across both, so they are consistent with each other and
not merely each valid on its own — see ``anam/ops/backup.py`` for why that
distinction is the whole point. The vector store is a plain directory copy,
recorded in the manifest as best-effort, and is rebuildable from the chunks
table with ``scripts/reconcile_vectors.py``.

**Writers block for the duration of the snapshot.** Milliseconds at this scale.

This never deletes, prunes, rotates or overwrites anything. There is no restore
command: restore is a Tier 3 task and has not been designed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from anam.ops import backup


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up the Anam stores")
    parser.add_argument(
        "--destination",
        type=Path,
        default=None,
        help="Where to write. Default: <backup_dir>/anam-backup-<UTC stamp>/",
    )
    parser.add_argument(
        "--no-vectors",
        action="store_true",
        help="Skip the ChromaDB directory (it is rebuildable from chunks)",
    )
    args = parser.parse_args()

    try:
        result = backup.create_backup(
            destination=args.destination, include_vectors=not args.no_vectors
        )
    except backup.BackupError as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1

    print(f"backup   : {result.directory}")
    print(f"created  : {result.created_at}")
    print(f"verified : {result.verified}")
    for artifact in result.artifacts:
        check = f" integrity={artifact.integrity_check}" if artifact.integrity_check else ""
        print(
            f"  {artifact.name:<12} {artifact.size_bytes:>12,} bytes  "
            f"[{artifact.consistency}]{check}"
        )
    for label, count in sorted(result.row_counts.items()):
        print(f"  rows {label:<24} {count:>8,}")
    for warning in result.warnings:
        print(f"  warning: {warning}")
    print(f"total    : {result.total_bytes:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
