"""One process writes vectors to a store at a time (piece 3.5, step 5).

Two halves, and the quiet one is why the refusal exists:

* the **recovery** in ``vectors.ChromaVectorStore.query`` handles the loud shape of
  B23 — a Chroma ``InternalError`` once another process has written;
* the **refusal** here handles the shape nothing can detect: a long-lived process
  that silently never sees the new vectors.

The B23 tests run a real second process, because the fault is in Chroma's view of
a store across processes and nothing in one process reproduces it.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from program import config
from program.memory import vectors
from program.ops import store_lock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

#: Operator scripts that write vectors, and must refuse while a server holds the
#: store. Every other script that touches the chunking, indexing or reconcile
#: modules is listed below with the reason it does not need the lock.
VECTOR_WRITING_SCRIPTS = {
    "close_idle_conversations.py": "final chunking embeds and upserts",
    "reconcile_vectors.py": "its whole job is writing vectors",
    "seed_dataset.py": "seeding chunks and embeds",
    "write_journal.py": "--index embeds the entry",
}

#: Scripts that mention a vector-writing module and still need no lock.
NO_LOCK_NEEDED = {
    "backup.py": "copies the store; it writes no vector, and backing up a running "
                 "store is supported",
}


def _clean_env(tmp_path) -> dict:
    """A child process pointed at a throwaway store, with no inherited ANAM_* dirs."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANAM_")}
    env["ANAM_DATA_DIR"] = str(tmp_path / "data")
    # The suite's copy of the tracked config files (conftest.pytest_configure), so the child
    # does not read the developer's config/local.toml either.
    env["ANAM_CONFIG_DIR"] = os.environ["ANAM_CONFIG_DIR"]
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_child(code: str, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        capture_output=True, text=True, env=env, timeout=120,
    )


# ---------------------------------------------------------------------------
# flock itself, on the real filesystem
# ---------------------------------------------------------------------------


def test_a_second_process_is_refused_while_a_holder_lives(isolated_data_dir, tmp_path):
    """Proven against another process, not by reading the flags."""
    env = _clean_env(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent("""
            import sys, time
            from program.ops import store_lock
            store_lock.hold_or_refuse("hold for a test")
            print("HELD", flush=True)
            time.sleep(30)
        """)],
        stdout=subprocess.PIPE, text=True, env=env,
    )
    try:
        assert holder.stdout.readline().strip() == "HELD"
        refused = _run_child("""
            from program.ops import store_lock
            try:
                store_lock.hold_or_refuse("reconcile vectors")
                print("TOOK")
            except store_lock.StoreInUse as exc:
                print("REFUSED", exc)
        """, env)
        assert refused.stdout.startswith("REFUSED"), refused.stdout + refused.stderr
        assert "reconcile vectors" in refused.stdout
        assert "pid" in refused.stdout, "the refusal says who holds it"
    finally:
        holder.kill()
        holder.wait(timeout=30)


def test_a_killed_holder_leaves_no_lock_to_clear(isolated_data_dir, tmp_path):
    """Why flock and not a pid file: the kernel releases it, so nothing is stale."""
    env = _clean_env(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent("""
            import time
            from program.ops import store_lock
            store_lock.hold_or_refuse("hold for a test")
            print("HELD", flush=True)
            time.sleep(30)
        """)],
        stdout=subprocess.PIPE, text=True, env=env,
    )
    assert holder.stdout.readline().strip() == "HELD"
    holder.kill()
    holder.wait(timeout=30)

    took = _run_child("""
        from program.ops import store_lock
        store_lock.hold_or_refuse("reconcile vectors")
        print("TOOK")
    """, env)

    assert took.stdout.strip() == "TOOK", took.stdout + took.stderr
    assert (Path(env["ANAM_DATA_DIR"]) / store_lock.LOCK_FILENAME).exists(), (
        "the file stays; it is the lock that is gone, not the text"
    )


def test_the_lock_file_lives_in_the_data_directory(isolated_data_dir):
    assert store_lock.lock_path() == config.data_dir() / "server.lock"
    assert store_lock.lock_path().is_relative_to(config.data_dir())


