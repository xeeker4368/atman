"""Client for the local ComfyUI instance. Phase 4, task A1c.

Design of record: ``docs/MEDIA_AND_CREATIVE_DESIGN.md``. This is the transport
layer only — it submits a workflow, waits for the result and returns the image
**bytes**. It writes nothing to the store and decides nothing about where an image
belongs; that is task A3's, which puts it under ``workspace/`` per Q1.

Same shape as ``program/engine/ollama.py``: named exceptions carrying the host and
how to check it, an explicit timeout on every request, and nothing that can hang.

Written against the responses this instance actually returns
===========================================================
Measured 2026-09-21 against ComfyUI 0.37.0, not taken from documentation — the
discipline ``web_search.py`` established after the live JSON disagreed with the docs
in four places. What was observed:

* ``POST /prompt`` returns ``{"prompt_id": ..., "number": ..., "node_errors": {}}``.
* A graph with no output node is rejected **HTTP 400** with
  ``error.type == "prompt_no_outputs"``.
* A graph naming a model that is not installed is rejected **HTTP 400** with
  ``error.type == "prompt_outputs_failed_validation"`` and, under
  ``node_errors[<id>].errors[]``, a ``value_not_in_list`` entry whose ``details``
  names the offending input and lists what *is* available. That is specific enough
  to raise :class:`ComfyUIModelMissing` rather than a generic failure — the same
  distinction ``ToolResult`` draws between ``INVALID_ARGUMENTS`` and ``TOOL_ERROR``.
* ``GET /history/<id>`` returns ``{}`` while the job is queued or running, and
  ``{id: {"status": {...}, "outputs": {...}}}`` once finished. Failure is
  ``status.status_str == "error"``.
* The image is fetched from ``GET /view?filename=&subfolder=&type=``.

``PreviewImage``, not ``SaveImage``
===================================
Both produce a fetchable PNG. ``SaveImage`` writes a **permanent** file into
ComfyUI's own ``output/`` tree; ``PreviewImage`` writes to ``temp/``, which ComfyUI
clears on startup, and reports ``type: "temp"``.

``PreviewImage`` is used because the caller stores the bytes itself. With
``SaveImage`` every generated image would exist twice — once under ``workspace/``
where the artifact record points, and once in a directory that grows without bound,
is outside the backup story and outside the go-live wipe. One copy, in the place the
row names.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from program import config

logger = logging.getLogger(__name__)

#: The node id carrying the image output in the graph this module builds. Referred
#: to by name rather than by guessing at "the last node", because `outputs` is keyed
#: by node id and a rename would otherwise fail silently.
_OUTPUT_NODE = "save"


class ComfyUIError(RuntimeError):
    """Base class. Every failure here names the host and how to check it."""


class ComfyUIUnreachable(ComfyUIError):
    """Nothing answered. ComfyUI is not running, or the host is wrong."""


class ComfyUITimeout(ComfyUIError):
    """The generation did not finish inside the deadline.

    Distinct from every other failure for the reason ``ToolResult.TIMEOUT`` is:
    the job may still be running and may still complete, so whether an image was
    produced is **unknown** rather than known not to have happened.
    """


class ComfyUIWorkflowRejected(ComfyUIError):
    """ComfyUI refused the graph before running it (HTTP 400)."""


class ComfyUIModelMissing(ComfyUIWorkflowRejected):
    """A named checkpoint or LoRA is not installed.

    A subclass so that catching the parent still catches it, but separable because
    the operator action is completely different: a rejected graph is a bug here, a
    missing model is a download.
    """


class ComfyUIGenerationFailed(ComfyUIError):
    """The graph ran and a node raised. The prompt was accepted; execution failed."""


class ComfyUINotLoopback(ComfyUIError):
    """The configured host is not loopback, and this client will not talk to it.

    ComfyUI has **no authentication**: its HTTP API executes any workflow posted to
    it. A non-loopback instance is an unauthenticated execution endpoint on the
    LAN, so this is refused at the point of use rather than trusted to config.
    """


@dataclass(frozen=True)
class GeneratedImage:
    """One image and the settings that produced it.

    Carries the prompt and every generation parameter because A3 indexes the
    **prompt** as the artifact's searchable text (Q14) — an image has no text of its
    own — and because a record of *how* it was made is what lets a later reader tell
    a 4-step Lightning image from a 20-step one.
    """

    image_bytes: bytes
    content_type: str
    prompt: str
    seed: int
    checkpoint: str
    lora: str
    steps: int
    cfg: float
    sampler: str
    scheduler: str
    width: int
    height: int
    duration_seconds: float
    #: ComfyUI's own filename for the temp file, for log correlation only. The file
    #: is in ComfyUI's `temp/` and is cleared on its next startup; nothing should
    #: read it back.
    source_filename: str = ""

    @property
    def size_bytes(self) -> int:
        return len(self.image_bytes)

    def to_metadata(self) -> dict[str, Any]:
        """Everything except the bytes, for an artifact row or a log line."""
        return {
            "prompt": self.prompt,
            "seed": self.seed,
            "checkpoint": self.checkpoint,
            "lora": self.lora,
            "steps": self.steps,
            "cfg": self.cfg,
            "sampler": self.sampler,
            "scheduler": self.scheduler,
            "width": self.width,
            "height": self.height,
            "duration_seconds": round(self.duration_seconds, 2),
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
        }


def _check_loopback(host: str) -> None:
    """Refuse a host that is not loopback. See :class:`ComfyUINotLoopback`."""
    parsed = urllib.parse.urlparse(host)
    hostname = parsed.hostname
    if not hostname:
        raise ComfyUINotLoopback(
            f"comfyui.host is {host!r}, which has no hostname to check."
        )
    try:
        resolved = socket.getaddrinfo(hostname, parsed.port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ComfyUIUnreachable(
            f"could not resolve {hostname!r} from comfyui.host ({host}): {exc}"
        ) from exc

    # EVERY address, not the first: a name with one loopback and one routable
    # record would otherwise pass on whichever the resolver happened to order
    # first. The same reasoning web_fetch's layer 2 records.
    for entry in resolved:
        address = ipaddress.ip_address(entry[4][0])
        # `::ffff:127.0.0.1` is loopback wearing an IPv6 costume, and the reverse is
        # also true: `::ffff:192.168.0.5` is routable. Unwrap before judging, the
        # same unwrapping web_fetch does before its own check.
        mapped = getattr(address, "ipv4_mapped", None)
        if mapped is not None:
            address = mapped
        if not address.is_loopback:
            raise ComfyUINotLoopback(
                f"comfyui.host is {host!r}, which resolves to {address} — not "
                f"loopback. ComfyUI has no authentication, so a reachable instance "
                f"executes any workflow anyone posts to it. Point comfyui.host at "
                f"127.0.0.1 and bind ComfyUI itself to loopback (start it WITHOUT "
                f"--listen, which broadens the bind to 0.0.0.0)."
            )


def _request(url: str, *, payload: dict[str, Any] | None = None, timeout: float) -> Any:
    """One HTTP call. Returns parsed JSON, or raw bytes for a non-JSON response."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return json.loads(body.decode("utf-8"))
            return body, content_type
    except urllib.error.HTTPError as exc:
        raise _rejection(exc) from exc
    except urllib.error.URLError as exc:
        raise ComfyUIUnreachable(
            f"could not reach ComfyUI at {config.comfyui_host()}: {exc.reason}. "
            f"Start it with `cd /Volumes/Dock\\ Storage/ComfyUI && "
            f"./venv/bin/python main.py` (see ops/comfyui/README.md)."
        ) from exc
    except TimeoutError as exc:
        raise ComfyUITimeout(
            f"ComfyUI at {config.comfyui_host()} did not answer within {timeout:g}s"
        ) from exc


