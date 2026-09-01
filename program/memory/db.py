"""Database layer: two SQLite stores, written atomically together.

Two databases with different jobs:

* ``archive.db``  — the durable record. Append-only, two tables, shape frozen,
  never migrated. What actually happened.
* ``working.db``  — operational and derived. Migrated freely. Rebuildable from
  the archive.

**Why the writes are atomic across both.** A message that reached one store and
not the other is a memory the system is wrong about, in one of two directions:
either the archive has a turn the operational store cannot see, or the
operational store shows a turn the durable record never kept. Both are silent.
So a message is written inside a single transaction over a connection that has
the archive ``ATTACH``ed, and either both rows land or neither does.

**Why DELETE journaling and not WAL.** SQLite only guarantees atomicity across
attached databases when no participating database is in WAL mode; in WAL the
commit is atomic within each database separately, which is exactly the
half-written state this design exists to prevent. WAL would be the faster
choice, and it is deliberately not taken. Do not switch this without re-reading
the SQLite documentation on atomic commit across attached databases.

**Foreign keys do not span attached databases** in SQLite, so the archive has
none pointing into working and vice versa. The two stores are joined by
convention on shared ids, not by constraint.

The entity never reads either database directly. These serve retrieval, the
operator, and background processes.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from program import config

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


def archive_path() -> Path:
    """Location of archive.db. Resolved at call time, never cached."""
    return config.data_dir() / "archive.db"


def working_path() -> Path:
    """Location of working.db. Resolved at call time, never cached."""
    return config.data_dir() / "working.db"


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string. One definition, used everywhere."""
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _read_schema(name: str) -> str:
    return (SCHEMA_DIR / name).read_text(encoding="utf-8")


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # DELETE, not WAL — see the module docstring. This is load-bearing.
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA foreign_keys = ON")


def _connect_archive_only() -> sqlite3.Connection:
    """Direct connection to archive.db. Used for initialisation only."""
    conn = sqlite3.connect(str(archive_path()), timeout=10)
    _configure(conn)
    return conn


def _connect_working_only() -> sqlite3.Connection:
    """Direct connection to working.db. Used for initialisation and migration."""
    conn = sqlite3.connect(str(working_path()), timeout=10)
    _configure(conn)
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Working database with the archive attached as ``archive``.

    This is the normal way to touch the stores. The attachment is what makes a
    cross-store write a single transaction.
    """
    conn = sqlite3.connect(str(working_path()), timeout=10)
    _configure(conn)
    conn.execute("ATTACH DATABASE ? AS archive", (str(archive_path()),))
    # The attached database carries its own journal mode and needs setting too;
    # a WAL archive would silently break the atomicity guarantee above.
    conn.execute("PRAGMA archive.journal_mode = DELETE")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """A connection inside an explicit transaction, committed or rolled back.

    Python's sqlite3 opens transactions implicitly and inconsistently depending
    on statement type, which makes "did that roll back?" hard to answer. Being
    explicit here means a failure part-way through a multi-store write leaves
    nothing behind.
    """
    with connection() as conn:
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def init_databases() -> None:
    """Create both databases and bring the working schema up to date.

    Safe to run repeatedly. The archive is created from its frozen definition
    and never migrated; the working store is created then migrated forward.
    """
    config.data_dir().mkdir(parents=True, exist_ok=True)

    conn = _connect_archive_only()
    try:
        conn.executescript(_read_schema("archive.sql"))
        conn.commit()
    finally:
        conn.close()

    conn = _connect_working_only()
    try:
        conn.executescript(_read_schema("working.sql"))
        conn.commit()
    finally:
        conn.close()

    # Imported here rather than at module scope: migrations imports this module
    # for its connection helpers.
    from program.memory import migrations

    migrations.run_working_migrations()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def create_user(name: str, role: str = "user", user_id: str | None = None) -> str:
    """Create a user in both stores atomically. Returns the user id."""
    if role not in ("admin", "user"):
        raise ValueError(f"unknown role: {role!r}")

    uid = user_id or new_id()
    created = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO archive.users (id, name, created_at) VALUES (?, ?, ?)",
            (uid, name, created),
        )
        conn.execute(
            "INSERT INTO users (id, name, role, created_at) VALUES (?, ?, ?, ?)",
            (uid, name, role, created),
        )
    return uid


def get_user(user_id: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_name(name: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()


def list_users() -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def start_conversation(user_id: str, conversation_id: str | None = None) -> str:
    cid = conversation_id or new_id()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO conversations (id, user_id, started_at) VALUES (?, ?, ?)",
            (cid, user_id, now_iso()),
        )
    return cid


def get_conversation(conversation_id: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()


def end_conversation(conversation_id: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE conversations SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (now_iso(), conversation_id),
        )


# ---------------------------------------------------------------------------
# Messages — the dual write
# ---------------------------------------------------------------------------


def save_message(
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
    tool_trace: str | None = None,
    message_id: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Write one message to both stores in a single transaction.

    Either both rows land or neither does. See the module docstring for why
    that matters and why WAL is not used.
    """
    if role not in ("user", "assistant"):
        raise ValueError(f"unknown role: {role!r}")

    mid = message_id or new_id()
    ts = timestamp or now_iso()

    with transaction() as conn:
        conn.execute(
            """INSERT INTO archive.messages
                   (id, conversation_id, user_id, role, content, tool_trace, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mid, conversation_id, user_id, role, content, tool_trace, ts),
        )
        conn.execute(
            """INSERT INTO messages
                   (id, conversation_id, user_id, role, content, tool_trace, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mid, conversation_id, user_id, role, content, tool_trace, ts),
        )
        conn.execute(
            "UPDATE conversations SET message_count = message_count + 1 WHERE id = ?",
            (conversation_id,),
        )
    return mid