def test_taking_the_lock_creates_the_data_directory(isolated_data_dir, monkeypatch):
    """seed_dataset takes it before anything would have made the directory."""
    fresh = config.data_dir() / "not-yet"
    monkeypatch.setenv("ANAM_DATA_DIR", str(fresh))
    config.reload()
    assert not fresh.exists()

    store_lock.hold_or_refuse("seed a corpus")
    try:
        assert (fresh / "server.lock").exists()
    finally:
        store_lock.release_for_process()


# ---------------------------------------------------------------------------
# The scripts
# ---------------------------------------------------------------------------


def test_a_vector_writing_script_refuses_while_a_server_holds_the_store(
    isolated_data_dir, capsys, monkeypatch
):
    from scripts import reconcile_vectors

    monkeypatch.setattr(sys, "argv", ["reconcile_vectors"])
    with store_lock.hold_for_server():
        code = reconcile_vectors.main()

    assert code == 1
    captured = capsys.readouterr()
    assert "refusing to reconcile vectors" in captured.err
    assert "another process is using this store" in captured.err
    assert "pid" in captured.err


def test_every_vector_writing_script_takes_the_lock():
    """A new script that writes vectors cannot omit the guard silently."""
    mentions_vectors = set()
    for path in sorted(SCRIPTS.glob("*.py")):
        source = path.read_text()
        if any(word in source for word in ("chunking", "reconcile", "indexing",
                                          "get_vector_store")):
            mentions_vectors.add(path.name)

    unaccounted = mentions_vectors - set(VECTOR_WRITING_SCRIPTS) - set(NO_LOCK_NEEDED)
    assert not unaccounted, (
        f"these scripts touch a vector-writing module and are neither listed as "
        f"needing the store lock nor exempted with a reason: {sorted(unaccounted)}"
    )
    stale = (set(VECTOR_WRITING_SCRIPTS) | set(NO_LOCK_NEEDED)) - mentions_vectors
    assert not stale, f"these are listed but no longer touch one: {sorted(stale)}"

    for name in VECTOR_WRITING_SCRIPTS:
        source = (SCRIPTS / name).read_text()
        assert "store_lock.hold_or_refuse(" in source, (
            f"{name} writes vectors and never takes the store lock"
        )
    for name in NO_LOCK_NEEDED:
        source = (SCRIPTS / name).read_text()
        assert "hold_or_refuse" not in source, f"{name} is listed as needing no lock"


def test_no_script_offers_a_way_round_the_refusal():
    """There is deliberately no force flag: the answer is to stop the server."""
    for name in VECTOR_WRITING_SCRIPTS:
        source = (SCRIPTS / name).read_text()
        for flag in ("--force", "--ignore-lock", "--no-lock"):
            assert flag not in source, f"{name} offers {flag}"


# ---------------------------------------------------------------------------
# The server's side
# ---------------------------------------------------------------------------


def test_a_second_server_on_one_store_is_refused(isolated_data_dir):
    with store_lock.hold_for_server():
        with pytest.raises(store_lock.StoreInUse, match="start a server"):
            with store_lock.hold_for_server():
                pass


def test_the_server_releases_the_lock_on_shutdown(isolated_data_dir):
    with store_lock.hold_for_server():
        pass

    # A script can run once the server has gone.
    store_lock.hold_or_refuse("reconcile vectors")
    store_lock.release_for_process()


def test_the_lock_is_taken_after_the_secret_check_and_the_probe():
    """An unconfigured server must still touch nothing on disk."""
    source = (ROOT / "program/api/app.py").read_text()
    tree = ast.parse(source)
    body = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    order = [
        ast.unparse(statement) for statement in body.body
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Constant)
    ]
    assert order[0].startswith("config.auth_session_secret")
    assert order[1].startswith("capability.probe")
    assert order[2].startswith("with store_lock.hold_for_server")
    assert "db.init_databases()" in order[2], "the lock must cover the migration"


# ---------------------------------------------------------------------------
# B23: the recovery, in two real processes
# ---------------------------------------------------------------------------


WRITER = """
    import os, random, sys
    os.environ["ANAM_DATA_DIR"] = sys.argv[1]
    from program.memory import vectors
    random.seed(int(sys.argv[2]))
    store = vectors.get_vector_store()
    store.upsert(sys.argv[3], [random.random() for _ in range(768)],
                 {"source_type": "conversation"})
    print("WROTE", store.count())
"""


