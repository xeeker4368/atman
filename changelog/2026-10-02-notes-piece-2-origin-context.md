# 2026-10-02 — Notes piece 2: `OriginContext` (stopped for diff review)

`docs/NOTES_BUILD_PLAN.md` piece 2, **Tier 2: diff review only**. It edits `registry.py`, `loop.py`
and `turn.py`, which every tool and every turn run through, so the digest result is the first thing
to read. **No tool takes origin yet; nothing is offered to the model; the server was not started.**

## The digest result (first)
`tests/test_origin.py::test_existing_tools_and_turns_are_byte_identical_to_before_piece_2` runs
three real turns (`turn.handle_user_message`, real loop, dispatch, store, gate, correction path;
only the model and the classifier are scripted) with a plain tool, a `takes_attribution` tool and
the real `creative_write`, and digests: every model call (messages, tools, options), the stored
tool trace of every assistant message (call ids, artifact ids and durations normalised), every prompt
the integrity gate's classifier was shown, and the keyword names each handler was called with.

**`e5c92a42…806762` on the HEAD production files** (`registry.py`, `loop.py`, `turn.py` as committed,
by `git stash` of the three files, before any piece-2 edit), **stable across 4 runs; identical after
the edit across 3 runs.** The three pre-existing tools and the gate see exactly what they saw.

**The first pin was wrong, and I am saying so.** I first took `3c16d545…` (6 identical runs), and it
then differed from a run on the unchanged code an hour later: the correction classifier's prompt
prints a minute-granular time (`2026-10-02T16:00`), which the scrubber missed. Found by diffing the
two states component by component (model calls, kwargs and gate prompts were identical; only that
hour differed). The scrubber now covers every ISO form, and the digest above is the re-taken one.
If a test pins a digest, it must be shown to be a function of the code and not of the clock.

## Built
- **`program/origin.py`**: `OriginContext(conversation_id, user_message_id, context_message_ids,
  call_id)`, frozen, hashable; refuses an empty conversation or message id. Carries no user, role or
  permission, so it is neither an `Actor` nor an `AttributionContext` (whose single field is
  untouched, asserted).
- **`registry.py`**: `Tool.takes_origin`; `ORIGIN_ARGUMENT`; `__post_init__` refuses `origin` in
  `parameters` of a declaring tool; `dispatch(..., origin=)` hands it **only** to a declaring tool,
  as a handler keyword, with **`call_id` filled into a copy** (the caller's object is unchanged), and
  a declaring tool with no origin raises `ToolError` (a wiring bug, as for attribution).
- **`loop.py`**: `run_turn(..., origin=)` passes it to dispatch and **reads nothing in it** (an AST
  test fails if `loop.py` ever reads an attribute of it).
- **`turn.py`**: `_build_origin`. `context_message_ids` = the conversation's own messages (the
  window holds dicts with no ids, so this is a superset) plus the messages behind the **passive**
  retrieval's chunks (`db.get_messages_in_chunks`). A failure reading those degrades to the
  conversation's alone, with a warning, and never fails the turn (it only widens a preference tier).

## Four guards, each proven to bite (PYTHONDONTWRITEBYTECODE=1)
1. **Declared, not inferred.** Mutations killed: origin handed to every tool (killed by 3 tests,
   **and by the digest alone**); a declaring tool with no origin not raising.
2. **Never model-settable.** Killed: the parameters guard removed; a model-supplied `origin` silently
   dropped instead of refused; origin added to the tool's schema (the model would be shown it).
3. **Never in the recorded arguments or the trace.** Killed: origin put into the recorded arguments;
   `call_id` not filled in. The stored trace of a real turn is checked for any origin content, and
   its `call_id` equals the one the handler received.
4. **The loop passes it through unread.** Killed: the loop dropping it; the loop reading an
   attribute of it.
Plus the turn builder: not passed to the loop; ignoring the retrieval's messages; naming the wrong
triggering message; omitting the current message; not degrading on a chunk-read failure. **18 tests**
in `tests/test_origin.py`; 14 mutations, all killed.

## The recorded gap, pinned
**`context_message_ids` does not include what a `memory_search` call surfaces during the turn.** The
origin is built before the loop runs. A test has a tool surface a message in round 1 and asserts a
`takes_origin` tool in round 2 still does not have it in its context. Recorded in N18 and the build
plan already; its consequence is that such a quote resolves in the store tier under the whole-store
uniqueness rule.

## Tested
Full suite 1,653 passed, 4 skipped; `ruff` clean (it also re-sorted the new imports in three files).
`BUILT.md` is **not** edited for this piece: its entry is written at the review's approval, in the
commit that lands then.
