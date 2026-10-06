"""Test-session guards that keep the suite out of the real runtime store.

This file exists in Phase 0, before there is any store to protect, on purpose.
The reference build added its equivalent only after its suite had been writing
real records into the production store for about seven weeks with nothing
failing — the writes either succeeded into production or were swallowed by an
``except Exception`` downstream. Adding the guard alongside the first store is
too late; the guard has to predate it.

**The mechanism is a fingerprint, and it had to change.** Armed at task 1.4, the
guard asked "was this directory *created* while the suite ran?" — which is
answerable only while the directory does not exist. By 2026-09-22 all five existed
(`data/` from the first seeded store onward), so **four of the five checks could
never fire again**, and a test that forgot ``isolated_data_dir`` and wrote rows into
the real ``working.db`` passed silently. `workspace/` had already needed a different
check for the same reason — it is part of the tracked skeleton — and got a file-set
snapshot at Phase 4 B0.

A file-set snapshot alone would not have been enough either: **writing rows into an
existing database creates no new file**. So all five are now watched one way, by
``{path: (size, mtime_ns)}`` captured at import and compared at session end, which
catches creation and modification together. One mechanism instead of two with a gap
between them.

The violation type derives from ``BaseException`` deliberately: retrieval and
indexing paths wrap store access in ``except Exception``, and a guard those can
swallow is not a guard.

**A second layer, at the moment a store is opened** (B15, 2026-09-24). The
fingerprint sees only what a test *changed*, so a test that lost its isolation and
merely *read* the real store passed silently. That happened: a B3 test draft called
``monkeypatch.undo()``, which also reverted ``isolated_data_dir``'s environment, and
its recovery check then ran migrations and assertions against the real
``working.db`` — already at the latest version, so nothing was written, nothing
changed, and the test passed without testing anything. ``sqlite3.connect`` and
``chromadb.PersistentClient`` are therefore wrapped for the whole session: opening
anything under a real runtime directory raises ``StoreIsolationViolation`` there and
then, read or write. Each violation is also recorded and re-reported at session end,
so one raised inside code that catches ``BaseException`` still fails the run. The
fingerprint stays as the backstop for writes that go around both entry points.

**Plain file opens too** (B16, 2026-09-30). ``builtins.open``, ``io.open`` (which
``pathlib`` calls, so ``Path.read_bytes`` and friends are covered) and ``os.open`` go
through the same check, so a test reading an uploaded artifact or a workspace piece
directly is stopped at the open as well. The routes NOT covered are listed beside the
wrappers and in ``BUILT.md``.

**Known limit, stated rather than implied:** the comparison cannot tell the suite's
writes from another process's. Running the suite while anything else is using the
real store will fail the session, and that is the right direction — a foreign write
is indistinguishable from a leak, and reporting it is safer than filtering it out.
"""

import builtins
import io
import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
import requests
import requests.sessions

from program import config
from program.memory import vectors
from program.ops import store_lock

# Captured at import — before any test can patch config.
REAL_DATA_DIR = str(config.data_dir())
REAL_CHROMA_DIR = vectors.chroma_path()
# Added with the backup CLI: it is a third real location the suite can write
# into, and isolating the data directory does not isolate this one. A backup
# test that forgot to repoint it wrote two real backup directories into the
# repo before this guard existed.
REAL_BACKUP_DIR = str(config.backup_dir())
# Added with file ingestion (task 2.6): a fourth real location, and the same
# trap as the backup directory — it resolves from its own config key, so
# repointing ANAM_DATA_DIR does not move it, even though its default sits inside
# `data/`. Uploaded files are the one thing here that cannot be regenerated.
REAL_ARTIFACT_DIR = str(config.artifact_dir())
# Added at Phase 4 B0 (2026-09-21), and it needs a DIFFERENT check from the four
# above. `workspace/` is part of the tracked repository skeleton — Phase 0 created
# `workspace/{generated,uploads,writing,research,journals}/` with a `.gitkeep` in
# each — so "was this directory created during the run?" can never fire for it. The
# question is whether the suite WROTE INTO it.
#
# The gap was real, not theoretical: `tests/test_image_generate.py` wrote 65 real
# PNGs into the repository's own `workspace/` before this existed, because
# `isolated_data_dir` repointed the data, backup and artifact directories and not
# this one. Same trap as the backup directory at task 1.14 and the artifact
# directory at 2.6, for the third time — it resolves from its own config key.
REAL_WORKSPACE_DIR = str(config.workspace_dir())

