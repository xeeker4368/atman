# O23: receipts for side-effect tools, backend half

Date: 2026-09-30 · design F50, approved at review, built as one task. Stops for review
before commit.

## What was wrong

- The gate's ACTION trigger cannot recognise every way the entity claims to have made or
  saved something (O23, measured). So a false "I saved it" could stand with nothing
  beside it.
- Found while designing the fix, and measured before any code existed: a write that
  fails **after** its `artifacts` row is committed came back `tool_error` with the row
  and file present. The entity was told only *"RuntimeError: embedder down"* about a
  piece that was in fact kept.

## What changed

- **Trace key `artifact_ids`**, on every entry (`[]` for tools that write nothing). It
  lives in the `tool_trace` JSON column, so no schema change.
- **`registry.ToolOutput(text, artifact_ids)`**: what the two side-effect handlers now
  return. `dispatch` stores `text` in `value` exactly as before, so the model sees
  nothing new on success.
- **Carrying ids on the failure path:**
  - storage raises its own **`indexing.StoredButNotIndexed`** (via `index_after_row`)
    once the row is committed;
  - the handler turns that into **`registry.ArtifactWriteError`** carrying the id;
  - storage still does not import the tools layer.
- **Dispatch refuses ids from a tool that does not declare `takes_attribution`**, the
  one definition of "which tools write".
- **`registry.side_effect_tools()`**, moved from the gate and read from the full
  catalogue. The gate keeps the name with identical behaviour: a disabled tool cannot
  appear in a live trace as having run.
- **`program/tools/receipts.py`**, `for_trace()`. `saved` is keyed on the `artifacts`
  row, found with one batched read, the new `db.get_artifacts_by_ids()`. Outcomes are
  `saved` / `not_saved` / `unknown`, per the approved table.
- **`POST /api/chat` returns `receipts`** beside `content`, never inside it.
- **Error-text fix, bundled as approved:** *"The piece was kept (artifact id …), but it
  could not be indexed into memory, so it will not come up in searches: …"*, with a
  matching image version. No re-indexing is promised.
- `BUILD_PLAN.md` Phase 9's chat-interface task now owes the receipt rendering and the
  `creative_write` text change.

## The failed-lookup case, named precisely (asked at review)

`test_a_failed_row_lookup_is_unknown_and_never_empty` exercises **the receipt's own
row-existence check raising a database error** (`sqlite3.OperationalError: database is
locked`) **while side-effect calls are present**. It is distinct from
`test_no_side_effect_call_means_no_receipts_and_no_lookup`, where the list is empty
because nothing was attempted and the lookup is never made.

On a failed lookup, every receipt that depends on the lookup becomes `unknown` and keeps
its artifact id. **One refinement to "all unknown", stated rather than slipped in:** a
receipt decided without the store keeps its certain outcome. That means a call that never
ran (`skipped`, `unknown_tool`, `invalid_arguments`) stays `not_saved`, because the failed
read tells us nothing new about it. The test asserts both halves, and that the count of
receipts equals the count of side-effect calls.

## Tested

- 27 new tests in `tests/test_receipts.py`. Four existing tests changed deliberately,
  because they pinned the contract this task changes:
  - two handler-return tests now read `.text` and assert `artifact_ids`;
  - the image embedder-failure test now expects `StoredButNotIndexed` naming the row and
    carrying the real cause;
  - the trace key-set test includes `artifact_ids == []`.
- **The gate is untouched, proven.** Every frozen case, under four scripted replies,
  gives byte-identical `to_json()`, `advisory_json()` and classifier prompt with and
  without the key. Proven to bite twice: showing the key in the prompt fails it, and
  making the ACTION clear depend on the key fails it on the verdict. The frozen
  fingerprint is pinned elsewhere and is unchanged.
- **Proven to bite:** 11 mutations, each run and each failing its own test:

  | mutation | tests failed |
  |---|---|
  | dispatch drops the ids from an `ArtifactWriteError` | 3 |
  | `to_trace_entry` omits the key | 12 |
  | no check that only writers report ids | 2 |
  | a receipt trusts the trace and skips the row | 1 |
  | the outcome label is checked before the ids | 1 |
  | a failed lookup returns `[]` | 1 |
  | a timeout reads as `not_saved` | 1 |
  | storage bypasses `index_after_row` | 2 |
  | the old error text | 1 |
  | the gate prompt shows the key | 1 |
  | the receipt leaks into `content` | 1 |
- **Verified live** (`gemma4:26b`, throwaway store):
  - the model called `creative_write` and got a receipt `saved` with the row's id;
  - with the embedder forced down, the trace said `tool_error`, the receipt said `saved`,
    the row existed, and the model received the kept-but-unindexed text.
- Full suite: **1,350 passed, 2 skipped**. `ruff` clean.

## Known limitations

- **No person sees a receipt until Phase 9** renders the field. `creative_write`'s
  *"Nothing shows it to anyone unless it comes up."* stays until then, because it is still
  true (timing (a)). The line is marked in the code.
- In the broken-embedder run the entity replied *"I have written the poem and saved it."*
  That is true (the piece is kept), but it doesn't mention that indexing failed. Nothing
  here makes the entity say so; the receipt is what tells the person.
- An accurate sentence naming the tool literally, such as *"creative_write succeeded"*,
  over a kept-but-unindexed call flags `success_over_failure`. Accurate prose ("I kept the
  piece, but it could not be indexed") does not. This is arguably right: the call did not
  fully succeed.
- A timed-out handler may still write a row after the wait is abandoned. Its receipt stays
  `unknown`, because nothing links a late row to the call.
- `ingest.py` (uploads) was not changed. It is not a tool and gets no receipts.

## Follow-up

- **B19** (filed): `invented_id` should accept ids in this turn's `artifact_ids`.
- Phase 9: rendering, plus the `creative_write` text change in the same change.