def _rejection(exc: urllib.error.HTTPError) -> ComfyUIError:
    """Turn a 400 into the most specific exception the body supports."""
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — a non-JSON error body is still a rejection
        return ComfyUIWorkflowRejected(
            f"ComfyUI rejected the workflow with HTTP {exc.code} and an "
            f"unreadable body"
        )

    node_errors = body.get("node_errors") or {}
    for node_id, detail in node_errors.items():
        for error in detail.get("errors") or []:
            if error.get("type") == "value_not_in_list":
                return ComfyUIModelMissing(
                    f"ComfyUI does not have a model the workflow asked for: "
                    f"{error.get('details', '')} (node {node_id}, "
                    f"{detail.get('class_type', '?')}). Install it under ComfyUI's "
                    f"models/ directory — see ops/comfyui/README.md for the URLs."
                )

    error = body.get("error") or {}
    return ComfyUIWorkflowRejected(
        f"ComfyUI rejected the workflow: {error.get('type', 'unknown')} — "
        f"{error.get('message', '')}. node_errors: "
        f"{json.dumps(node_errors)[:300] if node_errors else 'none'}"
    )


def _graph(prompt: str, seed: int, settings: dict[str, Any]) -> dict[str, Any]:
    """The txt2img workflow, in ComfyUI's API format.

    Built as data here rather than loaded from a saved workflow file, for the reason
    ``registry.Tool.parameters`` is declared rather than derived: the contract should
    be the thing that was written down, and a JSON file exported from the ComfyUI UI
    would carry editor state and node positions that mean nothing to this client.

    **The negative prompt is empty and that is not an oversight.** At ``cfg 1.0``
    the sampler skips the unconditional branch entirely, so no negative conditioning
    is evaluated. The node exists because ``KSampler`` requires the input; supplying
    text would be silently ignored, which is why the tool built on this exposes no
    such parameter.
    """
    return {
        "checkpoint": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": settings["checkpoint"]},
        },
        "lora": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["checkpoint", 0],
                "lora_name": settings["lora"],
                "strength_model": 1.0,
            },
        },
        "positive": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["checkpoint", 1], "text": prompt},
        },
        "negative": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["checkpoint", 1], "text": ""},
        },
        "latent": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": settings["width"],
                "height": settings["height"],
                "batch_size": 1,
            },
        },
        "sampler": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["lora", 0],
                "positive": ["positive", 0],
                "negative": ["negative", 0],
                "latent_image": ["latent", 0],
                "seed": seed,
                "steps": settings["steps"],
                "cfg": settings["cfg"],
                "sampler_name": settings["sampler"],
                "scheduler": settings["scheduler"],
                "denoise": 1.0,
            },
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sampler", 0], "vae": ["checkpoint", 2]},
        },
        _OUTPUT_NODE: {
            "class_type": "PreviewImage",
            "inputs": {"images": ["decode", 0]},
        },
    }


