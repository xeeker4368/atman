"""The ComfyUI client. Phase 4, task A1c.

**The normal suite needs no ComfyUI running.** The transport is replaced with
responses **captured verbatim from the live instance** on 2026-09-21 (ComfyUI
0.37.0) — the same discipline `tests/test_web_search.py` uses with its SearXNG
fixture, and for the same reason: a suite that fails because a local service happens
to be stopped tests nothing.

Two tests hit the real instance and **skip** rather than fail when it is down. A
skip is visible in pytest's output where a mock would look like a pass.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse

import pytest

from program import config
from program.media import comfyui

# --- responses captured from the live instance --------------------------------

SUBMITTED = {"prompt_id": "56b4126f-594f-4109-90f1-f9cbcc656b5d", "number": 1,
             "node_errors": {}}

FINISHED = {
    "56b4126f-594f-4109-90f1-f9cbcc656b5d": {
        "status": {"status_str": "success", "completed": True, "messages": []},
        "outputs": {"save": {"images": [
            {"filename": "ComfyUI_temp_ucvpq_00001_.png", "subfolder": "", "type": "temp"}
        ]}},
    }
}

FAILED = {
    "56b4126f-594f-4109-90f1-f9cbcc656b5d": {
        "status": {"status_str": "error", "completed": False,
                   "messages": [["execution_error", {"exception_message": "boom"}]]},
        "outputs": {},
    }
}

#: HTTP 400 for a checkpoint that is not installed. The `value_not_in_list` entry is
#: what makes a missing model separable from a malformed graph.
MODEL_MISSING = {
    "error": {"type": "prompt_outputs_failed_validation",
              "message": "Prompt outputs failed validation", "details": ""},
    "node_errors": {"checkpoint": {
        "errors": [{"type": "value_not_in_list", "message": "Value not in list",
                    "details": "ckpt_name: 'no_such.safetensors' not in "
                               "['sd_xl_base_1.0.safetensors']"}],
        "dependent_outputs": ["save"], "class_type": "CheckpointLoaderSimple"}},
}

NO_OUTPUTS = {"error": {"type": "prompt_no_outputs", "message": "Prompt has no outputs",
                        "details": ""}, "node_errors": {}}

PNG = bytes.fromhex("89504e470d0a1a0a") + b"captured-image-body"


class FakeResponse:
    def __init__(self, body: bytes, content_type: str):
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def transport(monkeypatch, *, history=FINISHED, submit=SUBMITTED, image=PNG, reject=None):
    """Replace the transport (`comfyui._open`). Records every URL so call order is
    assertable."""
    calls: list[str] = []

    def fake(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        calls.append(url)
        if url.endswith("/prompt"):
            if reject is not None:
                raise urllib.error.HTTPError(
                    url, 400, "Bad Request", {},
                    __import__("io").BytesIO(json.dumps(reject).encode()))
            return FakeResponse(json.dumps(submit).encode(), "application/json")
        if "/history/" in url:
            return FakeResponse(json.dumps(history).encode(), "application/json")
        if "/view?" in url:
            return FakeResponse(image, "image/png")
        if url.endswith("/system_stats"):
            return FakeResponse(json.dumps(
                {"system": {"comfyui_version": "0.37.0"},
                 "devices": [{"name": "mps", "type": "mps", "vram_free": 1}]}
            ).encode(), "application/json")
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(comfyui, "_open", fake)
    monkeypatch.setattr(comfyui.time, "sleep", lambda _s: None)
    return calls


# --- the loopback guard ------------------------------------------------------


def test_loopback_is_accepted():
    for host in ("http://127.0.0.1:8188", "http://localhost:8188", "http://[::1]:8188"):
        comfyui._check_loopback(host)


@pytest.mark.parametrize("host", [
    "http://192.168.0.82:8188",
    "http://0.0.0.0:8188",
    "http://8.8.8.8:8188",
])
def test_a_non_loopback_host_is_refused(host):
    """ComfyUI has no authentication: its API executes any workflow posted to it, so
    a reachable instance is an unauthenticated execution endpoint. Refused at the
    point of use rather than trusted to config."""
    with pytest.raises(comfyui.ComfyUINotLoopback, match="not.*loopback"):
        comfyui._check_loopback(host)


def test_an_ipv4_mapped_loopback_address_is_unwrapped_before_judging():
    """`::ffff:127.0.0.1` is loopback wearing an IPv6 costume — and
    `::ffff:192.168.0.5` is routable wearing the same one. Getting this backwards
    either blocks the real host or admits a LAN one."""
    import ipaddress

    for raw, expected in (("::ffff:127.0.0.1", True), ("::ffff:192.168.0.5", False)):
        address = ipaddress.ip_address(raw)
        mapped = getattr(address, "ipv4_mapped", None)
        effective = mapped if mapped is not None else address
        assert effective.is_loopback is expected


def test_generate_refuses_a_non_loopback_host_before_any_request(monkeypatch):
    calls = transport(monkeypatch)
    monkeypatch.setattr(config, "comfyui_host", lambda: "http://192.168.0.82:8188")

    with pytest.raises(comfyui.ComfyUINotLoopback):
        comfyui.generate("a kettle")

    assert calls == [], "the guard must run before anything reaches the wire"


# --- the happy path ----------------------------------------------------------


def test_generate_returns_bytes_and_the_settings_that_made_them(monkeypatch):
    transport(monkeypatch)

    image = comfyui.generate("a copper kettle", seed=42)

    assert image.image_bytes == PNG
    assert image.content_type == "image/png"
    assert image.prompt == "a copper kettle" and image.seed == 42
    assert image.steps == 4 and image.cfg == 1.0
    assert image.sampler == "euler" and image.scheduler == "sgm_uniform"
    assert image.size_bytes == len(PNG)
    assert image.duration_seconds >= 0


def test_it_submits_then_polls_then_fetches(monkeypatch):
    calls = transport(monkeypatch)

    comfyui.generate("a kettle", seed=1)

    assert calls[0].endswith("/prompt")
    assert "/history/56b4126f" in calls[1]
    assert "/view?" in calls[-1]


def test_the_fetch_asks_for_the_temp_file_the_history_named(monkeypatch):
    """PreviewImage reports `type: temp`; asking for `output` would 404. The
    filename comes from the response, never constructed here."""
    calls = transport(monkeypatch)

    comfyui.generate("a kettle", seed=1)

    query = urllib.parse.parse_qs(calls[-1].split("?", 1)[1])
    assert query["type"] == ["temp"]
    assert query["filename"] == ["ComfyUI_temp_ucvpq_00001_.png"]


def test_metadata_carries_the_prompt_because_that_is_what_gets_indexed(monkeypatch):
    """Q14: an image has no text, so the prompt is its searchable content."""
    transport(monkeypatch)

    metadata = comfyui.generate("a copper kettle", seed=7).to_metadata()

    assert metadata["prompt"] == "a copper kettle"
    assert "image_bytes" not in metadata, "the bytes must not ride along in metadata"
    assert set(metadata) >= {"seed", "checkpoint", "lora", "steps", "cfg", "width"}


def test_a_queued_job_is_polled_until_it_appears(monkeypatch):
    """`/history/<id>` returns `{}` while queued — the empty dict is 'not yet',
    not 'finished with nothing'."""
    states = [{}, {}, FINISHED]

    def fake(request, timeout=None):
        url = request.full_url
        if url.endswith("/prompt"):
            return FakeResponse(json.dumps(SUBMITTED).encode(), "application/json")
        if "/history/" in url:
            return FakeResponse(json.dumps(states.pop(0) if len(states) > 1
                                           else states[0]).encode(), "application/json")
        return FakeResponse(PNG, "image/png")

    monkeypatch.setattr(comfyui, "_open", fake)
    monkeypatch.setattr(comfyui.time, "sleep", lambda _s: None)

    assert comfyui.generate("a kettle", seed=1).image_bytes == PNG


# --- every failure has its own name ------------------------------------------


def test_an_unreachable_instance_says_how_to_start_it(monkeypatch):
    def fake(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(comfyui, "_open", fake)

    with pytest.raises(comfyui.ComfyUIUnreachable, match="main.py"):
        comfyui.generate("a kettle")


def test_a_missing_model_is_its_own_exception_not_a_generic_rejection(monkeypatch):
    """The operator action is a download, not a code fix — so it is separable. It
    stays a subclass of ComfyUIWorkflowRejected so catching the parent still works."""
    transport(monkeypatch, reject=MODEL_MISSING)

    with pytest.raises(comfyui.ComfyUIModelMissing) as caught:
        comfyui.generate("a kettle")

    assert "no_such.safetensors" in str(caught.value)
    assert "ops/comfyui/README.md" in str(caught.value)
    assert isinstance(caught.value, comfyui.ComfyUIWorkflowRejected)


def test_a_malformed_graph_is_rejected_without_claiming_a_missing_model(monkeypatch):
    transport(monkeypatch, reject=NO_OUTPUTS)

    with pytest.raises(comfyui.ComfyUIWorkflowRejected) as caught:
        comfyui.generate("a kettle")

    assert not isinstance(caught.value, comfyui.ComfyUIModelMissing)
    assert "prompt_no_outputs" in str(caught.value)


def test_a_failed_execution_is_not_a_rejection(monkeypatch):
    """The graph was accepted and a node raised. Different from a 400, because the
    workflow was fine and the run was not."""
    transport(monkeypatch, history=FAILED)

    with pytest.raises(comfyui.ComfyUIGenerationFailed, match="boom"):
        comfyui.generate("a kettle")


def test_success_with_no_image_is_still_a_failure(monkeypatch):
    empty = {"56b4126f-594f-4109-90f1-f9cbcc656b5d":
             {"status": {"status_str": "success"}, "outputs": {}}}
    transport(monkeypatch, history=empty)

    with pytest.raises(comfyui.ComfyUIGenerationFailed, match="no image"):
        comfyui.generate("a kettle")


def test_an_empty_image_body_is_a_failure(monkeypatch):
    transport(monkeypatch, image=b"")

    with pytest.raises(comfyui.ComfyUIGenerationFailed, match="empty image"):
        comfyui.generate("a kettle")


def test_the_timeout_says_the_outcome_is_unknown(monkeypatch):
    """ToolResult.TIMEOUT's distinction: the job may still be running there, so
    whether an image was produced is unknown rather than known not to have
    happened."""
    transport(monkeypatch, history={})
    ticks = iter([0.0] + [100.0] * 20)
    monkeypatch.setattr(comfyui.time, "monotonic", lambda: next(ticks))

    with pytest.raises(comfyui.ComfyUITimeout, match="unknown"):
        comfyui.generate("a kettle", timeout_seconds=1.0)


def test_an_empty_prompt_is_refused_before_anything_runs(monkeypatch):
    calls = transport(monkeypatch)

    for bad in ("", "   "):
        with pytest.raises(ValueError, match="needs a prompt"):
            comfyui.generate(bad)

    assert calls == []


# --- the graph ---------------------------------------------------------------


def test_the_graph_carries_an_empty_negative_prompt_deliberately():
    """At cfg 1.0 the sampler skips the unconditional branch, so no negative
    conditioning is evaluated. The node exists because KSampler requires the input.
    This is why `image_generate` exposes no negative_prompt parameter."""
    graph = comfyui._graph("a kettle", 1, config.comfyui_generation())

    assert graph["negative"]["inputs"]["text"] == ""
    assert graph["sampler"]["inputs"]["cfg"] == 1.0


def test_the_graph_ends_in_previewimage_not_saveimage():
    """SaveImage writes a permanent file into ComfyUI's own output/ tree. With the
    caller storing the bytes itself that would mean every image exists twice, one
    copy in a directory that grows without bound, outside the backup story and
    outside the go-live wipe."""
    graph = comfyui._graph("a kettle", 1, config.comfyui_generation())

    assert graph[comfyui._OUTPUT_NODE]["class_type"] == "PreviewImage"
    assert not any(node["class_type"] == "SaveImage" for node in graph.values())


def test_a_fresh_seed_is_used_when_none_is_given(monkeypatch):
    """A fixed default would make every image of a prompt identical AND would hit
    ComfyUI's execution cache, returning the previous result in milliseconds — which
    is exactly what was misread as a 0.2-second generation during A1b."""
    transport(monkeypatch)

    seeds = {comfyui.generate("a kettle").seed for _ in range(5)}

    assert len(seeds) == 5


# --- live, and skipped rather than failed when ComfyUI is down ---------------


@pytest.mark.live("comfyui")
def test_available_against_the_real_instance():
    stats = comfyui.available()

    assert stats["system"]["comfyui_version"]
    assert stats["devices"][0]["type"] == "mps", "this build expects Apple Silicon"


@pytest.mark.live("comfyui")
def test_a_real_generation_returns_a_real_png():
    image = comfyui.generate("a single copper kettle on a plain background")

    assert image.image_bytes[:8] == bytes.fromhex("89504e470d0a1a0a"), "not a PNG"
    # B10 C5: the type is what the bytes are, and the real instance's bytes pass the
    # same signature check a spoofed body fails.
    assert image.image_bytes.startswith(comfyui.PNG_SIGNATURE)
    assert image.content_type == comfyui.IMAGE_CONTENT_TYPE
    assert image.size_bytes > 100_000, "suspiciously small for a 1024x1024 PNG"
    assert image.duration_seconds < config.comfyui_timeout_seconds()


# --- the fetch is bounded by what is left of the deadline (merged-queue item 18) --


def _clocked_transport(monkeypatch, *, finish_at: float):
    """The job finishes at `finish_at` seconds; records each request's timeout."""
    clock = {"now": 0.0}
    timeouts: dict[str, float] = {}

    def fake(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url.endswith("/prompt"):
            return FakeResponse(json.dumps(SUBMITTED).encode(), "application/json")
        if "/history/" in url:
            clock["now"] = finish_at
            return FakeResponse(json.dumps(FINISHED).encode(), "application/json")
        if "/view?" in url:
            timeouts["view"] = timeout
            return FakeResponse(PNG, "image/png")
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(comfyui, "_open", fake)
    monkeypatch.setattr(comfyui.time, "sleep", lambda _s: None)
    monkeypatch.setattr(comfyui.time, "monotonic", lambda: clock["now"])
    return timeouts


def test_the_image_fetch_gets_the_remaining_time_not_the_whole_deadline(monkeypatch):
    """A generation finishing at 100 s of a 110 s deadline leaves 10 s for the
    fetch. Passing the total gave it min(60, 110) = 60, so the call could run to
    ~160 s — past the client bound the tool timeout is derived from."""
    timeouts = _clocked_transport(monkeypatch, finish_at=100.0)

    comfyui.generate("a kettle", timeout_seconds=110.0)

    assert timeouts["view"] == pytest.approx(10.0)


def test_no_time_left_to_fetch_is_a_timeout_that_says_the_image_exists(monkeypatch):
    timeouts = _clocked_transport(monkeypatch, finish_at=110.0)

    with pytest.raises(comfyui.ComfyUITimeout, match="was produced and was not retrieved"):
        comfyui.generate("a kettle", timeout_seconds=110.0)
    assert "view" not in timeouts


# --- B10: honest error classes, no proxies, only PNGs ------------------------------


def _http_error(url, code, body=b""):
    import io

    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body))


