# 2026-09-08 — Task 2.4: the `web_search` tool (and the SearXNG instance behind it)

**Tier 1 · Sonnet · not gated.** Part 1 (feasibility and response shape) was
reported before any code was written; this is part 2, built against what was
measured there.

## Files changed

Created: `program/tools/web_search.py`, `tests/test_web_search.py` (25),
`tests/fixtures/searxng_response.json`, `ops/searxng/docker-compose.yml`.
Modified: `program/tools/catalog.py`, `program/config.py`,
`config/defaults.toml`, `tests/test_tools.py` (the catalogue guards), `BUILT.md`.

No schema change, no new dependency (`requests` was already in use). 517 tests
pass (was 492); `ruff check` clean.

## The three judgment calls

### 1. `timeout_seconds = 15`

Same rule as `memory_search`'s 45: **sit above the longest bound the code inside
enforces for itself**, so a real failure surfaces as its own specific error
rather than as `TIMEOUT`, whose whole meaning is *we do not know what happened*.

* SearXNG's own `outgoing.request_timeout` is **3.0 s per upstream engine**, and
  engines are queried in parallel — so its internal work is bounded at ~3 s
  regardless of how many engines are enabled. Read from its live settings, not
  assumed.
* **Measured** over five real queries: 0.5 s, 0.7 s, 0.9 s round trip.
* `searxng.timeout_seconds = 10` — the HTTP timeout — is ~3x SearXNG's internal
  ceiling and ~11x the measured worst case, covering a container paging back in
  after idle and a growing engine list.
* The tool's 15 s sits above that 10 s, so a wedged instance reports *"SearXNG
  did not respond within 10s"* — which names what to check — instead of an opaque
  tool timeout.

It stays well under `agent.tool_budget_seconds` (120), so a `web_search` (15) and
a `memory_search` (45) and a retry all fit inside one turn's tool budget.

### 2. `searxng.max_results = 6`

**Derived from the context budget, not from taste.** It is the largest N whose
worst case provably fits under `agent.max_tool_result_chars` (4000), which is
where the agent loop truncates a tool result.

Measured from the real 27-result response:

| field | median | max |
|---|---|---|
| title | 57 | 109 |
| url | 70 | 116 |
| content | 214 | 444 (and **empty** on 1 of 27) |

With content rendered at most 300 chars, a worst-case result is ~537:

* **N=6** → 3,222 + header and notes ~430 = **~3,652** — fits
* N=7 → 3,759 + ~430 = ~4,189 — does not

Raw responses carry 23–27 results. Rendering them all runs ~10,000 characters and
would be cut mid-result by the loop, which is a worse outcome than choosing the
cap deliberately. The live run rendered **2,893 characters**, comfortably inside.

Titles clip at 120 and snippets at 300, both saying so with an ellipsis. **URLs
are never clipped** — a shortened URL is a URL that does not resolve, and the
model may quote it; a long link beats a wrong one, and the loop's cap is the
backstop for a pathological one. A test pins that.

### 3. One parameter: `query`

Following `memory_search`'s precedent, and for the same reason: a result-count
override is a tuning knob, and a model that raised it would push the render past
the loop's 4,000-character cap and get back a *truncated* result — worse output
from asking for more. `searxng.max_results` is derived from a budget the model
cannot see, so it is not the model's to set.

**Flagged rather than decided:** `categories` and `time_range` are the same shape
as `memory_search`'s `since`/`until` — real query capabilities rather than tuned
internals. `time_range` would let the model ask for recent-only results, which it
currently cannot. Left to the reviewer.

## Built against the measured shape

Each hazard from part 1, handled and pinned:

* **Results are not sorted.** `sort_results()` does it. A result with an unusable
  score sorts last rather than taking the search down.
* **Three shapes, not two.** A missing `results` key raises `WebSearchError`
  (→ `TOOL_ERROR`); an empty `results` list returns "the search ran and returned
  no results". A test asserts they produce different outcomes.
* **`content` can be empty.** Rendering emits the snippet line only when there is
  one; a test checks the real empty-content result leaves no dangling indented
  line.
* **`number_of_results` is never read.** A test greps the module for it *and*
  renders three payloads — absent, null, and a lying 99999 — asserting identical
  output.
* **`unresponsive_engines`** produces a degradation note naming each engine and
  its reason, the `memory_search` pattern.

## Web text is framed as content, not instruction

This is the first tool that puts **untrusted external text** into the prompt. The
header says these are pages written by other people, quoted as content to read
rather than instructions to follow.

**That framing is not a defence.** A header does not solve prompt injection and
nothing here claims it does; it is recorded as a known exposure rather than left
unsaid. Real handling belongs with the fabrication/provenance work, where web
content gets its own `source_type` and trust level.

## Tests: real fixture, real instance

The fixture is a **verbatim captured response** from the live instance — 27
results for "espresso grind size extraction". A guard test asserts it still
carries every hazard it was captured for, so an edited fixture cannot quietly
make the other tests prove less.

The normal run touches no network: these engines CAPTCHA unpredictably, and a
suite that fails because DuckDuckGo felt suspicious tests nothing useful. Two
live tests hit `127.0.0.1:8080` and skip when it is down. **Both ran and passed.**

`test_the_live_instance_is_bound_to_loopback_only` deserves a note: it initially
**skipped**, because `gethostbyname(gethostname())` returns `127.x` on this
machine — a guard that silently skips exactly when it matters. It now finds the
real interface address via a UDP socket toward TEST-NET-1 (no packets sent).
**Proven to bite:** rebinding SearXNG to `0.0.0.0` and re-running made it fail;
the binding was then restored and re-verified.

## Verified live, end to end

The model wrote its own query:
`web_search({"query": "SQLite FTS5 bm25() function return value"})` — dispatched
in **0.67 s of the 15 s allowed**, 2,893 characters rendered, 2 iterations, 9.5 s
total. The answer was correct, including the negative-score convention this
project's own retrieval work measured independently.

## `ops/searxng/docker-compose.yml`, and where the instance actually runs

Checked in with the loopback binding and the image **pinned to
`2026.9.8-3fdc6d753`** — verified to exist in the registry and to be
byte-identical to the running `latest` (same image ID after pulling both), rather
than trusted from the version label.

Pinning is the direct lesson of part 1: the instance was found on an unpinned
`latest` **from 2026-05-21, 3.5 months stale, returning zero results for every
query** — HTTP 200, valid JSON, `results: []`, all three engines CAPTCHA'd or
suspended. `latest` is what let it rot unnoticed. This project pins `num_ctx` and
model names for the same reason.

**The live containers still run from `/Volumes/Dock Storage/searxng/`**, where
they predate this repo; they were not migrated mid-task. The checked-in file is
the record of how it should run, and both it and `BUILT.md` say plainly that the
two locations can drift. Migrating is a small separate job.

`core-config/settings.yml` is not checked in — SearXNG generates ~2,700 lines of
upstream defaults on first run. Two values in it are load-bearing and are **not**
defaults, so both are documented in the compose file: `search.formats` must
include `json`, and `server.limiter` must be `false` or an automated local caller
gets 429s.
