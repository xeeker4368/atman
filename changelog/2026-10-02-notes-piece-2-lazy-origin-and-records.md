# 2026-10-02 — Notes piece 2: origin built only when a tool declares it; records; N4 amendment

Review rulings after piece 2's diff review. **No piece-3 code has been written**: piece 3 starts
only after this set is committed.

## Changed
- **`turn.py` builds the origin only when a tool this turn offers declares `takes_origin`.**
  `_offers_a_tool_that_takes_origin(registry)` resolves the registry as the loop does: the one
  passed in, or `default_registry()` when none is. With Notes dark nothing declares it, so a turn
  builds no origin. `history` is the same `get_conversation_messages` call as before, so nothing
  extra remains in the dark path.
- **A finding the test forced:** a bare count of `db.get_messages_in_chunks` calls was not zero in
  the dark path, because **the correction-candidate path reads those messages on every turn**
  (B2). So the test isolates the origin's own read by call stack
  (`chunk_read_by_origin == 0`, `built == 0`) rather than counting all reads.
- **Tests (`tests/test_origin.py`, 18 -> 20):** a turn whose tools none declare origin does none of
  the origin's work (retrieval does return a chunk, so a read would show); the default registry is
  checked when none is passed (the real default declares nothing, asserted; with a patched default
  that declares it, the origin is built once and reaches the tool). **Four mutations, each killed:**
  always built, `registry=None` treated as "none declares it", the check inverted, never built.
- **The digest is unchanged** (`e5c92a42…806762`, in the suite); full suite 1,655 passed, 4 skipped;
  `ruff` clean.

## Recorded
- **`BUILT.md`: piece 2's entry** (it was held for approval).
- **`NOW.md` B22: `archive.db` has no triggers, so its append-only rule is convention** (Tier 3, not
  built). Notes that migrations only reach `working.db` so the archive needs another mechanism
  (one candidate, not decided, plus a startup check that the triggers exist), that the `messages`
  copy in `working.db` is unguarded too, and what backup and restore would need (restore must verify
  the restored archive's triggers against the expected set and treat the two databases as a pair).
- **Piece-8 enabling checklist** (`docs/NOTES_BUILD_PLAN.md`): the real store is at schema version
  6, so its first startup applies migrations 7 and 8, and migration 8 has never run on real data.
- **N4 amendment and N17 #23** (`docs/NOTES_DESIGN.md`; the build plan's piece 3 and 4 follow): quote
  evidence resolves only against `role = 'user'` messages in v1, a quote of the entity's own earlier
  reply is refused as *"not a person's words"*, and the review command prints the speaker of each
  quote.