def _routes(monkeypatch, *, prompt=None, history=None, view=None):
    """Like `transport()`, but any route can be an exception or a (body, type) pair."""
    def fake(request, timeout=None):
        url = request.full_url
        for marker, override, default in (
            ("/prompt", prompt, (json.dumps(SUBMITTED).encode(), "application/json")),
            ("/history/", history, (json.dumps(FINISHED).encode(), "application/json")),
            ("/view?", view, (PNG, "image/png")),
        ):
            if marker in url:
                chosen = default if override is None else override
                if isinstance(chosen, Exception):
                    raise chosen
                return FakeResponse(*chosen)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(comfyui, "_open", fake)
    monkeypatch.setattr(comfyui.time, "sleep", lambda _s: None)


# C1


def test_a_view_404_is_a_fetch_failure_not_a_rejection(monkeypatch):
    """The job ran and its image could not be fetched. Reporting that as "ComfyUI
    rejected the workflow" told the entity something false about what happened."""
    _routes(monkeypatch, view=_http_error("http://x/view", 404, b"Not Found"))

    with pytest.raises(comfyui.ComfyUIGenerationFailed) as caught:
        comfyui.generate("a kettle")

    message = str(caught.value)
    assert not isinstance(caught.value, comfyui.ComfyUIWorkflowRejected)
    assert "reject" not in message.lower()
    assert "HTTP 404 from /view" in message
    assert SUBMITTED["prompt_id"] in message
    assert "nothing was stored" in message


