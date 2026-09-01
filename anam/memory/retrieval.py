"""Hybrid retrieval: BM25 + vector, fused by RRF, with uncalibrated floors.

Task 1.5. The approved design is ``docs/RETRIEVAL_DESIGN.md``; decisions are
cited here as D1–D9 rather than re-argued.

Two legs, one ordering
----------------------
* **Lexical** — FTS5 over ``chunks_fts``, ranked by ``bm25()``.
* **Vector** — cosine nearest neighbours from the Chroma collection.

Fused with Reciprocal Rank Fusion, which consumes only the two *orderings*.
That is the point: ``bm25()`` is negative, unbounded, and its magnitude scales
with the number of query terms, while cosine distance is bounded in ``[0, 2]``
and scales with nothing. There is no principled conversion between them, and
inventing one is where score fusion usually goes wrong (D3).

What this module does not do
----------------------------
* **No degenerate-query handling.** That is task 1.6, and it consumes the floor
  mechanism here rather than reimplementing it. See ``floor_applied`` on
  :class:`LegReport` and the note in D9 below.
* **No supersession resolution.** Task 3.5, Phase 3.
* **Provenance never enters scoring** (D6). ``source_type`` and ``source_trust``
  are carried onto results as metadata and are read by nothing in the ranking
  path. This is a named constraint from BUILD_PLAN, not an oversight.

Why this module degrades where chunking and reconcile abort
-----------------------------------------------------------
``_vector_leg()`` catches broadly and returns a lexical-only answer when the
embedder is unreachable. That is a deliberate divergence from
``anam/memory/chunking.py`` ("no exception is caught in the write loop; a
failure aborts the run and propagates") and ``anam/memory/reconcile.py``
("failure policy matches the chunking pipeline: abort and propagate"), and the
difference is the *shape of the work*, not a lapse:

* **Chunking and reconcile write, and nothing is waiting.** A partial write is
  worse than no write, and both are additive and resumable — aborting costs only
  the work not yet done, and the next checkpoint or pass picks it up with
  nothing lost. Continuing past a systemic error (a wrong dimension, an
  unreachable model) would produce a long run of identical failures and a report
  that looked like partial success.
* **Search reads, and a caller is waiting.** There is no data at stake in either
  direction, because this module never writes anything. Failing the whole query
  because one leg is down turns a usable lexical-only answer into no answer at
  all, for a caller who cannot retry later in any meaningful sense — the turn is
  happening now. So the leg degrades, and the reason is recorded in
  ``LegReport.skip_reason`` rather than swallowed.

Read side by side these are one rule applied to three situations, not three
policies: **abort when a failure could corrupt or when retrying is free; degrade
when nothing can be corrupted and a person is waiting.** ``anam/memory/idle.py``
sits between them for the same reason — it collects per-conversation failures
and raises them together at the end, because one unreachable model must not stop
every other conversation closing.

Floors are a mechanism with no thresholds (D4)
----------------------------------------------
Both floors ship **unset** — ``None``, not a low number. Those are different
states and the difference is load-bearing: a low-but-set floor is
indistinguishable at the call site from a calibrated floor that happened to pass,
so "did the floor fire?" stops being answerable. ``None`` makes "no floor is in
force" a first-class, inspectable fact, which is what task 1.6 needs to read.

They cannot be calibrated yet, and this is measured rather than asserted: the
two existing "off-topic" datapoints disagree. Task 1.4's synthetic test saw
0.658; a genuinely off-topic query against the seed corpus saw 0.5567. A floor
of 0.6 — a reasonable reading of the first — would admit the second.

D9, restated where it will be read
----------------------------------
With floors unset, the vector leg's post-floor contribution can only be zero
when the collection itself is empty (it always returns neighbours). So task
1.6's second condition is **structurally unreachable** until floors are
calibrated, and a green test suite must not be read as evidence otherwise.

Separately: the lexical leg returning results for nearly any query (D1's OR
semantics) is **not** in tension with task 1.6's first condition. 1.6 counts
*query terms*, not *result counts* — a query collapsing to one meaningful term
is degenerate whether the OR leg returned ten chunks or none.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from anam import config
from anam.engine import ollama
from anam.memory import db, vectors

logger = logging.getLogger(__name__)

#: Word characters only. FTS5's MATCH argument is a query *syntax*, not a bag of
#: words, so an apostrophe, a hyphen or a stray "AND" in ordinary user text
#: raises OperationalError. Verified: "what's the deal with espresso?" ->
#: `fts5: syntax error near "'"`. Everything non-word is dropped here, and each
#: surviving term is quoted, so no user input can reach MATCH as syntax (D1).
_WORD = re.compile(r"\w+", re.UNICODE)


class RetrievalError(RuntimeError):
    """Retrieval could not be completed."""


# ---------------------------------------------------------------------------
# Query construction (D1)
# ---------------------------------------------------------------------------


def query_terms(raw: str) -> list[str]:
    """Word-character terms from a raw query, order-preserving and de-duplicated.

    Also the input task 1.6 counts for its first condition. It counts *these*,
    not how many rows the lexical leg returned.
    """
    seen: dict[str, None] = {}
    for term in _WORD.findall(raw or ""):
        seen.setdefault(term.lower(), None)
    return list(seen)


def build_fts_query(raw: str) -> str | None:
    """A safe FTS5 MATCH expression, or None when there is nothing to match.

    Terms are OR-ed, not AND-ed. Measured: FTS5's default implicit AND returns
    **zero rows for every natural-language query tested** — a conversational
    query essentially never has all its terms in one chunk. OR gives the leg
    something to rank; ``bm25()`` does the ranking (D1).
    """
    terms = query_terms(raw)
    if not terms:
        return None
    return " OR ".join(f'"{term}"' for term in terms)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class LegReport:
    """What one leg contributed, and whether a floor was in force.

    ``floor_applied`` is the state task 1.6 reads. It is False whenever the
    threshold is unset, which is deliberately distinguishable from "a floor was
    applied and rejected nothing".
    """

    name: str
    candidates: int = 0
    kept: int = 0
    rejected_by_floor: int = 0
    floor_applied: bool = False
    floor_value: float | None = None
    ran: bool = True
    skip_reason: str | None = None


@dataclass
class RetrievedChunk:
    """One result, carrying where it came from and why it ranked where it did."""

    chunk_id: str
    text: str
    created_at: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None
    chunk_index: int | None = None
    first_message_id: str | None = None
    # Provenance. Metadata only — read by nothing in the ranking path (D6).
    source_type: str | None = None
    source_trust: str | None = None
    # Score provenance.
    rrf_score: float = 0.0
    bm25_rank: int | None = None
    bm25_score: float | None = None
    vector_rank: int | None = None
    vector_distance: float | None = None
    #: Split siblings of the same original message, attached after fusion and
    #: never ranked in their own right (D7).
    siblings: list["RetrievedChunk"] = field(default_factory=list)

    @property
    def legs(self) -> list[str]:
        found = []
        if self.bm25_rank is not None:
            found.append("lexical")
        if self.vector_rank is not None:
            found.append("vector")
        return found


@dataclass
class RetrievalResult:
    """The ranked results plus enough state to answer "why".

    Inspectability is not decoration here: "why did this chunk rank here" has to
    be answerable without re-running the query, and task 1.6 needs *structured*
    access to the leg reports rather than inferring from result counts.
    """

    query: str
    results: list[RetrievedChunk] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    fts_query: str | None = None
    lexical: LegReport = field(default_factory=lambda: LegReport("lexical"))
    vector: LegReport = field(default_factory=lambda: LegReport("vector"))
    time_filter_applied: bool = False
    allowed_ids: int | None = None
    rrf_k: int = 0

    def __len__(self) -> int:
        return len(self.results)

    @property
    def chunk_ids(self) -> list[str]:
        return [r.chunk_id for r in self.results]


# ---------------------------------------------------------------------------
# Time filter (D5) — pre-filter, computed in SQL, applied to both legs
# ---------------------------------------------------------------------------


def resolve_time_window(
    since: str | None = None,
    until: str | None = None,
) -> list[str] | None:
    """Chunk ids inside a time window, or None when no window was asked for.

    Matches a chunk when **either** its own ``created_at`` or the timestamp of
    any message in its range falls inside the window. ``created_at`` is when the
    chunk was *written*, which is not when the conversation happened — for
    back-filled or re-chunked content those diverge, and a user asking "what did
    we discuss last Tuesday" means the conversation.

    Returns a possibly-empty list when a window was given. Empty means "nothing
    is in the window", which is a real answer and must not be confused with
    "no window was requested" — hence None for the latter (D5).
    """
    if since is None and until is None:
        return None

    clauses = []
    params: list[Any] = []
    if since is not None:
        clauses.append("ts >= ?")
        params.append(since)
    if until is not None:
        clauses.append("ts <= ?")
        params.append(until)
    where = " AND ".join(clauses)

    sql = f"""
        SELECT DISTINCT id FROM (
            SELECT c.id AS id, c.created_at AS ts FROM chunks c
            UNION ALL
            SELECT c.id AS id, m.timestamp AS ts
              FROM chunks c
              JOIN messages m ON m.conversation_id = c.conversation_id
             WHERE c.first_message_id IS NOT NULL
        )
        WHERE {where}
    """
    with db.connection() as conn:
        return [row["id"] for row in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------------------
# The legs
# ---------------------------------------------------------------------------


def _lexical_leg(
    raw_query: str,
    limit: int,
    allowed_ids: Sequence[str] | None,
    report: LegReport,
) -> list[tuple[str, float]]:
    """(chunk_id, bm25_score) best-first.

    ``bm25()`` returns **negative** values, more negative meaning a better
    match, so ``ORDER BY`` ascending is best-first and the floor is an upper
    bound rather than a lower one. Verified against the seed corpus.
    """
    fts_query = build_fts_query(raw_query)
    if fts_query is None:
        report.ran = False
        report.skip_reason = "the query contained no word characters to match on"
        return []

    if allowed_ids is not None and not allowed_ids:
        report.ran = False
        report.skip_reason = "the time filter allowed no chunks"
        return []

    sql = [
        "SELECT c.id AS id, bm25(chunks_fts) AS score",
        "FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid",
        "WHERE chunks_fts MATCH ?",
    ]
    params: list[Any] = [fts_query]
    if allowed_ids is not None:
        placeholders = ",".join("?" * len(allowed_ids))
        sql.append(f"AND c.id IN ({placeholders})")
        params.extend(allowed_ids)
    sql.append("ORDER BY score LIMIT ?")
    params.append(limit)

    with db.connection() as conn:
        try:
            rows = conn.execute("\n".join(sql), params).fetchall()
        except sqlite3.OperationalError as exc:
            # build_fts_query() should make this unreachable. If it ever is
            # reached, the leg degrades rather than failing the whole retrieval.
            logger.warning("lexical leg failed for %r: %s", fts_query, exc)
            report.ran = False
            report.skip_reason = f"FTS5 rejected the query: {exc}"
            return []

    report.candidates = len(rows)
    return [(row["id"], row["score"]) for row in rows]


def _vector_leg(
    raw_query: str,
    limit: int,
    allowed_ids: Sequence[str] | None,
    report: LegReport,
) -> list[tuple[str, float]]:
    """(chunk_id, cosine_distance) best-first. Smaller distance is better."""
    if allowed_ids is not None and not allowed_ids:
        report.ran = False
        report.skip_reason = "the time filter allowed no chunks"
        return []

    try:
        vector = ollama.embed(raw_query)
    except Exception as exc:  # noqa: BLE001 - the leg degrades, retrieval does not
        logger.warning("vector leg unavailable: %s", exc)
        report.ran = False
        report.skip_reason = f"the query could not be embedded: {exc}"
        return []

    store = vectors.get_vector_store()
    response = store.query(vector, n_results=limit, ids=allowed_ids)
    ids = response["ids"][0] if response.get("ids") else []
    distances = response["distances"][0] if response.get("distances") else []

    report.candidates = len(ids)
    return list(zip(ids, distances))


# ---------------------------------------------------------------------------
# Floors (D4)
# ---------------------------------------------------------------------------


def _apply_floor(
    ranked: list[tuple[str, float]],
    threshold: float | None,
    keep: str,
    report: LegReport,
) -> list[tuple[str, float]]:
    """Drop candidates past the floor, or apply none at all when unset.

    ``keep`` is ``"below"`` for scores where smaller is better (cosine distance,
    and bm25 whose values are negative) — the only two cases here, but named
    rather than assumed so a future leg cannot inherit the wrong comparison.
    """
    report.floor_value = threshold
    if threshold is None:
        report.floor_applied = False
        report.kept = len(ranked)
        return ranked

    report.floor_applied = True
    if keep == "below":
        kept = [(cid, score) for cid, score in ranked if score <= threshold]
    else:
        kept = [(cid, score) for cid, score in ranked if score >= threshold]
    report.kept = len(kept)
    report.rejected_by_floor = len(ranked) - len(kept)
    return kept


# ---------------------------------------------------------------------------
# Fusion (D3)
# ---------------------------------------------------------------------------


def reciprocal_rank_fusion(
    legs: Iterable[Sequence[str]],
    k: int,
) -> dict[str, float]:
    """``score(d) = sum over legs of 1 / (k + rank)``, ranks 1-based.

    Only orderings are consumed; neither leg's raw score enters. See the module
    docstring for why.
    """
    scores: dict[str, float] = {}
    for leg in legs:
        for rank, chunk_id in enumerate(leg, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


# ---------------------------------------------------------------------------
# Hydration and sibling expansion (D7)
# ---------------------------------------------------------------------------


def _load_chunks(chunk_ids: Sequence[str]) -> dict[str, sqlite3.Row]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM chunks WHERE id IN ({placeholders})", list(chunk_ids)
        ).fetchall()
    return {row["id"]: row for row in rows}


def _to_result(row: sqlite3.Row) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row["id"],
        text=row["text"],
        created_at=row["created_at"],
        conversation_id=row["conversation_id"],
        user_id=row["user_id"],
        chunk_index=row["chunk_index"],
        first_message_id=row["first_message_id"],
        source_type=row["source_type"],
        source_trust=row["source_trust"],
    )


def _attach_siblings(results: list[RetrievedChunk], limit: int) -> None:
    """Attach split siblings to the hit that matched — never rank them (D7).

    Split pieces of one over-long message share ``first_message_id`` (task 1.3's
    design). A hit on piece 2 of 4 hands the model a fragment of a longer
    message, and the siblings are the rest of it.

    They are attached rather than injected because injecting them would let one
    long message occupy several of the top-N slots and crowd out every other
    conversation, and would assert relevance for pieces the ranking never
    established. Bounded by ``limit`` so a pathological split cannot overrun the
    context budget task 1.10 is metering.
    """
    if limit <= 0:
        return
    already = {r.chunk_id for r in results}
    wanted = {
        (r.conversation_id, r.first_message_id)
        for r in results
        if r.first_message_id is not None and r.conversation_id is not None
    }
    if not wanted:
        return

    found: dict[tuple[str, str], list[sqlite3.Row]] = {}
    with db.connection() as conn:
        for conversation_id, first_message_id in wanted:
            rows = conn.execute(
                "SELECT * FROM chunks WHERE conversation_id = ? "
                "AND first_message_id = ? ORDER BY chunk_index",
                (conversation_id, first_message_id),
            ).fetchall()
            if len(rows) > 1:
                found[(conversation_id, first_message_id)] = rows

    for result in results:
        key = (result.conversation_id, result.first_message_id)
        for row in found.get(key, []):
            if row["id"] in already or row["id"] == result.chunk_id:
                continue
            result.siblings.append(_to_result(row))
            if len(result.siblings) >= limit:
                break


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def search(
    query: str,
    top_k: int | None = None,
    since: str | None = None,
    until: str | None = None,
    expand_siblings: bool | None = None,
) -> RetrievalResult:
    """Hybrid search over the chunk store.

    ``since``/``until`` are ISO-8601 bounds resolved as a **pre-filter** on both
    legs (D5), so the results are the best matches *inside* the window rather
    than whichever of the best matches overall happen to fall in it.
    """
    k = config.retrieval_rrf_k()
    per_leg = config.retrieval_candidates_per_leg()
    limit = top_k if top_k is not None else config.retrieval_top_k()
    expand = (
        expand_siblings
        if expand_siblings is not None
        else config.retrieval_expand_siblings()
    )

    result = RetrievalResult(query=query, rrf_k=k)
    result.terms = query_terms(query)
    result.fts_query = build_fts_query(query)

    allowed = resolve_time_window(since, until)
    result.time_filter_applied = allowed is not None
    result.allowed_ids = None if allowed is None else len(allowed)

    lexical = _lexical_leg(query, per_leg, allowed, result.lexical)
    vector = _vector_leg(query, per_leg, allowed, result.vector)

    lexical = _apply_floor(
        lexical, config.retrieval_lexical_score_floor(), "below", result.lexical
    )
    vector = _apply_floor(
        vector, config.retrieval_vector_distance_floor(), "below", result.vector
    )

    lexical_ids = [cid for cid, _ in lexical]
    vector_ids = [cid for cid, _ in vector]
    fused = reciprocal_rank_fusion([lexical_ids, vector_ids], k)
    if not fused:
        return result

    lexical_scores = dict(lexical)
    vector_scores = dict(vector)
    lexical_rank = {cid: i for i, cid in enumerate(lexical_ids, start=1)}
    vector_rank = {cid: i for i, cid in enumerate(vector_ids, start=1)}

    # Ties broken by chunk id so the ordering is deterministic rather than
    # dependent on dict insertion order.
    ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    rows = _load_chunks([cid for cid, _ in ordered])

    for chunk_id, score in ordered:
        row = rows.get(chunk_id)
        if row is None:
            # Indexed but no longer in chunks. Nothing deletes chunks today, so
            # this is defensive: a stale vector must not fabricate a result.
            logger.warning("chunk %s ranked but is absent from chunks", chunk_id)
            continue
        item = _to_result(row)
        item.rrf_score = score
        item.bm25_rank = lexical_rank.get(chunk_id)
        item.bm25_score = lexical_scores.get(chunk_id)
        item.vector_rank = vector_rank.get(chunk_id)
        item.vector_distance = vector_scores.get(chunk_id)
        result.results.append(item)

    if expand:
        _attach_siblings(result.results, config.retrieval_max_siblings_per_hit())

    return result
