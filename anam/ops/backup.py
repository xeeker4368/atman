"""Backing up both databases and the vector store.

What "backup" means here, and why
=================================

**The two SQLite databases are captured with SQLite's own online backup API,
not a file copy** — and both are captured inside one held read lock, so they
are consistent *with each other* as well as individually.

A plain file copy is wrong here for two separate reasons:

1. *Torn pages.* A copy taken while a writer is part-way through committing can
   capture a database whose pages come from two different transactions. The
   rollback journal beside it may or may not be captured coherently with it.
   The backup API reads through SQLite, page by page, under a read lock, and
   cannot observe a half-written commit.

2. *Cross-database skew, which is the one that matters for this build.*
   ``anam/memory/db.py`` exists to guarantee that a message reaches
   ``archive.db`` and ``working.db`` in a single transaction or neither. Two
   independent backups — even two individually perfect ones — would capture
   archive at one instant and working at another, and a dual write landing
   between them reproduces in the backup exactly the half-state the write path
   was built to prevent. The whole atomicity guarantee would survive in the live
   stores and be lost in the copy of them.

So the snapshot is taken as:

* connection **A** opens a deferred transaction and reads from *both*
  databases, taking a SHARED lock on each. No writer can commit while it is
  held — verified, a concurrent writer gets ``database is locked``.
* connection **B** runs ``backup()`` for ``main`` and for the attached
  ``archive`` while A holds it.
* A commits, releasing.

**Why two connections.** ``Connection.backup()`` on a connection that is itself
holding a write transaction hangs — Python's backup loop retries ``SQLITE_BUSY``
against a lock only that same connection could release. This was established by
running it, not assumed. A *read* transaction on a *second* connection is
compatible with the backup's own read lock, which is what makes the pattern
work.

The cost is that writers block for the duration of the snapshot. At this
scale that is milliseconds, and correctness is worth more than that here.

The vector store is different, and says so
------------------------------------------
ChromaDB is a directory of SQLite plus binary HNSW index files. There is no
API that snapshots it transactionally, so it is copied as a directory and the
manifest records it as **best-effort**, not transactional. That is acceptable
precisely because it is *derived*: every vector is rebuildable from the
canonical ``chunks`` table via ``scripts/reconcile_vectors.py``. A backup with a
skewed vector store loses no information the archive does not still hold.

Scope
-----
This module **only ever creates files.** It never deletes, prunes, rotates or
overwrites — a destination that already exists is refused rather than replaced.
Backup rotation is deliberately absent: it is the one part of a backup tool that
can destroy data, and nothing has asked for it.

**Restore is not here and is not stubbed.** It is a Tier 3 task (BUILD_PLAN:
"Restore CLI (atomic, verified)") and needs its own design pass.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from anam import config
from anam.memory import db, vectors

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"

#: Consistency levels recorded per artifact, so a reader of the manifest never
#: has to guess which guarantee applies to which file.
TRANSACTIONAL = "transactional"
BEST_EFFORT = "best-effort"


class BackupError(RuntimeError):
    """A backup could not be completed, or could not be verified afterwards."""


@dataclass
class BackupArtifact:
    name: str
    relative_path: str
    size_bytes: int
    consistency: str
    sha256: str | None = None
    integrity_check: str | None = None
    note: str = ""


@dataclass
class BackupResult:
    directory: Path
    created_at: str
    artifacts: list[BackupArtifact] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    verified: bool = False

    @property
    def total_bytes(self) -> int:
        return sum(a.size_bytes for a in self.artifacts)


def _default_destination(now: datetime | None = None) -> Path:
    """``<backup_dir>/anam-backup-<UTC stamp>``, made unique if already taken.

    The stamp has second resolution, so two backups in the same second would
    otherwise collide and the second one would be refused. A numeric suffix
    keeps the never-overwrite rule intact while still letting them both run.
    An explicit ``--destination`` that already exists is still refused — the
    caller named that path and silently writing somewhere else would be worse.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    root = config.backup_dir()
    candidate = root / f"anam-backup-{stamp}"
    suffix = 2
    while candidate.exists():
        candidate = root / f"anam-backup-{stamp}-{suffix}"
        suffix += 1
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _integrity_check(path: Path) -> str:
    """Run ``PRAGMA integrity_check`` against a backed-up database."""
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "no result"
    finally:
        conn.close()


def _row_counts() -> dict[str, int]:
    """Counts recorded in the manifest, for comparing against a restore later."""
    counts: dict[str, int] = {}
    with db.connection() as conn:
        for label, sql in (
            ("archive.messages", "SELECT COUNT(*) FROM archive.messages"),
            ("archive.users", "SELECT COUNT(*) FROM archive.users"),
            ("working.messages", "SELECT COUNT(*) FROM messages"),
            ("working.users", "SELECT COUNT(*) FROM users"),
            ("working.conversations", "SELECT COUNT(*) FROM conversations"),
            ("working.chunks", "SELECT COUNT(*) FROM chunks"),
            ("working.settings", "SELECT COUNT(*) FROM settings"),
            ("working.supersedes", "SELECT COUNT(*) FROM supersedes"),
        ):
            counts[label] = conn.execute(sql).fetchone()[0]
    return counts