def available() -> dict[str, Any]:
    """ComfyUI's own view of itself, or raise.

    The cheap liveness check, the same role ``ollama.loaded_models()`` plays: it lets
    a caller report *"ComfyUI is not running"* as its own outcome rather than as a
    generation failure.
    """
    host = config.comfyui_host()
    _check_loopback(host)
    return _request(f"{host}/system_stats", timeout=min(10.0, config.comfyui_timeout_seconds()))


def generate(
    prompt: str,
    *,
    seed: int | None = None,
    timeout_seconds: float | None = None,
) -> GeneratedImage:
    """Generate one image. Returns its bytes; writes nothing anywhere.

    ``seed`` defaults to a fresh random one, because a fixed default would make
    every image of a given prompt identical **and** would silently hit ComfyUI's
    execution cache, returning the previous result in milliseconds. That was
    observed during A1b's measurement and read as a 0.2-second generation.

    The deadline covers **queue time as well as generation**: if ComfyUI is busy with
    another job, ours waits, and that wait is charged here. On a single-household
    instance that is unlikely; it is recorded because the timeout would otherwise
    look unaccountably short under load.
    """
    if not prompt or not prompt.strip():
        raise ValueError("generate() needs a prompt; an empty one produces noise")

    host = config.comfyui_host()
    _check_loopback(host)
    settings = config.comfyui_generation()
    deadline_total = (
        timeout_seconds if timeout_seconds is not None else config.comfyui_timeout_seconds()
    )
    if seed is None:
        # `secrets` rather than `random`: nothing security-sensitive rides on it, but
        # this is the one value that must not repeat across calls, and a seeded
        # global RNG somewhere else in the process could make it do exactly that.
        import secrets

        seed = secrets.randbelow(2**32)

    started = time.monotonic()
    submitted = _request(
        f"{host}/prompt",
        payload={"prompt": _graph(prompt.strip(), seed, settings)},
        timeout=min(30.0, deadline_total),
    )
    prompt_id = submitted.get("prompt_id")
    if not prompt_id:
        raise ComfyUIWorkflowRejected(
            f"ComfyUI accepted the request but returned no prompt_id: "
            f"{json.dumps(submitted)[:200]}"
        )

    entry = _await_result(host, prompt_id, started, deadline_total)
    image, content_type = _fetch_image(host, entry, deadline_total)
    elapsed = time.monotonic() - started
    logger.info(
        "generated a %dx%d image in %.1fs (%d steps, seed %d)",
        settings["width"], settings["height"], elapsed, settings["steps"], seed,
    )
    return GeneratedImage(
        image_bytes=image,
        content_type=content_type,
        prompt=prompt.strip(),
        seed=seed,
        duration_seconds=elapsed,
        source_filename=_image_reference(entry).get("filename", ""),
        **{k: settings[k] for k in (
            "checkpoint", "lora", "steps", "cfg", "sampler", "scheduler",
            "width", "height")},
    )


