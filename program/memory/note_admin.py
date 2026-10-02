"""What the operator does to notes. Notes piece 4 (`docs/NOTES_BUILD_PLAN.md`).

``scripts/note.py`` is the command line over this module. **This is the only human control on what
a note may say** (N0): the entity proposes, and nothing becomes a note until a person decides here.

Rules that hold for every write in this module:

* **One transaction per decision.** The note change, the proposal's status flip and the
  ``approval_log`` row (``db.record_approval``) are written inside one ``db.transaction()``, so a
  failure anywhere leaves nothing changed. There is no route by which a note changes and the log
  does not know.
* **The connection helper is used throughout** (``db.connection`` / ``db.transaction``), because
  ``FOREIGN KEY`` clauses are inert on a plain ``sqlite3`` connection.
* **It never migrates.** :func:`require_schema` refuses a store below schema version 8 before any
  other work, and refuses to create a store that does not exist. Migrations run at server startup
  only.
* **A proposal is flipped by an UPDATE from ``pending``**, in the same transaction as the note it
  creates or changes; the schema refuses any other route to a decided status.
* **``subject_user_id`` is set only when the reviewer asks for it** (``--subject-user``). A
  household member's name in the subject is *suggested*, never applied.
"""

from __future__ import annotations

import difflib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from program import config
from program.memory import db
from program.memory.db import retry_on_locked
from program.settings.permissions import OPERATOR_ID
from program.tools import note_texts

CAPABILITY = "notes"
MIN_SCHEMA_VERSION = 8
SUBJECT_KINDS = ("person", "topic", "project")
STALE_REASON = "the note changed since this was proposed"


class AdminError(Exception):
    """The operator asked for something that cannot be done. The message says why; nothing was
    changed. (Distinct from a decision *recorded* as a rejection.)"""


# --- the store --------------------------------------------------------------------------------


