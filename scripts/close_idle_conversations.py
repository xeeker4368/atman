#!/usr/bin/env python3
"""Close conversations that have gone quiet past their idle window.

    python -m scripts.close_idle_conversations [--dry-run]

Closing runs final chunking, which is what makes a conversation's trailing turns
retrievable from outside it — chunking never indexes the open trailing group.

**This is the only way an idle conversation closes today.** The per-request
sweep arrives with task 2.2's chat route; until then nothing runs automatically.
"""

from __future__ import annotations

import argparse
import sys

from program import config
from program.memory import idle
from program.ops import store_lock


def main() -> int:
    parser = argparse.ArgumentParser(description="Close idle conversations")
    parser.add_argument("--dry-run", action="store_true", help="Report, change nothing")
    parser.add_argument(
        "--drain", action="store_true",
        help="also finish final chunking for conversations closed but not chunked",
    )
    parser.add_argument(
        "--limit", type=int, default=5,
        help="with --drain, how many queued conversations to attempt (default 5)",
    )
    args = parser.parse_args()

    # Chunking writes vectors, and a second process writing them breaks a running
    # server's vector search until it restarts (B23).
    try:
        store_lock.hold_or_refuse("close idle conversations")
    except store_lock.StoreInUse as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"idle window (turn complete) : {config.idle_close_minutes()}m")
    print(f"in-flight grace             : {config.in_flight_grace_minutes()}m "
          f"(floor {config.IN_FLIGHT_GRACE_FLOOR_MINUTES}m)")

    candidates = idle.find_idle_conversations()
    for candidate in candidates:
        print(f"  {candidate.conversation_id[:8]}  {candidate.reason}")

    try:
        result = idle.close_idle_conversations(dry_run=args.dry_run)
    except idle.IdleCloseError as exc:
        print(f"\nSweep completed with failures:\n{exc}", file=sys.stderr)
        return 1

    print(f"examined : {result.examined}")
    print(f"closed   : {result.closed}{' (dry run)' if args.dry_run else ''}")
    print(f"chunked  : {result.chunked}")
    if result.skipped_recently_active:
        print(
            f"left open: {result.skipped_recently_active} "
            f"(a message arrived after this sweep judged them idle)"
        )

    if args.drain and not args.dry_run:
        drained = idle.drain_recovery_queue(limit=args.limit)
        print(
            f"queue    : {drained.queued} closed-but-unchunked, "
            f"{drained.chunked} chunked now"
        )
        for conversation_id, message in drained.failures:
            print(f"  {conversation_id[:8]}  still queued: {message}", file=sys.stderr)
        if drained.failures:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
