#!/usr/bin/env python3
"""Print what retrieval put into one assistant turn's prompt, for reviewing runs (decision #33).

    python scripts/show_retrieval.py --db PATH/working.db MESSAGE_ID [--text]
    python scripts/show_retrieval.py --db PATH/working.db --status

``MESSAGE_ID`` is an assistant message's id, or a unique prefix of one (8 characters is
usually enough). Prints the person's message the turn answered, the turn's capture (whether
retrieval ran, how many records, whether a cap cut the block), and each record in rank order:
its opening line as the prompt showed it, score, characters and continuation pieces. ``--text``
adds each record's stored chunk text, read from ``chunks`` (not what was rendered).

**Read-only, and no ``program`` import**: the store is opened with ``mode=ro`` and ``--db`` is
required, so this never reads the real store by default and needs no scratch environment.
``--status`` prints every assistant turn's capture status instead: how many turns had records,
retrieved nothing, did not run retrieval, were not captured (the capture failed after the
message was saved) or came before migration 9, and the ids of any not captured.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve(conn: sqlite3.Connection, prefix: str) -> sqlite3.Row:
    rows = conn.execute(
        "SELECT * FROM messages WHERE substr(id, 1, ?) = ? AND role = 'assistant'",
        (len(prefix), prefix),
    ).fetchall()
    if not rows:
        raise SystemExit(f"no assistant message starts with {prefix!r}")
    if len(rows) > 1:
        raise SystemExit(f"{prefix!r} matches {len(rows)} assistant messages; give more of the id")
    return rows[0]


def show(conn: sqlite3.Connection, prefix: str, with_text: bool = False) -> list[str]:
    """The lines to print for one turn."""
    message = _resolve(conn, prefix)
    asked = conn.execute(
        "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' "
        "AND (timestamp < ? OR (timestamp = ? AND id < ?)) "
        "ORDER BY timestamp DESC, id DESC LIMIT 1",
        (message["conversation_id"], message["timestamp"], message["timestamp"], message["id"]),
    ).fetchone()
    lines = [f"message      {message['id']}  ({message['timestamp']})",
             f"conversation {message['conversation_id']}",
             f"asked        {_one_line(asked['content']) if asked else '(none found)'}"]

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    turn = (conn.execute("SELECT * FROM turn_retrieval WHERE message_id = ?",
                         (message["id"],)).fetchone()
            if "turn_retrieval" in tables else None)
    if turn is None:
        lines.append("capture      not captured (a turn before migration 9, or its capture failed)")
        return lines

    lines.append(f"capture      {_OUTCOMES[turn['outcome']]}")
    if turn["outcome"] != "not_run":
        lines.append(f"searches     lexical {_leg(turn, 'lexical')}; vector {_leg(turn, 'vector')}")
    lines.append(
        f"prompt       {turn['records']} record(s); block {turn['block_chars']:,} chars; "
        f"{'TRUNCATED' if turn['truncated'] else 'not truncated'} "
        f"(continuation pieces withheld {turn['continuations_withheld']}, "
        f"hits left out {turn['hits_left_out']})")
    for row in conn.execute("SELECT * FROM turn_retrieved_records WHERE message_id = ? "
                            "ORDER BY rank", (message["id"],)):
        shown = row["label"] if row["label"] is not None else "(left out of the prompt)"
        lines.append(
            f"  {row['rank']:>2}. {shown}\n"
            f"      chunk {row['chunk_id']}  {row['source_type'] or '-'}  "
            f"score {row['score']:.5f}  lexical rank {_rank(row['lexical_rank'])}  "
            f"vector rank {_rank(row['vector_rank'])}  {row['rendered_chars']:,} chars  "
            f"continuations sent {row['continuations_sent']}, "
            f"withheld {row['continuations_withheld']}")
        if with_text:
            chunk = conn.execute("SELECT text FROM chunks WHERE id = ?",
                                 (row["chunk_id"],)).fetchone()
            body = chunk["text"] if chunk else "(no longer in chunks)"
            lines.extend("      | " + part for part in body.split("\n"))
    return lines


_OUTCOMES = {
    "records": "retrieval ran and returned records",
    "empty": "retrieval ran and returned nothing",
    "not_run": "retrieval did not run, or failed",
}

_STATUSES = ("records", "empty", "not_run", "not_captured", "before_capture")


def _leg(turn: sqlite3.Row, name: str) -> str:
    if turn[f"{name}_ran"]:
        return "ran"
    return f"did not run ({turn[f'{name}_skip_reason'] or 'no reason recorded'})"


def _rank(value: int | None) -> str:
    return "-" if value is None else str(value)


def status(conn: sqlite3.Connection) -> list[str]:
    """Every assistant turn's capture status, counted, with the uncaptured ones listed."""
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    if "turn_capture_status" not in views:
        return ["no capture in this store (migration 9 not applied)"]
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM turn_capture_status GROUP BY status").fetchall())
    lines = [f"{name:<15}{counts.get(name, 0)}" for name in _STATUSES]
    missed = conn.execute("SELECT message_id, timestamp FROM turn_capture_status "
                          "WHERE status = 'not_captured' ORDER BY timestamp").fetchall()
    lines.extend(f"  not captured: {r['message_id']}  ({r['timestamp']})" for r in missed)
    return lines


def _one_line(text: str, limit: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("message_id", nargs="?",
                        help="an assistant message id, or a unique prefix of one")
    parser.add_argument("--db", type=Path, required=True, help="the working.db to read")
    parser.add_argument("--text", action="store_true", help="also print each chunk's stored text")
    parser.add_argument("--status", action="store_true",
                        help="count every assistant turn's capture status instead")
    args = parser.parse_args(argv)
    if (args.message_id is None) != args.status:
        parser.error("give a message id, or --status")
    if not args.db.is_file():
        print(f"no such database: {args.db}", file=sys.stderr)
        return 2
    conn = _open(args.db)
    try:
        lines = status(conn) if args.status else show(conn, args.message_id, args.text)
        print("\n".join(lines))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
