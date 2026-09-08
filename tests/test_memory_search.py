"""The `memory_search` tool. Task 2.3.

**Retrieval is real in every test here.** Real chunks written through the real
chunking pipeline, real FTS5, real ChromaDB, real RRF fusion, real rendering —
what varies is only whether the *embedding* comes from Ollama. A fully mocked
test would prove the wiring and nothing about the tool working; this file's
whole point is that the thing it wraps actually ran.

Two levels, deliberately:

* the default fixture supplies deterministic embeddings so the ranking is
  predictable and the suite runs without Ollama — every other layer is genuine;
* `test_a_live_search_returns_real_records` uses **real embeddings against real
  Ollama**, and skips rather than fails when it is not reachable. A skip is
  visible in pytest output where a mock would look like a pass.
"""

from __future__ import annotations

import hashlib

import pytest

from program.engine import ollama, prompt
from program.memory import chunking, db, retrieval, vectors
from program.tools import memory_search, registry
from program.tools.memory_search import MEMORY_SEARCH
from program.tools.registry import ToolOutcome, ToolRegistry

live_only = pytest.mark.skipif(
    not ollama.is_available(),
    reason="Ollama is not reachable; live-call tests skipped",
)

ESPRESSO = [
    ("user", "My espresso is coming out sour and it runs too fast through the "
             "basket. What should I change first?"),
    ("assistant", "Sour with a fast shot points at under-extraction, so grind "
                  "finer before you change anything else. That raises "
                  "resistance and pulls more out of the puck."),
]

FAN = [
    ("user", "The Mac mini's fan has been running hard all afternoon. Is that "
             "something to worry about?"),
    ("assistant", "Sustained fan noise usually means something is holding the "
                  "GPU busy rather than a hardware fault. A model kept loaded "
                  "in memory will do it."),
]


def _deterministic_embedding(text: str, **kwargs) -> list[float]:
    """A stable vector per text. Not Ollama, but a real 768-wide vector.

    Derived from the text so that identical text embeds identically and
    different text does not — enough for Chroma to store, index and return
    neighbours for real, which is the part under test.
    """
    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[i % len(digest)] / 255.0) for i in range(768)]


def _write(user_id: str, turns) -> str:
    conversation_id = db.start_conversation(user_id)
    for role, content in turns:
        db.save_message(conversation_id, user_id, role, content)
    db.end_conversation(conversation_id)
    chunking.finalise_conversation(conversation_id)
    return conversation_id


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    """A real store with real chunks, indexed for real in FTS5 and Chroma."""
    monkeypatch.setattr(chunking.ollama, "embed", _deterministic_embedding)
    monkeypatch.setattr(retrieval.ollama, "embed", _deterministic_embedding)
    db.init_databases()
    user_id = db.create_user("Lyle", role="admin")
    _write(user_id, ESPRESSO)
    _write(user_id, FAN)
    return user_id


# --- The tool is registered, and is the only one -----------------------------


def test_memory_search_is_registered_in_the_default_registry():
    registry.reset_default_registry()
    assert "memory_search" in registry.default_registry().names
    assert registry.default_registry().get("memory_search") is MEMORY_SEARCH


def test_only_query_is_exposed_as_an_argument():
    """top_k, expand_siblings and the floors are retrieval's tuned internals.

    A model that could raise top_k could spend the context budget on one search;
    a model that could lower a floor could ask for weak matches it would then
    treat as memories.
    """
    assert MEMORY_SEARCH.required == ("query",)
    assert set(MEMORY_SEARCH.properties) == {"query"}
    for internal in ("top_k", "expand_siblings", "vector_distance_floor",
                     "lexical_score_floor", "since", "until"):
        assert internal not in MEMORY_SEARCH.properties


def test_the_declared_timeout_sits_above_what_retrieval_enforces_internally():
    """45s is derived, not chosen: three SQLite connections x busy_timeout.

    A query-only search opens three connections — the lexical leg, loading the
    ranked rows, attaching siblings — each able to wait `busy_timeout_seconds`
    for a lock. A shorter tool timeout would abandon the call before SQLite gave
    up, replacing "database is locked" with an uninformative timeout.
    """
    from program import config

    internal_ceiling = 3 * config.db_busy_timeout_seconds()
    assert internal_ceiling == 30
    assert MEMORY_SEARCH.resolved_timeout() == 45.0
    assert MEMORY_SEARCH.resolved_timeout() > internal_ceiling
    # And it stays reachable: a timeout above the turn's whole tool budget would
    # be clipped on every call and never mean anything.
    assert MEMORY_SEARCH.resolved_timeout() < config.agent_tool_budget_seconds()


