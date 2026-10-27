"""The note pass: after a conversation closes, the entity decides what in it becomes a note.

Design of record: ``docs/NOTES_V2_DESIGN.md`` revision 3 (decisions #36 and #37), section 1.
**It writes proposals, never notes.** Every proposal is inserted pending (#36(c)) and only the
operator's command decides it (``note_admin``).

**When it runs.** On a conversation that is closed, fully chunked and has no completed pass, read
from the ``note_pass_status`` view (migration 11). :func:`drain` takes the oldest such and is called
from ``scripts/close_idle_conversations.py --passes N`` and ``python -m scripts.note pass`` (and
the play kit's ``/kit/end``). **Never from the chat route's post-turn sweep** (2026-10-27): Ollama
runs one chat at a time and queue time counts against a waiting call's timeout, so a pass there
could make a later turn's gate check time out. A module lock lets one pass run at a time in a
process; across processes the scripts refuse while a server holds the store, and
``UNIQUE(conversation_id, attempt)`` refuses a second row for one attempt.

**What it sends.** One model call per piece, at the classifier's settings (the chat model,
temperature 0, the same ``num_ctx``) with its own output cap and timeout. ``soul.md`` and the
instruction (``note_texts.PASS_INSTRUCTION``) are the system message; the date, the person's
name, the active notes (N), the pending proposals (P), this conversation's drafts (D) and the piece
are the user message. **Tool results are never sent**: external text cannot reach a note.

**What happens to the reply** (section 1.1): a piece's blocks are held as drafts, which the next
piece sees and may DROP; after the last piece the surviving drafts go through quote resolution
(N4, this conversation as the context tier), the identity gate (N7, flag-only) and the untrusted
flag, and are written in **one transaction** with the attempt's ``note_passes`` row. An ADD that
duplicates an active note, a pending proposal or another draft is refused; a malformed block is
recorded, never repaired. A failed piece fails the attempt: nothing but the row is written, and
the outcome is ``unavailable``, retried by :func:`drain` up to ``notes.pass_max_attempts``.

**A cut output** (section 1.3) is detected by ``done_reason == "length"``, never by parsing.
Every block followed by its blank line is complete and kept; the block the output stopped in is
stored in the row and never written, even if it parses. The attempt reads ``truncated`` and is not
retried automatically.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from program import config
from program.engine import ollama
from program.memory import db
from program.memory import notes as notes_db
from program.memory.db import retry_on_locked
from program.tools import note_quotes
from program.tools import note_texts as texts

logger = logging.getLogger(__name__)

#: One pass at a time in this process (section 1).
_LOCK = threading.Lock()

KINDS = ("person", "topic", "project")
OUTCOMES = ("proposals", "none", "truncated", "unavailable")

#: How the entity's own messages are labelled in a piece.
ENTITY_LABEL = "you"


class PassRefused(Exception):
    """A pass that may not run: Notes is off, the conversation is not closed and chunked, or
    another pass holds the lock. Nothing was sent and nothing was written."""


# --- parsing --------------------------------------------------------------------------------


@dataclass
class Block:
    """One change the reply asked for."""

    action: str                       # add | revise | retire | drop
    raw: str
    number: int | None = None         # N{number} for revise/retire, D{number} for drop
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    #: ``{"raw": ..., "reason": ...}`` for each block dropped as malformed.
    malformed: list[dict[str, str]] = field(default_factory=list)


_HEAD = re.compile(r"^(ADD|REVISE N(\d+)|RETIRE N(\d+)|DROP D(\d+)|NONE)$")
_FIELD = re.compile(r"^([a-z]+):\s?(.*)$")
_NEEDS = {
    "add": ("kind", "subject", "line", "note", "quote"),
    "revise": ("line", "note", "quote"),
    "retire": ("quote",),
    "drop": (),
}


def _limits() -> dict[str, int]:
    return {
        "subject": config.notes_max_subject_chars(),
        "line": config.notes_line_max_chars(),
        "note": config.notes_max_text_chars(),
        "quote": config.notes_pass_quote_max_chars(),
    }


def split_blocks(text: str) -> list[str]:
    """Blocks are separated by one or more blank lines."""
    return [b.strip("\n") for b in re.split(r"\n[ \t]*\n", text.strip()) if b.strip()]


def parse_block(raw: str) -> Block:
    """One block, or ``ValueError`` saying why it is malformed. Never repaired. ``NONE`` is read
    in any case: the B3 trial's replies included a bare "none", which says nothing else."""
    lines = [line.rstrip() for line in raw.strip().split("\n")]
    if lines[0].strip().upper() == "NONE":
        lines[0] = "NONE"
    head = _HEAD.match(lines[0].strip())
    if head is None:
        raise ValueError(f"unknown first line {lines[0][:40]!r}")
    word = head.group(1).split(" ")[0].lower()
    if word == "none":
        if len(lines) > 1:
            raise ValueError("NONE followed by more lines")
        return Block("none", raw)
    number = next((int(g) for g in head.groups()[1:] if g is not None), None)
    block = Block(word, raw, number)
    limits = _limits()
    for line in lines[1:]:
        match = _FIELD.match(line.strip())
        if match is None:
            raise ValueError(f"not a field line: {line[:40]!r}")
        key, value = match.group(1), match.group(2).strip()
        if key not in _NEEDS[word]:
            raise ValueError(f"{key!r} is not a field of {word.upper()}")
        if key in block.fields:
            raise ValueError(f"{key!r} given twice")
        if not value:
            raise ValueError(f"{key!r} is empty")
        if key in limits and len(value) > limits[key]:
            raise ValueError(f"{key} is {len(value)} characters; the limit is {limits[key]}")
        block.fields[key] = value
    missing = [k for k in _NEEDS[word] if k not in block.fields]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    if word == "add" and block.fields["kind"] not in KINDS:
        raise ValueError(f"kind {block.fields['kind']!r} is not one of {', '.join(KINDS)}")
    return block


