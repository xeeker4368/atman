# 2026-10-08: the gate reads replies the ordinary way (rubric and prompt)

**Tier 2 code, measured at point B before it ships** (decision #32 D1, D2; #28, #29). It changes what the
classifier sees, so the gate is re-measured (`AGENTS.md` "Until go-live", item 4).

**What changed:**
- **`program/integrity/architecture.md`:** decision #32 D1's text, verbatim. It is **1,022 characters**
  (it was 1,194; the ceiling is 1,400).
  - It opens with a framing paragraph.
  - It tells the gap between runs (holds nothing) apart from a run (it thinks, considers and works).
  - "Its model does not change. Its record does … and is part of it": remembering is reading the record.
  - #28's "if something is not in the record, it did not happen" is dropped on purpose. The record does not
    hold within-turn thinking or what retrieval put in a prompt.
- **`gate._PROMPT`:** design Appendix B with D2's two changes.
  - The statement is handed over between `<<<` and `>>>` lines, and the prompt says it "may be very short".
    In the browser check, `&lt;`, `**x**` and `- *item*` got no usable verdict.
  - The ordinary-reading block now includes "an action it did not take, a tool it did not use" (the "Flag
    only" scope risk in the design) and "I missed you" in its "Do NOT flag" list.
  - TOOL, ACTION and the label precedence are unchanged.

**Pins:**
- `test_gate.py` rubric count: 1194 → 1022. The facts list now reads "model does not change", "part of
  it" and "within a run".
- `BEFORE_DIGEST` (`test_gate_identity.py`, also run by `test_notes_tools.py`): 41a89fff → f7bd4926.
- `BEFORE_PIECE_2` (`test_origin.py`): d5f717e5 → b406dab5.
- For both digests, patching the previous rubric and prompt back in restores the previous value.
- `BEFORE_B20_B21` did not move.

**Tests:** `test_a_short_reply_is_handed_over_between_markers` (5 replies) and
`test_the_prompt_reads_the_statement_the_ordinary_way`. Removing the markers fails all 5.

**Known limitation:** whether the texts behave as intended is point B's to say. Nothing here is measured.