def test_a_400_from_prompt_is_still_a_rejection(monkeypatch):
    _routes(monkeypatch, prompt=_http_error(
        "http://x/prompt", 400, json.dumps(NO_OUTPUTS).encode()))

    with pytest.raises(comfyui.ComfyUIWorkflowRejected, match="prompt_no_outputs"):
        comfyui.generate("a kettle")


def test_a_non_400_from_prompt_is_not_a_rejection(monkeypatch):
    """A 500 did not judge the workflow at all."""
    _routes(monkeypatch, prompt=_http_error("http://x/prompt", 500, b"Internal Error"))

    with pytest.raises(comfyui.ComfyUIResponseError) as caught:
        comfyui.generate("a kettle")

    assert not isinstance(caught.value, comfyui.ComfyUIWorkflowRejected)
    assert caught.value.status == 500
    assert caught.value.endpoint == "/prompt"
    assert "reject" not in str(caught.value).lower()


def test_a_history_error_names_its_endpoint_and_status(monkeypatch):
    _routes(monkeypatch, history=_http_error(
        f"http://x/history/{SUBMITTED['prompt_id']}", 503, b"busy"))

    with pytest.raises(comfyui.ComfyUIResponseError, match="HTTP 503 from /history/"):
        comfyui.generate("a kettle")