def parse(reply: str) -> Parsed:
    """The blocks in a reply. ``NONE`` alone is no blocks; a ``NONE`` block among others is
    ignored. Every other unparseable block is recorded as malformed."""
    out = Parsed()
    for raw in split_blocks(reply):
        try:
            block = parse_block(raw)
        except ValueError as exc:
            out.malformed.append({"raw": raw, "reason": str(exc)})
            continue
        if block.action != "none":
            out.blocks.append(block)
    return out


def complete_part(reply: str) -> tuple[str, str]:
    """For a cut reply: (every block followed by its blank line, the block it stopped in).

    The last blank line is the boundary. With none, the whole reply is the cut block."""
    match = None
    for match in re.finditer(r"\n[ \t]*\n", reply):
        pass
    if match is None:
        return "", reply.strip()
    return reply[:match.start()], reply[match.end():].strip()


# --- pieces ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    message_id: str
    role: str
    text: str           # rendered: "Name: content" or "you: content"


def exchanges(rows: Sequence[Any], names: dict[str, str]) -> list[list[Line]]:
    """Runs of whole exchanges: each person's message with the replies after it. Messages before
    the first person's message join the first exchange."""
    out: list[list[Line]] = []
    for row in rows:
        if row["role"] == "user":
            label = names.get(row["user_id"], "a person")
        elif row["role"] == "assistant":
            label = ENTITY_LABEL
        else:
            continue
        line = Line(row["id"], row["role"], f"{label}: {row['content']}")
        starts_new = row["role"] == "user" and out and any(x.role == "user" for x in out[-1])
        if not out or starts_new:
            out.append([line])
        else:
            out[-1].append(line)
    return out


def _cost(exchange: list[Line]) -> int:
    return sum(len(x.text) + 1 for x in exchange)


def split_pieces(groups: list[list[Line]], cap: int) -> list[list[Line]]:
    """Whole exchanges in order, no overlap, packed greedily up to ``cap`` characters. A piece
    always holds at least one whole exchange, whatever its size."""
    pieces: list[list[Line]] = []
    current: list[Line] = []
    used = 0
    for group in groups:
        cost = _cost(group)
        if current and used + cost > cap:
            pieces.append(current)
            current, used = [], 0
        current.extend(group)
        used += cost
    if current:
        pieces.append(current)
    return pieces


# --- the prompt -------------------------------------------------------------------------------


@dataclass
class Draft:
    number: int
    block: Block
    target_note_id: str | None = None


def _note_entry(n: int, note: Any) -> str:
    line = note["line"] if note["line"] else "(no line)"
    return f"N{n} · {note['subject_kind']} · {note['subject']} · {line}\n   {note['text']}"