def _await_result(host: str, prompt_id: str, started: float, deadline: float) -> dict[str, Any]:
    """Poll until the job appears in history, or the deadline passes."""
    interval = config.comfyui_poll_interval_seconds()
    while True:
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            raise ComfyUITimeout(
                f"ComfyUI did not finish generating within {deadline:g}s "
                f"(prompt {prompt_id}). The job may still be running there, so "
                f"whether an image was produced is unknown."
            )
        history = _request(
            f"{host}/history/{prompt_id}", timeout=min(30.0, max(remaining, 1.0))
        )
        entry = history.get(prompt_id) if isinstance(history, dict) else None
        if entry is not None:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                raise ComfyUIGenerationFailed(
                    f"ComfyUI ran the workflow and it failed: "
                    f"{json.dumps(status.get('messages', status))[:400]}"
                )
            return entry
        time.sleep(min(interval, max(remaining, 0.01)))


def _image_reference(entry: dict[str, Any]) -> dict[str, Any]:
    outputs = entry.get("outputs") or {}
    images = (outputs.get(_OUTPUT_NODE) or {}).get("images") or []
    if not images:
        raise ComfyUIGenerationFailed(
            f"ComfyUI reported success but produced no image. outputs: "
            f"{json.dumps(outputs)[:300]}"
        )
    return images[0]


def _fetch_image(host: str, entry: dict[str, Any], deadline: float) -> tuple[bytes, str]:
    reference = _image_reference(entry)
    query = urllib.parse.urlencode({
        "filename": reference.get("filename", ""),
        "subfolder": reference.get("subfolder", ""),
        "type": reference.get("type", "temp"),
    })
    result = _request(f"{host}/view?{query}", timeout=min(60.0, deadline))
    if isinstance(result, tuple):
        data, content_type = result
    else:  # a JSON body here means /view returned an error document, not an image
        raise ComfyUIGenerationFailed(
            f"expected image bytes from /view, got JSON: {json.dumps(result)[:200]}"
        )
    if not data:
        raise ComfyUIGenerationFailed("ComfyUI returned an empty image body")
    return data, content_type or "image/png"