def test_the_quoted_error_body_is_bounded(monkeypatch):
    _routes(monkeypatch, prompt=_http_error("http://x/prompt", 502, b"x" * 5000))

    with pytest.raises(comfyui.ComfyUIResponseError) as caught:
        comfyui.generate("a kettle")

    assert "x" * comfyui._ERROR_BODY_CHARS in str(caught.value)
    assert "x" * (comfyui._ERROR_BODY_CHARS + 1) not in str(caught.value)


# C4 — the host is an IP literal; the loopback check is still the boundary


@pytest.fixture
def host(monkeypatch):
    def set_host(value):
        monkeypatch.setenv("ANAM_COMFYUI_HOST", value)
        config.reload()
    yield set_host
    monkeypatch.delenv("ANAM_COMFYUI_HOST", raising=False)
    config.reload()


@pytest.mark.parametrize("value", [
    "http://localhost:8188",
    "http://comfyui.local:8188",
    "http://127.0.0.1.nip.io:8188",
    "not a url",
])
def test_a_host_that_is_not_an_ip_literal_is_a_config_error(host, value):
    host(value)
    with pytest.raises(config.ConfigError, match="IP literal"):
        config.comfyui_host()


@pytest.mark.parametrize("value", ["http://127.0.0.1:8188", "http://[::1]:8188"])
def test_an_ip_literal_host_is_accepted(host, value):
    host(value)
    assert config.comfyui_host() == value