def _pending_entry(n: int, p: Any, subjects: dict[str, str]) -> str:
    if p["action"] == "add":
        what = f"ADD {p['subject_kind']} · {p['subject']} · {p['line'] or ''}\n   {p['text']}"
    elif p["action"] == "revise":
        what = (f"REVISE the note on {subjects.get(p['target_note_id'], p['subject'])} · "
                f"{p['line'] or ''}\n   {p['text']}")
    else:
        what = f"RETIRE the note on {subjects.get(p['target_note_id'], p['subject'])}"
    return f"P{n} · {what}"


def _draft_entry(d: Draft, subjects: dict[str, str]) -> str:
    b = d.block
    if b.action == "add":
        what = (f"ADD {b.fields['kind']} · {b.fields['subject']} · {b.fields['line']}\n"
                f"   {b.fields['note']}")
    elif b.action == "revise":
        what = (f"REVISE the note on {subjects.get(d.target_note_id, '?')} · {b.fields['line']}\n"
                f"   {b.fields['note']}")
    else:
        what = f"RETIRE the note on {subjects.get(d.target_note_id, '?')}"
    return f"D{d.number} · {what}"


@dataclass
class Listed:
    text: str
    numbered: dict[int, str]       # N number -> note id
    cut: bool


def notes_section(piece_text: str, drafts: list[Draft]) -> Listed:
    """The notes (N), pending proposals (P) and drafts (D) for one piece, under
    ``notes.pass_notes_max_chars``. Drafts go first, then pending proposals, then notes: those
    ``notes_fts`` matches against the piece, then the most recently confirmed (section 1.2)."""
    cap = config.notes_pass_notes_max_chars()
    every = notes_db.active_notes_newest_first()
    subjects = {n["id"]: n["subject"] for n in every}
    matched = notes_db.matching_active_ids(piece_text, len(every) or 1) if every else []
    by_id = {n["id"]: n for n in every}
    ordered = [by_id[i] for i in matched if i in by_id]
    ordered += [n for n in every if n["id"] not in set(matched)]

    used = 0
    cut = False

    def take(entry: str) -> bool:
        nonlocal used
        if used + len(entry) + 1 > cap:
            return False
        used += len(entry) + 1
        return True

    draft_lines = [e for e in (_draft_entry(d, subjects) for d in drafts) if take(e)]
    pending_lines = []
    for i, p in enumerate(notes_db.pending_proposals(), start=1):
        entry = _pending_entry(i, p, subjects)
        if take(entry):
            pending_lines.append(entry)
        else:
            cut = True
    numbered: dict[int, str] = {}
    note_lines = []
    for n in ordered:
        entry = _note_entry(len(numbered) + 1, n)
        if not take(entry):
            cut = True
            continue
        numbered[len(numbered) + 1] = n["id"]
        note_lines.append(entry)
    if len(draft_lines) < len(drafts):
        cut = True
    parts = ["Your notes:", *(note_lines or ["none"]), "",
             "Pending changes, waiting for Lyle:", *(pending_lines or ["none"]), "",
             "Your drafts from earlier pieces of this conversation:", *(draft_lines or ["none"])]
    return Listed("\n".join(parts), numbered, cut)


def _local_date(stamp: str | None) -> str:
    try:
        moment = datetime.fromisoformat(stamp).astimezone(ZoneInfo(config.timezone()))
    except (TypeError, ValueError):
        return "an unknown date"
    return moment.strftime("%A %d %B %Y")


def instruction() -> str:
    return texts.pass_instruction(
        subject=config.notes_max_subject_chars(), line=config.notes_line_max_chars(),
        text=config.notes_max_text_chars(), quote=config.notes_pass_quote_max_chars(),
        min_words=config.notes_min_quote_words())


def build_messages(*, soul: str, date: str, people: str, listed: Listed,
                   piece: list[Line], index: int, total: int) -> list[dict[str, str]]:
    where = "The conversation" if total == 1 else f"The conversation, piece {index} of {total}"
    user = "\n".join([
        f"This conversation took place on {date}, with {people}.",
        "",
        listed.text,
        "",
        f"{where}:",
        *(x.text for x in piece),
    ])
    return [{"role": "system", "content": f"{soul}\n\n{instruction()}"},
            {"role": "user", "content": user}]