@pytest.fixture
def chroma_store(isolated_data_dir, tmp_path):
    """A real Chroma store in a temporary directory, plus a writer subprocess."""
    env = _clean_env(tmp_path)
    data_dir = Path(env["ANAM_DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)

    def write(seed: int, chunk_id: str):
        done = _run_child(WRITER, env, str(data_dir), str(seed), chunk_id)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    store = vectors.ChromaVectorStore(str(data_dir / "chromadb"))
    yield store, write
    vectors.reset_vector_store()


def _vector(seed: int) -> list[float]:
    import random

    random.seed(seed)
    return [random.random() for _ in range(768)]


def test_a_second_process_writing_vectors_does_not_break_the_first(chroma_store):
    """The loud shape: a first query on an empty collection, then another process."""
    store, write = chroma_store
    probe = _vector(1)
    assert store.query(probe, n_results=5)["ids"] == [[]]

    write(2, "written-elsewhere")

    result = store.query(probe, n_results=5)
    assert result["ids"][0] == ["written-elsewhere"], (
        "the store did not recover from the stale reader"
    )


def test_the_recovery_cures_a_stale_view_when_it_runs(chroma_store):
    """The quiet shape of B23, and what the recovery does about it once called."""
    store, write = chroma_store
    probe = _vector(1)
    write(9, "already-there")
    assert store.query(probe, n_results=5)["ids"][0] == ["already-there"]
    write(2, "written-elsewhere")

    store._recover_stale_reader()

    assert "written-elsewhere" in set(store.query(probe, n_results=5)["ids"][0])


def test_a_stale_view_raises_nothing_which_is_why_a_script_is_refused(chroma_store):
    """A CHECKED LIMITATION, not an oversight.

    When the collection already held a vector, another process's write produces no
    error — the query simply never returns the new vector, while ``count()`` and
    ``has()`` can both see it. There is nothing for the recovery to trigger on, so
    the refusal (``store_lock``) is what closes this shape, not the retry. If this
    test ever fails, Chroma has started reporting the staleness and the recovery
    may be able to cover it.
    """
    store, write = chroma_store
    probe = _vector(1)
    write(9, "already-there")
    store.query(probe, n_results=5)
    write(2, "written-elsewhere")

    returned = set(store.query(probe, n_results=5)["ids"][0])

    assert returned == {"already-there"}, "Chroma's behaviour has changed"
    assert store.has("written-elsewhere") is True
    assert store.count() == 2, "count sees what query does not"


def test_the_recovery_retries_only_once(isolated_data_dir, monkeypatch, tmp_path):
    """A store that always fails must not loop; the leg degrades instead (B24)."""
    import chromadb.errors

    store = vectors.ChromaVectorStore(str(tmp_path / "chromadb"))
    calls: list[int] = []

    def always_internal(*args, **kwargs):
        calls.append(1)
        raise chromadb.errors.InternalError("Nothing found on disk")

    monkeypatch.setattr(store, "_query", always_internal)
    recoveries: list[int] = []
    monkeypatch.setattr(
        store, "_recover_stale_reader", lambda: recoveries.append(1))

    with pytest.raises(chromadb.errors.InternalError):
        store.query(_vector(1), n_results=5)

    assert len(calls) == 2 and len(recoveries) == 1


def test_the_chroma_stale_reader_recovery_api_exists():
    """The recovery uses an internal Chroma API. If it goes, this says so loudly."""
    from chromadb.api.shared_system_client import SharedSystemClient

    assert callable(getattr(SharedSystemClient, "clear_system_cache", None)), (
        "chromadb no longer has SharedSystemClient.clear_system_cache. The B23 "
        "recovery was measured against the pinned version in requirements.lock; "
        "re-measure both shapes before changing the pin."
    )
    # The test fixture's descriptor release (tests/conftest.py::release_test_stores) relies on
    # two more pieces of the same internal API: the class-level cache of systems, and stop().
    assert isinstance(getattr(SharedSystemClient, "_identifier_to_system", None), dict), (
        "chromadb no longer has SharedSystemClient._identifier_to_system. The test fixture "
        "stops every cached system through it; without it each Chroma test leaks ~10 "
        "descriptors and the suite fails at ulimit -n 256."
    )
    from chromadb.config import System

    assert callable(getattr(System, "stop", None)), "chromadb's System.stop() is gone"