# --- Real retrieval through real dispatch ------------------------------------


def test_a_search_returns_the_matching_record_through_dispatch(store):
    """End to end: registry dispatch -> retrieval -> rendered text."""
    result = ToolRegistry([MEMORY_SEARCH]).dispatch(
        "memory_search", {"query": "sour espresso grind"}
    )

    assert result.outcome is ToolOutcome.OK
    assert result.ran is True
    assert "grind finer" in result.value.lower()
    assert "under-extraction" in result.value.lower()


def test_the_rendering_is_the_passive_retrieval_renderer_not_a_second_format(
    store,
):
    """Same chunk, same text, whether retrieved passively or by tool call."""
    query = "sour espresso grind"
    passive = prompt.render_retrieved(retrieval.search(query))
    through_tool = memory_search.search_memory(query)

    assert passive, "the fixture produced no retrievable records"
    assert through_tool == passive
    assert "records retrieved from earlier conversations" in through_tool
    assert "record 1 · " in through_tool, "timestamps are rendered at presentation"


def test_no_matches_is_stated_rather_than_returned_empty(store, monkeypatch):
    """A blank tool result is an invitation to fabricate; this is a finding."""
    monkeypatch.setattr(
        retrieval, "search", lambda query: retrieval.RetrievalResult(query=query)
    )

    text = memory_search.search_memory("something never discussed")

    assert text.startswith(memory_search.NO_MATCHES)
    assert text.strip()


def test_a_degraded_search_says_which_leg_did_not_run(store, monkeypatch):
    """Nothing found with the vector leg down is not the same claim as
    nothing found."""

    def embedder_down(text, **kwargs):
        raise ollama.OllamaUnreachable("nothing is listening")

    monkeypatch.setattr(retrieval.ollama, "embed", embedder_down)

    text = memory_search.search_memory("sour espresso grind")

    assert "This search was incomplete" in text
    assert "vector" in text
    # The lexical leg still answered — one leg down does not take the tool down.
    assert "grind finer" in text.lower()


def test_an_empty_query_is_a_tool_error_not_an_arbitrary_top_match(store):
    """Searching for nothing would return the corpus's arbitrary best matches
    and read like an answer."""
    result = ToolRegistry([MEMORY_SEARCH]).dispatch("memory_search", {"query": "   "})

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert "empty" in (result.error or "")


def test_a_missing_query_is_invalid_arguments_before_anything_runs(store):
    result = ToolRegistry([MEMORY_SEARCH]).dispatch("memory_search", {})

    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert result.ran is False


# --- Cross-user disclosure: decision #20, checked not re-decided -------------


def test_results_are_not_filtered_by_who_is_asking(isolated_data_dir, monkeypatch):
    """`NOW.md` #20: retrieval is not scoped by actor, and this tool adds no
    scoping of its own. The judgment sits at disclosure, not retrieval."""
    monkeypatch.setattr(chunking.ollama, "embed", _deterministic_embedding)
    monkeypatch.setattr(retrieval.ollama, "embed", _deterministic_embedding)
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    _write(lyle, ESPRESSO)
    _write(jodie, FAN)

    text = memory_search.search_memory("mac mini fan noise")

    # Jodie's conversation surfaces from a search with no actor involved at all.
    assert "fan noise" in text.lower()
    # And the tool takes no actor: there is nothing to scope by, by design.
    assert "actor" not in MEMORY_SEARCH.properties
    assert MEMORY_SEARCH.handler.__code__.co_varnames[:1] == ("query",)


# --- Live: real embeddings, real Ollama --------------------------------------


@live_only
def test_a_live_search_returns_real_records(isolated_data_dir):
    """No fake embedding anywhere: the vector leg genuinely ran.

    This is the test that separates "the wiring is right" from "the tool
    works" — everything above substitutes a deterministic vector, and this one
    does not.
    """
    vectors.reset_vector_store()
    db.init_databases()
    user_id = db.create_user("Lyle", role="admin")
    _write(user_id, ESPRESSO)
    _write(user_id, FAN)

    result = ToolRegistry([MEMORY_SEARCH]).dispatch(
        "memory_search", {"query": "why does my coffee taste sour"}
    )

    assert result.outcome is ToolOutcome.OK
    assert "grind finer" in result.value.lower()
    assert "This search was incomplete" not in result.value, "a leg was skipped"

    # The vector leg specifically — a lexical-only match would pass the
    # assertion above on the word "sour" alone.
    direct = retrieval.search("why does my coffee taste sour")
    assert direct.vector.ran and direct.vector.kept > 0
    assert any(chunk.vector_rank is not None for chunk in direct.results)