def call_model(messages: list[dict[str, str]]) -> tuple[str, str | None]:
    """One piece's call: (the reply's text, ``done_reason``)."""
    data = ollama.chat(
        messages,
        model=config.chat_model(),
        options={"num_predict": config.notes_pass_num_predict(),
                 "temperature": config.classifier_temperature()},
        timeout=config.notes_pass_timeout_seconds(),
    )
    return data.get("message", {}).get("content", "") or "", data.get("done_reason")


# --- writing ----------------------------------------------------------------------------------


@dataclass
class PassResult:
    conversation_id: str
    attempt: int
    outcome: str
    pieces: int
    proposals: list[str] = field(default_factory=list)
    set_aside: list[dict[str, Any]] = field(default_factory=list)
    cut_pieces: list[int] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0


def _key(text: str | None) -> str:
    return note_quotes.normalise(text or "")


def _untrusted_tools_in(rows: Sequence[Any]) -> list[str]:
    """Untrusted-output tools that returned text anywhere in the conversation, in first-seen
    order, read from the stored traces against the full catalogue (``Tool.untrusted_output``)."""
    from program.tools import registry

    untrusted = set(registry.untrusted_tools())
    seen: list[str] = []
    for row in rows:
        if row["role"] != "assistant" or not row["tool_trace"]:
            continue
        try:
            entries = json.loads(row["tool_trace"])
        except ValueError:
            continue
        for e in entries if isinstance(entries, list) else []:
            tool = e.get("tool") if isinstance(e, dict) else None
            if tool in untrusted and e.get("outcome") == "ok" and tool not in seen:
                seen.append(tool)
    return seen


