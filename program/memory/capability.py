"""Does this SQLite build the schema? A startup probe, not a version number.

The schema needs more than a version: FTS5 (``chunks_fts``, ``notes_fts``), the JSON functions
(``json_valid`` in migration 8's CHECK constraints), triggers with ``RAISE``, a view, and the
rank form of FTS5's ``integrity-check`` that ``scripts/note.py check`` runs. Which SQLite release
added which is easy to get wrong and differs by build (a distribution can compile FTS5 or JSON out),
so this does not compare a version. It builds the real schema, from ``working.sql`` through every
migration (migration 8 included), and the archive's, in an in-memory database, and runs the notes
index check. **Nothing touches disk.** If any statement fails the server refuses to start with a
plain message, instead of failing halfway through the first migration on a real store.

``TESTED_SQLITE_VERSION`` records the release the probe was last run against; it is information for
the message and the log, not a gate. The observed bounds (it passes on 3.51.1 and 3.53.1 and fails
on 3.45.1, at a trigger in migration 8) are kept beside it for the refusal message. A failure is
reported with SQLite's own error text, because the cause is not always a missing feature.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: The SQLite release this probe last passed on (Python 3.14.5's, 2026-10-04).
TESTED_SQLITE_VERSION = "3.53.1"
#: Also observed to build the schema (2026-10-04).
ALSO_PASSED_SQLITE_VERSION = "3.51.1"
#: Observed to FAIL (2026-10-04): the build stops at migration 8's trigger with
#: ``near "||": syntax error``. The cause is a SQL feature that release lacks, not FTS5 or JSON.
KNOWN_FAILING_SQLITE_VERSION = "3.45.1"


class SqliteCapabilityError(RuntimeError):
    """This SQLite cannot build the schema. The message says what to do."""


@dataclass(frozen=True)
class ProbeResult:
    version: str
    tables: frozenset[str]
    migrations_applied: int


def probe(connect: Callable[[str], sqlite3.Connection] = sqlite3.connect) -> ProbeResult:
    """Build the whole schema in memory. Returns what was built, or raises
    :class:`SqliteCapabilityError`. ``connect`` is a seam so a test can hand in a SQLite that
    lacks a feature."""
    from program.memory import db, migrations

    version = sqlite3.sqlite_version
    # Two databases, as in production: both define ``users``, so they cannot share one.
    archive = connect(":memory:")
    conn = connect(":memory:")
    try:
        for each in (archive, conn):
            each.row_factory = sqlite3.Row
            each.execute("PRAGMA foreign_keys = ON")
        archive.executescript(db._read_schema("archive.sql"))
        conn.executescript(db._read_schema("working.sql"))
        applied = 0
        conn.execute("BEGIN")  # migrations run inside the runner's transaction, as here
        for migration in sorted(migrations.MIGRATIONS, key=lambda m: m.version):
            migration.apply(conn)
            applied += 1
        conn.execute("COMMIT")
        conn.execute("INSERT INTO notes_fts(notes_fts, rank) VALUES ('integrity-check', 1)")
        tables = frozenset(
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type IN ('table', 'view')"))
    except sqlite3.Error as exc:
        raise SqliteCapabilityError(
            f"This Python's SQLite (version {version}) could not build the database schema. "
            f"SQLite's error from the failing statement: {exc}. "
            f"Observed: the schema builds on SQLite {ALSO_PASSED_SQLITE_VERSION} and "
            f"{TESTED_SQLITE_VERSION}, and fails on {KNOWN_FAILING_SQLITE_VERSION} (near \"||\": "
            f"syntax error, in migration 8's trigger). Use a Python whose sqlite3 module is a "
            f"newer release (for example the python.org or Homebrew build). "
            f"Nothing was written to disk."
        ) from exc
    finally:
        archive.close()
        conn.close()

    logger.info("SQLite %s built the schema in memory (%d migrations); last tested on %s",
                version, applied, TESTED_SQLITE_VERSION)
    return ProbeResult(version=version, tables=tables, migrations_applied=applied)