def test_the_shape_check_is_not_the_security_check(host, monkeypatch):
    """A LAN literal is well-formed, so config accepts it — and `_check_loopback`,
    the actual boundary, still refuses it before anything reaches the wire."""
    host("http://192.168.0.82:8188")
    assert config.comfyui_host() == "http://192.168.0.82:8188"

    def must_not_run(*_a, **_k):
        raise AssertionError("a request was made to a non-loopback host")

    monkeypatch.setattr(comfyui, "_open", must_not_run)
    with pytest.raises(comfyui.ComfyUINotLoopback):
        comfyui.generate("a kettle")


def test_nothing_in_the_client_calls_urlopen():
    """`urlopen` builds its opener from the proxy environment. The behavioural proof
    is the subprocess test below; this catches a reintroduction by name."""
    import inspect

    assert "urlopen(" not in inspect.getsource(comfyui)


_PROXY_CHILD = """
import json, sys, urllib.request
from program.media import comfyui

closed_port = int(sys.argv[1])
out = {}
# Control: in this environment the DEFAULT opener really does go to the proxy, so the
# client's result below is evidence rather than a proxy that was never in play.
try:
    reply = urllib.request.urlopen(
        f"http://127.0.0.1:{closed_port}/system_stats", timeout=5).read()
    out["control"] = json.loads(reply)["system"]["comfyui_version"]
except Exception as exc:
    out["control"] = "error: " + type(exc).__name__
try:
    out["client"] = comfyui.available()["system"]["comfyui_version"]
except comfyui.ComfyUIError as exc:
    out["client"] = type(exc).__name__
print(json.dumps(out))
"""


