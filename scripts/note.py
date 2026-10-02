"""The operator's command for notes: add, revise, retire, review and decide proposals.

    python -m scripts.note COMMAND [options]      (python -m scripts.note --help)

Design of record: ``docs/NOTES_DESIGN.md`` N4, N5, N10, N13; build plan piece 4. **This is the only
human control on what a note may say.** The entity proposes; nothing becomes a note until a person
decides here. Every command is non-interactive and explicit.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from program import config
from program.memory import note_admin as admin

EPILOG = """\
guarantees:
  * every decision, its note change and its approval_log row are written in ONE transaction:
    if any part fails, nothing changes.
  * this command never migrates and never creates a store. It refuses a store below schema
    version 8 (the real store is at version 6 until the server's first startup applies
    migrations 7 and 8). Migrations run only at server startup.
  * a proposal is decided once. A change of mind is a new proposal.
  * `approval off` lets the entity's proposals take effect with no one reviewing them. It is logged
    (approval_required_off), read fresh on every proposal (a running server sees it at once), and
    it does NOT apply proposals already pending. `review --applied` lists what it let through.
  * subject_user_id is set only by --subject-user. A household member's name in the subject is
    suggested, never applied.
  * the identity gate's verdict shown in `review` is a noisy aid, NOT a control: the control is you
    reading the proposal, its evidence and the entity's own reply.

exit status: 0 done; 1 refused or failed (nothing changed, or a stale proposal recorded as
rejected); 2 bad usage; 3 the store is not one this command may touch.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.note",
        description="Notes: operator add / revise / retire, and review of the entity's proposals.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    add = sub.add_parser("add", help="add a note now (origin operator, logged)")
    add.add_argument("--kind", required=True, choices=admin.SUBJECT_KINDS)
    add.add_argument("--subject", required=True, help="a short label, e.g. a name")
    add.add_argument("--text", required=True, help="the note: one short fact")
    add.add_argument("--subject-user", metavar="NAME",
                     help="link the note to this household member (never set otherwise)")
    add.add_argument("--evidence", action="append", metavar="MESSAGE_ID",
                     help="optional: a message id (or prefix) the note rests on; repeatable")

    revise = sub.add_parser("revise", help="replace an active note's text now (old one kept, "
                                           "marked superseded)")
    revise.add_argument("note_id")
    revise.add_argument("--text", required=True)
    revise.add_argument("--subject")
    revise.add_argument("--subject-user", metavar="NAME")

    retire = sub.add_parser("retire", help="retire an active note now (its text is kept)")
    retire.add_argument("note_id")

    lst = sub.add_parser("list", help="list notes (active by default)")
    lst.add_argument("--all", action="store_true", help="include superseded and retired notes")

    review = sub.add_parser(
        "review", help="with no id, list pending proposals; with an id, show everything a "
                       "reviewer needs to decide it")
    review.add_argument("proposal_id", nargs="?")
    review.add_argument("--applied", action="store_true",
                        help="show proposals applied WITHOUT review, each in full (read-only)")
    review.add_argument("--limit", type=int, default=20, help="with --applied: how many")

    approve = sub.add_parser("approve", help="approve a pending proposal as proposed")
    approve.add_argument("proposal_id")
    approve.add_argument("--subject-user", metavar="NAME",
                         help="link the note to this household member")

    edit = sub.add_parser("edit", help="approve with changed text (both texts kept in the log)")
    edit.add_argument("proposal_id")
    edit.add_argument("--text", required=True, help="the text that takes effect")
    edit.add_argument("--subject-user", metavar="NAME")

    reject = sub.add_parser("reject", help="reject a pending proposal")
    reject.add_argument("proposal_id")
    reject.add_argument("--reason", help="kept in the approval log")

    approval = sub.add_parser(
        "approval", help="switch approval on or off, or show it (the only way to change it)")
    approval.add_argument("state", choices=("on", "off", "status"))

    misses = sub.add_parser("misses", help="searches that found no note, newest first (read-only)")
    misses.add_argument("--limit", type=int, default=50)

    sub.add_parser("check", help="compare the search index with the active notes (read-only)")
    sub.add_parser("reindex", help="rebuild the search index from the notes (changes no note)")
    return parser