def _snapshot_databases(destination: Path) -> list[BackupArtifact]:
    """Capture both databases at one consistent instant.

    See the module docstring for why this is shaped the way it is: a read lock
    held on one connection across both ``backup()`` calls made on another.
    """
    working_dest = destination / "working.db"
    archive_dest = destination / "archive.db"

    with db.connection() as holder:
        # Deferred transaction, then a read from each database. The reads are
        # what actually acquire the SHARED locks — BEGIN alone acquires nothing.
        holder.execute("BEGIN")
        try:
            holder.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone()
            holder.execute("SELECT COUNT(*) FROM archive.sqlite_schema").fetchone()

            with db.connection() as reader:
                target = sqlite3.connect(str(working_dest))
                try:
                    reader.backup(target)
                finally:
                    target.close()

                target = sqlite3.connect(str(archive_dest))
                try:
                    reader.backup(target, name="archive")
                finally:
                    target.close()
        finally:
            holder.execute("COMMIT")

    artifacts = []
    for name, path in (("working.db", working_dest), ("archive.db", archive_dest)):
        artifacts.append(
            BackupArtifact(
                name=name,
                relative_path=name,
                size_bytes=path.stat().st_size,
                consistency=TRANSACTIONAL,
                sha256=_sha256(path),
                integrity_check=_integrity_check(path),
                note=(
                    "SQLite online backup API, taken under a read lock held "
                    "across both databases, so the pair is mutually consistent."
                ),
            )
        )
    return artifacts


def _copy_vector_store(destination: Path, warnings: list[str]) -> BackupArtifact | None:
    """Copy the ChromaDB directory. Best-effort by nature — see the docstring."""
    source = Path(vectors.chroma_path())
    if not source.exists():
        warnings.append(
            f"no vector store at {source}; nothing to copy. Vectors are "
            f"rebuildable from the chunks table regardless."
        )
        return None

    target = destination / "chroma"
    shutil.copytree(source, target)

    files = [p for p in target.rglob("*") if p.is_file()]
    return BackupArtifact(
        name="chroma",
        relative_path="chroma",
        size_bytes=sum(p.stat().st_size for p in files),
        consistency=BEST_EFFORT,
        note=(
            f"Directory copy of {len(files)} file(s). NOT transactionally "
            f"consistent: ChromaDB has no snapshot API and its HNSW index files "
            f"cannot be captured atomically. Acceptable because vectors are "
            f"derived — rebuild with scripts/reconcile_vectors.py against the "
            f"canonical chunks table, which IS captured transactionally."
        ),
    )


def create_backup(
    destination: Path | None = None,
    include_vectors: bool = True,
    now: datetime | None = None,
) -> BackupResult:
    """Back up both databases and, optionally, the vector store.

    Creates ``<backup_dir>/anam-backup-<UTC timestamp>/`` unless ``destination``
    names somewhere else. Refuses a destination that already exists — this
    module never overwrites anything.
    """
    if not db.working_path().exists() or not db.archive_path().exists():
        raise BackupError(
            f"no databases to back up. Expected {db.working_path()} and "
            f"{db.archive_path()}. Run init_databases() first."
        )

    created_at = (now or datetime.now(timezone.utc)).isoformat()
    target = Path(destination) if destination is not None else _default_destination(now)

    if target.exists():
        raise BackupError(
            f"{target} already exists. Backups never overwrite; choose another "
            f"destination or move the existing one aside."
        )

    counts = _row_counts()
    target.mkdir(parents=True)

    warnings: list[str] = []
    try:
        artifacts = _snapshot_databases(target)
        if include_vectors:
            vector_artifact = _copy_vector_store(target, warnings)
            if vector_artifact is not None:
                artifacts.append(vector_artifact)
        else:
            warnings.append("vector store skipped at the caller's request")
    except Exception as exc:
        raise BackupError(f"backup to {target} failed: {exc}") from exc

    failed = [
        a for a in artifacts
        if a.integrity_check is not None and a.integrity_check != "ok"
    ]
    if failed:
        raise BackupError(
            "backup completed but failed verification: "
            + "; ".join(f"{a.name}: {a.integrity_check}" for a in failed)
            + f". The files are left in place at {target} for inspection."
        )

    result = BackupResult(
        directory=target,
        created_at=created_at,
        artifacts=artifacts,
        row_counts=counts,
        warnings=warnings,
        verified=True,
    )
    _write_manifest(result)
    logger.info(
        "backup written to %s (%d artifact(s), %d bytes)",
        target, len(artifacts), result.total_bytes,
    )
    return result


def _write_manifest(result: BackupResult) -> None:
    """Record what was captured, and under which guarantee."""
    manifest = {
        "created_at": result.created_at,
        "source": {
            "working_db": str(db.working_path()),
            "archive_db": str(db.archive_path()),
            "chroma_dir": str(vectors.chroma_path()),
        },
        "schema_version": _schema_version(),
        "row_counts": result.row_counts,
        "artifacts": [asdict(a) for a in result.artifacts],
        "warnings": result.warnings,
        "consistency": {
            TRANSACTIONAL: (
                "SQLite online backup API under a read lock held across both "
                "databases. The archive/working pair cannot be mutually skewed."
            ),
            BEST_EFFORT: (
                "Plain directory copy. Derived data only, rebuildable from the "
                "transactionally captured chunks table."
            ),
        },
        "restore": (
            "No restore tooling exists. Restore is a Tier 3 task and has not "
            "been designed; do not improvise one from these files without it."
        ),
    }
    (result.directory / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def _schema_version() -> int | None:
    try:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM schema_version"
            ).fetchone()
            return row["v"] if row else None
    except sqlite3.OperationalError:
        return None


def read_manifest(backup_dir: Path) -> dict:
    """Read a backup's manifest. Used by tests and by whoever inspects one."""
    return json.loads((Path(backup_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