def _prepare(drafts: list[Draft], conversation: Any, context_ids: frozenset[str],
             untrusted: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The proposals to write, and what was set aside (duplicates, unresolved quotes)."""
    from program.integrity import gate

    active = notes_db.active_notes_newest_first()
    pending = notes_db.pending_proposals()
    taken_text = {_key(r["text"]) for r in [*active, *pending] if r["text"]}
    taken_line = {(_key(r["subject"]), _key(r["line"])) for r in [*active, *pending]
                  if r["line"]}
    rows: list[dict[str, Any]] = []
    set_aside: list[dict[str, Any]] = []
    for d in drafts:
        b = d.block
        if b.action == "add":
            line_key = (_key(b.fields["subject"]), _key(b.fields["line"]))
            if _key(b.fields["note"]) in taken_text or line_key in taken_line:
                set_aside.append({"raw": b.raw, "reason": "duplicate of an active note, a "
                                  "pending proposal or another draft"})
                continue
        try:
            resolved = note_quotes.resolve_quote(b.fields["quote"], context_ids)
        except texts.QuoteRefused as exc:
            set_aside.append({"raw": b.raw, "reason": f"quote: {exc}"})
            continue
        text = b.fields.get("note")
        if b.action == "add":
            taken_text.add(_key(text))
            taken_line.add((_key(b.fields["subject"]), _key(b.fields["line"])))
        target = None
        if d.target_note_id:
            with db.connection() as conn:
                target = conn.execute("SELECT * FROM notes WHERE id = ?",
                                      (d.target_note_id,)).fetchone()
        verdict = gate.check_identity(text) if text else None
        rows.append({
            "id": db.new_id(), "action": b.action, "target_note_id": d.target_note_id,
            "subject_kind": b.fields["kind"] if b.action == "add" else target["subject_kind"],
            "subject": b.fields["subject"] if b.action == "add" else target["subject"],
            "text": text, "line": b.fields.get("line"),
            "evidence": json.dumps([resolved.to_dict()]),
            "conversation_id": conversation["id"], "user_message_id": resolved.message_id,
            "user_id": conversation["user_id"],
            "untrusted_context": json.dumps(untrusted),
            "integrity_check": verdict.to_json() if verdict is not None else None,
        })
    return rows, set_aside


@retry_on_locked
def _write(conversation_id: str, outcome: str, rows: list[dict[str, Any]], *, pieces: int,
           replies: list[str], cut_pieces: list[int], cut_blocks: list[str],
           set_aside: list[dict[str, Any]], notes_list_cut: bool, error: str | None,
           seconds: float) -> int:
    """The attempt's row and its proposals, in ONE transaction. Returns the attempt number."""
    with db.transaction() as conn:
        attempt = conn.execute(
            "SELECT COALESCE(MAX(attempt), 0) + 1 FROM note_passes WHERE conversation_id = ?",
            (conversation_id,)).fetchone()[0]
        now = db.now_iso()
        for r in rows:
            conn.execute(
                """INSERT INTO note_proposals
                       (id, action, target_note_id, subject_kind, subject, text, line, evidence,
                        conversation_id, user_message_id, call_id, user_id, status,
                        untrusted_context, integrity_check, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'pending', ?, ?, ?)""",
                (r["id"], r["action"], r["target_note_id"], r["subject_kind"], r["subject"],
                 r["text"], r["line"], r["evidence"], r["conversation_id"],
                 r["user_message_id"], r["user_id"], r["untrusted_context"],
                 r["integrity_check"], now))
        conn.execute(
            """INSERT INTO note_passes
                   (id, conversation_id, attempt, outcome, pieces, proposals, piece_replies,
                    cut_pieces, cut_blocks, set_aside, notes_list_cut, error, seconds,
                    created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (db.new_id(), conversation_id, attempt, outcome, pieces, len(rows),
             json.dumps(replies), json.dumps(cut_pieces), json.dumps(cut_blocks),
             json.dumps(set_aside), int(notes_list_cut), error, round(seconds, 3), now))
    return attempt


# --- running ----------------------------------------------------------------------------------


def status(conversation_id: str) -> Any:
    with db.connection() as conn:
        return conn.execute("SELECT * FROM note_pass_status WHERE conversation_id = ?",
                            (conversation_id,)).fetchone()


def run_pass(conversation_id: str, *, wait: bool = True) -> PassResult:
    """One attempt at one conversation. Raises :class:`PassRefused` when it may not run."""
    if not config.notes_enabled():
        raise PassRefused("Notes is off (notes.enabled); no pass runs.")
    if not _LOCK.acquire(blocking=wait):
        raise PassRefused("another note pass is running in this process.")
    try:
        return _run(conversation_id)
    finally:
        _LOCK.release()


def _run(conversation_id: str) -> PassResult:
    conversation = db.get_conversation(conversation_id)
    if conversation is None:
        raise PassRefused(f"no conversation {conversation_id!r}.")
    if conversation["ended_at"] is None:
        raise PassRefused("the conversation is still open; a pass runs only after it closes.")
    if not conversation["chunked"]:
        raise PassRefused("the conversation is closed but not yet chunked; its pass waits.")

    started = time.monotonic()
    rows = db.get_conversation_messages(conversation_id)
    names = {u["id"]: u["name"] for u in db.list_users()}
    groups = exchanges(rows, names)
    if not any(x.role == "user" for g in groups for x in g):
        attempt = _write(conversation_id, "none", [], pieces=0, replies=[], cut_pieces=[],
                         cut_blocks=[], set_aside=[], notes_list_cut=False, error=None,
                         seconds=time.monotonic() - started)
        return PassResult(conversation_id, attempt, "none", 0)

    from program.engine import prompt

    soul = prompt.load_soul()
    pieces = split_pieces(groups, config.notes_pass_piece_max_chars())
    people = ", ".join(dict.fromkeys(names.get(r["user_id"], "a person")
                                     for r in rows if r["role"] == "user"))
    # The day it was said: the first message's, which a conversation started and left idle
    # before its first message would otherwise misstate.
    date = _local_date(rows[0]["timestamp"] if rows else conversation["started_at"])

    drafts: list[Draft] = []
    next_draft = 1
    replies: list[str] = []
    cut_pieces: list[int] = []
    cut_blocks: list[str] = []
    set_aside: list[dict[str, Any]] = []
    list_cut = False
    for index, piece in enumerate(pieces, start=1):
        listed = notes_section("\n".join(x.text for x in piece), drafts)
        list_cut = list_cut or listed.cut
        messages = build_messages(soul=soul, date=date, people=people, listed=listed,
                                  piece=piece, index=index, total=len(pieces))
        try:
            reply, done_reason = call_model(messages)
        except Exception as exc:  # noqa: BLE001 - a failed piece fails the attempt, recorded
            error = f"piece {index} of {len(pieces)}: {type(exc).__name__}: {exc}"
            logger.warning("note pass for %s unavailable: %s", conversation_id[:8], error)
            attempt = _write(conversation_id, "unavailable", [], pieces=len(pieces),
                             replies=replies, cut_pieces=cut_pieces, cut_blocks=cut_blocks,
                             set_aside=set_aside, notes_list_cut=list_cut, error=error,
                             seconds=time.monotonic() - started)
            return PassResult(conversation_id, attempt, "unavailable", len(pieces),
                              error=error, seconds=time.monotonic() - started)
        replies.append(reply)
        usable = reply
        if done_reason == "length":
            usable, cut = complete_part(reply)
            cut_pieces.append(index)
            cut_blocks.append(cut)
        parsed = parse(usable)
        set_aside.extend({**m, "piece": index, "why": "malformed"} for m in parsed.malformed)
        for block in parsed.blocks:
            if block.action == "drop":
                found = next((d for d in drafts if d.number == block.number), None)
                if found is None:
                    set_aside.append({"raw": block.raw, "piece": index, "why": "malformed",
                                      "reason": f"no draft D{block.number}"})
                else:
                    drafts.remove(found)
                    set_aside.append({"raw": found.block.raw, "piece": index, "why": "dropped",
                                      "reason": f"D{found.number} dropped"})
                continue
            target = None
            if block.action in ("revise", "retire"):
                target = listed.numbered.get(block.number)
                if target is None:
                    set_aside.append({"raw": block.raw, "piece": index, "why": "malformed",
                                      "reason": f"no note N{block.number} was shown"})
                    continue
            drafts.append(Draft(next_draft, block, target))
            next_draft += 1

    context_ids = frozenset(r["id"] for r in rows)
    try:
        proposals, refused = _prepare(drafts, conversation, context_ids,
                                      _untrusted_tools_in(rows))
    except Exception as exc:  # noqa: BLE001 - nothing written but the row, which says why
        error = f"after the last piece: {type(exc).__name__}: {exc}"
        logger.warning("note pass for %s unavailable: %s", conversation_id[:8], error)
        attempt = _write(conversation_id, "unavailable", [], pieces=len(pieces), replies=replies,
                         cut_pieces=cut_pieces, cut_blocks=cut_blocks, set_aside=set_aside,
                         notes_list_cut=list_cut, error=error,
                         seconds=time.monotonic() - started)
        return PassResult(conversation_id, attempt, "unavailable", len(pieces), error=error,
                          seconds=time.monotonic() - started)
    set_aside.extend({**r, "why": "refused"} for r in refused)
    outcome = "truncated" if cut_pieces else ("proposals" if proposals else "none")
    seconds = time.monotonic() - started
    attempt = _write(conversation_id, outcome, proposals, pieces=len(pieces), replies=replies,
                     cut_pieces=cut_pieces, cut_blocks=cut_blocks, set_aside=set_aside,
                     notes_list_cut=list_cut, error=None, seconds=seconds)
    logger.info("note pass for %s: %s, %d proposal(s), %d piece(s), %.1fs",
                conversation_id[:8], outcome, len(proposals), len(pieces), seconds)
    return PassResult(conversation_id, attempt, outcome, len(pieces),
                      [p["id"] for p in proposals], set_aside, cut_pieces, None, seconds)


def due(limit: int) -> list[str]:
    """Conversations whose pass is due, oldest closed first: ``not_run``, or ``unavailable``
    with attempts left. ``truncated`` is never due (section 1.3)."""
    with db.connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT conversation_id FROM note_pass_status "
            "WHERE status = 'not_run' OR (status = 'unavailable' AND attempts < ?) "
            "ORDER BY ended_at, conversation_id LIMIT ?",
            (config.notes_pass_max_attempts(), limit))]


def drain(limit: int = 1) -> list[PassResult]:
    """Run the pass on up to ``limit`` due conversations. Does nothing when Notes is off or
    another pass holds the lock. Never raises for a failed pass: that is recorded as
    ``unavailable``."""
    if not config.notes_enabled():
        return []
    if not _LOCK.acquire(blocking=False):
        return []
    try:
        results = []
        for cid in due(limit):
            try:
                results.append(_run(cid))
            except Exception as exc:  # noqa: BLE001 - logged; the next drain tries again
                logger.warning("note pass for %s did not run: %s: %s", cid[:8],
                               type(exc).__name__, exc)
        return results
    finally:
        _LOCK.release()
