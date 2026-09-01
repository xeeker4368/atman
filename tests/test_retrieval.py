"""Hybrid retrieval — tested against the seed corpus, not toy strings.

The corpus (`program/ops/seed.py`) was built for exactly this: the espresso /
pour-over adjacency, the split notebook message, and the open conversation whose
trailing group is deliberately unindexed.
"""

from __future__ import annotations

import hashlib

import pytest

from program import config
from program.engine import ollama
from program.memory import chunking, db, retrieval
from program.ops import seed


@pytest.fixture
def corpus(isolated_data_dir, monkeypatch):
    """The real seed corpus with deterministic, content-derived embeddings.

    Not random: identical text must embed identically. Similarity between
    *different* texts is not modelled, so these tests assert plumbing,
    ordering and policy — never semantic quality, which is the checkpoint's job
    and is exercised separately in the live run recorded in the changelog.
    """
    def fake_embed(text, **kwargs):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(768)]

    monkeypatch.setattr(ollama, "embed", fake_embed)
    monkeypatch.setattr(chunking.ollama, "embed", fake_embed)
    monkeypatch.setattr(retrieval.ollama, "embed", fake_embed)
    return seed.seed()


# --- D1: query construction --------------------------------------------------


def test_raw_punctuation_does_not_reach_fts5(corpus):
    """Regression for a measured crash: an apostrophe raises fts5 syntax error."""
    result = retrieval.search("what's the deal with espresso?")
    assert result.fts_query is not None
    assert "'" not in result.fts_query
    assert result.lexical.ran


@pytest.mark.parametrize(
    "query",
    [
        "what's the deal with espresso?",
        "espresso -sour",
        "a AND",
        "coffee OR tea",
        'grinder "pour over"',
        "NEAR(a b)",
        "café — naïve? (yes!)",
    ],
)
def test_hostile_queries_never_raise(corpus, query):
    """Every one of these is a real FTS5 operator or syntax error if passed raw."""
    result = retrieval.search(query)
    assert isinstance(result, retrieval.RetrievalResult)


def test_terms_are_or_ed_not_and_ed(corpus):
    """Measured: implicit AND returns zero rows for every natural-language query."""
    result = retrieval.search("my espresso tastes sour and pulls too fast")
    assert " OR " in result.fts_query
    assert result.lexical.candidates > 0


def test_a_query_with_no_word_characters_skips_the_lexical_leg(corpus):
    result = retrieval.search("?!  ...  ")
    assert result.terms == []
    assert result.fts_query is None
    assert result.lexical.ran is False
    assert "no word characters" in result.lexical.skip_reason


def test_terms_are_lowercased_and_deduplicated(corpus):
    assert retrieval.query_terms("Espresso espresso ESPRESSO sour") == [
        "espresso", "sour"
    ]


# --- D1: bm25 sign and ordering ---------------------------------------------


def test_bm25_is_negative_and_best_first(corpus):
    """More negative is a better match, so ordering is ascending.

    Getting this backwards would silently invert the lexical leg.
    """
    result = retrieval.search("espresso")
    lexical = [r for r in result.results if r.bm25_score is not None]
    assert lexical
    assert all(r.bm25_score < 0 for r in lexical)
    by_rank = sorted(lexical, key=lambda r: r.bm25_rank)
    scores = [r.bm25_score for r in by_rank]
    assert scores == sorted(scores)


# --- D3: fusion --------------------------------------------------------------


def test_rrf_formula_is_reciprocal_rank_over_k_plus_rank():
    fused = retrieval.reciprocal_rank_fusion([["a", "b"], ["b", "a"]], k=60)
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)


def test_agreement_between_legs_outranks_a_single_leg_hit():
    """The property fusion exists for."""
    fused = retrieval.reciprocal_rank_fusion([["both", "only_lex"], ["both"]], k=60)
    assert fused["both"] > fused["only_lex"]


def test_fusion_uses_ranks_not_scores(corpus):
    """bm25 magnitude scales with term count; distances are [0,2]. Ranks only."""
    result = retrieval.search("espresso grinder coffee")
    for item in result.results:
        contributions = 0.0
        if item.bm25_rank is not None:
            contributions += 1 / (result.rrf_k + item.bm25_rank)
        if item.vector_rank is not None:
            contributions += 1 / (result.rrf_k + item.vector_rank)
        assert item.rrf_score == pytest.approx(contributions)