#: Every real runtime directory the suite must stay out of, keyed by the name of
#: the `config` accessor that resolves it. `tests/test_directories.py` asserts this
#: covers every accessor, by comparing resolved paths rather than by grepping this
#: file for a spelling.
REAL_DIRS: dict[str, str] = {
    "data_dir": REAL_DATA_DIR,
    "chroma": REAL_CHROMA_DIR,
    "backup_dir": REAL_BACKUP_DIR,
    "artifact_dir": REAL_ARTIFACT_DIR,
    "workspace_dir": REAL_WORKSPACE_DIR,
}

#: Written by the OS, not by the suite. Their presence or mtime says nothing about
#: isolation, and Finder touching one mid-run would otherwise fail the session.
_IGNORED_NAMES = {".DS_Store"}


def _fingerprint() -> dict[str, tuple[int, int]]:
    """Every file under every real runtime directory, as ``{path: (size, mtime_ns)}``.

    **Size and mtime, not just the path set, and that is the whole point.** The
    four original checks asked "was this directory *created* during the run?",
    which was answerable only while the directories did not exist. All five exist
    now — `data/` from the moment a store was seeded — so those checks could never
    fire again, and a test that forgot `isolated_data_dir` and wrote rows into the
    real `working.db` passed silently. A path-set snapshot (which is what
    `workspace/` used) would not have caught that either: **writing rows into an
    existing database creates no new file.** Comparing `(size, mtime_ns)` catches
    creation and modification with one mechanism, so the five directories are no
    longer watched two different ways with a gap between them.

    Nested roots are deduplicated by keying on the path: `data/chromadb` and
    `data/artifacts` sit inside `data/`, so a file under either is recorded once.
    """
    seen: dict[str, tuple[int, int]] = {}
    for root in REAL_DIRS.values():
        base = Path(root)
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.name in _IGNORED_NAMES:
                continue
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
            except OSError:  # vanished mid-walk, or unreadable — not evidence
                continue
            seen[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return seen


_FINGERPRINT_AT_IMPORT = _fingerprint()


class StoreIsolationViolation(BaseException):
    """Raised when the suite touched a real runtime store.

    Derives from ``BaseException`` deliberately: retrieval, indexing and tool
    dispatch all wrap work in ``except Exception``, and a guard those can swallow
    is not a guard. `registry.dispatch` and `turn._after_durable` both name it for
    that reason.
    """


# --- the open-time layer (B15) ------------------------------------------------

_REAL_ROOTS = [os.path.realpath(root) for root in REAL_DIRS.values()]

#: Every open of a real store, recorded before raising so the session-end check
#: still reports one that something caught.
_OPEN_VIOLATIONS: list[str] = []


def _real_root_for(target: object) -> str | None:
    """The real runtime directory ``target`` lies in, or ``None``."""
    if target is None:
        return None
    text = os.fspath(target) if isinstance(target, (str, os.PathLike)) else None
    if text is None or text == ":memory:" or text == "":
        return None
    if text.startswith("file:"):
        parsed = urlparse(text)
        if parsed.query and "mode=memory" in parsed.query:
            return None
        text = unquote(parsed.path)
    resolved = os.path.realpath(text)
    for root in _REAL_ROOTS:
        if resolved == root or resolved.startswith(root + os.sep):
            return root
    return None


def _refuse(what: str, target: object) -> None:
    root = _real_root_for(target)
    if root is None:
        return
    message = (
        f"{what} opened {os.fspath(target)!r}, inside the real runtime directory "
        f"{root!r}. A read counts: a test that lost its isolation and only reads "
        f"passes without testing anything. Take `isolated_data_dir`, and never call "
        f"`monkeypatch.undo()` in a test that uses it — the fixture's environment "
        f"lives on the same monkeypatch."
    )
    _OPEN_VIOLATIONS.append(message)
    raise StoreIsolationViolation(message)


_real_sqlite_connect = sqlite3.connect


def _guarded_connect(database, *args, **kwargs):
    _refuse("sqlite3.connect", database)
    return _real_sqlite_connect(database, *args, **kwargs)


sqlite3.connect = _guarded_connect

try:
    import chromadb as _chromadb
except ImportError:  # pragma: no cover - chromadb is a declared dependency
    _chromadb = None

if _chromadb is not None:
    _real_persistent_client = _chromadb.PersistentClient

    def _guarded_persistent_client(path=None, *args, **kwargs):
        # Chroma 1.x opens its own SQLite from Rust, so the sqlite3 wrapper above
        # never sees it; the client constructor is the Python-side entry point.
        _refuse("chromadb.PersistentClient", path)
        return _real_persistent_client(path, *args, **kwargs)

    _chromadb.PersistentClient = _guarded_persistent_client


# --- plain file opens (B16) ---------------------------------------------------
#
# B15 guarded the two ways a *store* is opened. A test that lost its isolation could
# still read a real file directly (an uploaded artifact, a workspace piece) and pass
# without testing anything. Three entry points, because on Python 3.14
# `builtins.open is io.open` but the NAMES are looked up separately: bare `open()`
# resolves through `builtins`, while `pathlib`'s `Path.open`, `read_bytes`,
# `read_text` and `write_*` call `io.open`. Patching one name alone would leave every
# `Path` read unguarded. `os.open` is the low-level route beneath both.
#
# NOT covered, deliberately (recorded in BUILT.md as the scope limit): `io.FileIO`
# constructed directly; files opened by C extensions (SQLite and Chroma are guarded at
# their own entry points above); `mmap`; subprocesses; and `os.open` with `dir_fd`,
# whose path is relative to a directory descriptor this cannot resolve portably.


def _path_of(file: object) -> object:
    """The path an open call names, or ``None`` for a file descriptor."""
    if isinstance(file, int):
        return None
    if isinstance(file, bytes):
        return os.fsdecode(file)
    return file


_real_builtin_open = builtins.open
_real_io_open = io.open
_real_os_open = os.open


def _guarded_builtin_open(file, *args, **kwargs):
    _refuse("open", _path_of(file))
    return _real_builtin_open(file, *args, **kwargs)


def _guarded_io_open(file, *args, **kwargs):
    _refuse("io.open", _path_of(file))
    return _real_io_open(file, *args, **kwargs)


def _guarded_os_open(path, flags, mode=0o777, *, dir_fd=None):
    if dir_fd is None:
        _refuse("os.open", _path_of(path))
    return _real_os_open(path, flags, mode, dir_fd=dir_fd)


builtins.open = _guarded_builtin_open
io.open = _guarded_io_open
os.open = _guarded_os_open


# --- live tests, and the guard that keeps every other test off the real Ollama ---------------
#
# A test that calls a real service is marked ``@pytest.mark.live("ollama")`` (or "searxng",
# "comfyui", "internet") and is skipped unless ``--run-live`` is given. Nothing is probed at
# collection: the old ``skipif(not ollama.is_available())`` ran a request at import, so merely
# collecting the suite called Ollama (seven times), SearXNG and example.com. With ``--run-live``
# the named service is probed when the test is about to run, and the test is skipped if it is down.
#
# Every test WITHOUT the marker, and collection itself, is refused any request to the real Ollama
# (``OllamaGuardViolation``, a BaseException so ``except Exception`` cannot swallow it), recorded
# and re-reported at session end like the store guard. Tests that exercise a failing Ollama point
# ``ANAM_OLLAMA_HOST`` at a dead port, which is not the real one and so is not refused.


class OllamaGuardViolation(BaseException):
    """An unmarked test, or collection, sent a request to the real Ollama."""


_LIVE_ALLOWED = False
_OLLAMA_VIOLATIONS: list[str] = []
_REAL_OLLAMA_PORT = 11434
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_REAL_OLLAMA_AT_IMPORT = urlparse(config.ollama_host())


def _is_real_ollama(url: object) -> bool:
    """True for the default local Ollama (loopback, port 11434) or the host configured at import."""
    parsed = urlparse(str(url))
    host, port = parsed.hostname, parsed.port
    if host is None:
        return False
    if host in _LOOPBACK_NAMES and port == _REAL_OLLAMA_PORT:
        return True
    return (host == _REAL_OLLAMA_AT_IMPORT.hostname and port == _REAL_OLLAMA_AT_IMPORT.port
            and host in _LOOPBACK_NAMES)


_real_session_request = requests.sessions.Session.request


def _guarded_session_request(self, method, url, *args, **kwargs):
    if not _LIVE_ALLOWED and _is_real_ollama(url):
        message = (f"{method} {url} reached the real Ollama from a test (or from collection) "
                   f"that is not marked @pytest.mark.live(\"ollama\"). Patch the call, or mark "
                   f"the test.")
        _OLLAMA_VIOLATIONS.append(message)
        raise OllamaGuardViolation(message)
    return _real_session_request(self, method, url, *args, **kwargs)


requests.sessions.Session.request = _guarded_session_request


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="run tests marked live (a real Ollama, SearXNG, ComfyUI or the internet)")


