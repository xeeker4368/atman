"""Write, read back and index the reflection journal.

    python -m scripts.write_journal [--date YYYY-MM-DD] [--dry-run] [--with-clause]
    python -m scripts.write_journal --list-unindexed
    python -m scripts.write_journal --index ARTIFACT_ID
    python -m scripts.write_journal --show-records DATE [--with-clause]

Design of record: ``docs/REFLECTION_JOURNAL_DESIGN.md`` J1, J7. The default date is
yesterday (local). Today and future days are refused. An existing entry for the date is
reported and nothing is done. A day with no messages writes nothing.

**A run stores the entry and prints it with the gate's verdict. It does not index it.**
The entry reaches memory only when you have read it and run ``--index``. The gate's
verdict is **a noisy aid, not a control**: the control is you reading the entry.
``--show-records DATE`` prints the day's records exactly as the entity was given them
(read-only: no model call, nothing written), so the entry can be read against its source.
``--with-clause`` selects the prompt arm that includes *"and there was no thinking about
it in between"*, which the live run is deciding about.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from program.artifacts import indexing
from program.artifacts import journal as storage
from program.engine import ollama
from program.memory import db
from program.reflection import journal

WHAT_YOU_CAN_DO = """\
What you can do with this entry, from here:
  - Read it (above). The verdict is an aid to reading, and it is wrong in both
    directions: it misses lived-through narrative with no time marker and it flags
    some accurate sentences. Your reading is the control.
  - If a cited sentence really is a fabrication, report it. Nothing here records the
    judgment.
  - Read it against its source:  python -m scripts.write_journal --show-records {date}
    The gate checks no counts, names or event claims, and those are the errors entries
    actually make.
  - Index it once you have read it:  python -m scripts.write_journal --index {id}
    Until then it is stored and is not in memory.
  - You cannot edit, delete or regenerate it from this command. A verdict changes
    nothing about the entry, and a second entry for the same date is refused."""


def print_verdict(verdict) -> None:
    data = verdict.to_dict()
    print(f"integrity verdict: {data['status']}")
    if data["status"] == "unavailable":
        print("  The classifier could not run. This entry is UNCHECKED, not clean:"
              f" {data.get('semantic_error')}")
    for finding in data.get("findings", []):
        print(f"  - cited: {finding.get('evidence')!r}")
        print(f"    reason: {finding.get('detail')}")
    if data.get("out_of_scope_discarded"):
        print(f"  ({data['out_of_scope_discarded']} tool or action objection(s) were "
              f"dropped as out of scope for this check.)")


def cmd_write(args) -> int:
    covered = date.fromisoformat(args.date) if args.date else None
    try:
        result = journal.write_entry(covered, clause=args.with_clause, dry_run=args.dry_run)
    except journal.DateRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except ollama.OllamaOutputTruncated as exc:
        print(f"nothing stored: {exc}", file=sys.stderr)
        return 1
    except (journal.JournalError, ollama.OllamaError) as exc:
        print(f"nothing stored: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    day = result.covered.isoformat()
    if result.status == "exists":
        print(f"An entry for {day} already exists ({result.existing_id}). Nothing was done.")
        return 0
    if result.status == "empty":
        print(f"No messages on {day}. Nothing was written: an entry about an empty day "
              f"invites filler.")
        return 0

    p = result.prepared
    if result.status == "dry_run":
        print(f"dry run for {day} (no model call, nothing written)")
        print(f"  window (UTC)        : {p.window[0]} to {p.window[1]}")
        print(f"  messages            : {p.messages_in} in {p.conversations} conversation(s)")
        print(f"  omitted to fit      : {p.messages_omitted}")
        print(f"  clipped (shown)     : {p.messages_clipped}")
        print(f"  estimated prompt    : {p.estimated_prompt_tokens} tokens")
        print(f"  prompt arm          : {p.prompt_revision}")
        for note in result.notes:
            print(f"  note                : {note}")
        return 0

    print(f"entry for {day}  ({p.messages_in} messages, {p.messages_omitted} omitted, "
          f"{p.messages_clipped} clipped, {p.prompt_revision})\n")
    print(result.text)
    print()
    print(f"stored at: {result.entry.absolute_path}")
    print(f"artifact id: {result.entry.artifact_id}  (stored, NOT indexed)")
    print_verdict(result.verdict)
    print()
    print(WHAT_YOU_CAN_DO.format(id=result.entry.artifact_id, date=day))
    return 0


def cmd_show_records(date_text: str, clause: bool) -> int:
    """The records section of the prompt, byte for byte as the entity received it.

    Read-only: ``prepare`` makes no model call and writes nothing. The heading lines are
    prefixed ``# `` and end before the records, so everything from ``RECORDS OF`` on is the
    entity's own text, unchanged.
    """
    try:
        prepared = journal.prepare(date.fromisoformat(date_text), clause=clause)
    except journal.DateRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except journal.JournalError as exc:
        print(f"cannot render: {exc}", file=sys.stderr)
        return 1
    if prepared is None:
        print(f"No messages on {date_text}.")
        return 0
    start = prepared.system.index("RECORDS OF")
    print(f"# records for {date_text}, as the entity was given them ({prepared.prompt_revision})")
    print(f"# {prepared.messages_in} messages in {prepared.conversations} conversation(s); "
          f"{prepared.messages_omitted} omitted to fit; {prepared.messages_clipped} clipped")
    print()
    print(prepared.system[start:])
    return 0


def cmd_index(artifact_id: str) -> int:
    try:
        count, _ = indexing.index_existing(artifact_id)
    except indexing.AlreadyIndexed as exc:
        print(f"already indexed: {exc}")
        return 0
    except indexing.IndexRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - reported; nothing was written, a retry works
        print(f"not indexed (no chunks were written; a retry is safe): "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {artifact_id}: {count} chunk(s). It can now come up in memory, "
          f"labelled as a later interpretation.")
    return 0


def cmd_list_unindexed() -> int:
    rows = storage.list_unindexed()
    if not rows:
        print("No unindexed journal entries.")
        return 0
    now = datetime.now(timezone.utc)
    print("Entries with no chunks. 'Not yet read' and 'read and declined' look the same "
          "here (J7, a known gap until Phase 6).")
    for row in rows:
        note = json.loads(row["extraction_note"] or "{}")
        verdict = json.loads(row["integrity_check"]) if row["integrity_check"] else None
        age = now - datetime.fromisoformat(row["created_at"])
        print(f"  {row['id']}  covers {note.get('covered_date')}  "
              f"age {age.days}d {age.seconds // 3600}h  "
              f"verdict {verdict['status'] if verdict else 'none recorded'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Write, read back and index the journal")
    parser.add_argument("--date", help="local day to cover, YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the window, counts and size; no model call, no write")
    parser.add_argument("--with-clause", action="store_true",
                        help="prompt arm with the 'no thinking in between' clause")
    parser.add_argument("--index", metavar="ARTIFACT_ID",
                        help="index an entry you have read into memory")
    parser.add_argument("--list-unindexed", action="store_true")
    parser.add_argument("--show-records", metavar="DATE",
                        help="print the day's records as the entity saw them; no model call")
    args = parser.parse_args()
    try:
        db.require_store_not_migrating()   # every subcommand; migrations run only at server startup
    except db.StoreWouldMigrateError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    db.init_databases()
    if args.show_records:
        return cmd_show_records(args.show_records, args.with_clause)
    if args.index:
        return cmd_index(args.index)
    if args.list_unindexed:
        return cmd_list_unindexed()
    return cmd_write(args)


if __name__ == "__main__":
    sys.exit(main())