def test_results_are_ordered_by_fused_score(corpus):
    result = retrieval.search("espresso and coffee grinding")
    scores = [r.rrf_score for r in result.results]
    assert scores == sorted(scores, reverse=True)


def test_ordering_is_deterministic_across_runs(corpus):
    a = retrieval.search("coffee").chunk_ids
    b = retrieval.search("coffee").chunk_ids
    assert a == b


# --- D4: floors --------------------------------------------------------------


def test_floors_ship_unset_and_report_as_not_applied(corpus):
    """`None`, not a low number — the distinction task 1.6 depends on."""
    assert config.retrieval_vector_distance_floor() is None
    assert config.retrieval_lexical_score_floor() is None

    result = retrieval.search("espresso")
    assert result.vector.floor_applied is False
    assert result.lexical.floor_applied is False
    assert result.vector.floor_value is None
    assert result.vector.rejected_by_floor == 0


def test_an_off_topic_query_still_returns_results(corpus):
    """Nothing is silently rejecting: permissive means no floor at all."""
    result = retrieval.search("aeronautical engineering tolerances")
    assert result.results
    assert result.vector.floor_applied is False


def test_the_floor_mechanism_works_when_a_threshold_is_set(monkeypatch, corpus):
    """The mechanism is built and wired even though it ships unset."""
    monkeypatch.setenv("ANAM_RETRIEVAL_VECTOR_FLOOR", "0.0")
    config.reload()

    result = retrieval.search("espresso")

    assert result.vector.floor_applied is True
    assert result.vector.floor_value == 0.0
    # Every real distance exceeds 0.0, so the floor rejects everything.
    assert result.vector.rejected_by_floor == result.vector.candidates
    assert result.vector.kept == 0


def test_a_set_floor_is_distinguishable_from_an_unset_one(monkeypatch, corpus):
    """The exact confusion D4 exists to prevent."""
    unset = retrieval.search("espresso")
    monkeypatch.setenv("ANAM_RETRIEVAL_VECTOR_FLOOR", "99.0")
    config.reload()
    permissive = retrieval.search("espresso")

    # Same outcome — nothing rejected — but distinguishable state.
    assert unset.vector.rejected_by_floor == permissive.vector.rejected_by_floor == 0
    assert unset.vector.floor_applied is False
    assert permissive.vector.floor_applied is True


def test_the_lexical_floor_is_an_upper_bound_because_bm25_is_negative(
    monkeypatch, corpus
):
    monkeypatch.setenv("ANAM_RETRIEVAL_LEXICAL_FLOOR", "-100.0")
    config.reload()
    result = retrieval.search("espresso")
    # No real score is below -100, so an upper-bound comparison rejects all.
    assert result.lexical.floor_applied is True
    assert result.lexical.kept == 0


# --- D9: what task 1.6 can and cannot do -------------------------------------


def test_the_vector_leg_cannot_contribute_zero_while_floors_are_unset(corpus):
    """Task 1.6's second condition is structurally unreachable. Pinned.

    If this ever fails, floors have been calibrated and task 1.6 became
    testable — which is a real change, not a broken test.
    """
    for query in ("espresso", "aeronautical tolerances", "zzzz nonsense"):
        result = retrieval.search(query)
        assert result.vector.floor_applied is False
        assert result.vector.kept == result.vector.candidates
        assert result.vector.candidates > 0


def test_term_counting_is_independent_of_lexical_result_counts(corpus):
    """Open question #4: OR semantics does not break 1.6's first condition.

    1.6 counts query terms, not result counts. A one-term query is degenerate
    whether the OR leg returned ten chunks or none.
    """
    degenerate = retrieval.search("coffee")
    rich = retrieval.search("espresso grinder extraction sour bitter")

    assert len(degenerate.terms) == 1
    assert len(rich.terms) == 5
    # The degenerate query still returns plenty of lexical hits...
    assert degenerate.lexical.candidates > 0
    # ...which says nothing about its term count.
    assert len(degenerate.terms) < len(rich.terms)


