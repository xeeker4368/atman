# 2026-10-08: the earlier-tools record becomes one system list (decision #30, #32 D4)

**Tier 2 (prompt-facing text and prompt assembly), measured at point C before it ships.**

**Why.** Batch 1 put a system-written line at the start of each earlier tool-calling reply's content. In
run 3 the entity copied that line into its own replies three times (C4 t3, C7 t3, C11 t5) on turns where
no tool ran.

**What changed.**
- `earlier_tools.system_list(rows)` builds one list, placed after the speaker line and before the retrieved
  records:
  - It opens with a header saying the system wrote it, not the entity, and that nothing in it ran on this
    turn.
  - Each entry is `- Your reply to "<first 40 characters of the person's message>": <calls>.`
  - Each call shows the tool, a short query, the outcome and the receipt word: the same pieces as before,
    and never a result. At most 3 calls per entry, then "and N more calls".
  - Entries go newest first while they fit under **800 characters** in total. The rest are counted in a
    closing "- And N earlier replies used tools."
  - An unreadable trace is left out, with the warning kept.
- History now goes to the model as stored, and `with_tool_records` is gone.
- `AssembledPrompt.earlier_tools_chars` reports the list, so the parts still sum to the system string. The
  list is never in the situation string, so the gate's input is unchanged.
- **Budget:** the derivation in `config/defaults.toml` and `tests/test_turn.py` now reserves the speaker
  line (28 characters plus a 64-character name allowance) and the list at its cap. That is 224 tokens.
  The headroom beside a maximal 50,000-character message falls from ~390 to **~166 tokens**. The schema
  figure was re-read at 6,156 characters (1,539 tokens).

**Pins:** `BEFORE_PIECE_2` moves from b406dab5 to 382154e8. With the old line-in-history patched back in,
the previous value holds. `BEFORE_B20_B21` and `BEFORE_DIGEST` did not move.

**Tests:** `tests/test_earlier_tools.py` (14 tests, rewritten), plus the order test in `test_prompt.py`.
Mutation-checked: the list not passed (2 fail), folded into the situation (1), cap off (3), budget
omitting it (1), oldest first (2).

**Follow-up:** whether the entity still copies tool lines is point C's to measure.
