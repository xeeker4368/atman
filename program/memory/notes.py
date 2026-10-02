"""Reading and writing the Notes tables. Notes piece 3 (`docs/NOTES_BUILD_PLAN.md`).

The schema is migration 8 (`migrations._v8_notes`); its guards live there. This module is the
small set of queries the two note tools and the turn need, and **it writes nothing but a pending
proposal and a pending proposal's `untrusted_context`**: no note is created, changed or retired
here (that is the operator command's job, piece 4), and nothing here can decide a proposal.

Kept out of ``db.py`` so that module, whose locking behaviour is a stop-list item, is untouched.
Every function that opens a write transaction carries ``@retry_on_locked``, enforced by a test.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Sequence

from program.memory import db
from program.memory.db import retry_on_locked
from program.memory.retrieval import build_fts_query


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
            f"SELECT rowid, * FROM notes WHERE rowid IN ({marks}) AND status = 'active'", ids)}
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


@retry_on_locked
def insert_pending_proposal(
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
) -> None:
    """Insert one proposal, **pending**. Nothing else: the schema refuses any other starting
    status, and no note is touched. ``integrity_check`` is the identity gate's verdict JSON, or
    ``None`` when there was no text to judge (a retire): NULL means no verdict recorded, never
    clean. ``untrusted_context`` is left NULL (not recorded) and filled after the turn."""
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


@retry_on_locked
def set_untrusted_context(call_id: str, tools: Sequence[str]) -> int:
    """Record which untrusted-output tools ran before a proposal, on the proposal(s) made by
    this call. **Only a still-pending proposal is updated** (a decided one is frozen by the
    schema; the ``WHERE`` keeps this from ever trying). ``[]`` means *recorded, none*; the column
    stays NULL (*not recorded*) if this never runs. Returns the rows updated."""
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE note_proposals SET untrusted_context = ? "
            "WHERE call_id = ? AND status = 'pending'",
            (json.dumps(list(tools)), call_id))
        return cur.rowcount