def require_schema() -> int:
    """Refuse a store this command must not touch; return its schema version.

    **Never migrates and never creates**: it opens ``working.db`` read-only and reads
    ``schema_version``. A missing store, or one below version 8, is refused with the reason. The
    real store is at version 6 until the server's first startup applies migrations 7 and 8.
    """
    working, archive = db.working_path(), db.archive_path()
    for path in (working, archive):
        if not path.exists():
            raise AdminError(
                f"there is no store at {path}. This command never creates one: databases are "
                f"created and migrated by the server's startup."
            )
    try:
        conn = sqlite3.connect(f"file:{working}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise AdminError(f"cannot open {working} read-only: {exc}") from exc
    try:
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        except sqlite3.OperationalError:
            version = 0
    finally:
        conn.close()
    if version < MIN_SCHEMA_VERSION:
        raise AdminError(
            f"this store is at schema version {version}; Notes needs version "
            f"{MIN_SCHEMA_VERSION}. This command never migrates: migrations run only when the "
            f"server starts."
        )
    return version


def _now() -> str:
    return db.now_iso()


# --- lookups ----------------------------------------------------------------------------------


def _by_prefix(conn: sqlite3.Connection, table: str, prefix: str, what: str) -> sqlite3.Row:
    prefix = (prefix or "").strip()
    if not prefix:
        raise AdminError(f"give a {what} id.")
    escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    rows = conn.execute(f"SELECT * FROM {table} WHERE id LIKE ? ESCAPE '!'",
                        (escaped + "%",)).fetchall()
    if not rows:
        raise AdminError(f"no {what} has an id starting {prefix!r}.")
    if len(rows) > 1:
        raise AdminError(f"{prefix!r} matches {len(rows)} {what}s; give more of the id.")
    return rows[0]


def household_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return [u for u in conn.execute("SELECT * FROM users ORDER BY created_at")
            if u["name"] != db.ENTITY_USER_NAME]


def household_match(conn: sqlite3.Connection, subject: str) -> sqlite3.Row | None:
    """The household member whose name is the subject (case-insensitive), or ``None``.
    A **suggestion** only: nothing here applies it."""
    wanted = (subject or "").strip().lower()
    for user in household_users(conn):
        if user["name"].lower() == wanted:
            return user
    return None


def _resolve_subject_user(conn: sqlite3.Connection, name: str | None) -> str | None:
    if name is None:
        return None
    for user in household_users(conn):
        if user["name"].lower() == name.strip().lower():
            return user["id"]
    known = ", ".join(u["name"] for u in household_users(conn)) or "(none)"
    raise AdminError(f"{name!r} is not a household member. Known: {known}.")


def _check_note_fields(
    kind: str, subject: str, text: str | None, need_text: bool
) -> tuple[str, str | None]:
    if kind not in SUBJECT_KINDS:
        raise AdminError(f"kind must be one of {', '.join(SUBJECT_KINDS)}; got {kind!r}.")
    subject = (subject or "").strip()
    if not subject:
        raise AdminError("the subject is empty.")
    if len(subject) > config.notes_max_subject_chars():
        raise AdminError(f"the subject is {len(subject)} characters; the limit is "
                         f"{config.notes_max_subject_chars()}.")
    text = text.strip() if isinstance(text, str) else text
    if need_text:
        if not text:
            raise AdminError("the note text is empty.")
        if len(text) > config.notes_max_text_chars():
            raise AdminError(f"the note is {len(text)} characters; the limit is "
                             f"{config.notes_max_text_chars()}. A note is one short fact: "
                             f"make it two notes.")
    return subject, text


def _insert_note(conn: sqlite3.Connection, *, kind, subject, subject_user_id, text, origin,
                 version=1, previous_note_id=None) -> str:
    note_id = db.new_id()
    now = _now()
    conn.execute(
        "INSERT INTO notes (id, subject_kind, subject, subject_user_id, text, status, version, "
        "previous_note_id, origin, created_at, last_confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
        (note_id, kind, subject, subject_user_id, text, version, previous_note_id, origin, now,
         now))
    return note_id


def _require_active(conn: sqlite3.Connection, note_prefix: str) -> sqlite3.Row:
    note = _by_prefix(conn, "notes", note_prefix, "note")
    if note["status"] != "active":
        raise AdminError(f"note {note['id'][:8]} is {note['status']}, not active.")
    return note


def _check_evidence_ids(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    out = []
    for prefix in ids or []:
        out.append(_by_prefix(conn, "messages", prefix, "message")["id"])
    return out


# --- the operator's own notes (immediate, origin operator, logged) ----------------------------


@dataclass(frozen=True)
class Done:
    note_id: str | None
    message: str


@retry_on_locked
def operator_add(kind: str, subject: str, text: str, *, subject_user: str | None = None,
                 evidence: list[str] | None = None) -> Done:
    subject, text = _check_note_fields(kind, subject, text, need_text=True)
    with db.transaction() as conn:
        user_id = _resolve_subject_user(conn, subject_user)
        evidence_ids = _check_evidence_ids(conn, evidence or [])
        note_id = _insert_note(conn, kind=kind, subject=subject, subject_user_id=user_id,
                               text=text, origin="operator")
        db.record_approval(conn, capability=CAPABILITY, subject_kind="note", subject_id=note_id,
                           decision="operator_add", decided_by=OPERATOR_ID,
                           detail={"note_id": note_id, "evidence_message_ids": evidence_ids})
    return Done(note_id, f"added note {note_id[:8]} (origin operator).")


@retry_on_locked
def operator_revise(note_prefix: str, text: str, *, subject: str | None = None,
                    subject_user: str | None = None) -> Done:
    with db.transaction() as conn:
        old = _require_active(conn, note_prefix)
        new_subject, text = _check_note_fields(old["subject_kind"], subject or old["subject"],
                                               text, need_text=True)
        user_id = (_resolve_subject_user(conn, subject_user)
                   if subject_user is not None else old["subject_user_id"])
        conn.execute("UPDATE notes SET status = 'superseded' WHERE id = ?", (old["id"],))
        new_id = _insert_note(conn, kind=old["subject_kind"], subject=new_subject,
                              subject_user_id=user_id, text=text, origin="operator",
                              version=old["version"] + 1, previous_note_id=old["id"])
        db.record_approval(conn, capability=CAPABILITY, subject_kind="note", subject_id=new_id,
                           decision="operator_revise", decided_by=OPERATOR_ID,
                           detail={"previous_note_id": old["id"], "note_id": new_id,
                                   "before": old["text"], "after": text})
    return Done(new_id, f"revised: note {old['id'][:8]} is superseded by {new_id[:8]} "
                        f"(version {old['version'] + 1}).")


@retry_on_locked
def operator_retire(note_prefix: str) -> Done:
    with db.transaction() as conn:
        old = _require_active(conn, note_prefix)
        conn.execute("UPDATE notes SET status = 'retired', retired_at = ? WHERE id = ?",
                     (_now(), old["id"]))
        db.record_approval(conn, capability=CAPABILITY, subject_kind="note", subject_id=old["id"],
                           decision="operator_retire", decided_by=OPERATOR_ID,
                           detail={"note_id": old["id"], "text": old["text"]})
    return Done(old["id"], f"retired note {old['id'][:8]} (its text is kept).")


# --- deciding a proposal ----------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    outcome: str                  # approved | edited | rejected | rejected_stale
    proposal_id: str
    note_id: str | None
    message: str
    suggestion: str | None = None  # a household name the subject matches and was NOT applied


def _flip(conn: sqlite3.Connection, proposal_id: str, status: str, resulting: str | None) -> None:
    cur = conn.execute(
        "UPDATE note_proposals SET status = ?, decided_at = ?, resulting_note_id = ? "
        "WHERE id = ? AND status = 'pending'", (status, _now(), resulting, proposal_id))
    if cur.rowcount != 1:
        raise AdminError("that proposal is already decided; decisions are final. A change of "
                         "mind is a new proposal.")


@retry_on_locked
def decide(proposal_prefix: str, decision: str, *, text: str | None = None,
           subject_user: str | None = None, reason: str | None = None) -> Decision:
    """Approve, edit or reject one pending proposal, in **one transaction** with the note change
    and the log row. ``decision`` is ``approved``, ``edited`` or ``rejected``.

    A revise or retire whose target is no longer active is **refused at approval and recorded as
    rejected** with that reason, so a stale proposal can never overwrite a newer note.
    """
    if decision not in ("approved", "edited", "rejected"):
        raise AdminError(f"unknown decision {decision!r}.")
    if decision == "edited" and not (text or "").strip():
        raise AdminError("an edit needs the changed text.")
    with db.transaction() as conn:
        proposal = _by_prefix(conn, "note_proposals", proposal_prefix, "proposal")
        pid = proposal["id"]
        if proposal["status"] != "pending":
            raise AdminError(f"proposal {pid[:8]} is already {proposal['status']}; decisions are "
                             f"final. A change of mind is a new proposal.")

        if decision == "rejected":
            _flip(conn, pid, "rejected", None)
            db.record_approval(conn, capability=CAPABILITY, subject_kind="note_proposal",
                               subject_id=pid, decision="rejected", decided_by=OPERATOR_ID,
                               detail={"reason": reason} if reason else {})
            return Decision("rejected", pid, None, f"rejected proposal {pid[:8]}.")

        target = None
        if proposal["action"] in ("revise", "retire"):
            target = conn.execute("SELECT * FROM notes WHERE id = ?",
                                  (proposal["target_note_id"],)).fetchone()
            if target is None or target["status"] != "active":
                _flip(conn, pid, "rejected", None)
                db.record_approval(
                    conn, capability=CAPABILITY, subject_kind="note_proposal", subject_id=pid,
                    decision="rejected", decided_by=OPERATOR_ID,
                    detail={"reason": STALE_REASON, "target_note_id": proposal["target_note_id"],
                            "target_status": target["status"] if target else "missing"})
                return Decision("rejected_stale", pid, None,
                                f"refused: {STALE_REASON}. Proposal {pid[:8]} is recorded as "
                                f"rejected and no note was changed.")

        user_id = _resolve_subject_user(conn, subject_user)
        entity_text = proposal["text"]
        final_text = (text or "").strip() if decision == "edited" else entity_text
        suggestion = None
        if user_id is None and subject_user is None:
            match = household_match(conn, proposal["subject"])
            suggestion = match["name"] if match else None

        if proposal["action"] == "retire":
            conn.execute("UPDATE notes SET status = 'retired', retired_at = ? WHERE id = ?",
                         (_now(), target["id"]))
            result_id = target["id"]
            detail: dict[str, Any] = {"action": "retire", "note_id": result_id}
        else:
            _, final_text = _check_note_fields(proposal["subject_kind"], proposal["subject"],
                                               final_text, need_text=True)
            if proposal["action"] == "add":
                result_id = _insert_note(
                    conn, kind=proposal["subject_kind"], subject=proposal["subject"],
                    subject_user_id=user_id, text=final_text, origin="entity")
                detail = {"action": "add", "note_id": result_id}
            else:
                conn.execute("UPDATE notes SET status = 'superseded' WHERE id = ?",
                             (target["id"],))
                result_id = _insert_note(
                    conn, kind=proposal["subject_kind"], subject=proposal["subject"],
                    subject_user_id=(user_id if subject_user is not None
                                     else target["subject_user_id"]),
                    text=final_text, origin="entity", version=target["version"] + 1,
                    previous_note_id=target["id"])
                detail = {"action": "revise", "note_id": result_id,
                          "previous_note_id": target["id"]}
        detail["subject_user_id"] = user_id
        if decision == "edited":
            detail["before"], detail["after"] = entity_text, final_text
        _flip(conn, pid, decision, result_id)
        db.record_approval(conn, capability=CAPABILITY, subject_kind="note_proposal",
                           subject_id=pid, decision=decision, decided_by=OPERATOR_ID,
                           detail=detail)
    verb = {"approved": "approved", "edited": "approved with changed text"}[decision]
    note = (f"{verb}: proposal {pid[:8]} -> note {result_id[:8]} "
            f"({'retired' if proposal['action'] == 'retire' else 'active'}).")
    return Decision(decision, pid, result_id, note, suggestion)


# --- reading ----------------------------------------------------------------------------------


def list_notes(include_inactive: bool = False) -> list[sqlite3.Row]:
    with db.connection() as conn:
        where = "" if include_inactive else "WHERE status = 'active'"
        return conn.execute(f"SELECT n.*, u.name AS subject_user_name FROM notes n "
                            f"LEFT JOIN users u ON u.id = n.subject_user_id {where} "
                            f"ORDER BY n.created_at").fetchall()


def list_pending() -> list[sqlite3.Row]:
    with db.connection() as conn:
        return conn.execute(
            "SELECT p.*, u.name AS made_for FROM note_proposals p "
            "LEFT JOIN users u ON u.id = p.user_id WHERE p.status = 'pending' "
            "ORDER BY p.created_at").fetchall()


def _message_view(conn: sqlite3.Connection, message_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT m.id, m.role, m.user_id, m.content, m.timestamp, m.conversation_id, "
        "m.integrity_check, m.integrity_advisory, u.name AS speaker "
        "FROM messages m LEFT JOIN users u ON u.id = m.user_id WHERE m.id = ?",
        (message_id,)).fetchone()
    return dict(row) if row else None


def _reply_for(conn: sqlite3.Connection, conversation_id: str, call_id: str | None):
    """The assistant message whose stored trace contains this call id: the entity's own reply to
    the turn that made the proposal."""
    if not call_id:
        return None
    for row in conn.execute(
            "SELECT id, tool_trace FROM messages WHERE conversation_id = ? AND role = 'assistant' "
            "AND tool_trace LIKE ? ORDER BY timestamp", (conversation_id, f"%{call_id}%")):
        try:
            entries = json.loads(row["tool_trace"])
        except ValueError:
            continue
        if any(isinstance(e, dict) and e.get("call_id") == call_id for e in entries):
            return _message_view(conn, row["id"])
    return None


def review_view(proposal_prefix: str) -> dict[str, Any]:
    """Everything a reviewer reads about one proposal, assembled from primary records."""
    with db.connection() as conn:
        p = dict(_by_prefix(conn, "note_proposals", proposal_prefix, "proposal"))
        conversation = conn.execute(
            "SELECT c.*, u.name AS owner FROM conversations c "
            "LEFT JOIN users u ON u.id = c.user_id WHERE c.id = ?",
            (p["conversation_id"],)).fetchone()
        evidence = []
        for item in json.loads(p["evidence"]):
            message = _message_view(conn, item["message_id"])
            evidence.append({**item, "message": message})
        target = conn.execute("SELECT * FROM notes WHERE id = ?",
                              (p["target_note_id"],)).fetchone() if p["target_note_id"] else None
        match = household_match(conn, p["subject"])
        made_for = conn.execute("SELECT name FROM users WHERE id = ?", (p["user_id"],)).fetchone()
        return {
            "proposal": p,
            "conversation": dict(conversation) if conversation else None,
            "trigger": _message_view(conn, p["user_message_id"]),
            "evidence": evidence,
            "target": dict(target) if target else None,
            "target_is_active": bool(target and target["status"] == "active"),
            "diff": (list(difflib.unified_diff(
                target["text"].splitlines(), (p["text"] or "").splitlines(),
                "the note now", "proposed", lineterm=""))
                if target is not None and p["action"] == "revise" else None),
            "household_suggestion": match["name"] if match else None,
            "made_for": made_for["name"] if made_for else None,
            "reply": _reply_for(conn, p["conversation_id"], p["call_id"]),
        }


def misses(limit: int = 50) -> list[dict[str, Any]]:
    """``note_search`` calls that found nothing, newest first, read from stored traces.

    A read-only query over ``messages.tool_trace``. A miss is a ``note_search`` entry with
    ``outcome == "ok"`` whose ``value`` is exactly ``note_texts.NO_MATCH``: the shared constant, so
    rewording that sentence in one place cannot silently empty this report. It makes misses
    **visible, not explained**: whether one was a paraphrase, a note never made or a retired note
    still needs a person.
    """
    found: list[dict[str, Any]] = []
    with db.connection() as conn:
        for row in conn.execute(
                "SELECT m.id, m.timestamp, m.tool_trace, u.name AS owner FROM messages m "
                "JOIN conversations c ON c.id = m.conversation_id "
                "LEFT JOIN users u ON u.id = c.user_id "
                "WHERE m.role = 'assistant' AND m.tool_trace LIKE '%note_search%' "
                "ORDER BY m.timestamp DESC"):
            try:
                entries = json.loads(row["tool_trace"])
            except ValueError:
                continue
            for e in entries:
                if (isinstance(e, dict) and e.get("tool") == "note_search"
                        and e.get("outcome") == "ok" and e.get("value") == note_texts.NO_MATCH):
                    found.append({"when": row["timestamp"], "whose_turn": row["owner"],
                                  "query": (e.get("arguments") or {}).get("query"),
                                  "message_id": row["id"]})
    return found[:limit]


# --- the index --------------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexReport:
    active: int
    indexed: int
    missing: list[int]       # active but not in the index
    extra: list[int]         # in the index but not active
    fts_check: str           # "ok" or the error FTS5 raised

    @property
    def consistent(self) -> bool:
        return not self.missing and not self.extra and self.fts_check == "ok"


def check_index() -> IndexReport:
    with db.connection() as conn:
        active = {r[0] for r in conn.execute("SELECT rowid FROM notes WHERE status = 'active'")}
        indexed = {r[0] for r in conn.execute("SELECT id FROM notes_fts_docsize")}
        try:
            conn.execute("INSERT INTO notes_fts(notes_fts, rank) VALUES ('integrity-check', 1)")
            verdict = "ok"
        except sqlite3.DatabaseError as exc:
            verdict = f"{type(exc).__name__}: {exc}"
    return IndexReport(len(active), len(indexed), sorted(active - indexed),
                       sorted(indexed - active), verdict)


@retry_on_locked
def reindex() -> IndexReport:
    """Rebuild the derived index from ``notes``. Changes no note."""
    with db.transaction() as conn:
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
    return check_index()
