# 2026-09-08 — Task 2.2: agent loop

**Tier 1 · Sonnet · not gated — with one part deliberately held back.**

## Summary

The iterate-and-dispatch turn: call the model, dispatch whatever tool calls come
back, feed the results in, repeat until an answer or the iteration limit. Plus
the first authenticated route in the application, `POST /api/chat`, and the
per-tool timeout enforcement task 2.1 explicitly deferred here.

**Held back pending review: the idle-close re-derivation.**
`conversations.in_flight_grace_minutes` and
`config.IN_FLIGHT_GRACE_FLOOR_MINUTES` are **unchanged** at 30 and 20. The
arithmetic that should replace them was reported for review before landing —
those two values are a correctness constraint whose spec was Tier 2 when
idle-close was built, and the numbers this task produces are what that spec was
waiting on. Everything else here is built and verified.

## Files changed

Created: `program/engine/loop.py`, `program/engine/turn.py`,
`program/api/routes/chat.py`, `tests/test_loop.py` (17), `tests/test_turn.py`
(14), `tests/test_chat_route.py` (10).
Modified: `program/tools/registry.py`, `program/engine/history.py`,
`program/config.py`, `config/defaults.toml`, `program/api/app.py`,
`tests/test_tools.py` (+9), `BUILT.md`.

No schema change, no migration, no new dependency. 477 tests pass (was 426);
`ruff check` clean.

## Three obligations from BUILD_PLAN's row

**(a) A real `Actor`, not `Actor.operator()`.** The route depends on
`require_actor`, so the actor reaching `turn.handle_user_message()` was built by
`db.get_actor()` from a verified session token. `Actor.operator()` appears
nowhere in this task's code. A test posts a body containing another user's
`user_id` and asserts the messages are still attributed to the token's owner —
the body has no such field, and proving that is cheaper than assuming it.

**(b) The user's message is persisted before generation begins.** Tested from
*inside* the model call rather than by reading the order of two lines: the fake
model, mid-generation, queries
`db.get_open_conversations_with_activity()` and asserts the conversation reports
`last_role == "user"` and therefore selects `in_flight_grace_minutes`. That is
the exact property idle-close depends on, checked against idle-close's own
query.

The visible consequence — a failed turn leaves a user message with no reply — is
also tested. It is an accurate record, not debris, and the grace window is what
covers it.

**(c) The idle-close re-derivation.** Reported, not landed. See above.

## Per-tool timeouts, which is what 2.1 deferred

`Tool` gained `timeout_seconds`, and `dispatch()` now runs the handler in a
daemon thread it waits on for exactly that long. `None` means *take
`tools.default_timeout_seconds`*; there is deliberately **no way to declare an
unbounded tool**, because an unbounded tool makes the turn unbounded and the
idle-close floor assumes a bounded turn. A non-positive value raises at
construction.

**What the timeout bounds is how long the turn waits, not how long the tool
runs.** Python cannot safely kill a running thread. A handler that overruns
keeps going; the daemon flag stops it holding up interpreter exit. Subprocess
isolation would make the kill real and is not worth its cost for tools that need
in-process access to the database and the vector store.

That limitation is why **`TIMEOUT` is its own outcome rather than a flavour of
`TOOL_ERROR`**, and why `ToolResult.ran` is **True** for it: the handler was
entered and may have completed after the wait was abandoned. A timed-out
`web_fetch` may well have fetched. For task 3.1 those are different claims —
"this did not happen" and "whether this happened cannot be determined" — and
collapsing them would make the second unsayable.

`SKIPPED` is the opposite state, produced by the loop rather than by dispatch:
the turn's aggregate tool budget was spent, so nothing was started. It exists so
that **every tool message the model receives has a matching trace entry**. A
result with no record is precisely the asymmetry the fabrication gate cannot
reason over.

`except Exception` became a `BaseException` capture in the worker thread that
re-raises non-`Exception`s in the calling thread — otherwise `KeyboardInterrupt`,
`SystemExit` and the suite's `StoreIsolationViolation` would die with the worker
and read here as a timeout. The existing test that pins this still passes.

## The loop always ends in an answer

Iteration `L` is sent with **no tools attached**. The model cannot ask for
another round because it is not offered one, so a turn cannot terminate on an
unanswered tool call. `L` therefore counts model calls and the number of tool
rounds is `L - 1`.

The alternative — stop after `L` tool-bearing calls and return whatever text
accompanied the last one — ends the turn mid-thought with no answer and no
explanation. This costs one model call in the worst case.

If the model emits tool calls anyway on that final tool-free iteration, they are
recorded as `SKIPPED` rather than dropped: it asked for something and nothing
ran, and a trace that omitted the request could not say so later.

## Two bounds, because one is not enough

`agent.max_iterations` bounds model calls. `agent.tool_budget_seconds` bounds
the wall clock spent waiting on tools **in aggregate across the turn** — a
single iteration may emit several calls, so a per-tool timeout alone leaves the
turn unbounded. Each dispatch gets `min(the tool's own timeout, what remains)`.
Together they make the maximum turn duration finite, which is the only reason
the in-flight grace floor can be derived at all.

## The window is re-planned every iteration

Tool results are appended and then the whole prompt is re-assembled, so they are
priced against the same token budget as everything else and old history is
evicted to make room for them.

The obvious shortcut — budget once, then append freely — spends the turn's slack
silently and hands the overflow to the model server, which drops the oldest
content without reporting it. That is the exact failure `history.py` exists to
prevent, and it would have been reintroduced here. A test with `num_ctx` at 4096
asserts history is windowed and the newest content survives.

This needed two small extensions to `history.py`, both closing gaps it already
recorded against itself:

