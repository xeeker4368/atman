"""Reading and writing the Notes tables. Notes pieces 3 and 6 (`docs/NOTES_BUILD_PLAN.md`), and
Notes v2 (`docs/NOTES_V2_DESIGN.md`).

The schema is migration 8 (`migrations._v8_notes`) with migration 11's `line` column; the guards
live there. This module is the small set of queries `note_search`, the listing, the note pass and
the operator command need. **It decides no proposal.** Since Notes v2 there is no auto-apply: the
pass writes proposals pending (`note_pass`), and every decision is the operator command's
(`note_admin`), through `apply_change` below.

Kept out of ``db.py`` so that module, whose locking behaviour is a stop-list item, is untouched.
It opens no write transaction of its own: its writers take the caller's connection.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Sequence

from program.memory import db
from program.memory.retrieval import build_fts_query
from program.settings import store

logger = logging.getLogger(__name__)

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
                previous_note_id: str | None = None, line: str | None = None) -> str:
    note_id = db.new_id()
    now = db.now_iso()
    conn.execute(
        "INSERT INTO notes (id, subject_kind, subject, subject_user_id, text, line, status, "
        "version, previous_note_id, origin, created_at, last_confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
        (note_id, kind, subject, subject_user_id, text, line, version, previous_note_id, origin,
         now, now))
    return note_id


def apply_change(conn: sqlite3.Connection, *, action: str, kind: str, subject: str,
                 target: sqlite3.Row | None, text: str | None, subject_user_id: str | None,
                 carry_link: bool, line: str | None = None) -> tuple[str, dict[str, Any]]:
    """Write the note change a proposal asks for, **inside the caller's transaction**. Returns the
    resulting note's id (a retire's is the retired note) and the log detail. The one place a
    proposal's change is made (the operator's decision). New and changed notes are written with
    ``origin = 'entity'``; ``carry_link`` keeps a revised note's existing ``subject_user_id``
    unless one was given."""
    if action == "retire":
        conn.execute("UPDATE notes SET status = 'retired', retired_at = ? WHERE id = ?",
                     (db.now_iso(), target["id"]))
        return target["id"], {"action": "retire", "note_id": target["id"]}
    if action == "add":
        note_id = insert_note(conn, kind=kind, subject=subject, subject_user_id=subject_user_id,
                              text=text, origin="entity", line=line)
        return note_id, {"action": "add", "note_id": note_id}
    conn.execute("UPDATE notes SET status = 'superseded' WHERE id = ?", (target["id"],))
    note_id = insert_note(
        conn, kind=kind, subject=subject,
        subject_user_id=(target["subject_user_id"] if carry_link else subject_user_id),
        text=text, origin="entity", version=target["version"] + 1,
        previous_note_id=target["id"], line=line)
    return note_id, {"action": "revise", "note_id": note_id, "previous_note_id": target["id"]}


def active_notes_for_listing() -> list[sqlite3.Row]:
    """Active notes in the listing's order (Notes v2 section 3): person notes first, then the
    rest, each newest-confirmed first."""
    with db.connection() as conn:
        return conn.execute(
            "SELECT * FROM notes WHERE status = 'active' "
            "ORDER BY CASE WHEN subject_kind = 'person' THEN 0 ELSE 1 END, "
            "last_confirmed_at DESC, rowid DESC").fetchall()


def active_notes_newest_first() -> list[sqlite3.Row]:
    """Every active note, the most recently confirmed first (the note pass's fallback order)."""
    with db.connection() as conn:
        return conn.execute(
            "SELECT * FROM notes WHERE status = 'active' "
            "ORDER BY last_confirmed_at DESC, rowid DESC").fetchall()


def matching_active_ids(text: str, limit: int) -> list[str]:
    """Ids of active notes ``notes_fts`` matches against ``text``, best first. Re-read and
    filtered to active like :func:`search_active`."""
    return [row["id"] for row in search_active(text, limit)]


def pending_proposals() -> list[sqlite3.Row]:
    """Every pending proposal, oldest first."""
    with db.connection() as conn:
        return conn.execute(
            "SELECT * FROM note_proposals WHERE status = 'pending' "
            "ORDER BY created_at, rowid").fetchall()
