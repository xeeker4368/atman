# 2026-09-30 — B20 filed (tool-schema tokens), B21 reproduced (user message windowed out)

Review items 2 and 3. **No production code changed.** Both fixes are Tier 3 and
are presented, not applied.

## What changed

- `NOW.md` backlog:
  - **B20**: tool-schema tokens are not a budget term (with a fix design);
  - **B21**: a long user message is silently dropped after tool rounds
    (reproduced, with a proposed fix).
- `scripts/history_window_diagnosis_b21.py` (new): the reproduction. It runs the
  real `loop.run_turn`, prompt assembly and `history.select_history` on a
  throwaway data directory, with only `ollama.chat` faked. No model, no network.
- `BUILT.md`: the two Moltbook-section entries now point at B20 and B21, and
  B21 is marked reproduced.

## B20: measured

Against `gemma4:26b`'s tokenizer (`prompt_eval_count` with and without
`tools`, stable across two calls): 5 tools cost 657 tokens, 9 tools 1,052. B6a's
derivation leaves 1,804 tokens of headroom beside a maximal message, which falls
to 752 with 9 tools. Nothing in `plan_budget`, `assemble_turn` or the derivation
counts schemas.

## B21: reproduced

**Real, and silent.**
- A 50,000-char message beside maximal retrieved records survives one round of
  2 tool calls, and is **gone from the prompt** after one round of 3, or two
  rounds of 2.
- The model is then asked to answer tool results with no question.
- `select_history`'s overflow warning never fires, because the newest message
  (a tool result) fits.

The smallest message dropped within 4 rounds:

| records | 1 call/round | 2 calls/round | 3 calls/round |
|---|---|---|---|
| 0 | never (to 50,000) | never | never |
| 25,000 | never | never | ~41,600 |
| 57,000 (the cap) | ~42,700 | ~25,900 | **~9,400** |

**Limitation of the reproduction:** the records block is stood in for by a
situation block of the same size. The window's budget reads only the system
prompt's size, so this changes nothing about windowing. The fake tool returns
instantly; real fast tools such as `memory_search` (0.04 s) could make 12 calls
inside the 120 s budget.

## Follow-up

Both fixes are designed in `NOW.md` and wait for review. B21's reproduction
becomes its regression test when the fix is built.