# --- D5: time filter ---------------------------------------------------------


def test_no_window_means_no_filter(corpus):
    result = retrieval.search("espresso")
    assert result.time_filter_applied is False
    assert result.allowed_ids is None


def test_a_window_covering_everything_changes_nothing(corpus):
    unfiltered = retrieval.search("coffee")
    filtered = retrieval.search("coffee", since="2000-01-01T00:00:00+00:00")
    assert filtered.time_filter_applied is True
    assert filtered.chunk_ids == unfiltered.chunk_ids


def test_a_window_excluding_everything_returns_nothing(corpus):
    result = retrieval.search("coffee", until="2000-01-01T00:00:00+00:00")
    assert result.time_filter_applied is True
    assert result.allowed_ids == 0
    assert result.results == []
    assert result.lexical.ran is False
    assert result.vector.ran is False
    assert "time filter" in result.lexical.skip_reason


def test_the_window_is_a_pre_filter_on_both_legs(corpus):
    """Post-filtering would empty the result when the best matches fall outside.

    Restricting to one conversation's window must still return that
    conversation's best matches, not nothing.
    """
    espresso_id = corpus.conversations["espresso"]
    rows = db.get_conversation_chunks(espresso_id)
    created = rows[0]["created_at"]

    result = retrieval.search("coffee", since=created, until=created)
    assert result.time_filter_applied is True
    assert result.allowed_ids and result.allowed_ids > 0
    for item in result.results:
        assert item.created_at >= created or item.conversation_id == espresso_id


def test_an_empty_allow_list_is_not_confused_with_no_filter(corpus):
    """`None` means unfiltered; `[]` means nothing is in the window."""
    assert retrieval.resolve_time_window(None, None) is None
    assert retrieval.resolve_time_window(until="1999-01-01T00:00:00+00:00") == []


# --- D6: provenance is metadata only -----------------------------------------


def test_provenance_is_returned_but_never_scored(corpus):
    result = retrieval.search("espresso")
    assert result.results
    for item in result.results:
        assert item.source_type == "conversation"
        assert item.source_trust == "firsthand"


def test_changing_source_trust_does_not_change_ranking(corpus):
    """The named constraint, asserted rather than assumed."""
    before = retrieval.search("coffee grinder")
    ids_before = before.chunk_ids
    scores_before = [r.rrf_score for r in before.results]

    with db.transaction() as conn:
        conn.execute("UPDATE chunks SET source_trust = 'unverified'")

    after = retrieval.search("coffee grinder")
    assert after.chunk_ids == ids_before
    assert [r.rrf_score for r in after.results] == scores_before


# --- D7: sibling expansion ---------------------------------------------------


def _split_sibling_text(corpus) -> str:
    """Text of one piece of the corpus's split message.

    Used as a query so the piece ranks first deterministically under the
    content-derived stub embedding.
    """
    rows = db.get_conversation_chunks(corpus.conversations["notebook"])
    firsts = [r["first_message_id"] for r in rows]
    split_first = next(f for f in firsts if firsts.count(f) > 1)
    return next(r["text"] for r in rows if r["first_message_id"] == split_first)


def test_split_siblings_are_attached_not_ranked(corpus):
    """A hit on one piece surfaces the rest of that message, without taking slots."""
    notebook_id = corpus.conversations["notebook"]
    rows = db.get_conversation_chunks(notebook_id)
    firsts = [r["first_message_id"] for r in rows]
    split_first = next(f for f in firsts if firsts.count(f) > 1)
    sibling_ids = {r["id"] for r in rows if r["first_message_id"] == split_first}

    # Query with one sibling's exact text. The stubbed embedding is derived
    # from content, so that chunk's distance is 0 and it ranks first
    # deterministically — rather than depending on which chunk a hash happens
    # to favour, which is not a property worth asserting.
    target = next(r for r in rows if r["id"] in sibling_ids)
    query = target["text"]

    # top_k=1 so only one piece can rank and the other must be attached. At a
    # larger top_k on a 12-chunk corpus both pieces rank on their own, which is
    # correct behaviour and exercises nothing.
    narrow = retrieval.search(query, top_k=1)
    hit = narrow.results[0]
    assert hit.chunk_id in sibling_ids, "the split message did not rank first"
    assert hit.siblings, "the unranked sibling was not attached"
    for sib in hit.siblings:
        assert sib.first_message_id == hit.first_message_id
        assert sib.chunk_id != hit.chunk_id
        # Attached, never promoted into the ranked list.
        assert sib.chunk_id not in narrow.chunk_ids


