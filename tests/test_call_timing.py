"""Ollama timing capture per model call in a turn, and the chat model's keep_alive.

Speed investigation, part C. The HTTP layer is faked (``requests.post``) so the real
client code runs: what is captured is what ``ollama.chat`` and ``ollama.embed`` see.
"""

from __future__ import annotations

import json
import logging
import shutil

import pytest

from program import config
from program.engine import call_timing, ollama, turn
from program.memory import db
from program.settings.permissions import Actor, Role

TIMING = {
    "load_duration": 11, "prompt_eval_count": 6565, "prompt_eval_duration": 19_320_000_000,
    "eval_count": 64, "eval_duration": 2_140_000_000, "total_duration": 21_540_000_000,
}


class _Response:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def fake_http(monkeypatch):
    """Fake Ollama over HTTP. Records every payload; answers chat and embed."""
    sent = []

    def post(url, json=None, timeout=None, stream=False):
        sent.append((url, json))
        if url.endswith("/api/embed"):
            width = config.expected_embedding_dimension()
            return _Response({"embeddings": [[0.0] * width], "load_duration": 5,
                              "total_duration": 40_000_000, "prompt_eval_count": 9})
        return _Response({"message": {"role": "assistant", "content": "CONSISTENT"},
                          "done_reason": "stop", **TIMING})

    monkeypatch.setattr(ollama.requests, "post", post)
    return sent


def _timing_lines(caplog):
    prefix = "ollama call timing {"
    return [json.loads(r.getMessage()[len("ollama call timing "):]) for r in caplog.records
            if r.name == "program.engine.call_timing" and r.getMessage().startswith(prefix)]


# --- the capture module -------------------------------------------------------


def test_outside_a_turn_nothing_is_recorded(fake_http, caplog):
    caplog.set_level(logging.INFO, logger="program.engine.call_timing")
    ollama.chat([{"role": "user", "content": "hi"}], model="m")
    assert _timing_lines(caplog) == []


def test_each_call_in_a_turn_is_one_line_with_its_step_and_the_turn_message(
        fake_http, caplog):
    caplog.set_level(logging.INFO, logger="program.engine.call_timing")
    with call_timing.turn("conv-1"):
        call_timing.set_message("conv-1", "msg-1")
        with call_timing.step("query_embedding"):
            ollama.embed("query")
        with call_timing.step("main"):
            ollama.chat([{"role": "user", "content": "hi"}], model="m")
            ollama.chat([{"role": "user", "content": "hi"}], model="m")
        with call_timing.step("gate"):
            ollama.chat_text([{"role": "user", "content": "classify"}], model="m")

    lines = _timing_lines(caplog)
    assert [(x["step"], x["n"], x["seq"], x["endpoint"]) for x in lines] == [
        ("query_embedding", 1, 1, "embed"), ("main", 1, 2, "chat"),
        ("main", 2, 3, "chat"), ("gate", 1, 4, "chat")]
    assert {(x["conversation_id"], x["message_id"]) for x in lines} == {("conv-1", "msg-1")}
    assert {k: lines[1][k] for k in TIMING} == TIMING
    assert lines[0]["prompt_eval_count"] == 9 and lines[0]["total_duration"] == 40_000_000
    assert "eval_count" not in lines[0]
    assert all("missing" not in x for x in lines)


def test_a_missing_field_is_null_and_named_and_the_call_returns_unchanged(
        monkeypatch, caplog):
    body = {"message": {"role": "assistant", "content": "ok"}, "eval_count": 3}
    monkeypatch.setattr(ollama.requests, "post", lambda *a, **k: _Response(body))
    caplog.set_level(logging.INFO, logger="program.engine.call_timing")
    with call_timing.turn("c"):
        result = ollama.chat([{"role": "user", "content": "hi"}], model="m")

    assert result == body
    (line,) = _timing_lines(caplog)
    assert line["eval_count"] == 3 and line["prompt_eval_duration"] is None
    assert line["missing"] == ["load_duration", "prompt_eval_count", "prompt_eval_duration",
                               "eval_duration", "total_duration"]


def test_a_failed_write_is_logged_and_the_call_still_returns(fake_http, monkeypatch, caplog):
    def broken(entry):
        raise OSError("disk full")

    monkeypatch.setattr(call_timing, "_write", broken)
    caplog.set_level(logging.INFO, logger="program.engine.call_timing")
    with call_timing.turn("c"):
        result = ollama.chat([{"role": "user", "content": "hi"}], model="m")
        vector = ollama.embed("q")

    assert result["message"]["content"] == "CONSISTENT"
    assert len(vector) == config.expected_embedding_dimension()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["ollama call timing not recorded (chat call): OSError: disk full",
                        "ollama call timing not recorded (embed call): OSError: disk full"]


def test_a_non_dict_response_records_every_field_missing_without_raising(caplog):
    caplog.set_level(logging.INFO, logger="program.engine.call_timing")
    with call_timing.turn("c"):
        call_timing.record("embed", "m", None)
    (line,) = _timing_lines(caplog)
    assert line["missing"] == list(call_timing.FIELDS["embed"])