# --- formatting -------------------------------------------------------------------------------


def when(iso: str | None) -> str:
    if not iso:
        return "unknown time"
    try:
        local = datetime.fromisoformat(iso).astimezone(ZoneInfo(config.timezone()))
    except ValueError:
        return iso
    return local.strftime("%Y-%m-%d %H:%M")


def untrusted_line(raw: str | None) -> str:
    """NULL, [] and a list are three different statements and are never merged."""
    if raw is None:
        return "not recorded (the turn did not store it, so this is NOT a statement that none ran)"
    tools = json.loads(raw)
    if not tools:
        return "none (recorded: no tool returning outside text ran before this proposal)"
    return ("WARNING: this turn read text written OUTSIDE the household before proposing: "
            + ", ".join(tools))


def verdict_lines(raw: str | None, indent: str = "  ") -> list[str]:
    if raw is None:
        return [f"{indent}no verdict recorded (this is not 'clean')"]
    data = json.loads(raw)
    lines = [f"{indent}status: {data.get('status')}"]
    if data.get("status") == "unavailable":
        lines.append(f"{indent}the classifier could not run; this is UNCHECKED, not clean "
                     f"({data.get('semantic_error')})")
    for finding in data.get("findings", []):
        lines.append(f"{indent}cited: {finding.get('evidence')!r}")
        lines.append(f"{indent}reason: {finding.get('detail')}")
    return lines


#: What happened to the proposal the entity's reply is about, by the row's own status.
#: Never says "pending" for a decided one, and never says a note exists for a pending one.
OUTCOME_LINES = {
    "pending": "This proposal is pending. Nothing is saved until you approve it.",
    "approved": "This proposal was approved. The note was saved.",
    "edited": "This proposal was approved with changed text. The note was saved with that text.",
    "rejected": "This proposal was rejected. Nothing was saved.",
    "applied_without_review": "This proposal was applied without review. The note was saved "
                              "and no person reviewed it.",
}


def outcome_line(status: str) -> str:
    return OUTCOME_LINES.get(status, f"This proposal's status is {status!r}.")


