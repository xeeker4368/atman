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


def main() -> int:
    parser = argparse.ArgumentParser(description="Close idle conversations")
    parser.add_argument("--dry-run", action="store_true", help="Report, change nothing")
    args = parser.parse_args()

    print(f"idle window (turn complete) : {config.idle_close_minutes()}m")
    print(f"in-flight grace             : {config.in_flight_grace_minutes()}m "
          f"(floor {config.IN_FLIGHT_GRACE_FLOOR_MINUTES}m)")

    candidates = idle.find_idle_conversations()
    for conversation_id, reason in candidates:
        print(f"  {conversation_id[:8]}  {reason}")

    try:
        result = idle.close_idle_conversations(dry_run=args.dry_run)
    except idle.IdleCloseError as exc:
        print(f"\nSweep completed with failures:\n{exc}", file=sys.stderr)
        return 1

    print(f"examined : {result.examined}")
    print(f"closed   : {result.closed}{' (dry run)' if args.dry_run else ''}")
    print(f"chunked  : {result.chunked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
