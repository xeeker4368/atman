#!/usr/bin/env python3
"""Seed a small, varied corpus for the Phase 1 retrieval checkpoint.

    python -m scripts.seed_dataset [--allow-existing] [--dry-run]

Everything is written through the real pipeline — ``db.save_message()`` then
``chunking.finalise_conversation()`` — so the chunks carry genuine provenance
and real embeddings. **A reachable Ollama instance is required.**

Additive only: nothing is deleted or overwritten, and seeding a store that
already holds conversations is refused unless ``--allow-existing`` is passed.

Every chunk lands as ``source_type="conversation"``, which is the provisional
convention ``program/memory/chunking.py`` uses. Task 1.7 owns the real vocabulary
and has not landed, so this corpus is deliberately single-provenance.
"""

from __future__ import annotations

import argparse
import sys

from program.memory import db
from program.ops import seed, store_lock


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a development corpus")
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="Add the corpus even if the store already holds conversations",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Describe what would be written, touching nothing",
    )
    args = parser.parse_args()

    # Seeding chunks and embeds, so it writes vectors; a second process doing that
    # breaks a running server's vector search until it restarts (B23). Taken before
    # anything is written, and it creates the data directory itself if needed.
    if not args.dry_run:
        try:
            store_lock.hold_or_refuse("seed a corpus")
        except store_lock.StoreInUse as exc:
            print(exc, file=sys.stderr)
            return 1

    if args.dry_run:
        print(f"{len(seed.CONVERSATIONS)} conversation(s), "
              f"{len(seed.USERS)} user(s). Nothing written.\n")
        for spec in seed.CONVERSATIONS:
            state = "closed + chunked" if spec.close else "LEFT OPEN"
            print(f"  {spec.key:<14} {spec.user:<7} {len(spec.turns)} turns  {state}")
            print(f"  {'':<14} {spec.topic} — {spec.note}")
        return 0

    try:
        result = seed.seed(allow_existing=args.allow_existing)
    except seed.SeedError as exc:
        print(f"Seeding refused: {exc}", file=sys.stderr)
        return 1
    except db.StoreWouldMigrateError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    print(f"users         : {', '.join(sorted(result.users))}")
    print(f"conversations : {len(result.conversations)}")
    print(f"messages      : {result.messages}")
    print(f"chunks        : {result.chunks}")
    print(f"left open     : {', '.join(result.open_conversations) or 'none'}")
    print(f"split chunks  : {', '.join(result.split_conversations) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