* `_normalise()` now **preserves `tool_calls` and `tool_name` when present**, and
  only then. Stripping an assistant message's tool calls while keeping the tool
  result that answered it leaves the model a reply to a question it cannot see
  it asked.
* `estimate_message_tokens()` now **prices** them, by serialising the calls to
  the JSON that actually gets sent. Charging them zero is the under-count
  direction, which is the one that overflows. `history.py`'s note that tool
  traces "are also not priced yet" no longer applies — though they are the
  densest text this system sends, so the known 4.0-chars/token gap bites them
  hardest.

## Degrade or abort

On the criterion `prompt.py` and `retrieval.py` already record: *abort when a
failure could corrupt something or when retrying is free; degrade when nothing
can be corrupted and a person is waiting.*

* A **failed, unknown, malformed, timed-out or skipped tool** is fed back to the
  model, which can answer around it. Tested for each.
* A **failed retrieval** degrades to no retrieved records, with a warning.
  `retrieval.search()` already survives one leg being down; this covers the
  whole call failing.
* An **unreachable model** propagates. A turn with no model behind it has no
  honest degraded form. The route reports it as 503 carrying the specific
  exception's text, because "cannot reach Ollama, check `ollama ps`" is
  actionable and "something went wrong" is not.

## The route

`POST /api/chat`, mounted in `create_app()`. Ownership is enforced — a user may
only speak into their own conversation — and a conversation that does not exist
is refused **identically** to one belonging to someone else, so the difference
cannot be used to discover which ids exist.

**No capability is registered for chat.** `permissions.py`'s rule is that only
capabilities something actually enforces get registered. What this enforces is
ownership, which keys on `conversations.user_id`, not on `role`. Retrieval
remains unfiltered by actor per `NOW.md` decision #20; nothing here changes that.

A **closed** conversation starts a new one rather than being reopened, and the
response says so via `new_conversation`. Closing is what triggers final chunking
and sets `chunked`; appending to a conversation already chunked in full would
leave the appended turns indexed by nothing — the state idle-close exists to
prevent.

**The idle sweep runs after the response**, as a background task, with the active
conversation excluded. Running it inline would put an embedding call per idle
conversation inside the user's wait, and — worse — would put an unbounded number
of them inside the in-flight-grace floor's arithmetic. Deferring it keeps the
floor derivable from the loop's own limits. The cost is that a sweep can overlap
the next turn's writes, which is the contention `db.py`'s retry already handles
and measures. A test backdates a conversation, posts a turn, and asserts the
stale one closed while the active one did not.

## Verified live, not only under TestClient

* **A real model was handed a real tool schema for the first time** — 2.1's
  standing `[unverified]`. Against `gemma4:26b` with one TEST-ONLY tool
  registered: the model emitted `read_thermostat(room="study")`, the loop
  dispatched it, the handler recorded being called with `"study"`, the result
  went back, and the model answered from it. 2 iterations, 5.4 s, trace written
  to `messages.tool_trace` as JSON.
* **A real uvicorn server, a real login, a real turn.** `POST /api/login` then
  `POST /api/chat` over HTTP on port 8123, answered 200 with content. `grep -c
  'v1\.'` over the server log returns **0** — no token in the logs, consistent
  with the auth task's finding.

## Judgment values, flagged

Four new ones, all in `config/defaults.toml` with their reasoning, all
bootstrap-only and deliberately **not** settings-backed — the in-flight grace
floor is derived from two of them, so a panel that could raise either at runtime
could invalidate the floor without touching it.

| Key | Value | Basis |
|---|---|---|
| `agent.max_iterations` | 5 | 4 for the deepest tool chain Phase 2 can produce (`memory_search` → `web_search` → `web_fetch` → answer), 1 for a retry after a malformed call |
| `agent.tool_budget_seconds` | 120 | derived: `(max_iterations - 1) x tools.default_timeout_seconds` |
| `agent.max_tool_result_chars` | 4000 | ~1000 tokens at the 4.0 chars/token in `[history]` |
| `tools.default_timeout_seconds` | 30 | covers a cold embedding load with margin; four fit the turn budget |

**Flagged consequence for Phase 4:** a tool needing 300 s — image generation
carried exactly that in the reference build — cannot complete inside a turn
under this budget. Either image generation becomes asynchronous (submit, return
a handle, notify) or the budget rises knowingly and the grace floor rises with
it. That is Phase 4's decision, not one made here.

## Known gaps

* **The current-situation block does not exist**, so `situation` is `""` and the
  entity currently gets no timestamp and no elapsed-time statement. It is a
  parameter on `handle_user_message()` precisely so the Phase 1 task that builds
  it drops in without touching the loop. Nothing here weakens the pairing
  enforcement — an elapsed-time statement without its pairing still raises in
  `build_system_prompt()`.
* **No tool is registered**, so a production turn today never calls one. The
  whole tool path is exercised by TEST-ONLY tools and by the live run above.
  `memory_search` is the next task.
* **Tool exchanges are not message rows.** The schema's role is `user` or
  `assistant`; the record of what the tools did is the trace on the assistant
  row. The consequence is that on the *next* turn the model sees its own answer
  but not the tool calls behind it. That is a real behavioural property, not an
  oversight — changing it means a schema change, which is Tier 3.
* **Streaming is not built.** `ollama.chat_stream()` exists and nothing calls it;
  tool-call detection needs the complete message anyway. The Phase 8 interface
  task is where streaming earns its place.
* **`retrieval.search()` embeds with the global 300 s Ollama timeout**, which is
  ~4.5 minutes of the derived floor for a call that really takes seconds. Giving
  it its own timeout would shave that, and touches Tier 3 retrieval code — worth
  its own task rather than an incidental patch.
* **An empty answer is logged, not substituted.** A model that emits only tool
  calls can produce one. Inventing text would be the system speaking in the
  entity's voice.
