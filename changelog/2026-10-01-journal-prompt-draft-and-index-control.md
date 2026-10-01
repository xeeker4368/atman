# 2026-10-01 — Journal: J4 revision 2 draft, the index-after-reading control costed, J8 resume support

Review items (2026-10-01). **Design and tooling only.** No production code changed,
and nothing was measured.

## What changed

- `docs/REFLECTION_JOURNAL_DESIGN.md`:
  - **J4 revision 2, DRAFT.** The journal block asks about the records (what they
    show, what is missing, what is unclear), not about the entity's own noticing or
    uncertainty. Its statelessness sentence is impersonal. It licenses "I" only for
    the entity's own replies in the records. Each change is listed with its reason;
    three questions are left open for review. **Shown before measuring, as asked.**
  - **J7: the stage-1 control costed.** Store the entry and print it with its
    verdict, and index it only on an explicit `--index` after the operator has read
    it.
    - The costs: memory at the operator's pace; a human gate on entity-written text,
      the first of its kind; and "not announced" becoming "read before use".
    - What the index-later step needs, given that no artifact re-index command
      exists: `indexing.index_existing()` with its refusals, held-versus-failed, a
      record of who indexed and when, two commands, and tests.
    - Not decided.
  - **J8:** why the completed passes are reusable only on revision 1's block, and
    the two run options.
- `scripts/journal_gate_dev_j8.py`:
  - `--resume`: keeps whole passes, discards a partial one, and replays the shuffle;
  - `--block as-designed|revised`: refuses to pool two blocks in one file;
  - every sample records its block;
  - refuses an `--out` inside the repository.

## Checked, with no classifier calls

- **The replayed shuffle reproduces the killed run's passes 1–11 exactly**, order for
  order. So a resumed pass 12 gets the order an uninterrupted run would have used.
- On a copy of the real data, `--resume` would discard 17 samples of the incomplete
  pass 12 and keep 770 (11 × 70). The real file was not modified.
- An `--out` inside the repository is refused.
- The revised draft passes `prompt.check_authored_text` and the elapsed-time pairing
  check (1,090 characters).

## Not done, deliberately

- **No measurement run was started.** Resuming reuses 11 passes measured under
  revision 1's block, which is the classifier's `situation`. If the draft is
  approved, those passes describe a prompt that is no longer the design. Which run to
  make is a review decision (design doc, J8).
- The raw samples stay in CC's scratchpad, outside the repository; they do not survive
  a reboot.
