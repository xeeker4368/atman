"""Versioned migrations for working.db.

Built with the first schema rather than retrofitted when the first migration is
needed. Retrofitting means the first few schema changes happen by hand, and the
runner arrives without knowing what state any given database is actually in.

**The archive is never migrated.** Its shape is frozen (see
``schema/archive.sql``), so there is deliberately no archive equivalent of this
module. If a change seems to require altering the archive, that is a signal the
field belongs in working.db instead.

Adding a migration: append a ``Migration`` to ``MIGRATIONS`` with the next
version number. Never edit or renumber an applied migration — a database that
already ran version N will not run it again, so changing it silently produces
two different schemas that both claim to be version N.

Migrations run inside a transaction and record themselves in ``schema_version``
as part of that same transaction, so a failure leaves neither the change nor the
version row.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from program.memory import db


@dataclass(frozen=True)
class Migration:
    """One forward schema change.

    ``apply`` receives an open connection inside a transaction. There are no
    down-migrations: reversing a schema change on a store holding real memory is
    a restore-from-backup operation, not a routine one.
    """

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


# Version 1 is the initial schema in working.sql, recorded as applied when the
# database is first created. New changes start at 2.
INITIAL_VERSION = 1
INITIAL_NAME = "initial_schema"

MIGRATIONS: list[Migration] = []


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    return {row["version"] for row in rows}


def _record(conn: sqlite3.Connection, version: int, name: str) -> None:
    conn.execute(
        "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
        (version, name, db.now_iso()),
    )


def current_version(conn: sqlite3.Connection | None = None) -> int:
    """Highest applied version, or 0 if the database has no schema yet."""

    def _query(c: sqlite3.Connection) -> int:
        row = c.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return row["v"] or 0

    if conn is not None:
        return _query(conn)
    with db.connection() as own:
        return _query(own)


def run_working_migrations() -> list[str]:
    """Apply every pending migration in order. Returns the names applied."""
    applied: list[str] = []

    with db.transaction() as conn:
        known = _applied_versions(conn)

        if INITIAL_VERSION not in known:
            # working.sql has just been executed, so the initial schema exists;
            # this records that fact rather than performing it.
            _record(conn, INITIAL_VERSION, INITIAL_NAME)
            known.add(INITIAL_VERSION)
            applied.append(INITIAL_NAME)

        for migration in sorted(MIGRATIONS, key=lambda m: m.version):
            if migration.version in known:
                continue
            if migration.version <= INITIAL_VERSION:
                raise ValueError(
                    f"migration {migration.version} ({migration.name}) collides with "
                    f"the initial schema version {INITIAL_VERSION}"
                )
            migration.apply(conn)
            _record(conn, migration.version, migration.name)
            applied.append(migration.name)

    return applied


def verify_no_duplicate_versions() -> None:
    """Fail loudly if two migrations claim the same version.

    Called by the test suite. A duplicate version means one of them will be
    skipped on a database that has already seen the other, producing two
    different schemas that both report the same version.
    """
    seen: dict[int, str] = {}
    for migration in MIGRATIONS:
        if migration.version in seen:
            raise ValueError(
                f"duplicate migration version {migration.version}: "
                f"{seen[migration.version]} and {migration.name}"
            )
        seen[migration.version] = migration.name
