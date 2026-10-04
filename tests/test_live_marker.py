"""Piece 4a: the live-test marker, and proof that collecting the suite touches no network.

Before this, seven test modules evaluated ``skipif(not ollama.is_available())`` at import, so
``pytest --collect-only`` sent seven ``GET /api/version`` requests to Ollama, and two more modules
probed SearXNG and example.com the same way. The proof here is a watcher on every socket connect
and DNS lookup (``tests/netwatch.py``), run against the real suite in a subprocess, plus a decoy
module that does what the old tests did, which must be seen: a watcher that cannot fire proves
nothing.
"""

from __future__ import annotations

import http.server
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import requests

from tests import conftest

ROOT = Path(__file__).resolve().parents[1]


def _clean_env() -> dict[str, str]:
    """The environment without the isolation fixture's ``ANAM_*`` directories: in the child they
    would make the temporary directory look like a real runtime store to its own guard."""
    return {k: v for k, v in os.environ.items() if not k.startswith("ANAM_")}


def _run_collection(cwd: Path, log: Path, *args: str) -> subprocess.CompletedProcess:
    env = _clean_env()
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT),
        "ANAM_NETWATCH_LOG": str(log),
    })
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-p", "tests.netwatch", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )


class _Decoy(http.server.BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self):  # noqa: N802 - http.server's name
        _Decoy.seen.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # silence
        pass


def test_the_watcher_fires_on_a_module_that_probes_at_import(tmp_path):
    """The control: a module written the way the old tests were is seen, by the watcher and by
    the decoy server it called."""
    _Decoy.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Decoy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        (tmp_path / "test_old_style.py").write_text(
            "import pytest, requests\n"
            f"UP = requests.get('http://127.0.0.1:{port}/api/version', timeout=3).ok\n"
            "@pytest.mark.skipif(not UP, reason='down')\n"
            "def test_x():\n    pass\n"
        )
        log = tmp_path / "net.log"
        done = _run_collection(tmp_path, log, "--rootdir", str(tmp_path), "--confcutdir",
                               str(tmp_path), str(tmp_path))
        assert done.returncode == 0, done.stdout + done.stderr
        assert f"connect ('127.0.0.1', {port})" in log.read_text()
        assert _Decoy.seen == ["/api/version"]
    finally:
        server.shutdown()


def test_collecting_the_whole_suite_makes_no_network_connection(tmp_path):
    log = tmp_path / "net.log"
    done = _run_collection(ROOT, log, "tests")
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    assert "tests collected" in done.stdout
    touched = log.read_text() if log.exists() else ""
    assert touched == "", f"collection touched the network:\n{touched}"


def test_an_unmarked_test_is_refused_the_real_ollama():
    before = len(conftest._OLLAMA_VIOLATIONS)
    try:
        with pytest.raises(conftest.OllamaGuardViolation):
            requests.get("http://localhost:11434/api/version", timeout=1)
        assert len(conftest._OLLAMA_VIOLATIONS) == before + 1
    finally:
        del conftest._OLLAMA_VIOLATIONS[before:]


@pytest.mark.parametrize("url", [
    "http://localhost:11434/api/chat", "http://127.0.0.1:11434/api/embed",
    "http://[::1]:11434/api/tags",
])
def test_the_real_ollama_is_recognised_under_every_loopback_name(url):
    assert conftest._is_real_ollama(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1/api/chat",          # the dead port the failure tests use
    "http://127.0.0.1:8080/search",         # SearXNG
    "http://example.com:11434/api/chat",    # not loopback
    "https://example.com/",
])
def test_other_addresses_are_not_refused(url):
    assert not conftest._is_real_ollama(url)


def test_the_guard_does_not_break_the_failure_tests_dead_port(monkeypatch):
    """A test of an unreachable Ollama points the host at port 1: that must still raise the
    client's own error, not the guard's."""
    from program import config
    from program.engine import ollama

    monkeypatch.setenv("ANAM_OLLAMA_HOST", "http://127.0.0.1:1")
    config.reload()
    with pytest.raises(ollama.OllamaUnreachable):
        ollama.chat([{"role": "user", "content": "hi"}], timeout=2)


def test_a_live_marked_test_is_skipped_without_the_flag(tmp_path):
    (tmp_path / "test_marked.py").write_text(
        "import pytest\n"
        "@pytest.mark.live('ollama')\n"
        "def test_would_call_ollama():\n    raise AssertionError('ran')\n"
    )
    (tmp_path / "conftest.py").write_text(
        "from tests.conftest import pytest_addoption, pytest_runtest_setup  # noqa: F401\n"
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    live(service): a live test\n"
    )
    env = dict(_clean_env(), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert "1 skipped" in done.stdout, done.stdout + done.stderr
    assert "ran" not in done.stdout.replace("ran in", "")