def test_the_turn_context_ends_with_its_block_even_when_the_block_raises(fake_http, caplog):
    caplog.set_level(logging.INFO, logger="program.engine.call_timing")
    with pytest.raises(RuntimeError):
        with call_timing.turn("c"):
            raise RuntimeError("turn failed")
    ollama.chat([{"role": "user", "content": "hi"}], model="m")
    assert _timing_lines(caplog) == []


# --- a whole turn ------------------------------------------------------------


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    # The real client embeds the query (faked over HTTP); retrieval itself is tested elsewhere.
    monkeypatch.setattr(turn.retrieval, "search", lambda query: (ollama.embed(query), None)[1])
    return Actor(user_id=lyle, name="Lyle", role=Role.ADMIN)


def test_a_turn_logs_every_model_call_against_its_user_message(store, fake_http, caplog):
    caplog.set_level(logging.INFO, logger="program.engine.call_timing")
    outcome = turn.handle_user_message(store, "hello", situation="")

    lines = _timing_lines(caplog)
    steps = [(x["step"], x["endpoint"]) for x in lines]
    assert steps[:3] == [("query_embedding", "embed"), ("main", "chat"), ("gate", "chat")]
    assert all(step == "corrections" for step, _ in steps[3:])
    assert {x["message_id"] for x in lines} == {outcome.user_message_id}
    assert [x["seq"] for x in lines] == list(range(1, len(lines) + 1))


def test_a_turn_completes_when_every_timing_write_fails(store, fake_http, monkeypatch, caplog):
    monkeypatch.setattr(call_timing, "_write", lambda entry: 1 / 0)
    caplog.set_level(logging.WARNING, logger="program.engine.call_timing")
    outcome = turn.handle_user_message(store, "hello", situation="")

    assert outcome.content == "CONSISTENT"
    assert db.get_conversation_messages(outcome.conversation_id)[-1]["content"] == "CONSISTENT"
    assert len([r for r in caplog.records if "not recorded" in r.getMessage()]) >= 3


# --- keep_alive ----------------------------------------------------------------


@pytest.fixture
def config_with(tmp_path, monkeypatch):
    """The real defaults.toml, plus a local.toml holding ``text`` (or none)."""
    def install(text=None):
        shutil.copy(config.PROJECT_ROOT / "config" / "defaults.toml", tmp_path)
        if text is not None:
            (tmp_path / "local.toml").write_text(text)
        monkeypatch.setenv("ANAM_CONFIG_DIR", str(tmp_path))
        config.reload()
    yield install
    config.reload()


def _chat_payloads(sent):
    return [payload for url, payload in sent if url.endswith("/api/chat")]


def test_keep_alive_absent_sends_nothing(config_with, fake_http):
    config_with()
    assert config.chat_keep_alive() is None
    ollama.chat([{"role": "user", "content": "hi"}])
    ollama.embed("q")
    assert all("keep_alive" not in payload for _, payload in fake_http)


def test_keep_alive_empty_is_unset(config_with, fake_http):
    config_with('[ollama]\nchat_keep_alive = ""\n')
    assert config.chat_keep_alive() is None
    ollama.chat([{"role": "user", "content": "hi"}])
    assert "keep_alive" not in _chat_payloads(fake_http)[0]


def test_keep_alive_set_goes_on_chat_model_calls_only(config_with, fake_http):
    config_with('[ollama]\nchat_keep_alive = "30m"\n')
    ollama.chat([{"role": "user", "content": "hi"}])                      # the chat model
    ollama.chat([{"role": "user", "content": "hi"}], model=config.chat_model())
    ollama.chat([{"role": "user", "content": "hi"}], model="some-other-model")
    ollama.embed("q")

    chats = _chat_payloads(fake_http)
    assert [p.get("keep_alive") for p in chats] == ["30m", "30m", None]
    embeds = [p for url, p in fake_http if url.endswith("/api/embed")]
    assert "keep_alive" not in embeds[0]


def test_keep_alive_goes_on_the_classifier_while_it_runs_on_the_chat_model(
        config_with, fake_http):
    from program.integrity import classifier

    config_with('[ollama]\nchat_keep_alive = -1\n')
    assert config.classifier_model() == config.chat_model()
    classifier.classify("prompt")
    assert _chat_payloads(fake_http)[0]["keep_alive"] == -1


def test_keep_alive_goes_on_a_streamed_chat_too(config_with, monkeypatch):
    sent = []

    class _Stream:
        status_code = 200

        def iter_lines(self):
            yield b'{"done": true}'

        def close(self):
            pass

    monkeypatch.setattr(ollama.requests, "post",
                        lambda url, json=None, timeout=None, stream=False:
                        (sent.append(json), _Stream())[1])
    config_with('[ollama]\nchat_keep_alive = "10m"\n')
    list(ollama.chat_stream([{"role": "user", "content": "hi"}]))
    assert sent[0]["keep_alive"] == "10m"


@pytest.mark.parametrize("value", ["true", "1.5", "[1]"])
def test_keep_alive_of_the_wrong_type_is_refused(config_with, value):
    config_with(f"[ollama]\nchat_keep_alive = {value}\n")
    with pytest.raises(ValueError, match="chat_keep_alive"):
        config.chat_keep_alive()