def _probe(service: str) -> bool:
    """Whether a live service answers. Only called for a live-marked test under --run-live."""
    global _LIVE_ALLOWED
    _LIVE_ALLOWED = True
    try:
        if service == "ollama":
            from program.engine import ollama
            return ollama.is_available()
        if service == "searxng":
            return requests.get(config.searxng_url(), timeout=3).status_code == 200
        if service == "comfyui":
            from program.media import comfyui
            comfyui.available()
            return True
        if service == "internet":
            requests.get("https://example.com/", timeout=5)
            return True
        raise ValueError(f"unknown live service {service!r}")
    except Exception:  # noqa: BLE001 - any failure means the service is not there
        return False
    finally:
        _LIVE_ALLOWED = False


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    marker = item.get_closest_marker("live")
    if marker is None:
        return
    if not item.config.getoption("--run-live"):
        pytest.skip("live test: pass --run-live to run it")
    service = marker.args[0] if marker.args else "ollama"
    if not _probe(service):
        pytest.skip(f"{service} is not reachable; live test skipped")


@pytest.fixture(autouse=True)
def _ollama_guard(request):
    """Allow the real Ollama only for a test marked live."""
    global _LIVE_ALLOWED
    _LIVE_ALLOWED = request.node.get_closest_marker("live") is not None
    yield
    _LIVE_ALLOWED = False


