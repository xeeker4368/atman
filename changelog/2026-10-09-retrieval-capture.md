# Retrieval capture per turn (decision #33)

**What changed.** Each assistant turn now records what retrieval put into its prompt.
- Migration 9 adds two `working.db` tables:
  - `turn_retrieval`, one row per turn: an explicit outcome (`records`, `empty`, `not_run`), whether
    each search ran and why not, the record count, the block's characters, the continuation pieces
    withheld, the hits left out, and whether the block was truncated.
  - `turn_retrieved_records`, one row per ranked hit: chunk id, source type, the record's opening line as
    rendered, the RRF score, rank, lexical and vector ranks, rendered characters, and continuation pieces.
  - `turn_capture_status`, a view with one row per assistant message: its outcome, `not_captured`
    (capture failed after the save) or `before_capture` (older than migration 9). A row in the
    message's own transaction would need a change to `db.save_message` (Tier 3), so this is the fix.
- `prompt.render_records()` returns the text plus that account. `render_retrieved()` is its `.text`, and
  `AssembledPrompt.records` keeps the final one.
- `turn.py` writes the capture through `retrieval_capture.record()` after the answer is durable, in its own
  transaction under `db.retry_on_locked`.
- `scripts/show_retrieval.py` prints one turn's capture, or `--status` for every turn, read-only.

**Why.** Run review could not tell "never shown the fact" from "shown it and missed it".

**What did not change.** The prompt text is unchanged: `BEFORE_B20_B21`, `BEFORE_PIECE_2` and
`BEFORE_RENDER_SETTING` all hold. `db.py` is untouched. Nothing in `program/` reads the tables.

**Tests.** `tests/test_retrieval_capture.py` has 27: the schema, its constraints and its rollback, the
renderer's account under each cap, the turn's write, failed, empty and one-search retrieval, a capture
failing before or mid-write (`not_captured`, never `empty`), older turns, the lock retry, the backup copy,
the no-reader scan and the script. Mutation-checked: without the turn's call 4 tests fail; without the
retry decorator, 1; outcome, status view, ranks and search state each caught. `working.db` is copied whole by the SQLite backup API
(`test_the_capture_is_in_the_backup`).

**Test-only changes.** `tests/test_db.py`'s table set gains the two tables; `tests/test_notes_schema.py`
pinned "the latest version is 8" and now asserts the latest declared version, as the migration 7 test does.

**Limits.** Only a turn's final prompt is captured (a tool-calling turn may have sent fuller records
earlier). `chunk_id` has no foreign key. Turns before migration 9 read as `before_capture`.