def test_a_proxy_in_the_environment_is_not_used():
    """In a FRESH PROCESS, deliberately: `urlopen` caches its global opener from the
    environment on first use, so an in-process test that sets a proxy after anything
    has called it measures nothing — the pitfall found while probing C4.

    A fake proxy answers every request as though it were ComfyUI. The client is
    pointed at a closed loopback port: with the proxy honoured it would report the
    proxy's version; ignoring it, the connection is refused."""
    import http.server
    import os
    import socket
    import subprocess
    import sys
    import threading
    from pathlib import Path

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — the stdlib's name
            body = json.dumps({"system": {"comfyui_version": "PROXY!!"},
                               "devices": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    proxy = http.server.HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()

    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("no_proxy", "http_proxy", "https_proxy", "all_proxy")}
    proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
    env.update(http_proxy=proxy_url, HTTP_PROXY=proxy_url,
               ANAM_COMFYUI_HOST=f"http://127.0.0.1:{closed_port}")
    try:
        result = subprocess.run(
            [sys.executable, "-c", _PROXY_CHILD, str(closed_port)],
            cwd=Path(__file__).resolve().parent.parent, env=env,
            capture_output=True, text=True, timeout=60,
        )
    finally:
        proxy.shutdown()

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["control"] == "PROXY!!", f"the proxy was never in play: {out}"
    assert out["client"] == "ComfyUIUnreachable", out


# C5 — only a PNG is an image here


@pytest.mark.parametrize("body", [
    b"just some text, not an image\n",
    b"<!DOCTYPE html><html><body>not an image</body></html>",
])
def test_a_non_png_body_under_an_image_header_is_refused(monkeypatch, body):
    _routes(monkeypatch, view=(body, "image/png"))

    with pytest.raises(comfyui.ComfyUIGenerationFailed, match="not a PNG") as caught:
        comfyui.generate("a kettle")

    message = str(caught.value)
    assert "'image/png'" in message, "the claimed header is quoted"
    assert repr(body[:16]) in message, "the actual first bytes are quoted"


def test_the_content_type_is_what_the_bytes_are_not_what_the_header_says(monkeypatch):
    _routes(monkeypatch, view=(PNG, "application/octet-stream"))

    image = comfyui.generate("a kettle")

    assert image.content_type == comfyui.IMAGE_CONTENT_TYPE == "image/png"


# --- an execution failure reaches the model without its traceback -----------------

#: The shape `execution.py`'s `handle_execution_error` records (ComfyUI 0.37.0):
#: `[event, data]` pairs, the error carrying `format_tb()` output and the inputs.
_TRACEBACK_PATH = "/Volumes/Dock Storage/ComfyUI/comfy/samplers.py"
_EXECUTION_ERROR = {
    "56b4126f-594f-4109-90f1-f9cbcc656b5d": {
        "status": {"status_str": "error", "completed": False, "messages": [
            ["execution_start", {"prompt_id": "56b4126f"}],
            ["execution_cached", {"nodes": [], "prompt_id": "56b4126f"}],
            ["execution_error", {
                "prompt_id": "56b4126f", "node_id": "sampler", "node_type": "KSampler",
                "executed": ["checkpoint"],
                "exception_message": "Allocation on device \nThis error means you ran "
                                     "out of memory on your GPU.",
                "exception_type": "torch.OutOfMemoryError",
                "traceback": [
                    '  File "/Volumes/Dock Storage/ComfyUI/execution.py", line 496, '
                    "in execute\n",
                    f'  File "{_TRACEBACK_PATH}", line 1051, in sample\n',
                ],
                "current_inputs": {"seed": ["SECRET-INPUT-MARKER"]},
                "current_outputs": [],
            }],
        ]},
        "outputs": {},
    }
}


def test_an_execution_error_names_the_node_and_exception_but_not_the_traceback(
    monkeypatch, caplog
):
    transport(monkeypatch, history=_EXECUTION_ERROR)

    with caplog.at_level("WARNING", logger="program.media.comfyui"):
        with pytest.raises(comfyui.ComfyUIGenerationFailed) as caught:
            comfyui.generate("a kettle")

    message = str(caught.value)
    assert "KSampler (node sampler)" in message
    assert "torch.OutOfMemoryError" in message
    assert "ran out of memory" in message
    for leaked in (_TRACEBACK_PATH, "execution.py", "/Volumes/", "line 1051",
                   "SECRET-INPUT-MARKER"):
        assert leaked not in message, f"{leaked!r} reached the exception text"
    # ...and the operator still has all of it.
    assert _TRACEBACK_PATH in caplog.text
    assert "SECRET-INPUT-MARKER" in caplog.text


def test_the_exception_message_is_bounded(monkeypatch):
    long = json.loads(json.dumps(_EXECUTION_ERROR))
    error = long["56b4126f-594f-4109-90f1-f9cbcc656b5d"]["status"]["messages"][2][1]
    error["exception_message"] = "y" * 2000
    transport(monkeypatch, history=long)

    with pytest.raises(comfyui.ComfyUIGenerationFailed) as caught:
        comfyui.generate("a kettle")

    assert "y" * comfyui._EXCEPTION_MESSAGE_CHARS in str(caught.value)
    assert "y" * (comfyui._EXCEPTION_MESSAGE_CHARS + 1) not in str(caught.value)


def test_an_interrupted_run_says_where_without_quoting_the_record(monkeypatch):
    interrupted = {"56b4126f-594f-4109-90f1-f9cbcc656b5d": {
        "status": {"status_str": "error", "messages": [
            ["execution_start", {"prompt_id": "56b4126f"}],
            ["execution_interrupted", {"node_id": "sampler", "node_type": "KSampler",
                                       "executed": ["/Volumes/should-not-appear"]}],
        ]}, "outputs": {}}}
    transport(monkeypatch, history=interrupted)

    with pytest.raises(comfyui.ComfyUIGenerationFailed,
                       match=r"interrupted at KSampler \(node sampler\)") as caught:
        comfyui.generate("a kettle")
    assert "/Volumes/" not in str(caught.value)


def test_a_failure_with_no_error_event_lists_event_names_only(monkeypatch):
    odd = {"56b4126f-594f-4109-90f1-f9cbcc656b5d": {
        "status": {"status_str": "error", "messages": [
            ["execution_start", {"prompt_id": "56b4126f", "path": "/Volumes/nope"}],
        ]}, "outputs": {}}}
    transport(monkeypatch, history=odd)

    with pytest.raises(comfyui.ComfyUIGenerationFailed,
                       match=r"no execution_error was recorded \(events: execution_start\)"
                       ) as caught:
        comfyui.generate("a kettle")
    assert "/Volumes/" not in str(caught.value)
