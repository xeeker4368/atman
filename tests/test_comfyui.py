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
    """Replace urlopen. Records every URL so call order is assertable."""
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

    monkeypatch.setattr(comfyui.urllib.request, "urlopen", fake)
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

    monkeypatch.setattr(comfyui.urllib.request, "urlopen", fake)
    monkeypatch.setattr(comfyui.time, "sleep", lambda _s: None)

    assert comfyui.generate("a kettle", seed=1).image_bytes == PNG


# --- every failure has its own name ------------------------------------------


def test_an_unreachable_instance_says_how_to_start_it(monkeypatch):
    def fake(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(comfyui.urllib.request, "urlopen", fake)

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


def comfyui_running() -> bool:
    try:
        comfyui.available()
        return True
    except comfyui.ComfyUIError:
        return False


@pytest.mark.skipif(not comfyui_running(), reason="ComfyUI is not running on 8188")
def test_available_against_the_real_instance():
    stats = comfyui.available()

    assert stats["system"]["comfyui_version"]
    assert stats["devices"][0]["type"] == "mps", "this build expects Apple Silicon"


@pytest.mark.skipif(not comfyui_running(), reason="ComfyUI is not running on 8188")
def test_a_real_generation_returns_a_real_png():
    image = comfyui.generate("a single copper kettle on a plain background")

    assert image.image_bytes[:8] == bytes.fromhex("89504e470d0a1a0a"), "not a PNG"
    assert image.size_bytes > 100_000, "suspiciously small for a 1024x1024 PNG"
    assert image.duration_seconds < config.comfyui_timeout_seconds()
