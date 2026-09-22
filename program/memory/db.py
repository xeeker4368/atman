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

import functools
import logging
import random
import sqlite3
import time
import uuid
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from program import config

logger = logging.getLogger(__name__)

T = TypeVar("T")

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
    conn = sqlite3.connect(str(archive_path()), timeout=config.db_busy_timeout_seconds())
    _configure(conn)
    return conn


def _connect_working_only() -> sqlite3.Connection:
    """Direct connection to working.db. Used for initialisation and migration."""
    conn = sqlite3.connect(str(working_path()), timeout=config.db_busy_timeout_seconds())
    _configure(conn)
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Working database with the archive attached as ``archive``.

    This is the normal way to touch the stores. The attachment is what makes a
    cross-store write a single transaction.
    """
    conn = sqlite3.connect(str(working_path()), timeout=config.db_busy_timeout_seconds())
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
# Write contention
# ---------------------------------------------------------------------------

#: SQLite's lock-contention messages. Matched on text because the sqlite3 module
#: surfaces SQLITE_BUSY and SQLITE_LOCKED as a bare ``OperationalError`` with no
#: distinguishing code.
_LOCK_MESSAGES = ("database is locked", "database table is locked")


def _is_lock_error(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return any(message in text for message in _LOCK_MESSAGES)


def retry_on_locked(fn: Callable[..., T]) -> Callable[..., T]:
    """Retry a write that lost a lock race, until a wall-clock deadline.

    **Why this exists.** Measured: writer-vs-writer contention is absorbed
    entirely by ``busy_timeout`` (0 failures in 320 concurrent writes), but a
    lock held *longer* than the timeout fails ~8% of writes. ``ops/backup.py``
    creates exactly that shape, holding a read transaction across both stores
    for the length of a snapshot. The failure is transient by construction — the
    holder will finish — so retrying matches the failure's nature.

    **Why it wraps whole functions rather than living in ``transaction()``.** A
    ``@contextmanager`` cannot replay its caller's body: by the time ``__exit__``
    sees the exception the body has already run. And the failures land *inside*
    the body and at ``COMMIT`` (measured), not during connection setup, so there
    is no earlier point to retry from. Each decorated function is already a
    self-contained unit of work.

    **Why this cannot break the atomicity guarantee.** Retry runs entirely
    outside ``transaction()``: it only sees the exception after the context
    manager has committed or rolled back and ``connection()``'s ``finally`` has
    closed the connection. It never observes or resumes a half-open transaction,
    and each attempt opens a *fresh* connection that re-``ATTACH``es and
    re-applies both journal-mode pragmas. Nothing about the transaction's
    semantics, the ``ATTACH``, or the ``DELETE`` journaling choice changes —
    retry changes how many transactions are attempted, never what one does.

    **Only lock errors are retried.** ``sqlite3.IntegrityError`` in particular
    must not be: ``insert_chunk`` relies on a duplicate-index violation as the
    arbiter between two concurrent writers, so retrying it would spin on a real
    conflict. Other ``OperationalError``s (``no such table``) are permanent.

    **A ``COMMIT`` that raises ``SQLITE_BUSY`` did not commit.** In rollback-journal
    mode it failed to take the exclusive lock, the transaction stays open, and
    ``transaction()`` rolls it back — so a retry cannot duplicate a write that
    already landed.

    **Not applied to migrations.** ``run_working_migrations()`` calls arbitrary
    ``migration.apply(conn)`` and mutates Python state inside its ``with`` block,
    which a rollback cannot undo. It also runs once, single-threaded, at startup.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        deadline = config.db_write_retry_deadline_seconds()
        base = config.db_write_retry_base_delay_seconds()
        started = time.monotonic()
        attempt = 0

        while True:
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if not _is_lock_error(exc):
                    raise
                elapsed = time.monotonic() - started
                remaining = deadline - elapsed
                if remaining <= 0:
                    logger.warning(
                        "%s gave up after %.1fs of lock contention (%d retries): %s",
                        fn.__name__, elapsed, attempt, exc,
                    )
                    raise

                attempt += 1
                delay = min(base * (2 ** (attempt - 1)), _RETRY_DELAY_CAP)
                delay *= 0.5 + random.random()          # +/-50% jitter
                delay = min(delay, remaining)
                logger.warning(
                    "%s hit lock contention (attempt %d, %.1fs elapsed); "
                    "retrying in %.2fs: %s",
                    fn.__name__, attempt, elapsed, delay, exc,
                )
                time.sleep(delay)

    return wrapper


