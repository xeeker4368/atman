"""Resolving `supersedes` links for retrieval. Task 3.5, R1–R3 and R9.

Design of record: ``docs/RETRIEVAL_SUPERSESSION_DESIGN.md``. The classifier that
writes these links is ``program/integrity/corrections.py``.

**Annotate, never suppress** (CO7). Nothing here removes, reorders or penalises a
result. A corrected record still surfaces, in the same position, with the
correction attached beside it — *"transparency is the reason, not cost."*

**Resolution happens after fusion and cannot reach ranking** (R1), the same shape
D7 gave split siblings. This module takes chunk ids and returns data; it does not
import ``retrieval``, so there is no path by which a link could influence a score.

Two states, not one
===================
Since CO8 a correction may say an earlier claim is wrong **without** giving the
correct value, and migration 6 records which (``supersedes.replacement``). A link
therefore does not imply a replacement exists, and the two render differently:
*corrected* against *contradicted, with no replacement given*.

Following the chain, and why termination is a separate problem
=============================================================
``A <- B <- C`` means ``C`` is the current statement; surfacing ``B`` would annotate
a record with a correction that has itself been corrected. So the walk goes forward
to the **tip**, and it carries two independent stops:

* a **visited set per origin**, which stops a loop. The schema's cycle triggers keep
  loops out of the table, but a restore, a hand-edited row or a future bug can all
  produce one, and *retrieval hanging mid-turn is the worst possible place to
  discover it* (``docs/DB_SCHEMA.md``).
* a **depth bound**, which stops a pathologically long chain from spending the turn.

They fail differently and neither implies the other. Both are recorded rather than
only obeyed: a cycle reaching retrieval is an operator-visible fact.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field

from program.memory import db

logger = logging.getLogger(__name__)

#: How many links deep a chain is followed. A JUDGMENT value. Real chains are
#: expected to be one link; this exists so a corrupt or adversarial chain cannot
#: spend the turn, not because ten is meaningful.
MAX_DEPTH = 10

#: Annotations rendered under one chunk before the rest become a count. A JUDGMENT
#: value — see `prompt.py` for where the budget arithmetic lands.
MAX_PER_CHUNK = 3

REPLACED = "replaced"
CONTRADICTED = "contradicted"


@dataclass(frozen=True)
class Supersession:
    """One correction attached to a retrieved chunk. Never scored (R1, D6)."""

    #: The message inside the chunk that was corrected.
    superseded_message_id: str
    superseded_text: str
    #: The current statement — the tip of the chain, not the first link.
    superseding_message_id: str
    superseding_text: str
    superseding_timestamp: str
    #: ``replaced`` or ``contradicted``, taken from the **last** link in the chain.
    #: For ``A <-(replaced) B <-(contradicted) C`` the reader's question is whether a
    #: current value exists, and it does not: B supplied one and C withdrew it. The
    #: first link's state would answer a question nobody asked.
    replacement: str
    #: How many links were followed. 1 is the ordinary case.
    depth: int = 1


@dataclass
class SupersessionReport:
    """Enough state to answer "were corrections resolved, and what happened".

    Task 1.6's precedent: *structured* access rather than inference from counts.
    ``resolved`` being False is not benign — unannotated results present a corrected
    claim as current, which is the outcome this mechanism exists to prevent — so the
    state is inspectable rather than only logged (R7).
    """

    resolved: bool = True
    links_followed: int = 0
    annotations: int = 0
    cycles_detected: int = 0
    depth_limit_hit: int = 0
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "resolved": self.resolved,
            "links_followed": self.links_followed,
            "annotations": self.annotations,
            "cycles_detected": self.cycles_detected,
            "depth_limit_hit": self.depth_limit_hit,
            "skip_reason": self.skip_reason,
        }


@dataclass
class _Branch:
    """One partially-walked chain, from the message a chunk actually contains."""

    origin_id: str
    current_id: str
    current_content: str
    current_timestamp: str
    replacement: str
    depth: int
    visited: set[str] = field(default_factory=set)


def _tip(branch: _Branch, superseded_text: str) -> Supersession:
    return Supersession(
        superseded_message_id=branch.origin_id,
        superseded_text=superseded_text,
        superseding_message_id=branch.current_id,
        superseding_text=branch.current_content,
        superseding_timestamp=branch.current_timestamp,
        replacement=branch.replacement,
        depth=branch.depth,
    )


def resolve_for_chunks(
    chunk_ids: Sequence[str],
) -> tuple[dict[str, list[Supersession]], SupersessionReport]:
    """``({chunk_id: [Supersession, ...]}, report)`` for these chunks.

    Takes ids and returns data — no ``RetrievedChunk``, no import of ``retrieval``,
    no actor (R8). Links exist only within one user's own claims, so annotation
    needs no actor to be correct, and decision #20 forbids scoping retrieval by who
    is asking.
    """
    report = SupersessionReport()
    if not chunk_ids:
        return {}, report

    first_hop = db.get_supersedes_for_chunks(chunk_ids)
    if not first_hop:
        return {}, report

    # The text of each corrected message, and which chunks contain it. A message
    # can appear in more than one chunk row when a long message was split.
    superseded_text: dict[str, str] = {}
    chunks_for_origin: dict[str, list[str]] = {}
    branches: list[_Branch] = []

    for row in first_hop:
        origin = row["superseded_id"]
        superseded_text[origin] = row["superseded_content"]
        chunks = chunks_for_origin.setdefault(origin, [])
        if row["chunk_id"] not in chunks:
            chunks.append(row["chunk_id"])
        report.links_followed += 1
        branches.append(_Branch(
            origin_id=origin,
            current_id=row["superseding_id"],
            current_content=row["superseding_content"],
            current_timestamp=row["superseding_timestamp"],
            replacement=row["replacement"],
            depth=1,
            visited={origin, row["superseding_id"]},
        ))

    tips: list[Supersession] = []
    frontier = branches
    depth = 1
    while frontier:
        if depth >= MAX_DEPTH:
            # Every unfinished branch is reported at the deepest message reached,
            # which is the most current statement known rather than nothing.
            report.depth_limit_hit += len(frontier)
            logger.warning(
                "supersedes chain reached the depth bound (%d) on %d branch(es); "
                "annotating with the deepest resolved message",
                MAX_DEPTH, len(frontier),
            )
            tips.extend(_tip(b, superseded_text[b.origin_id]) for b in frontier)
            break

        onward = db.get_supersedes_from([b.current_id for b in frontier])
        by_message: dict[str, list[sqlite3.Row]] = {}
        for row in onward:
            by_message.setdefault(row["from_id"], []).append(row)

        next_frontier: list[_Branch] = []
        for branch in frontier:
            rows = by_message.get(branch.current_id, [])
            unseen = [r for r in rows if r["superseding_id"] not in branch.visited]
            if len(unseen) != len(rows):
                # A link pointing back into this chain. The schema should have
                # refused it, so its presence is worth saying out loud.
                report.cycles_detected += 1
                logger.warning(
                    "supersedes cycle reached retrieval while resolving %s; "
                    "the schema's cycle guard was bypassed at write time",
                    branch.origin_id[:8],
                )
            if not unseen:
                tips.append(_tip(branch, superseded_text[branch.origin_id]))
                continue
            # Two messages superseding the same claim is a branch, not an error:
            # `UNIQUE` is on the pair, not on the superseded side. Both are carried
            # (R3) rather than picking one, which would be an unrecorded judgment.
            for row in unseen:
                report.links_followed += 1
                next_frontier.append(_Branch(
                    origin_id=branch.origin_id,
                    current_id=row["superseding_id"],
                    current_content=row["superseding_content"],
                    current_timestamp=row["superseding_timestamp"],
                    replacement=row["replacement"],
                    depth=branch.depth + 1,
                    visited=branch.visited | {row["superseding_id"]},
                ))
        frontier = next_frontier
        depth += 1

    by_chunk: dict[str, list[Supersession]] = {}
    for tip in tips:
        for chunk_id in chunks_for_origin[tip.superseded_message_id]:
            by_chunk.setdefault(chunk_id, []).append(tip)

    for items in by_chunk.values():
        items.sort(key=lambda s: (s.superseded_message_id, s.superseding_timestamp))
    report.annotations = sum(len(v) for v in by_chunk.values())
    return by_chunk, report
