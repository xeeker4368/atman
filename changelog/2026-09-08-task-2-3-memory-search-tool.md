# 2026-09-08 — Task 2.3: the `memory_search` tool

**Tier 1 · Sonnet · not gated.**

## Summary

The first real tool. `program/tools/memory_search.py` wraps
`retrieval.search()`, and `program/tools/catalog.py` — new — is where the tool
list now lives. `TOOLS` is no longer empty.

## Files changed

Created: `program/tools/memory_search.py`, `program/tools/catalog.py`,
`tests/test_memory_search.py` (11).
Modified: `program/tools/registry.py` (the catalogue moved out; the docstring
follows it), `tests/test_tools.py` (the two empty-registry guards became
catalogue guards, +1 import-order test), `BUILT.md`.

No schema change, no config change, no new dependency. 492 tests pass (was 480);
`ruff check` clean.

## The timeout: 45s, derived rather than picked

**The brief's premise did not survive measurement, so the number is not built on
it.** The concern was that a cold embedding call makes the vector leg slow, and
that every other ceiling in this build treats a cold Ollama call as up to 300s.
Measured 2026-09-08:

| | |
|---|---|
| embedding cold, with `gemma4:26b` resident at 100% GPU | **0.31 s** |
| embedding warm | 0.03 s |
| full `retrieval.search()`, first call (Chroma construction) | 0.66 s |
| full `retrieval.search()`, warm | 0.04 s |

The 19.1 s cold load and the 300 s ceiling are the **26B chat model's**.
`nomic-embed-text` is ~137M parameters; it cold-loads in a third of a second
even with 17 GB of chat model already resident. A cold embedding call is not
what makes this tool slow.

**Lock contention is.** A query-only search opens three SQLite connections — the
lexical leg, loading the ranked rows, attaching siblings — and each can wait up
to `database.busy_timeout_seconds` (10 s) for a lock, so **the code's own
ceiling is 30 s**. That gives the rule the value follows:

> A tool's timeout should sit **above the longest bound the code inside it
> enforces for itself**, so that a real failure surfaces as its own specific
> error rather than as an uninformative timeout.

Below 30 s, a locked database would be abandoned by *our* timeout before SQLite
gave up, turning a precise "database is locked" into "timed out, outcome
unknown" — the least useful outcome the registry can produce, and the one whose
whole point is that it means *we do not know*. 45 s clears that with margin.

It also stays **under** `agent.tool_budget_seconds` (120), so it is reachable:
the loop hands each dispatch `min(the tool's timeout, what remains)`, and a
declared value above the turn budget would be clipped on every call and never
mean anything — the same "protection that exists only on paper" that task 2.1
refused. A test pins both directions: `> 3 × busy_timeout` and `< tool budget`.

## Rendering: `render_retrieved()` already existed, and is reused

Confirmed by reading it, not assumed. `program/engine/prompt.py` has
`render_retrieved()` (with `_render_chunk()` under it), used for passive
retrieval context in the system prompt. It carries two things that are not
decoration: the header stating these are stored records rather than the current
conversation, and the per-chunk timestamp that task 1.3 deliberately kept out of
chunk *text* so date strings would not enter either index.

`memory_search` calls it. **A test asserts the tool's output is byte-identical
to the passive rendering** for the same query, so the same chunk cannot come to
read one way when retrieved passively and another when retrieved by tool call.

Two things are added around it, both because they are *states* rather than
formats:

* **Empty results get a sentence.** `render_retrieved()` returns `""` for no
  matches — correct in a system prompt, where the section simply does not
  appear, and wrong in a tool result, where a blank return is an invitation to
  fabricate. "No stored records matched" is a finding.
* **A degraded search says which leg did not run.** Keyword-only results make "I
  searched my memory" only partly true, and it matters most when *nothing* was
  found: nothing found with the vector leg down is a different claim from
  nothing found.

**Known limitation, flagged for task 3.1:** the handler returns the rendered
string, so `ToolResult.value` — and therefore the trace — holds the exact text
the model was shown rather than a list of chunk ids. That supports checking a
claim against the retrieved text, but not an id-level lookup. If 3.1 wants ids,
the seam is a small protocol in `loop.render_tool_result` (a value that carries
its own rendered text). Not built speculatively — 3.1 should define what it
needs first.

## Cross-user disclosure: consistency check, not a new decision

