"""Reading and writing the Notes tables. Notes pieces 3 and 6 (`docs/NOTES_BUILD_PLAN.md`).

The schema is migration 8 (`migrations._v8_notes`); its guards live there. This module is the
small set of queries the two note tools and the turn need. **It writes a proposal and, in exactly
one case, decides it: when approval is off, `record_proposal` inserts the proposal pending and, in
the same transaction, writes the note, flips the proposal to `applied_without_review` and writes
the log row** (N10, piece 6). Every other decision is the operator command's (`note_admin`), which
shares `apply_change` below so a note is changed by one piece of code whoever decides.

Kept out of ``db.py`` so that module, whose locking behaviour is a stop-list item, is untouched.
Every function that opens a write transaction carries ``@retry_on_locked``, enforced by a test.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Sequence

from program.memory import db
from program.memory.db import retry_on_locked
from program.memory.retrieval import build_fts_query
from program.settings import store

logger = logging.getLogger(__name__)

#: Who the log names for a decision no person made.
AUTO_DECIDER = "approval_off"
APPROVAL_SETTING = "notes.approval_required"


def search_active(query: str, limit: int) -> list[sqlite3.Row]:
    """Active notes matching ``query``, best first. **Lexical only (N1).**

    The index is read for ids, then **the rows are re-read and filtered to ``status = 'active'``**
    (N13, N17 #20), so a stale index entry can never show a retired or superseded note. Returns
    ``[]`` when the query has no searchable term. The query is never given to FTS5 as syntax
    (``retrieval.build_fts_query``).
    """
    expression = build_fts_query(query)
    if expression is None:
        return []
    with db.connection() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) "
            "LIMIT ?", (expression, limit * 4))]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = {r["rowid"]: r for r in conn.execute(
            f"SELECT n.rowid AS rowid, n.*, "
            f"(SELECT p.action FROM note_proposals p WHERE p.resulting_note_id = n.id "
            f" AND p.status = 'applied_without_review' AND p.action IN ('add', 'revise') "
            f" LIMIT 1) AS applied_action "
            f"FROM notes n WHERE n.rowid IN ({marks}) AND n.status = 'active'", ids)}
    return [rows[i] for i in ids if i in rows][:limit]


def _like_escape(text: str) -> str:
    """Escape LIKE's wildcards with ``!`` (use with ``ESCAPE '!'``)."""
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def get_active_by_id_prefix(prefix: str) -> list[sqlite3.Row]:
    """Active notes whose id starts with ``prefix`` (the tool shows the first 8 characters)."""
    with db.connection() as conn:
        return conn.execute(
            "SELECT * FROM notes WHERE status = 'active' AND id LIKE ? ESCAPE '!'",
            (_like_escape(prefix) + "%",),
        ).fetchall()


def messages_by_ids(ids: Sequence[str]) -> list[sqlite3.Row]:
    """Messages by id, in chunks that stay under SQLite's variable limit."""
    wanted = list(dict.fromkeys(ids))
    out: list[sqlite3.Row] = []
    with db.connection() as conn:
        for start in range(0, len(wanted), 500):
            part = wanted[start:start + 500]
            marks = ",".join("?" for _ in part)
            out.extend(conn.execute(
                f"SELECT id, role, user_id, content, timestamp FROM messages "
                f"WHERE id IN ({marks})", part))
    return out


def messages_containing(word: str, roles: Sequence[str]) -> list[sqlite3.Row]:
    """Messages of the given roles whose lower-cased text contains ``word``: a **prefilter**,
    narrowed in Python by the exact normalised match. Covers every stored message, including the
    open trailing group that chunking has not indexed."""
    marks = ",".join("?" for _ in roles)
    with db.connection() as conn:
        return conn.execute(
            f"SELECT id, role, user_id, content, timestamp FROM messages "
            f"WHERE role IN ({marks}) AND lower(content) LIKE ? ESCAPE '!'",
            [*roles, f"%{_like_escape(word.lower())}%"]).fetchall()


def approval_required_in(conn: sqlite3.Connection) -> bool:
    """Whether a proposal needs a person, **read fresh, on this connection**.

    Never through ``store``'s cache: the operator command runs in another process, and a cached
    *off* in a running server would keep applying proposals after approval was switched back on.
    Read inside the caller's transaction, so the decision and the write it governs cannot be
    separated by a toggle. **Fails closed**: no row, an unreadable table, an undecodable value or
    any error means approval IS required. The TOML seed is deliberately not consulted (a file edit
    is not a logged event)."""
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?",
                           (APPROVAL_SETTING,)).fetchone()
        if row is None:
            return True
        return store.decode(APPROVAL_SETTING, row[0]) is not False
    except Exception as exc:  # noqa: BLE001 - fail closed on any failure
        logger.warning("approval_required could not be read (%s: %s); approval is required",
                       type(exc).__name__, exc)
        return True