#: Longest single backoff sleep. JUDGMENT value — the common case is a lock
#: about to free, so sleeping longer than this mostly adds latency.
_RETRY_DELAY_CAP = 1.0


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


@retry_on_locked
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


#: The entity's own row in ``users``, for a write with no person present
#: (Phase 4 Q2c). **NEVER RENDER THIS VALUE.** It is a sentinel, not a label, and
#: deliberately unspeakable: CLAUDE.md says the entity *"has no name and must not
#: be given one — not by code, prompt, config, or docs."*
#:
#: The first attempt here was ``"the system"``, on the grounds that it reads as a
#: role description rather than a name. **A reachability trace killed that**, and
#: the path is worth keeping written down because it is not obvious:
#:
#: * ``chunking._format_line()`` renders every user message as
#:   ``f"{user_name}: {content}"``, where ``user_name`` comes from
#:   ``db.get_user(conversation["user_id"])["name"]`` — so a **conversation owned
#:   by this row would bake its name into chunk text**, which is what reaches
#:   FTS5, the embedding vector, and the retrieved-records block of the prompt.
#:   That is permanent: re-chunking reproduces it, and the embedding cannot be
#:   edited after the fact.
#: * ``turn.py`` passes ``actor.name`` into the correction classifier's prompt
#:   (``corrections._render``) and into ``situation``'s speaker field, both
#:   model-facing.
#:
#: Neither is reachable *today* — this row owns no conversation and cannot produce
#: an ``Actor``, because it cannot log in. But a reflection journal or a research
#: run owning its own conversation is one ordinary Phase 5 task away, and the
#: chunk-text path is irreversible once taken. A sentinel nobody could mistake for
#: a name meant to be spoken costs nothing now and cannot be un-taken later.
#:
#: Underscore-delimited rather than ``operator``-style bare, because
#: ``permissions.OPERATOR_NAME`` is a word a person might legitimately be called
#: and this must not be.
ENTITY_USER_NAME = "__entity__"

#: The entity's row takes ``role = 'user'``, and that is forced rather than
#: chosen: ``users.role`` is ``CHECK (role IN ('admin', 'user'))`` in
#: ``working.sql``, so a third value needs the table recreated — and the reviewed
#: decision was a real row in the existing table with no schema change. ``user``
#: over ``admin`` on least privilege.
#:
#: **What actually keeps this row from being an account**: ``password_hash`` stays
#: ``NULL``, and *a NULL hash never authenticates*. Nothing here relies on the
#: role to make that true. See the design doc for the residual — an operator could
#: set a password on this row with ``scripts/set_password.py``, and closing that
#: needs an auth-side guard, which is a separate Tier 3 change.
ENTITY_USER_ROLE = "user"


@retry_on_locked
def entity_user_id() -> str:
    """The entity's ``users.id``, created on first use.

    Created lazily rather than in ``init_databases()`` so that an empty store
    stays empty — every test run builds a store, and a user row appearing in all
    of them would change what existing tests see.

    The ``UNIQUE`` constraint on ``name`` is what makes this safe under two
    concurrent first writes: the loser catches ``IntegrityError`` and re-reads,
    rather than both inserting or one silently winning.
    """
    existing = get_user_by_name(ENTITY_USER_NAME)
    if existing is not None:
        return existing["id"]
    try:
        return create_user(ENTITY_USER_NAME, role=ENTITY_USER_ROLE)
    except sqlite3.IntegrityError:
        row = get_user_by_name(ENTITY_USER_NAME)
        if row is None:
            raise
        return row["id"]