def test_no_sibling_is_ever_lost(corpus):
    """The invariant that matters: a sibling is ranked, or attached, or absent
    because its whole message never matched — never silently dropped."""
    notebook_id = corpus.conversations["notebook"]
    rows = db.get_conversation_chunks(notebook_id)
    firsts = [r["first_message_id"] for r in rows]
    split_first = next(f for f in firsts if firsts.count(f) > 1)
    sibling_ids = {r["id"] for r in rows if r["first_message_id"] == split_first}

    for top_k in (1, 2, 3, 10):
        result = retrieval.search(_split_sibling_text(corpus), top_k=top_k)
        ranked = set(result.chunk_ids)
        attached = {s.chunk_id for r in result.results for s in r.siblings}
        if ranked & sibling_ids:
            assert sibling_ids <= (ranked | attached), (
                f"top_k={top_k}: a sibling was neither ranked nor attached"
            )
        # And never both, which would duplicate it in the prompt.
        assert not (ranked & attached)


def test_sibling_expansion_can_be_turned_off(corpus):
    text = _split_sibling_text(corpus)
    result = retrieval.search(text, top_k=1, expand_siblings=False)
    assert all(not r.siblings for r in result.results)


def test_sibling_attachment_is_bounded(monkeypatch, corpus):
    monkeypatch.setenv("ANAM_RETRIEVAL_EXPAND_SIBLINGS", "true")
    config.reload()
    monkeypatch.setattr(config, "retrieval_max_siblings_per_hit", lambda: 1)
    result = retrieval.search(_split_sibling_text(corpus), top_k=1)
    assert all(len(r.siblings) <= 1 for r in result.results)


def test_unsplit_chunks_get_no_siblings(corpus):
    result = retrieval.search("tomato leaves yellow")
    tomatoes = corpus.conversations["tomatoes"]
    for item in result.results:
        if item.conversation_id == tomatoes:
            assert item.siblings == []


# --- The corpus's own properties ---------------------------------------------


def test_the_open_conversation_is_unreachable(corpus):
    """Its trailing group is deliberately unindexed; retrieval must not find it."""
    open_id = corpus.conversations["open-thread"]
    for query in ("backup schedule", "remind me tomorrow", "scheduler"):
        result = retrieval.search(query)
        for item in result.results:
            assert item.conversation_id != open_id


def test_results_carry_full_score_provenance(corpus):
    """"Why did this rank here" must be answerable without re-running."""
    result = retrieval.search("espresso coffee")
    assert result.rrf_k == 60
    assert result.terms == ["espresso", "coffee"]
    for item in result.results:
        assert item.legs
        if "lexical" in item.legs:
            assert item.bm25_rank is not None and item.bm25_score is not None
        if "vector" in item.legs:
            assert item.vector_rank is not None and item.vector_distance is not None


def test_top_k_bounds_the_result_count(corpus):
    result = retrieval.search("coffee", top_k=2)
    assert len(result) <= 2


def test_an_empty_store_returns_nothing_without_raising(isolated_data_dir):
    db.init_databases()
    result = retrieval.search("anything at all")
    assert result.results == []


def test_a_vector_leg_failure_degrades_rather_than_failing_retrieval(
    corpus, monkeypatch
):
    """One leg down must not take retrieval with it."""
    def boom(text, **kwargs):
        raise ollama.OllamaUnreachableError("no server")

    monkeypatch.setattr(retrieval.ollama, "embed", boom)
    result = retrieval.search("espresso")

    assert result.vector.ran is False
    assert "could not be embedded" in result.vector.skip_reason
    assert result.results, "the lexical leg should still have produced results"
