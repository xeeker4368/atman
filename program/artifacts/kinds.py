"""What kinds of artifact exist, and where each one's bytes live. Phase 4 P0.

Design of record: ``docs/MEDIA_AND_CREATIVE_DESIGN.md`` (Q1, Q3).

``artifacts.storage_path`` is stored **relative to a root**, and until Phase 4
there was only one root so the column's comment could simply name it. There are
now two, because ``config.py`` keeps them apart on purpose:

* ``artifact_dir()`` — files **people hand the entity**.
* ``workspace_dir()`` — the entity's **own output**.

*"Keeping them apart means the go-live wipe and the governance blocklist can treat
them differently without unpicking one directory."*

So the root is a property of the artifact **kind**, resolved here (Q1: a
type-to-root mapping in code, no schema migration). A table rather than a chain of
conditionals, the same shape ``permissions.CAPABILITIES``, ``store.SETTINGS`` and
``catalog.TOOLS`` all use: the full set is greppable from one place.

**An unregistered type raises.** It does not fall back to a default root — a wrong
root writes bytes somewhere the backup, the wipe and the blocklist do not expect,
and a silent default is how that would happen without an error.

The provenance vocabulary
=========================
``source_type`` and ``source_trust`` live here too, for the same reason the root
does: they are properties of the kind. **Task 1.7 owns this vocabulary and has not
landed** — ``working.sql`` names a ``program/memory/provenance.py`` that does not
exist — so these sit in the same provisional shape as ``chunking.py``'s
``conversation``/``firsthand`` pair and ``ingest.py``'s ``file``/``secondhand``
pair, for 1.7 to collect when it arrives.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from program import config


@dataclass(frozen=True)
class ArtifactKind:
    """One kind of artifact, and everything about it that is not per-row."""

    artifact_type: str
    #: Resolved at call time, never captured as a module-level path —
    #: ``config.py``'s own rule, and what lets the test suite repoint the stores.
    root: Callable[[], Path]
    #: Subdirectory under ``root``, or ``""`` for none.
    #:
    #: ``workspace/`` is **not** a flat space: Phase 0 created
    #: ``workspace/{generated,uploads,writing,research,journals}/`` deliberately and
    #: tracks each with a ``.gitkeep``, and `.gitignore`'s ``workspace/*/*`` rule was
    #: verified against ``workspace/generated/`` specifically. Sharding directly under
    #: ``workspace/`` would scatter 256 hex directories across that structure and
    #: bury the five intended ones.
    subdirectory: str
    source_type: str
    source_trust: str
    note: str


KINDS: tuple[ArtifactKind, ...] = (
    ArtifactKind(
        artifact_type="upload",
        root=config.artifact_dir,
        # `artifact_dir()` is already dedicated to uploads, so it needs no further
        # subdivision. `workspace/uploads/` exists from the Phase 0 skeleton and is
        # NOT this — uploads go to data/artifacts/ per config.py's split.
        subdirectory="",
        source_type="file",
        source_trust="secondhand",
        note=(
            "A file a person handed over. `secondhand` because it is not the "
            "entity's own experience: what it says is the document's claim."
        ),
    ),
    ArtifactKind(
        artifact_type="creative_writing",
        root=config.workspace_dir,
        subdirectory="writing",
        source_type="creative_writing",
        source_trust="firsthand",
        note=(
            "Decision #10. `firsthand` because the entity wrote it — it is its "
            "own output, not a report of someone else's. **The fabrication "
            "gate's usual reading of `firsthand` does not apply here: fiction "
            "is not a truth-claim**, so a story saying something untrue about "
            "the world is not the gate's concern. Nothing reads the value "
            "anyway (`test_source_trust_does_not_change_ranking`), so this is a "
            "record rather than a lever."
        ),
    ),
    ArtifactKind(
        artifact_type="generated_image",
        root=config.workspace_dir,
        subdirectory="generated",
        source_type="generated_image",
        source_trust="firsthand",
        note=(
            "Also the entity's own output, so the same root and the same trust "
            "as creative writing — the split is uploads-versus-output, not "
            "text-versus-binary. Its indexed text is the **prompt** (Q14): an "
            "image carries no text, and the prompt is what makes it findable by "
            "what was asked for."
        ),
    ),
)

_BY_TYPE = {kind.artifact_type: kind for kind in KINDS}


class UnknownArtifactKindError(KeyError):
    """An artifact type that is not registered. Never a permissive default."""


def kind(artifact_type: str) -> ArtifactKind:
    try:
        return _BY_TYPE[artifact_type]
    except KeyError:
        raise UnknownArtifactKindError(
            f"{artifact_type!r} is not a registered artifact kind. Known: "
            f"{', '.join(sorted(_BY_TYPE))}. A kind is registered by the task "
            f"that builds the thing it describes — an unregistered type is a "
            f"missing registration, not a reason to guess a storage root."
        ) from None


def root_for(artifact_type: str) -> Path:
    """Where this kind's bytes live, including its subdirectory if it has one.

    Resolved fresh on every call — a captured path would not follow the test suite
    repointing the stores, and would not follow an operator moving them either.
    """
    entry = kind(artifact_type)
    base = entry.root()
    return base / entry.subdirectory if entry.subdirectory else base


def storage_path(artifact_type: str, artifact_id: str) -> tuple[str, Path]:
    """``(relative_path, absolute_path)`` for a new artifact of this kind.

    Two hex characters of the id become a subdirectory — a flat directory with
    thousands of files is slow to list and unpleasant to inspect, and this is the
    shape git uses for objects. **The path derives only from the generated id**,
    never from anything a client or a model supplied.

    Lifted from ``ingest.py``'s ``_storage_path`` so that both write paths shard
    identically; ``ingest.py`` now calls this rather than keeping its own copy.
    """
    relative = f"{artifact_id[:2]}/{artifact_id}"
    absolute = root_for(artifact_type) / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    return relative, absolute
