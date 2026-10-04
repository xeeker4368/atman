"""One process writes vectors to a store at a time.

The defect this exists for (B23, measured 2026-10-03 and again while designing
piece 3.5) is in ChromaDB, not here: **a long-lived process that has queried the
collection does not see vectors another process writes.** It fails in two shapes.

* **Loud.** If the collection had no on-disk HNSW segment when that process first
  queried, every later query raises ``InternalError: ... Error creating hnsw
  segment reader: Nothing found on disk``, until the process restarts.
* **Quiet.** If it did, later queries simply never return the new vectors — no
  error, while ``count()`` and ``get()`` can see them.

``vectors.ChromaVectorStore`` recovers from the loud shape (it catches the error,
clears Chroma's system cache, reopens its client and retries once). **Nothing can
detect the quiet one**, which is why the refusal here is the load-bearing half: a
script that writes vectors refuses to run while a server holds the store, so the
situation does not arise.

Why an advisory file lock rather than a pid file
-----------------------------------------------
``flock`` is released by the kernel when the holder dies, so **there is no stale
lock to detect or clear** — a crashed server leaves a file whose text is stale and
whose authority is gone. A pid file would need a liveness check, and
``os.kill(pid, 0)`` is wrong exactly when it matters, because a reused pid reads
as alive. The file's text (pid, start time, port, resolved data directory) exists
only to make the refusal legible.

Proven in two real processes on both filesystems this project uses, 2026-10-04:
on the data volume (`/dev/disk5s1`, APFS) and on a scratch directory
(`/dev/disk3s1`, APFS), a second process is refused with ``BlockingIOError``
(errno 35) while a holder lives, and takes the lock immediately after the holder
is killed with ``SIGKILL``. ``flock`` is advisory, per open file description, and
is not reliable over NFS; this store is local.

What is NOT locked
------------------
The guard is at a **script's entry point**, not inside ``vectors.upsert``. A
server's own writes — the post-response checkpoint, the idle sweep, the recovery
drain — are the writes that are safe while it runs, and they must not be refused.
Reading is not locked either: another process querying the store disturbs nobody.
"""

from __future__ import annotations

import fcntl
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from program import config

logger = logging.getLogger(__name__)

#: The lock lives inside the data directory, so it follows the store a process is
#: actually using — including a scratch store. It is deliberately **not** its own
#: config key: a new runtime directory would need backup and isolation coverage of
#: its own (``AGENTS.md``, "Adding a runtime directory"), and this is a file, not a
#: directory, inside one that already has both.
LOCK_FILENAME = "server.lock"


class StoreInUse(RuntimeError):
    """Something else holds this store's lock.

    For a script, that something is a running server, and the fix is to stop it.
    For a server, it is another server on the same store, which is the same
    hazard seen from the other side.
    """


def lock_path() -> Path:
    return config.data_dir() / LOCK_FILENAME


def _describe() -> str:
    """Whatever the lock file says, for the refusal message. Never authoritative."""
    try:
        text = lock_path().read_text().strip()
    except OSError:
        return "no details recorded"
    return text.replace("\n", ", ") or "no details recorded"


@contextmanager
def _flock(action: str, note: str | None) -> Iterator[int]:
    # The data directory may not exist yet: a fresh store, or a script run before
    # the first server start. Creating it here is why `seed_dataset` can take the
    # lock before `db.init_databases()` would have made it.
    config.data_dir().mkdir(parents=True, exist_ok=True)
    path = lock_path()
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StoreInUse(
                f"refusing to {action}: another process is using this store "
                f"({_describe()}). Stop it first — vectors written by a second "
                f"process break the running server's vector search until it "
                f"restarts, and the quiet half of that failure is silent."
            ) from None
        if note is not None:
            os.ftruncate(fd, 0)
            os.write(fd, note.encode("utf-8"))
        yield fd
    finally:
        # Closing releases the lock; the file stays, carrying text with no
        # authority. The kernel does the same if this process dies instead.
        os.close(fd)


@contextmanager
def hold_for_server() -> Iterator[None]:
    """Hold the store for a running server, refusing a second one.

    Taken in the application's lifespan **after** the secret check and the
    capability probe — so an unconfigured server still touches nothing — and
    **before** the databases are created or migrated, which is the work that must
    not happen twice at once.
    """
    note = (
        f"pid {os.getpid()}\n"
        f"started {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"port {config.api_port()}\n"
        f"data {config.data_dir()}\n"
    )
    with _flock("start a server on this store", note):
        logger.info("holding %s for this server", lock_path())
        yield


#: Locks this process holds, by resolved lock path. Keyed by path rather than kept
#: as one flag because the lock is per store: a process that has taken one store's
#: lock has said nothing about another's, and a second call for the same store is
#: idempotent rather than a refusal from itself.
_held_for_process: dict[Path, object] = {}


def hold_or_refuse(action: str) -> None:
    """Take the store for the rest of this process, or raise :class:`StoreInUse`.

    What a vector-writing script calls before it does anything. The lock is held
    for the whole run — so two such scripts cannot overlap either, and a server
    cannot start underneath one — and released when the process exits.

    ``action`` is the plain-words thing being refused, e.g. ``"reconcile
    vectors"``. There is deliberately no force flag: the correct response is to
    stop the server.
    """
    path = lock_path()
    if path in _held_for_process:
        return
    note = (
        f"pid {os.getpid()}\n"
        f"holding to {action}\n"
        f"started {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"data {config.data_dir()}\n"
    )
    manager = _flock(action, note)
    manager.__enter__()
    _held_for_process[path] = manager


def release_for_process() -> None:
    """Drop every lock taken by :func:`hold_or_refuse`. For tests.

    A script never needs this: the process exits and the kernel releases. A test
    process runs many stores in one interpreter, which is the case this exists for.
    """
    while _held_for_process:
        _, manager = _held_for_process.popitem()
        manager.__exit__(None, None, None)
