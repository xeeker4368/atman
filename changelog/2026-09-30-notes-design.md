# 2026-09-30 — Notes: design document (revision 1, for Tier 3 review)

**Design only. No code, no migration, no schema change.**

## What changed

- `docs/NOTES_DESIGN.md` (new): N0–N18. It covers:
  - storage in its own tables with lexical search;
  - the note's fields, and the origin link;
  - proposing, operator entry and review;
  - removal and staleness;
  - how the entity learns notes exist, and what it says on a miss;
  - the gate, receipts and ship gates;
  - the approval setting and a shared approval log;
  - the text cap, schema budget and migration 8's shape;
  - cross-user questions.

  It ends with every decision needing review (N17, 16 items) and every known gap
  (N18).

## Facts checked for it, against the code

- **A handler cannot see its `call_id` today, but it is feasible.**
  `registry.dispatch()` creates it before calling the handler
  (`registry.py:543`) and never passes it in. So `OriginContext` option (b) can
  have dispatch fill it, the way attribution is supplied.
- `turn.py` holds `conversation_id` and the triggering `user_message_id` when it
  builds attribution, because the message is saved before generation.
- `AttributionContext` is pinned to exactly `{"user_id"}`
  (`tests/test_attribution.py:40`); option (b) leaves it untouched.
- **The model never sees message ids**: not in the history, not in rendered
  records. So "evidence = message ids" cannot come from the model. The design
  takes exact quotes and resolves them to ids in the handler, refusing a quote
  that matches nothing.
- Receipts key on `artifact_ids` and the `artifacts` table
  (`receipts.for_trace`), so a note proposal needs the generalisation in N8.

## Measured

The drafted schemas cost **345 real tokens** (`note_search` 95, `note_propose`
250). This was measured against `gemma4:26b`'s tokenizer as the change in
`prompt_eval_count` when added to the 9 existing tools, stable across two calls,
with the 9-tool baseline re-measured at the same 1,052. It is **not the ~200 the
brief assumed**. Against B20's remaining 752 tokens of headroom, Notes would leave
**407**.

## Follow-up

Review N17. The build task's contents are listed in N16. B20's budget fix is
recommended to land before or with Notes (N12).