@pytest.fixture(scope="session", autouse=True)
def _guard_runtime_store():
    """Fail the session if the suite opened, created or modified anything real."""
    yield

    if _OLLAMA_VIOLATIONS:
        raise OllamaGuardViolation(
            f"the suite sent {len(_OLLAMA_VIOLATIONS)} request(s) to the real Ollama from unmarked "
            f"tests; the first:\n  {_OLLAMA_VIOLATIONS[0]}"
        )

    if _OPEN_VIOLATIONS:
        raise StoreIsolationViolation(
            f"the suite opened a real runtime store {len(_OPEN_VIOLATIONS)} time(s); "
            f"the first:\n  {_OPEN_VIOLATIONS[0]}"
        )

    after = _fingerprint()
    created = sorted(set(after) - set(_FINGERPRINT_AT_IMPORT))
    modified = sorted(
        path for path in set(after) & set(_FINGERPRINT_AT_IMPORT)
        if after[path] != _FINGERPRINT_AT_IMPORT[path]
    )
    if not created and not modified:
        return

    def _listing(label: str, paths: list[str]) -> str:
        shown = "\n".join(f"      {p}" for p in paths[:5])
        more = f"\n      ... and {len(paths) - 5} more" if len(paths) > 5 else ""
        return f"  - {len(paths)} file(s) {label}:\n{shown}{more}"

    parts = []
    if created:
        parts.append(_listing("created in a real runtime directory", created))
    if modified:
        parts.append(_listing("MODIFIED in a real runtime directory", modified))

    raise StoreIsolationViolation(
        "the suite touched a real runtime store:\n"
        + "\n".join(parts)
        + "\n\n    A test that reads or writes a store must take `isolated_data_dir`, "
        "which repoints ANAM_DATA_DIR, ANAM_BACKUP_DIR, ANAM_ARTIFACT_DIR and "
        "ANAM_WORKSPACE_DIR at a temporary path. See AGENTS.md, 'Adding a runtime "
        "directory'.\n    Note this cannot tell the suite's writes from another "
        "process's: do not run the suite while anything else is using the real store."
    )