def render_review(view: dict) -> str:
    p = view["proposal"]
    out: list[str] = []
    add = out.append
    add(f"PROPOSAL {p['id']}")
    add(f"  action: {p['action']}    status: {p['status']}    proposed {when(p['created_at'])}"
        f"    made on behalf of: {view['made_for'] or p['user_id']}")
    add("")
    add("UNTRUSTED CONTEXT (read this first)")
    add(f"  {untrusted_line(view['untrusted_raw'])}")
    add("")
    add("ORIGIN")
    conv = view["conversation"] or {}
    add(f"  conversation: {p['conversation_id']}  (belongs to {conv.get('owner') or 'unknown'}, "
        f"started {when(conv.get('started_at'))})")
    trigger = view["trigger"]
    if trigger:
        add(f"  triggering message: {trigger['id']}")
        add(f"    [{when(trigger['timestamp'])}] {trigger['speaker'] or trigger['role']}: "
            f"{trigger['content']}")
    else:
        add(f"  triggering message: {p['user_message_id']} (not found)")
    add(f"  call id: {p['call_id'] or '(none recorded)'}")
    add("")
    add("PROPOSED")
    add(f"  kind: {p['subject_kind']}    subject: {p['subject']}")
    if view["household_suggestion"]:
        add(f"  suggestion: the subject names household member {view['household_suggestion']}. "
            f"Nothing is linked unless you approve with --subject-user "
            f"{view['household_suggestion']}.")
    if p["text"] is not None:
        add(f"  text: {p['text']}")
    if p["action"] in ("revise", "retire"):
        target = view["target"]
        if target is None:
            add(f"  target note: {p['target_note_id']} (not found)")
        else:
            state = "active" if view["target_is_active"] else target["status"].upper()
            add(f"  target note: {target['id']}  version {target['version']}  [{state}]")
            add(f"    the note now says: {target['text']}")
            if not view["target_is_active"] and p["status"] == "pending":
                add("    ** the note changed since this was proposed: approving will be refused "
                    "and recorded as rejected **")
        if view["diff"]:
            add("  diff:")
            out.extend(f"    {line}" for line in view["diff"])
    add("")
    add("EVIDENCE")
    for i, item in enumerate(view["evidence"], start=1):
        message = item["message"]
        add(f"  {i}. quote: {item['quote']!r}")
        speaker = (f"{message['speaker']} (role {message['role']})" if message else "unknown")
        add(f"     speaker: {speaker}    tier: {item['tier']}    "
            f"identical messages: {item.get('identical_count', 1)}"
            + ("  (the most recent is shown)" if item.get("identical_count", 1) > 1 else ""))
        if message:
            add(f"     message {message['id']} [{when(message['timestamp'])}]:")
            add(f"       {message['content']}")
        if item["tier"] == "store":
            add("     (found only outside this turn's context: nothing in the conversation "
                "showed it to the entity)")
    add("")
    result = view.get("result_note")
    if result is not None:
        add("RESULT")
        add(f"  note {result['id']}  version {result['version']}  [{result['status']}]  "
            f"origin {result['origin']}")
        add(f"    text now: {result['text']}")
        add("")
    add("IDENTITY GATE on the proposed text (flag-only; a noisy aid, NOT a control: it misses "
        "lived-through claims with no time marker and flags some accurate sentences; read the "
        "proposal yourself)")
    out.extend(verdict_lines(p["integrity_check"]))
    add("")
    add("THE ENTITY'S OWN REPLY TO THAT TURN (does it tell the person the note is saved?)")
    reply = view["reply"]
    if reply is None:
        add("  no reply found whose trace contains this call (the turn may not have completed)")
    else:
        add(f"  [{when(reply['timestamp'])}] {reply['content']}")
        add("  the gate's verdict on that reply:")
        out.extend(verdict_lines(reply["integrity_check"], "    "))
        if reply["integrity_advisory"]:
            for note in json.loads(reply["integrity_advisory"]):
                add(f"    advisory: {note}")
        add(f"  ({outcome_line(p['status'])})")
    add("")
    if p["status"] == "pending":
        short = p["id"][:8]
        add(f"decide:  approve {short} [--subject-user NAME]   |   edit {short} --text \"...\"   |"
            f"   reject {short} [--reason \"...\"]")
    else:
        add(f"this proposal is {p['status']}; decisions are final.")
    return "\n".join(out)


def render_applied(limit: int) -> str:
    rows = admin.list_applied(limit)
    if not rows:
        return "No proposals have been applied without review."
    parts = [f"{len(rows)} proposal(s) applied WITHOUT review, newest first (read-only). Each is "
             f"shown as a pending one is: this is where you judge whether approval could stay off."]
    for r in rows:
        parts.append("=" * 100)
        parts.append(render_review(admin.review_view(r["id"])))
    return "\n".join(parts)


def render_pending(rows) -> str:
    if not rows:
        return "No pending proposals."
    lines = [f"{len(rows)} pending proposal(s), oldest first. `review ID` shows one in full."]
    for r in rows:
        raw = r["untrusted_context"]
        if raw is None:
            flag = "untrusted: not recorded"
        elif not json.loads(raw):
            flag = "untrusted: none"
        else:
            flag = "UNTRUSTED CONTEXT: " + ", ".join(json.loads(raw))
        lines.append(f"  {r['id'][:8]}  {r['action']:6} {r['subject_kind']:7} "
                     f"{r['subject'][:30]!r:34} for {r['made_for']}  {when(r['created_at'])}  "
                     f"[{flag}]")
    return "\n".join(lines)


