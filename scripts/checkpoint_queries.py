#!/usr/bin/env python3
"""Phase 1 checkpoint: real queries against the real seed corpus.

Run from the repo root: python checkpoint_queries.py

Not a test file — this is the manual, human-reviewed checkpoint
BUILD_PLAN.md calls for: "run a handful of real queries against the
seeded dataset... confirm retrieval ranks sensibly." Floor-rejection and
degenerate-query behavior are explicitly NOT in scope here, since floors
are intentionally unset per BUILD_PLAN's Phase 1 notes.
"""

from __future__ import annotations

from program.memory import retrieval


def show(label: str, query: str, **kwargs):
    print(f"\n{'=' * 70}")
    print(f"{label}")
    print(f"query: {query!r}  {kwargs if kwargs else ''}")
    print("=" * 70)

    result = retrieval.search(query, **kwargs)

    print(f"terms extracted     : {result.terms}")
    print(f"fts5 query          : {result.fts_query!r}")
    print(f"lexical leg ran     : {result.lexical.ran}  "
          f"(candidates={result.lexical.candidates}, kept={result.lexical.kept}, "
          f"floor_applied={result.lexical.floor_applied})")
    print(f"vector leg ran      : {result.vector.ran}  "
          f"(candidates={result.vector.candidates}, kept={result.vector.kept}, "
          f"floor_applied={result.vector.floor_applied})")
    print(f"time filter applied : {result.time_filter_applied}")
    print(f"\n{len(result.results)} result(s):")

    for i, r in enumerate(result.results, start=1):
        legs = "+".join(r.legs) if r.legs else "none"
        preview = r.text[:90].replace("\n", " ")
        print(f"  [{i}] rrf={r.rrf_score:.4f}  legs={legs:<12}  "
              f"conv={r.conversation_id[:8]}  user={r.user_id[:8] if r.user_id else '?'}")
        print(f"       bm25_rank={r.bm25_rank} bm25={r.bm25_score}  "
              f"vec_rank={r.vector_rank} dist={r.vector_distance}")
        print(f"       text: {preview}...")
        for j, sib in enumerate(r.siblings, start=1):
            sib_preview = sib.text[:70].replace("\n", " ")
            print(f"         sibling {j}: {sib_preview}...")


if __name__ == "__main__":
    # 1. The deliberate adjacency, direction A.
    show("ESPRESSO QUERY (should favor espresso, but pour-over is a near neighbor)",
         "how do I get better espresso extraction")

    # 2. The deliberate adjacency, direction B — does it flip correctly?
    show("POUR-OVER QUERY (should favor pour-over, espresso should still show up)",
         "what's the right grind for pour-over coffee")

    # 3. A genuinely off-topic query against this corpus.
    show("OFF-TOPIC QUERY (nothing in the corpus is about this)",
         "aeronautical engineering tolerances for turbine blades")

    # 4. The split-message conversation — does a hit surface siblings?
    show("NOTEBOOK QUERY (should hit the split grandmother's-notebook content)",
         "grandmother's baking notebook recipe")

    # 5. The open conversation should never appear, period.
    show("OPEN-THREAD CONTENT (whatever open-thread's unfinished topic was, "
         "it should NOT appear at all — its trailing group is unindexed)",
         "unfinished open thread")

    # 6. A hostile/punctuation query, since 1.5's own design flagged this as a
    # real crash risk that had to be fixed.
    show("HOSTILE QUERY (apostrophe — this used to crash FTS5 before task 1.5)",
         "what's the deal with the fan noise?")

    # 7. Sanity check on the multi-turn / turn-cap conversations.
    show("RETRIEVAL-DESIGN QUERY (the 16-turn conversation)",
         "how does the hybrid retrieval scoring work")

    show("CHECKIN QUERY (the 18-turn/9-turn-pair conversation, turn-cap boundary)",
         "how was the week going")

    # 8. Explicit sibling check — a query aimed squarely at split content.
    show("SIBLING CHECK (expand_siblings explicit True)",
         "notebook recipe", expand_siblings=True)
