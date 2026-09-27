# B2: the correction candidate pool

Date: 2026-09-24 · merged-queue items 6, 22b, C3 · plan B2 · Tier 3 (correction
classifier), plan approved before implementation

## What was wrong

All three defects are in `corrections.candidates()`.

1. **Item 6: corrections lost on the turn that opens a new group.** The turn's own two
   messages are saved before candidates are gathered, and they are excluded. On a turn
   that opens a new chunk group, the current open group holds only those two excluded
   messages. That happens on the ninth, seventeenth, and so on (the 8-turn cap), and at
   any seal forced by size. Before B1 there were also no mid-conversation chunks for
   retrieval to supply. So a correction of anything said earlier in the conversation was
   silently lost.
2. **Item 22b: retrieved candidates crowded out.** `MAX_CANDIDATES = 12` was applied
   across both roles, but each classifier call then filters to one role. A full open
   group (up to 16 messages) took every slot, so retrieval-derived candidates never
   reached the classifier. Those are the ones that catch a correction of something said
   days ago.
3. **C3: one connection per retrieved chunk.** `db.get_messages_in_chunk()` was called
   once per chunk, so up to 10 fresh connections per turn, each running
   `PRAGMA journal_mode`.

## What changed

- **The open group is taken as it stood before this turn.**
  - The conversation's messages, minus `exclude_message_ids`, go into
    `chunking.open_group_messages()`.
  - Greedy packing gives the same earlier groups however many messages are added later.
    So this is the open group minus the current turn in the ordinary case, and the group
    that just sealed at a boundary.
  - That is the approved plan ("also take the most recently sealed group") without a
    special case.
  - `chunking.py` is unchanged.
- **`MAX_CANDIDATES` is now per role.** The docstring says why. Each prompt still shows
  at most twelve candidates.
- **New `db.get_messages_in_chunks(chunk_ids)`.**
  - It is the per-chunk timestamp-window join in one query, with split siblings
    deduplicated.
  - It is a read, so it gets no `@retry_on_locked`. Adding retries to reads would be a
    `db.py` locking change, which is its own checkpoint.
  - `candidates()` now opens 2 connections instead of up to 11.
- **Unchanged:** `get_messages_in_chunk()` stays for its other callers and tests. The
  prompt, parser, `classify()` and the schema are untouched.

## Tested

Four new tests in `tests/test_corrections.py`, on a real store, with real chunking where
it matters:

- **Ninth-turn boundary.** The test first asserts that the current open group is only the
  ninth turn. It then asserts that all eight earlier user claims are offered and the
  current turn is not.
- **Ordinary turn (control).** The pool is exactly the earlier turns.
- **Retrieval not crowded out.** Four claims from a closed, chunked conversation
  alongside a full live open group. Retrieval-derived claims survive, and neither role
  exceeds twelve.
- **One read, not one per chunk.**
  - The batched reader returns exactly what the per-chunk reader did.
  - `candidates()` never calls the per-chunk reader and opens exactly 2 connections.

**Proof it bites:** the previous `corrections.py` fails 3 of the 4. The control passes
either way.

**Measurement of record:** the frozen correction eval builds its own pools and calls
`classify()` directly, so it is unaffected by construction (checked in
`correction_eval.py`). Nothing changed in the prompt.

## A regression found while verifying, from Group A item 19, not from B2

The full-suite run failed
`test_backup.py::test_a_write_during_the_snapshot_cannot_land_in_one_store_only`.

- **Isolated:** 4 of 6 runs fail with the current `backup.py`. The previous `backup.py`
  passes 6 of 6 in 0.22 s.
- **Cause, established experimentally:**
  - Item 19 removed the source reads that used to come before the snapshot. Putting one
    throwaway read back restores 6 of 6, so the old code avoided the window by timing.
  - The stall is inside `reader.backup()` (10.7, 21.5 and 32.6 s). The holder's SHARED
    lock blocks the writer's commit, the writer's PENDING lock blocks the backup reader,
    and it only clears when the writer's 10 s busy timeout lapses.
  - The defect predates this week. It is the "observed once" flake already in BUILT.md.
- **Prototype, reverted:** a `BEGIN IMMEDIATE` holder improved it but did not fix it.
  2 of 6 runs still took 11 s, and another backup test failed.
- **Not fixed.** Filed as B14, Tier 3. Decision needed on the suite in the meantime.

Full suite: **1192 passed, 1 failed (that test), 2 skipped**. `ruff check .` clean.

## Known limitations

- **Live behaviour of the wider pool is unmeasured.** More candidates now reach the
  classifier. CO10.2's false link needed a six-candidate pool to reproduce, which is why
  B11 is ordered after this.
- **B1's interaction is now reachable** (current-conversation chunks arriving via the
  retrieval source). Deduplication by message id covers overlap with the open group. No
  end-to-end turn test covers it yet.