def get_user(user_id: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_name(name: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()


@retry_on_locked
def set_password_hash(user_id: str, password_hash: str) -> None:
    """Store a password hash for a user. Working store only.

    ``password_hash`` lives only in working: the archive holds the append-only
    record of who existed and when, not their credentials, so this is a
    single-store write with no cross-store atomicity to preserve.

    Written by ``scripts/set_password.py`` and by nothing else. There is no
    self-service reset flow and no password-change endpoint — for a two-person
    household the operator setting a new password *is* the reset path
    (docs/AUTH_DESIGN.md A9).
    """
    if not password_hash:
        raise ValueError("refusing to store an empty password hash")

    with transaction() as conn:
        cursor = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"no such user: {user_id}")


@retry_on_locked
def touch_last_seen(user_id: str) -> None:
    """Record that a user just authenticated.

    Called on login only, never per request. A write on the request path would
    put database contention on every chat turn, which the retry decorator
    exists to survive rather than to invite (A6).
    """
    with transaction() as conn:
        conn.execute(
            "UPDATE users SET last_seen_at = ? WHERE id = ?", (now_iso(), user_id)
        )


def get_actor(user_id: str):
    """Load a user row as an :class:`~program.settings.permissions.Actor`.

    Returns ``None`` for an unknown id rather than inventing an actor — an
    unrecognised user must not resolve to a default role.

    Imported here rather than at module scope: ``permissions`` is a leaf that
    should not pull the database layer in, and this is the only direction the
    dependency runs.
    """
    from program.settings.permissions import Actor, Role

    row = get_user(user_id)
    if row is None:
        return None
    return Actor(user_id=row["id"], name=row["name"], role=Role(row["role"]))


def list_users() -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@retry_on_locked
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


@retry_on_locked
def end_conversation(conversation_id: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE conversations SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (now_iso(), conversation_id),
        )


# ---------------------------------------------------------------------------
# Messages — the dual write
# ---------------------------------------------------------------------------


@retry_on_locked
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


@retry_on_locked
def set_message_integrity_check(message_id: str, verdict_json: str) -> None:
    """Record the fabrication gate's verdict for one assistant message.

    Working store only. The archive's shape is frozen, and a verdict is a
    derived judgment about a message rather than part of the message itself —
    which is `migrations.py`'s own test for what belongs in working.
    """
    with transaction() as conn:
        conn.execute(
            "UPDATE messages SET integrity_check = ? WHERE id = ?",
            (verdict_json, message_id),
        )


@retry_on_locked
def set_message_integrity_advisory(message_id: str, advisory_json: str) -> None:
    """Record what the classifier said about tool claims — a signal, not a verdict.

    Separate from :func:`set_message_integrity_check` because the two mean
    different things: that column decides whether a turn was flagged, this one
    never does. Working store only, for the same reason.
    """
    with transaction() as conn:
        conn.execute(
            "UPDATE messages SET integrity_advisory = ? WHERE id = ?",
            (advisory_json, message_id),
        )


def get_messages_in_chunk(chunk_id: str) -> list[sqlite3.Row]:
    """The messages a chunk was built from.

    **A timestamp-window join, because there is no ordinal to use.**
    ``chunks.first_message_id``/``last_message_id`` are uuid4 hex and therefore
    unordered, and ``messages`` carries no sequence number — only ``timestamp``,
    which is indexed. So the range is resolved by looking up the two endpoint
    messages' timestamps and taking everything between them in that conversation.
    The endpoints are exact (they are named by id); only the interior relies on
    the window, which is why a same-second collision between *interior* messages
    is harmless. See ``docs/CORRECTION_DESIGN.md`` C3 and CO2.
    """
    with connection() as conn:
        return conn.execute(
            """
            SELECT m.* FROM messages m
              JOIN chunks c ON c.id = ?
              JOIN messages f ON f.id = c.first_message_id
              JOIN messages l ON l.id = c.last_message_id
             WHERE m.conversation_id = c.conversation_id
               AND m.timestamp >= f.timestamp
               AND m.timestamp <= l.timestamp
             ORDER BY m.timestamp, m.id
            """,
            (chunk_id,),
        ).fetchall()


#: What ``supersedes.replacement`` may hold, matching the column's CHECK. Here as
#: well as in the schema so a caller can validate before the database refuses —
#: but the CHECK is the guarantee, since this module is not the only possible
#: writer. See RETRIEVAL_SUPERSESSION_DESIGN R4.
REPLACEMENT_STATES = ("replaced", "contradicted")


@retry_on_locked
def create_supersedes_link(
    superseding_message_id: str,
    superseded_message_id: str,
    replacement: str,
    classifier_model: str | None = None,
    confidence: float | None = None,
    rationale: str | None = None,
) -> str:
    """Record that one message supersedes another. Never edits either.

    Working store only: a correction is a judgment *about* messages rather than
    part of them, which is `migrations.py`'s own test for what belongs in working
    rather than in the frozen archive.

    ``replacement`` is ``replaced`` or ``contradicted`` (migration 6) and has **no
    default**, positioned before the optional arguments so it cannot be omitted.
    CO8 lets a correction state that a claim is wrong without saying what is true
    instead, so a link no longer implies a new value exists; task 3.5 renders the
    two differently and cannot recover the distinction from anything else on the
    row. A default here would silently manufacture whichever state is cheaper to
    render.

    ``confidence`` is written as supplied and is normally ``None`` — see
    ``docs/CORRECTION_DESIGN.md`` C7: the classifier emits no calibrated number,
    and a self-reported one would be an unmeasured constant of exactly the kind
    this build keeps refusing.
    """
    link_id = uuid.uuid4().hex
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO supersedes (
                id, superseding_message_id, superseded_message_id, replacement,
                classifier_model, confidence, rationale, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (link_id, superseding_message_id, superseded_message_id, replacement,
             classifier_model, confidence, rationale, now_iso()),
        )
    return link_id


def get_supersedes_links(message_id: str | None = None) -> list[sqlite3.Row]:
    """Every link, or every link touching one message. Read-only."""
    with connection() as conn:
        if message_id is None:
            return conn.execute(
                "SELECT * FROM supersedes ORDER BY created_at"
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM supersedes
             WHERE superseding_message_id = ? OR superseded_message_id = ?
             ORDER BY created_at
            """,
            (message_id, message_id),
        ).fetchall()


def get_supersedes_for_chunks(chunk_ids: Sequence[str]) -> list[sqlite3.Row]:
    """Corrections touching any message inside these chunks. One query, read-only.

    Task 3.5's first hop (``docs/RETRIEVAL_SUPERSESSION_DESIGN.md`` R2). Links name
    *messages* and retrieval returns *chunks*, so the mapping is the same
    timestamp-window join :func:`get_messages_in_chunk` documents — done once for
    the whole result set rather than per chunk, because per-chunk plus per-message
    would be up to ``top_k (10) x max_turns (8)`` queries inside a turn on a
    database whose lock contention is a recorded issue.

    Chunks from file ingestion have NULL message-id columns, so the join drops them
    rather than needing a guard: a document has no messages to correct.
    """
    if not chunk_ids:
        return []
    placeholders = ", ".join("?" for _ in chunk_ids)
    with connection() as conn:
        return conn.execute(
            f"""
            SELECT c.id                       AS chunk_id,
                   sup.replacement            AS replacement,
                   sd.id                      AS superseded_id,
                   sd.content                 AS superseded_content,
                   sd.timestamp               AS superseded_timestamp,
                   sd.role                    AS superseded_role,
                   sg.id                      AS superseding_id,
                   sg.content                 AS superseding_content,
                   sg.timestamp               AS superseding_timestamp,
                   sg.role                    AS superseding_role
              FROM chunks c
              JOIN messages f  ON f.id = c.first_message_id
              JOIN messages l  ON l.id = c.last_message_id
              JOIN messages sd ON sd.conversation_id = c.conversation_id
                              AND sd.timestamp >= f.timestamp
                              AND sd.timestamp <= l.timestamp
              JOIN supersedes sup ON sup.superseded_message_id = sd.id
              JOIN messages sg ON sg.id = sup.superseding_message_id
             WHERE c.id IN ({placeholders})
             ORDER BY c.id, sd.timestamp, sd.id, sg.timestamp, sg.id
            """,
            tuple(chunk_ids),
        ).fetchall()


def get_supersedes_from(message_ids: Sequence[str]) -> list[sqlite3.Row]:
    """What supersedes each of these messages. One query per chain level.

    Task 3.5 follows a chain forward to its tip (R3) — ``A <- B <- C`` means ``C``
    is the current statement, and surfacing ``B`` would annotate a record with a
    correction that has itself been corrected. Batched by level so the usual case
    (depth 1, nothing further) costs exactly one extra query and a long chain costs
    one per level rather than one per message.
    """
    if not message_ids:
        return []
    placeholders = ", ".join("?" for _ in message_ids)
    with connection() as conn:
        return conn.execute(
            f"""
            SELECT sup.superseded_message_id AS from_id,
                   sup.replacement           AS replacement,
                   sg.id                     AS superseding_id,
                   sg.content                AS superseding_content,
                   sg.timestamp              AS superseding_timestamp,
                   sg.role                   AS superseding_role
              FROM supersedes sup
              JOIN messages sg ON sg.id = sup.superseding_message_id
             WHERE sup.superseded_message_id IN ({placeholders})
             ORDER BY sg.timestamp, sg.id
            """,
            tuple(message_ids),
        ).fetchall()


def get_conversation_messages(conversation_id: str) -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp, id",
            (conversation_id,),
        ).fetchall()


def get_previous_user_message_time(
    user_id: str, exclude_message_id: str | None = None
) -> str | None:
    """When this person last spoke, before the message being answered now.

    Returns an ISO-8601 timestamp, or ``None`` when they have never spoken
    before — which is a real answer and must not be confused with zero elapsed
    time (the current-situation block renders the two differently).

    **Across all conversations, not scoped to one.** The question the elapsed
    figure answers is "how long since I last spoke with this person", which is a
    property of the person rather than the thread. Conversation-scoping would
    also be actively wrong here: ``idle_close_minutes`` is 15, so conversations
    close automatically after a short quiet period, and nearly every session
    would report "no prior message" — a discontinuity manufactured by a janitor
    setting rather than one that happened.

    **Scoped strictly to this user, and to what they said.** ``role = 'user'``
    because the entity's own replies are not the person speaking, and
    ``user_id`` because one household member's activity must never appear in
    another's figure. This is a different axis from decision #20, which governs
    what *retrieval* may surface and deliberately stays unfiltered by actor.

    ``exclude_message_id`` is not optional in practice, and the reason is a trap
    rather than a preference: ``turn.py`` persists the user's message *before*
    generation (task 2.2's obligation (b)), so by the time this is called the
    message being answered is already the most recent row. Without excluding it
    every turn would report a gap of roughly zero — plausible, constant, and
    always wrong. Passing the id makes the exclusion explicit and testable
    rather than making correctness depend on the order of two statements.
    """
    sql = (
        "SELECT MAX(timestamp) AS last FROM messages "
        "WHERE user_id = ? AND role = 'user'"
    )
    params: list[str] = [user_id]
    if exclude_message_id is not None:
        sql += " AND id != ?"
        params.append(exclude_message_id)

    with connection() as conn:
        row = conn.execute(sql, params).fetchone()
    return row["last"] if row else None


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


@retry_on_locked
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
    artifact_id: str | None = None,
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
                    artifact_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk_id, conversation_id, user_id, text, source_type, source_trust,
                chunk_index, first_message_id, last_message_id, text_sha256,
                artifact_id, now, now,
            ),
        )


# ---------------------------------------------------------------------------
# Artifacts (task 2.6). Design of record: docs/INGESTION_DESIGN.md.
# ---------------------------------------------------------------------------


@retry_on_locked
def insert_artifact(
    *,
    artifact_id: str,
    user_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    storage_path: str,
    artifact_type: str,
    extraction_status: str,
    extracted_text: str | None = None,
    extraction_note: str | None = None,
) -> None:
    """Record one ingested file. Working store only — see INGESTION_DESIGN I2.

    Keyword-only for the same reason ``insert_chunk`` is: eleven columns, several
    of them strings that would be silently interchangeable positionally.
    """
    with transaction() as conn:
        conn.execute(
            """INSERT INTO artifacts
                   (id, user_id, filename, content_type, size_bytes, sha256,
                    storage_path, artifact_type, extraction_status,
                    extracted_text, extraction_note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (artifact_id, user_id, filename, content_type, size_bytes, sha256,
             storage_path, artifact_type, extraction_status, extracted_text,
             extraction_note, now_iso()),
        )


def get_artifact(artifact_id: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()


def get_artifact_by_hash(sha256: str, user_id: str) -> sqlite3.Row | None:
    """An identical file already uploaded by this user, if there is one."""
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM artifacts WHERE sha256 = ? AND user_id = ? "
            "ORDER BY created_at LIMIT 1",
            (sha256, user_id),
        ).fetchone()


def list_artifacts(user_id: str | None = None) -> list[sqlite3.Row]:
    with connection() as conn:
        if user_id is None:
            return conn.execute(
                "SELECT * FROM artifacts ORDER BY created_at DESC"
            ).fetchall()
        return conn.execute(
            "SELECT * FROM artifacts WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()


def get_artifact_chunks(artifact_id: str) -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM chunks WHERE artifact_id = ? ORDER BY chunk_index",
            (artifact_id,),
        ).fetchall()


@retry_on_locked
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
