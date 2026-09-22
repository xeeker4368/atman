"""The ``image_generate`` tool. Phase 4, task A2.

Design of record: ``docs/MEDIA_AND_CREATIVE_DESIGN.md`` (Q4, Q12, Q13, Q15).

A thin wrapper, the shape ``memory_search`` has over ``retrieval``: it adds no
generation logic of its own. ``program/media/comfyui.py`` talks to ComfyUI,
``program/artifacts/generated.py`` stores the result, and this turns the pair into
something the model can call.

One parameter, and deliberately only one
========================================
``prompt``. Following ``memory_search``'s reasoning about ``top_k``: the generation
settings are a **matched set** with a measured story — SDXL Lightning needs cfg 1.0
with euler/sgm_uniform, and 4 steps was chosen after a visual comparison — so a model
that could change steps or resolution could quietly triple the turn's cost or run a
misconfigured sampler.

**There is no ``negative_prompt``, and that is the interesting absence** (Q15, locked
at review). At cfg 1.0 the sampler skips the unconditional branch entirely, so no
negative conditioning is ever evaluated. A parameter for it would be **accepted and
silently ignored** — worse than absent, because the model would have no way to notice
its instruction had no effect. The revisit trigger is a **cfg change**, not a phase:
if cfg ever rises above 1.0 the negative prompt becomes functional and exposing it
should be reconsidered then.

``enabled``, and what it means here
===================================
Decision #12's first axis: *does this capability exist at all right now*. So a
disabled tool is **not offered to the model** rather than offered and refused — the
schema never enters the prompt, which is both the honest reading of "does not exist"
and a saving, since every tool's schema costs context on every tool-bearing call.

There is no ``approval_required`` (Q4). A generated image has no external effect; it
lands in the local store. ``GUIDANCE.md`` reserves those flags for *"fully unattended,
no-human-in-the-loop execution"*, and an image the person just asked for is not that.
A second axis here would gate nothing — the *"gate mounted on nothing"* shape
``ingest.py`` refused when it declined to register an upload capability. **That
reasoning assumes generation stays live-turn-only**; wiring it into an autonomous
session is the trigger to revisit, not to inherit.
"""

from __future__ import annotations

import logging

from program import config
from program.artifacts import generated
from program.attribution import AttributionContext
from program.media import comfyui
from program.tools.registry import Tool

logger = logging.getLogger(__name__)

#: Above what the code inside enforces for itself, so a real failure surfaces as its
#: own named error rather than as an uninformative TIMEOUT — memory_search's 45 and
#: web_fetch's 25 follow the same rule. `comfyui.timeout_seconds` is 110, derived
#: from a measured 87 s worst case (cold ComfyUI with both models resident); see
#: config/defaults.toml for the full table and for why the first value, 90, was
#: wrong.
#:
#: 115 stays under agent.tool_budget_seconds (120), so the call is reachable rather
#: than clipped on every turn. The margin between them is deliberately thin: an image
#: turn is an image-only turn, which the numbers make unavoidable rather than a
#: choice.
TIMEOUT_SECONDS = 115.0

#: Said in the tool's own result, every time, and not as a disclaimer.
#:
#: The entity has **no vision** — deferred in `PROJECT.md`, blocked on hardware that
#: does not exist. A result that read "here is your image" would invite the model to
#: describe something it has not seen, which is precisely the fabrication the
#: integrity gate exists to catch. Cheaper to make the tool's own output truthful
#: than to catch the consequence downstream.
_NOT_SEEN = (
    "Nothing in this system has seen this image — there is no vision capability "
    "here. Describing what it looks like would be fabrication; the file itself is "
    "the record."
)


def generate_image(prompt: str, attribution: AttributionContext) -> str:
    """Generate one image, store it, and describe where it went.

    ``attribution`` arrives from the caller, never from the model (P0): it says whose
    record the artifact belongs to. A model that could set it could attribute a write
    to the other household member.

    Exceptions propagate. ``registry.dispatch`` converts them to ``TOOL_ERROR`` and
    the agent loop feeds that back for the model to answer around — the right
    behaviour here, because a failed generation is not a failed turn and every
    failure this can raise is already named and specific.
    """
    image = comfyui.generate(prompt)
    stored = generated.store(image, attribution.user_id)

    logger.info(
        "image_generate produced %s for user %s in %.1fs",
        stored.artifact_id[:8], attribution.user_id[:8], image.duration_seconds,
    )
    return _render(prompt, image, stored)


def _render(prompt: str, image: comfyui.GeneratedImage, stored: generated.StoredImage) -> str:
    """What the model is told. Q13: a text confirmation plus the path and the id.

    Not an image and not a description of one. The entity has no vision and there is
    no frontend until Phase 8, so what a successful generation *is*, right now, is a
    file on disk and a row that names it.
    """
    return (
        f"An image was generated and saved.\n"
        f"  artifact id: {stored.artifact_id}\n"
        f"  file: {stored.storage_path} ({stored.width}x{stored.height} "
        f"{image.content_type}, {stored.size_bytes:,} bytes)\n"
        f"  prompt: {prompt.strip()!r}\n"
        f"  settings: seed {image.seed}, {image.steps} steps, {image.lora}\n"
        f"  took {image.duration_seconds:.1f}s\n"
        f"\n{_NOT_SEEN}"
    )


IMAGE_GENERATE = Tool(
    name="image_generate",
    description=(
        "Generate an image from a text description and save it. Returns the saved "
        "file's path and id, not the image itself — nothing here can see images. "
        "Describe what should be in the picture; style and composition belong in the "
        "same description. One image per call, at 1024x1024."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "What the image should show. A full description works better "
                    "than keywords."
                ),
            },
        },
        "required": ["prompt"],
    },
    handler=generate_image,
    #: Its first real consumer (P0). The handler writes an artifact row, whose
    #: `user_id` is NOT NULL, and the model is not asked whose record it is.
    takes_attribution=True,
    timeout_seconds=TIMEOUT_SECONDS,
    enabled=config.image_generation_enabled,
)