def render_notes(rows) -> str:
    if not rows:
        return "No notes."
    lines = []
    for r in rows:
        link = f"  (linked to {r['subject_user_name']})" if r["subject_user_name"] else ""
        confirmed = when(r["last_confirmed_at"])
        lines.append(f"{r['id'][:8]}  v{r['version']}  {r['status']:10} {r['subject_kind']:7} "
                     f"{r['subject']!r}  origin {r['origin']}  confirmed {confirmed}"
                     f"{link}\n          {r['text']}")
    return "\n".join(lines)


# --- running ----------------------------------------------------------------------------------


def run(args) -> int:
    c = args.command
    if c == "add":
        print(admin.operator_add(args.kind, args.subject, args.text, subject_user=args.subject_user,
                                 evidence=args.evidence).message)
    elif c == "revise":
        print(admin.operator_revise(args.note_id, args.text, subject=args.subject,
                                    subject_user=args.subject_user).message)
    elif c == "retire":
        print(admin.operator_retire(args.note_id).message)
    elif c == "list":
        print(render_notes(admin.list_notes(args.all)))
    elif c == "review" and args.applied:
        print(render_applied(args.limit))
    elif c == "review":
        if args.proposal_id:
            print(render_review(admin.review_view(args.proposal_id)))
        else:
            if not admin.notes_db.approval_required_now():
                print("NOTE: approval is OFF. New proposals are applied at once; the ones below "
                      "stay pending until you decide them.\n")
            print(render_pending(admin.list_pending()))
    elif c == "approval":
        if args.state == "status":
            required = admin.notes_db.approval_required_now()
            print("approval is REQUIRED." if required else
                  "approval is OFF: proposals are applied without review.")
            print(f"{admin.pending_count()} pending proposal(s).")
        else:
            print(admin.set_approval_required(args.state == "on").message)
    elif c in ("approve", "edit", "reject"):
        decision = {"approve": "approved", "edit": "edited", "reject": "rejected"}[c]
        result = admin.decide(args.proposal_id, decision,
                              text=getattr(args, "text", None),
                              subject_user=getattr(args, "subject_user", None),
                              reason=getattr(args, "reason", None))
        print(result.message)
        if result.suggestion:
            print(f"note: the subject names household member {result.suggestion}, and no link "
                  f"was set. To link it: note revise {result.note_id[:8]} --text \"...\" "
                  f"--subject-user {result.suggestion}")
        return 1 if result.outcome == "rejected_stale" else 0
    elif c == "misses":
        found = admin.misses(args.limit)
        if not found:
            print("No searches that found nothing.")
        for m in found:
            print(f"{when(m['when'])}  {m['whose_turn']}'s turn  query: {m['query']!r}")
        print("(A miss is visible here, not explained: it may be a paraphrase, a note never made, "
              "or a retired note.)" if found else "")
    elif c == "check":
        report = admin.check_index()
        print(f"active notes: {report.active}; indexed: {report.indexed}; "
              f"FTS5 integrity check: {report.fts_check}")
        if report.missing:
            print(f"  active but not indexed (rowids): {report.missing}")
        if report.extra:
            print(f"  indexed but not active (rowids): {report.extra}")
        print("consistent" if report.consistent else "DRIFT: run `reindex`")
        return 0 if report.consistent else 1
    elif c == "reindex":
        report = admin.reindex()
        print(f"rebuilt the index from notes: active {report.active}, indexed {report.indexed}, "
              f"FTS5 check: {report.fts_check}")
        return 0 if report.consistent else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        # Before anything else touches a database: refuse a store this command may not use.
        admin.require_schema()
    except admin.AdminError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    try:
        return run(args)
    except admin.AdminError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
