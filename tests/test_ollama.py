"""Ollama client tests.

**These run against the real local Ollama instance wherever possible.** The
reference build shipped a search integration whose tests all passed and which
was never confirmed to work against a live service; the tests proved the parser,
not the integration. The same gap is easy to reproduce here, so the chat and
embedding paths are exercised for real.

Live tests skip — rather than fail — when Ollama is not reachable, so the suite
still runs on a machine without it. That skip is the one place this file trades
coverage for portability, and a skipped live test is visible in pytest output
where a mocked one would look like a pass.

The failure paths use **real failure injection, not mocks**: an unreachable port
is a genuinely closed port, and the timeout test is a real socket that accepts a
connection and then says nothing.
"""

from __future__ import annotations

import socket
import threading

import pytest

from anam import config
from anam.engine import ollama

live_only = pytest.mark.skipif(
    not ollama.is_available(),
    reason="Ollama is not reachable; live-call tests skipped",
)


@pytest.fixture
def clean_config():
    """Reset the config cache around a test that changes the environment."""
    config.reload()
    yield
    config.reload()


# ---------------------------------------------------------------------------
# Failure paths — no mocks, real failure injection
# ---------------------------------------------------------------------------


def test_unreachable_host_raises_clearly(monkeypatch, clean_config):
    """Ollama not running must fail fast and say so, not hang or return empty."""
    # Port 1 is reserved and never listening.
    monkeypatch.setenv("ANAM_OLLAMA_HOST", "http://127.0.0.1:1")
    config.reload()

    with pytest.raises(ollama.OllamaUnreachable) as exc:
        ollama.chat([{"role": "user", "content": "hello"}], timeout=5)

    message = str(exc.value)
    assert "127.0.0.1:1" in message
    assert "ollama ps" in message  # tells the operator how to check


def test_unreachable_host_also_surfaces_on_embed(monkeypatch, clean_config):
    monkeypatch.setenv("ANAM_OLLAMA_HOST", "http://127.0.0.1:1")
    config.reload()
    with pytest.raises(ollama.OllamaUnreachable):
        ollama.embed("anything", timeout=5)


def test_is_available_is_false_when_unreachable(monkeypatch, clean_config):
    monkeypatch.setenv("ANAM_OLLAMA_HOST", "http://127.0.0.1:1")
    config.reload()
    assert ollama.is_available() is False


def test_silent_server_times_out_rather_than_hanging(monkeypatch, clean_config):
    """A server that accepts and never answers must not hang the caller.

    This is the failure mode that matters most in a live turn: unreachable is
    obvious and fast, but a socket that accepts and stalls will block forever
    without an explicit timeout.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    accepted: list[socket.socket] = []

    def accept_and_stall():
        try:
            conn, _ = server.accept()
            accepted.append(conn)  # held open, never written to
        except OSError:
            pass

    thread = threading.Thread(target=accept_and_stall, daemon=True)
    thread.start()

    monkeypatch.setenv("ANAM_OLLAMA_HOST", f"http://127.0.0.1:{port}")
    config.reload()
    try:
        with pytest.raises(ollama.OllamaTimeout):
            ollama.chat([{"role": "user", "content": "hello"}], timeout=2)
    finally:
        for conn in accepted:
            conn.close()
        server.close()


@live_only
def test_unknown_model_names_the_model_and_the_fix():
    with pytest.raises(ollama.OllamaModelNotFound) as exc:
        ollama.chat(
            [{"role": "user", "content": "hi"}],
            model="definitely-not-a-real-model:1b",
            timeout=30,
        )
    message = str(exc.value)
    assert "definitely-not-a-real-model:1b" in message
    assert "ollama pull" in message


# ---------------------------------------------------------------------------
# Options handling
# ---------------------------------------------------------------------------


def test_think_is_separated_from_options(clean_config):
    """Ollama takes `think` at the payload top level, not inside `options`.

    Sending it in the wrong place is ignored rather than rejected, so nothing
    would fail — it would just quietly not apply.
    """
    options, think = ollama._split_options(None)
    assert "think" not in options
    assert think is False


def test_num_ctx_is_present_and_pinned(clean_config):
    options, _ = ollama._split_options(None)
    assert options["num_ctx"] == 32768


def test_caller_options_override_config(clean_config):
    options, think = ollama._split_options({"num_ctx": 4096, "think": True})
    assert options["num_ctx"] == 4096
    assert think is True


# ---------------------------------------------------------------------------
# Live calls
# ---------------------------------------------------------------------------


@live_only
def test_live_chat_returns_text():
    data = ollama.chat(
        [{"role": "user", "content": "Reply with exactly the word: ok"}],
        options={"temperature": 0},
    )
    assert "ok" in data["message"]["content"].lower()
    # Ollama's own counters — task 1.10 calibrates its token estimate from these.
    assert data["prompt_eval_count"] > 0


@live_only
def test_live_chat_text_helper():
    assert "ok" in ollama.chat_text(
        [{"role": "user", "content": "Reply with exactly the word: ok"}],
        options={"temperature": 0},
    ).lower()


@live_only
def test_live_num_ctx_actually_reaches_the_server():
    """The pin has to take effect, not merely be present in a dict.

    After a chat call, Ollama reports the context size it loaded the model with.
    This is the difference between configuring num_ctx and it working.
    """
    ollama.chat(
        [{"role": "user", "content": "Reply with exactly the word: ok"}],
        options={"temperature": 0},
    )
    loaded = {m["name"]: m for m in ollama.loaded_models()}
    chat_model = config.chat_model()
    matching = [m for name, m in loaded.items() if name.startswith(chat_model)]
    assert matching, f"{chat_model} not loaded after a chat call; loaded: {sorted(loaded)}"
    assert matching[0]["context_length"] == 32768


@live_only
def test_live_embed_returns_expected_dimension():
    vector = ollama.embed("the harbour was full of small boats")
    assert len(vector) == config.expected_embedding_dimension() == 768
    assert all(isinstance(x, float) for x in vector[:10])


@live_only
def test_live_embed_is_deterministic_for_the_same_input():
    a = ollama.embed("identical text")
    b = ollama.embed("identical text")
    assert a == b


@live_only
def test_live_embed_differs_for_different_input():
    a = ollama.embed("the harbour was full of small boats")
    b = ollama.embed("quarterly depreciation schedules for fixed assets")
    assert a != b


@live_only
def test_live_embedding_dimension_guard_fires(monkeypatch, clean_config):
    """A model returning the wrong width must raise, not write a bad vector.

    Exercised against a real embedding call with the expectation deliberately
    wrong, so the guard is proven to fire rather than assumed to.
    """
    monkeypatch.setenv("ANAM_CONFIG_DIR", str(config.config_dir()))
    config.reload()
    monkeypatch.setattr(config, "expected_embedding_dimension", lambda: 999)
    with pytest.raises(ollama.EmbeddingDimensionError) as exc:
        ollama.embed("harbour")
    assert "768" in str(exc.value)
    assert "999" in str(exc.value)


@live_only
def test_live_stream_yields_chunks_and_terminates():
    chunks = list(
        ollama.chat_stream(
            [{"role": "user", "content": "Count: 1 2 3"}],
            options={"temperature": 0},
        )
    )
    assert len(chunks) > 1
    assert chunks[-1].get("done") is True
    text = "".join(c.get("message", {}).get("content", "") for c in chunks)
    assert text.strip()


@live_only
def test_live_is_available_is_true():
    assert ollama.is_available() is True