def get_conversation_messages(conversation_id: str) -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp, id",
            (conversation_id,),
        ).fetchall()


def get_archive_message(message_id: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM archive.messages WHERE id = ?", (message_id,)
        ).fetchone()


def count_messages() -> tuple[int, int]:
    """Return (archive_count, working_count). Equal unless something is wrong."""
    with connection() as conn:
        a = conn.execute("SELECT COUNT(*) AS n FROM archive.messages").fetchone()["n"]
        w = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
    return a, w


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


def get_chunk_by_index(conversation_id: str, chunk_index: int) -> sqlite3.Row | None:
    """The chunk at this position, or None.

    The existence check that stops a checkpoint re-embedding what a previous
    checkpoint already wrote.
    """
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM chunks WHERE conversation_id = ? AND chunk_index = ?",
            (conversation_id, chunk_index),
        ).fetchone()


def get_conversation_chunks(conversation_id: str) -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM chunks WHERE conversation_id = ? ORDER BY chunk_index",
            (conversation_id,),
        ).fetchall()


def insert_chunk(
    *,
    chunk_id: str,
    conversation_id: str | None,
    user_id: str | None,
    text: str,
    source_type: str,
    source_trust: str,
    text_sha256: str,
    chunk_index: int | None = None,
    first_message_id: str | None = None,
    last_message_id: str | None = None,
) -> None:
    """Write one chunk row. The FTS index follows via trigger, same transaction.

    Raises ``sqlite3.IntegrityError`` if a row already occupies this
    ``(conversation_id, chunk_index)`` — which is how two concurrent writers are
    resolved rather than by locking alone.
    """
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            """INSERT INTO chunks
                   (id, conversation_id, user_id, text, source_type, source_trust,
                    chunk_index, first_message_id, last_message_id, text_sha256,
                    created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk_id, conversation_id, user_id, text, source_type, source_trust,
                chunk_index, first_message_id, last_message_id, text_sha256, now, now,
            ),
        )


def mark_conversation_chunked(conversation_id: str) -> None:
    """Record that a conversation has been chunked *in full*.

    Set only by final chunking at close. A checkpoint never reaches this, because
    it always leaves the trailing group open.
    """
    with transaction() as conn:
        conn.execute(
            "UPDATE conversations SET chunked = 1 WHERE id = ?", (conversation_id,)
        )


def get_unchunked_ended_conversations(limit: int | None = None) -> list[sqlite3.Row]:
    """Closed conversations that final chunking has not completed for.

    The recovery queue: final chunking that aborted part-way leaves a row here
    for a later pass to retry.
    """
    sql = (
        "SELECT * FROM conversations "
        "WHERE ended_at IS NOT NULL AND chunked = 0 ORDER BY ended_at"
    )
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    with connection() as conn:
        return conn.execute(sql, params).fetchall()


def get_open_conversations_with_activity() -> list[sqlite3.Row]:
    """Open conversations, each with its last message time and role.

    ``last_message_at`` falls back to ``started_at`` for a conversation that has
    no messages — otherwise it would be NULL and the conversation could never be
    judged idle, so it would never close.

    ``last_role`` is what distinguishes an in-flight turn from a completed one:
    an assistant message means the model has answered, a user message means a
    turn may still be running. Idle-close applies a different window to each.
    """
    with connection() as conn:
        return conn.execute(
            """
            SELECT
                c.id,
                c.user_id,
                c.started_at,
                c.message_count,
                COALESCE(m.last_timestamp, c.started_at) AS last_message_at,
                m.last_role
            FROM conversations c
            LEFT JOIN (
                SELECT
                    conversation_id,
                    MAX(timestamp) AS last_timestamp,
                    -- The role of the row holding that MAX. SQLite's bare-column
                    -- rule makes other columns in a MAX() aggregate come from
                    -- the matching row, which is exactly what is wanted here.
                    role AS last_role
                FROM messages
                GROUP BY conversation_id
            ) m ON m.conversation_id = c.id
            WHERE c.ended_at IS NULL
            ORDER BY last_message_at
            """
        ).fetchall()
