"""The ``creative_write`` tool. Phase 4, task B4.

Design of record: ``docs/MEDIA_AND_CREATIVE_DESIGN.md``; decision #10 and
``GUIDANCE.md``'s creative-writing section.

**This tool keeps writing, it does not produce it.** The entity composes the piece in
its own output and calls this to persist it — unlike ``image_generate``, where a
separate program does the making and the tool fetches the result. So the parameter is
the *text*, not a prompt, and there is no model call anywhere in this path.

What decision #10 asks for, and what that means here
====================================================
* **No gate.** Creative writing is the lowest-risk category in the build: no external
  effect, nothing irreversible. Nothing here inspects, scores or filters what was
  written, and that absence is deliberate rather than unfinished.
* **No capability, no ``enabled`` flag.** Both household members may do this (#17), so
  ``role`` draws no line and a capability would always return True — the *"gate mounted
  on nothing"* ``ingest.py`` refused for uploads. And unlike image generation there is
  no external service to be unavailable, so there is nothing for an ``enabled`` flag to
  describe.
* **Private by default** means *not proactively announced*, not *hidden* (Q9).
  Retrieval stays unfiltered per decision #20 and **nothing here excludes it** — a
  stored piece can surface in a later conversation like any other record. The judgment
  sits at the point of disclosure, exercised as entity discretion, which is `soul.md`'s
  to establish and explicitly not this task's.
* **Refusal is not implemented here either.** The entity being able to decline to share
  a piece is a `soul.md` values statement (decision #10, `GUIDANCE.md`) and its own
  Tier 3 thread. A tool cannot grant it and this one does not try.

Two parameters, and why the second is optional
==============================================
``text`` is required. ``title`` is optional, because a piece can be saved without the
entity having to name it, and a derived title is honest — it is the opening words
rather than a generated summary. Generating one would mean a second model call
producing a paraphrase presented as the work's own title.
"""

from __future__ import annotations

import logging

from program.artifacts import writing
from program.attribution import AttributionContext
from program.tools.registry import Tool

logger = logging.getLogger(__name__)

#: No model call, no external service, no network — a file write, a row and an
#: embedding per chunk. The embedding is the only part that can be slow, and
#: ``memory_search``'s measurement puts a warm one at 0.03 s and a cold one at 0.31 s.
#:
#: 30 is `tools.default_timeout_seconds`, and this is the one tool so far with no
#: reason to depart from it: nothing inside enforces a longer bound of its own, which is
#: the criterion `memory_search`'s 45 and `web_fetch`'s 25 were each derived against.
#: A long piece embeds several chunks, and even a hundred of them at the measured
#: 0.075 s is well inside it.
TIMEOUT_SECONDS = 30.0


def write_creatively(
    text: str, attribution: AttributionContext, title: str | None = None
) -> str:
    """Keep a piece the entity has written. Returns where it went.

    Exceptions propagate to ``registry.dispatch``, which turns them into
    ``TOOL_ERROR`` for the model to answer around. A failed save is not a failed turn —
    and the text is still in the answer the entity just produced, so nothing is lost
    that the person cannot see.
    """
    stored = writing.store(text, attribution.user_id, title=title)

    logger.info(
        "creative_write kept %s (%d chars) for user %s",
        stored.artifact_id[:8], stored.characters, attribution.user_id[:8],
    )
    return (
        f"Saved.\n"
        f"  title: {stored.title!r}\n"
        f"  artifact id: {stored.artifact_id}\n"
        f"  file: {stored.storage_path} ({stored.characters:,} characters)\n"
        f"  indexed into memory as {stored.chunks_written} record(s), so it can be "
        f"found again later.\n"
        f"\nIt is kept, not published. Nothing shows it to anyone unless it comes up."
    )


CREATIVE_WRITE = Tool(
    name="creative_write",
    description=(
        "Keep a piece of your own creative writing — a story, a poem, a fragment. "
        "Write the piece in full and pass it as `text`; this saves it and indexes it "
        "so it can be found again. It does not write anything for you. Saved work is "
        "private in the sense that nothing announces it, but it can surface later "
        "like any other record."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The complete piece, as you want it kept.",
            },
            "title": {
                "type": "string",
                "description": (
                    "Optional. A title for it. If you leave this out, the opening "
                    "words are used."
                ),
            },
        },
        "required": ["text"],
    },
    handler=write_creatively,
    #: It writes an artifact row, whose `user_id` is NOT NULL, and the model is not
    #: asked whose record it is.
    takes_attribution=True,
    timeout_seconds=TIMEOUT_SECONDS,
)