def approval_required_now() -> bool:
    """The same read on its own connection, for the operator command's status line."""
    try:
        with db.connection() as conn:
            return approval_required_in(conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("approval_required could not be read (%s: %s); approval is required",
                       type(exc).__name__, exc)
        return True


def insert_note(conn: sqlite3.Connection, *, kind: str, subject: str,
                subject_user_id: str | None, text: str, origin: str, version: int = 1,
                previous_note_id: str | None = None) -> str:
    note_id = db.new_id()
    now = db.now_iso()
    conn.execute(
        "INSERT INTO notes (id, subject_kind, subject, subject_user_id, text, status, version, "
        "previous_note_id, origin, created_at, last_confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
        (note_id, kind, subject, subject_user_id, text, version, previous_note_id, origin, now,
         now))
    return note_id


def apply_change(conn: sqlite3.Connection, *, action: str, kind: str, subject: str,
                 target: sqlite3.Row | None, text: str | None, subject_user_id: str | None,
                 carry_link: bool) -> tuple[str, dict[str, Any]]:
    """Write the note change a proposal asks for, **inside the caller's transaction**. Returns the
    resulting note's id (a retire's is the retired note) and the log detail. The one place a
    proposal's change is made, shared by the operator's decisions and by auto-apply, so the two
    cannot drift. New and changed notes are written with ``origin = 'entity'``; ``carry_link``
    keeps a revised note's existing ``subject_user_id`` unless one was given."""
    if action == "retire":
        conn.execute("UPDATE notes SET status = 'retired', retired_at = ? WHERE id = ?",
                     (db.now_iso(), target["id"]))
        return target["id"], {"action": "retire", "note_id": target["id"]}
    if action == "add":
        note_id = insert_note(conn, kind=kind, subject=subject, subject_user_id=subject_user_id,
                              text=text, origin="entity")
        return note_id, {"action": "add", "note_id": note_id}
    conn.execute("UPDATE notes SET status = 'superseded' WHERE id = ?", (target["id"],))
    note_id = insert_note(
        conn, kind=kind, subject=subject,
        subject_user_id=(target["subject_user_id"] if carry_link else subject_user_id),
        text=text, origin="entity", version=target["version"] + 1,
        previous_note_id=target["id"])
    return note_id, {"action": "revise", "note_id": note_id, "previous_note_id": target["id"]}


@retry_on_locked
def record_proposal(
    *,
    proposal_id: str,
    action: str,
    target_note_id: str | None,
    subject_kind: str,
    subject: str,
    text: str | None,
    evidence: list[dict[str, Any]],
    conversation_id: str,
    user_message_id: str,
    call_id: str | None,
    user_id: str,
    integrity_check: str | None,
) -> str:
    """Insert one proposal and, **if approval is off, apply it in the same transaction**.

    Returns ``"pending"`` or ``"applied"``. The proposal is always inserted ``pending`` first (the
    schema refuses any other start). With approval off, the same transaction then writes the note
    change, flips the proposal to ``applied_without_review`` and writes the log row: if the log row
    cannot be written, **none of it** happens, including the insert. The setting is read inside the
    transaction, fresh, and fails closed (:func:`approval_required_in`). A revise or retire whose
    target is no longer active is **not** applied: the proposal stays pending.
    ``subject_user_id`` is never set here (never silently).
    """
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO note_proposals
                   (id, action, target_note_id, subject_kind, subject, text, evidence,
                    conversation_id, user_message_id, call_id, user_id, status,
                    integrity_check, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (proposal_id, action, target_note_id, subject_kind, subject, text,
             json.dumps(evidence), conversation_id, user_message_id, call_id, user_id,
             integrity_check, db.now_iso()),
        )
        if approval_required_in(conn):
            return "pending"
        target = None
        if action in ("revise", "retire"):
            target = conn.execute("SELECT * FROM notes WHERE id = ?", (target_note_id,)).fetchone()
            if target is None or target["status"] != "active":
                return "pending"
        result_id, detail = apply_change(
            conn, action=action, kind=subject_kind, subject=subject, target=target, text=text,
            subject_user_id=None, carry_link=True)
        conn.execute(
            "UPDATE note_proposals SET status = 'applied_without_review', decided_at = ?, "
            "resulting_note_id = ? WHERE id = ? AND status = 'pending'",
            (db.now_iso(), result_id, proposal_id))
        detail["subject_user_id"] = None
        db.record_approval(conn, capability="notes", subject_kind="note_proposal",
                           subject_id=proposal_id, decision="applied_without_review",
                           decided_by=AUTO_DECIDER, detail=detail)
        return "applied"


@retry_on_locked
def set_untrusted_context(call_id: str, tools: Sequence[str]) -> int:
    """Record which untrusted-output tools ran before a proposal, for the proposal(s) made by
    this call. **A still-pending proposal gets it on its row.** A proposal already applied (approval
    was off) is decided and therefore frozen by the schema, every column, so its context cannot be
    written onto it: it is appended to the approval log instead (``untrusted_context_recorded``,
    once per proposal) and the review reads it from there. ``[]`` means *recorded, none*; both
    stay unrecorded (NULL / no row) if this never runs. Returns the rows updated or appended."""
    changed = 0
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE note_proposals SET untrusted_context = ? "
            "WHERE call_id = ? AND status = 'pending'",
            (json.dumps(list(tools)), call_id))
        changed += cur.rowcount
        for row in conn.execute(
                "SELECT id FROM note_proposals WHERE call_id = ? "
                "AND status = 'applied_without_review'", (call_id,)).fetchall():
            if conn.execute(
                    "SELECT 1 FROM approval_log WHERE capability = 'notes' AND subject_id = ? "
                    "AND decision = 'untrusted_context_recorded'", (row["id"],)).fetchone():
                continue
            db.record_approval(conn, capability="notes", subject_kind="note_proposal",
                               subject_id=row["id"], decision="untrusted_context_recorded",
                               decided_by="system", detail={"tools": list(tools)})
            changed += 1
    return changed