**Confirmed: `memory_search` does not filter by the calling actor.** The handler
takes `query` and nothing else — there is no actor to filter by, which a test
asserts against the handler's own signature. `NOW.md` decision #20 is already
settled: retrieval is not scoped by who is asking, `user_id` rides along as
metadata, and the judgment sits at **disclosure** — whether to say a thing once
it has surfaced.

Adding a filter here would not have been a small safety improvement. It would
have answered an open question in the opposite direction from the one settled,
in the layer specifically kept free of it.

**Demonstrated live rather than asserted:** in the run below, Lyle asked about
pour-over gear and the record returned was **Jodie's conversation**, unfiltered.
The tool description tells the model so ("What is searched is not limited to the
person you are speaking with now"), matching what `soul.md` already states —
a tool implying a boundary the system does not enforce would be a false
self-description, which is the thing the fabrication gate treats as ground truth.

## Parameters: `query` only

`top_k`, `expand_siblings` and the relevance floors are not exposed. They are
retrieval's tuned internals with a calibration story behind them, and the model
is not part of that story: one that could raise `top_k` could spend the turn's
whole context budget on a single search, and one that could lower a floor could
ask for weak matches it would then have to treat as memories.

**One flagged as its own judgment, not taken silently:** `since` / `until` are a
different case. They are a query capability rather than a tuned internal — task
1.5 built the structured time filter *because* task 1.3 stripped date strings
out of both indexes, and BUILD_PLAN records answering "what did we discuss last
Tuesday" as the obligation that replaced lexical date matching. With no exposed
parameter the model cannot reach it, so that capability is currently
unreachable. Adding two optional ISO-8601 string parameters would be small.
Left to the reviewer.

## A circular import, found by running it

`memory_search.py` imports `Tool` from `registry.py`, so `registry.py` cannot
import tool modules at module scope. The first attempt put the import at the
*bottom* of `registry.py`, which works only when the registry is imported first
— importing `program.tools.memory_search` first raised `ImportError: cannot
import name 'MEMORY_SEARCH' from partially initialized module`.

Fixed by splitting the layers rather than by hiding the cycle deeper:

* `registry.py` — the mechanism. Knows what a tool is and how to dispatch one.
  Imports no tool.
* `catalog.py` — the contents. Imports every tool and lists them.
  `default_registry()` imports it at call time, when the direction is
  unambiguous.

Task 2.1's actual requirement was never about the tuple's address: *"the full
set must be greppable from one place rather than depending on which modules
happened to be imported."* That still holds, and now the file's name says it.
**A test spawns a subprocess for each of the three import orders** and asserts
the registry resolves, so this cannot regress into working-by-import-order.

## Tests: retrieval is real in all of them

Every test writes real chunks through the real chunking pipeline into real FTS5
and real ChromaDB, and runs real RRF fusion and real rendering. What varies is
only whether the embedding comes from Ollama:

* the default fixture supplies a deterministic 768-wide vector derived from the
  text — stable, distinct per text, and genuinely stored and queried by Chroma —
  so ranking is predictable and the suite runs without Ollama;
* **`test_a_live_search_returns_real_records` uses real embeddings against real
  Ollama** and skips rather than fails when it is unreachable. It asserts the
  vector leg specifically ran and contributed (`vector.kept > 0`, a result
  carrying a `vector_rank`), because a lexical-only match would have passed a
  text assertion on its own. It **ran and passed** here, not skipped.

## Verified live, end to end

Both runs against a real seeded store (8 conversations, 53 messages, 12 chunks)
with real embeddings and the real model:

1. **The model answered without calling the tool** — 1 iteration, 13.6 s,
   correct answer. Passive per-turn retrieval had already placed the pour-over
   record in the system prompt, so it had the answer before deciding whether to
   search. **Worth flagging as an observation, not a defect:** with retrieval
   running passively every turn, `memory_search` is largely redundant *for the
   user's current message* and earns its keep when the model needs a query
   different from what the user just said. Its schema costs context on every
   tool-bearing call. Whether that trade is right is a real question, and it is
   the reviewer's.
2. **The tool path in isolation** (passive retrieval disabled for that run only,
   in the throwaway script — no product change): 2 iterations, 13.2 s. The model
   formulated its own query, `memory_search({'query': 'pour-over coffee gear'})`,
   dispatch ran real retrieval in **0.692 s of the 45 s allowed**, `ran=True`,
   and the answer came from the rendered records — including, as noted above,
   another user's conversation.
