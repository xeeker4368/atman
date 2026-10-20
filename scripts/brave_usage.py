#!/usr/bin/env python3
"""Print this month's Brave Search calls and what is left of the credit (decisions #38, #39).

    python scripts/brave_usage.py [--db PATH/working.db] [--month YYYY-MM]

An operator script for the real store: ``--db`` defaults to the configured ``working.db``.
**Read-only**: the store is opened with ``mode=ro``, and it never reads the Brave key. The
figures come from ``brave_calls`` (migration 10, applied at server startup) and from
``config/defaults.toml`` / ``local.toml`` ``[search]``. Calls made outside this store (Lyle's own
tests, a comparison run, the play kit's own store) are not in the count; the stop's 20
withheld calls are for them.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from program import config  # noqa: E402
from program.memory import db  # noqa: E402
from program.tools import brave  # noqa: E402


def report(conn: sqlite3.Connection, month: str | None = None) -> list[str]:
    """The lines to print. ``month`` is ``YYYY-MM`` (UTC); the current month when None."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "brave_calls" not in tables:
        return ["No brave_calls table in this store: migration 10 has not run on it yet. It "
                "runs when the server starts."]
    at = brave.now()
    if month is not None:
        year, number = (int(part) for part in month.split("-"))
        at = at.replace(year=year, month=number, day=1)
    current = brave.usage(at, conn=conn)
    outcomes = dict(conn.execute(
        "SELECT outcome, COUNT(*) FROM brave_calls WHERE month = ? GROUP BY outcome",
        (current.month,)).fetchall())
    price = float(config.get("search", "brave_price_per_1000_usd", 5.0)) / 1000
    credit = float(config.get("search", "brave_credit_usd", 5.0))
    left = max(credit - current.used * price, 0.0)
    if current.header_limit is None:
        header = "none sent this month"
    else:
        used_or_not = ("used as an earlier stop" if current.header_limit > 0
                       else "not used: Brave's monthly limit reads 0 on this plan")
        header = (f"limit {current.header_limit}, remaining {current.header_remaining} "
                  f"({used_or_not})")
    state = ("STOPPED: no Brave calls until next month" if current.stopped
             else "WARNING: past the warning" if current.used >= current.warn else "ok")
    return [
        f"Brave fallback usage, {current.month} (UTC)",
        f"  calls this month   {current.used}  "
        f"(ok {outcomes.get('ok', 0)}, failed {outcomes.get('failed', 0)}, "
        f"outcome not recorded {outcomes.get('sent', 0)})",
        f"  warning at         {current.warn}",
        f"  stop at            {current.stop}  ({config.brave_credit_calls()} the credit buys, "
        f"less {config.BRAVE_UNSEEN_CALLS} for calls this store does not see)",
        f"  calls left         {max(current.stop - current.used, 0)}",
        f"  credit left        ${left:.2f} of ${credit:.2f}  "
        f"({current.used} x ${price:.3f}; calls outside this store not included)",
        f"  Brave's own count  {header}",
        f"  fallback setting   {config.search_fallback()}",
        f"  state              {state}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=None,
                        help="working.db to read (default: the configured one)")
    parser.add_argument("--month", default=None, help="YYYY-MM, UTC (default: this month)")
    args = parser.parse_args(argv)
    path = args.db or db.working_path()
    if not path.exists():
        print(f"no database at {path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        print("\n".join(report(conn, args.month)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