def release_test_stores() -> None:
    """Close what a test's stores hold open: every cached Chroma system, and store locks.

    ``vectors.reset_vector_store()`` empties only this project's cache. Chroma keeps its
    own process-wide cache of systems (``SharedSystemClient._identifier_to_system``), and a
    system keeps about ten descriptors open until it is **stopped**; clearing the cache
    alone releases nothing (measured 2026-10-04, ~2,050 descriptors over the suite, fatal at
    ``ulimit -n 256``). Order matters: our cache first, so no later test is handed a client
    whose system is stopped; then ``stop()``, which closes the descriptors; then Chroma's
    cache. Both are internal Chroma API, pinned and checked in
    ``tests/test_store_lock.py::test_the_chroma_stale_reader_recovery_api_exists``.

    A script's ``main()`` run in-process keeps its ``store_lock`` for the life of the
    process by design; a test process runs many stores, so it is released here too.
    """
    from chromadb.api.shared_system_client import SharedSystemClient

    vectors.reset_vector_store()
    for system in list(SharedSystemClient._identifier_to_system.values()):
        system.stop()
    SharedSystemClient.clear_system_cache()
    store_lock.release_for_process()


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point the configured data directory at a temporary path — for EVERY test.

    **Autouse since B15 (2026-09-24).** It used to be opt-in, and 77 tests never
    opted in: they call settings-backed ``config`` accessors (``chat_model()``,
    ``model_options()``, ``num_ctx``, the classifier settings), which go through
    ``settings.store.resolve()`` to the real ``working.db``. Harmless only because
    the real settings table happened to be empty — the first setting saved through
    the admin panel would have had those tests silently running against production
    configuration. Isolation is now the default rather than something each test has
    to remember, the same shape as B4 (correct behaviour by default, not opt-in).
    Tests that still name ``isolated_data_dir`` get this same instance and its path.

    Works because ``program.config`` resolves values through accessor functions at
    call time rather than binding module-level constants at import — see the
    module docstring in ``program/config.py`` for why that distinction matters.
    """
    monkeypatch.setenv("ANAM_DATA_DIR", str(tmp_path))
    # The backup directory resolves from its own config key, so repointing the
    # data directory alone leaves backups writing into the real one.
    monkeypatch.setenv("ANAM_BACKUP_DIR", str(tmp_path / "backups"))
    # Same reason as the backup directory: its own key, so isolating the data
    # directory leaves it pointing at the real one.
    monkeypatch.setenv("ANAM_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    # Third instance of the same trap, and the one that actually leaked: the
    # workspace resolves from its own key too, so isolating the data directory left
    # generated images writing into the repository.
    monkeypatch.setenv("ANAM_WORKSPACE_DIR", str(tmp_path / "workspace"))
    # Moltbook is switched OFF for every test, so its tools are never offered and no
    # unrelated test (a live-model one especially) can have the model call the real
    # service. Tests of those tools switch it on explicitly. The key is blanked too,
    # so nothing in a test can read the real one through config (posting, later).
    # The environment layer wins over config/local.toml.
    monkeypatch.setenv("ANAM_MOLTBOOK_ENABLED", "false")
    monkeypatch.setenv("ANAM_MOLTBOOK_API_KEY", "")
    # Notes ships dark: its tools are not offered in any test unless that test switches them on.
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "false")
    config.reload()
    # Stores are cached per resolved path, so clearing here means this test gets
    # its own vector store rather than one another test built for another path.
    vectors.reset_vector_store()
    yield tmp_path
    monkeypatch.delenv("ANAM_DATA_DIR", raising=False)
    monkeypatch.delenv("ANAM_BACKUP_DIR", raising=False)
    monkeypatch.delenv("ANAM_ARTIFACT_DIR", raising=False)
    monkeypatch.delenv("ANAM_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("ANAM_MOLTBOOK_ENABLED", raising=False)
    monkeypatch.delenv("ANAM_MOLTBOOK_API_KEY", raising=False)
    monkeypatch.delenv("ANAM_NOTES_ENABLED", raising=False)
    config.reload()
    release_test_stores()
