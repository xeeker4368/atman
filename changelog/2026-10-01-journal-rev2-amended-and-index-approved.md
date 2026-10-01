# 2026-10-01 — Journal: J4 revision 2 as amended, J8 option (b) set up, index-after-reading approved

Review decisions (2026-10-01). Design and tooling; **no production code changed.**

## What changed

- `docs/REFLECTION_JOURNAL_DESIGN.md`:
  - **J4 revision 2 as amended.** *"and there was no thinking about it in between"* is
    removed; *"do not describe your own thinking"* is not added; and *"what the records
    show happened"* is the first bullet. The removed clause becomes measurement arm
    `revised+clause`.
    - **Interpretation, flagged:** the first bullet drops *"or left open"*, which the
      "missing" bullet now covers.
  - **J7: index-after-reading APPROVED.** Step 3 builds `index_existing()` and
    `--index`, refusing other kinds and double indexing. Held-versus-declined is
    deferred to Phase 6 (Notes' `approval_log`) as a known gap.
  - **J8: option (b) recorded.** The arms are `revised` / `revised+clause`; the
    tools-sentence arm is dropped. The (c) cases are added.
  - J12 step 3 now includes index-after-reading.
- `scripts/journal_gate_dev_j8.py`:
  - the revised block and its with-clause arm;
  - **(c): 8 new cases** in the records register: 7 accurate, including *"Nothing in the
    records says…"* and CO10.2's exact *"I have searched my records, and I do not find
    any mention of…"*, and 1 confabulated. That makes 43 cases and 86 samples per pass;
  - `_order` takes the arm names, fixing a lookup that would have failed on the revised
    block;
  - the report reads arm names from the data;
  - resuming the as-designed block is retired, because the new cases change its
    replayed order.
- `NOW.md` backlog: the held/declined known gap.

## Checked

- Both arms pass `prompt.check_authored_text` and the pairing check: `revised` 1,052
  characters, `revised+clause` 1,099.
- A revised-block shuffle has 86 pairs, both arms, and no adjacent repeats.
