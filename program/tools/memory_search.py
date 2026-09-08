"""The ``memory_search`` tool: hybrid retrieval, model-callable.

A thin wrapper over ``program.memory.retrieval.search()``. It adds no ranking,
no filtering and no second retrieval path — task 1.5 owns all of that (D1–D9),
and a tool that quietly re-tuned it would make "why did this rank here"
answerable in two places with different answers.

One parameter, deliberately
---------------------------
``query`` only. ``top_k``, ``expand_siblings`` and the relevance floors are
retrieval's own tuned internals, not per-call knobs: a model that could raise
``top_k`` could spend the turn's whole context budget on one search, and a model
that could lower a floor could ask for weak matches it would then have to treat
as memories. They are settings with a calibration story behind them, and the
model is not part of that story.

*Flagged as its own judgment, not decided here:* ``since``/``until`` are a
different case. They are a query capability rather than a tuned internal — task
1.5 built the structured time filter specifically because task 1.3 stripped
date strings out of the indexes, and BUILD_PLAN records answering "what did we
discuss last Tuesday" as the obligation that replaced lexical date matching.
With no exposed parameter, the model cannot reach it. Adding them is a real
scope decision and is left to the reviewer rather than taken silently.

Cross-user disclosure is not handled here, on purpose
-----------------------------------------------------
This tool does **not** filter by the calling actor. That is ``NOW.md`` decision
#20, already made: retrieval is not scoped by who is asking, ``user_id`` rides
along as metadata, and the judgment sits at the point of **disclosure** —
whether to say a thing once it has surfaced — exercised as discretion each time
rather than as a rule applied for it.

Adding a filter here would not be a small safety improvement; it would silently
answer an open question in the opposite direction from the one that was settled,
in the layer specifically kept free of it. The tool's description says so to the
model, because ``soul.md`` already states the mechanism honestly and a tool
implying a boundary the system does not enforce would be a false
self-description — the exact thing the fabrication gate treats as ground truth.

Rendering reuses the passive-retrieval renderer
-----------------------------------------------
``prompt.render_retrieved()`` already turns a ``RetrievalResult`` into text for
the system prompt, including the timestamps task 1.3 stripped from chunk text
and the header stating these are stored records rather than the current
conversation. This tool calls it. A second format would mean the same chunk
reads one way when retrieved passively and another way when retrieved by tool
call, for no reason beyond where it entered.

Two things are added around it, both because they are *states*, not formats:

* **An empty result gets an explicit sentence.** ``render_retrieved()`` returns
  ``""`` for no matches, which is right in a system prompt — the section simply
  does not appear — and wrong here. A blank tool result is an invitation to
  fabricate; "nothing matched" is a finding.
* **A degraded search says so.** If a leg did not run, the results are
  keyword-only or vector-only, and "I searched my memory" is then only partly
  true. That matters most on *no* results: nothing found with the vector leg
  down is not the same claim as nothing found.
"""

from __future__ import annotations

import logging

from program.engine import prompt
from program.memory import retrieval
from program.memory.retrieval import RetrievalResult
from program.tools.registry import Tool

logger = logging.getLogger(__name__)

#: What the model is told when a search matched nothing. A sentence rather than
#: an empty string — see the module docstring.
NO_MATCHES = (
    "No stored records matched that search. Nothing was found to have been "
    "said before about this."
)


def _degradation_note(result: RetrievalResult) -> str:
    """A line naming any leg that did not run, or ``""`` when both did."""
    down = [leg for leg in (result.lexical, result.vector) if not leg.ran]
    if not down:
        return ""
    described = "; ".join(
        f"{leg.name} ({leg.skip_reason or 'no reason recorded'})" for leg in down
    )
    return (
        f"\n\n[This search was incomplete: the {described} leg did not run, so "
        f"these results come from the other leg alone.]"
    )


def render_search_result(result: RetrievalResult) -> str:
    """A retrieval result as the text the model reads."""
    body = prompt.render_retrieved(result) or NO_MATCHES
    return body + _degradation_note(result)


def search_memory(query: str) -> str:
    """Handler for ``memory_search``.

    Raises on a blank query rather than searching for nothing. The registry
    validates that the argument is present and is a string, but not that it says
    anything; an empty search would return the corpus's arbitrary top matches and
    read like an answer. Raising surfaces as ``TOOL_ERROR`` with the reason,
    which the loop feeds back so the model can call it properly.
    """
    text = (query or "").strip()
    if not text:
        raise ValueError(
            "query was empty. memory_search needs something to search for — "
            "pass the subject you are trying to recall."
        )

    result = retrieval.search(text)
    logger.info(
        "memory_search(%r): %d result(s), lexical ran=%s vector ran=%s",
        text[:80],
        len(result.results),
        result.lexical.ran,
        result.vector.ran,
    )
    return render_search_result(result)


MEMORY_SEARCH = Tool(
    name="memory_search",
    description=(
        "Search stored records of earlier conversations, by meaning and by "
        "keyword together. Returns the closest-matching records with the dates "
        "they were stored. These are records of things said before, not part of "
        "the conversation happening now. What is searched is not limited to the "
        "person you are speaking with now."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What to search for. Plain words describing the subject; "
                    "this is matched both semantically and as keywords, so a "
                    "short natural phrase works better than a single word."
                ),
            }
        },
        "required": ["query"],
    },
    handler=search_memory,
    # JUDGMENT VALUE. Derived from the longest bound the code inside this tool
    # enforces for itself, not from a guess about how slow retrieval feels.
    #
    # A query-only search opens three SQLite connections (the lexical leg,
    # loading the ranked rows, attaching siblings) and each can wait up to
    # database.busy_timeout_seconds = 10s for a lock, so the code's own ceiling
    # is 30s. A tool timeout below that would abandon the call *before* SQLite
    # gives up, replacing a specific "database is locked" with an uninformative
    # timeout — so the rule is: sit above what the inside enforces.
    #
    # MEASURED 2026-09-08, so the number is not built on the vector leg being
    # slow — it isn't:
    #   embedding cold, with gemma4:26b resident at 100% GPU   0.31 s
    #   embedding warm                                         0.03 s
    #   full retrieval.search(), first call (Chroma construct)  0.66 s
    #   full retrieval.search(), warm                           0.04 s
    # The 19.1s cold-load and 300s ceiling elsewhere in this build are the 26B
    # CHAT model's. nomic-embed-text is ~137M parameters and loads in a third of
    # a second, so a cold embedding call is not what makes this tool slow. Lock
    # contention is.
    #
    # 45s is the 30s of self-enforced lock waiting plus margin, and it stays
    # under agent.tool_budget_seconds (120) so it is actually reachable rather
    # than clipped to something smaller on every call.
    timeout_seconds=45.0,
)
