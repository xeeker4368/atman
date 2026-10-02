# 2026-10-02 — Notes piece 1: migration 8, with the review's rulings applied

`docs/NOTES_BUILD_PLAN.md` piece 1, **resubmitted after the first schema review**. Tier 3. No code
reads or writes the new tables; `notes.enabled` does not exist and no tool is offered.

## Rulings applied (review, 2026-10-02)
- **FTS over `active_notes`: approved.** Rebuild and both integrity checks are now tested after
  retire, supersede and edit sequences, including detecting and repairing a drifted index.
- **`chunks_fts` uses the same implicit-rowid approach, and nothing runs `VACUUM`.** `chunks` and
  `notes` both have `TEXT` primary keys, so their rowids are implicit and both indexes use
  `content_rowid = 'rowid'` (`'rid'` for the view). Searched `program/`, `scripts/`, `ops/`,
  `tests/`, `config/`, `start.sh` and `run_server.py`: no `VACUUM`. A test now scans for a
  statement; planting one fails it. *Why it matters:* an implicit rowid is not guaranteed stable
  across `VACUUM`, which could silently pair index entries with the wrong rows.
- **Extra constraints kept, except the closed `approval_log.decision` list.** Removed from the
  schema; validated in code: `db.APPROVAL_DECISIONS` (a constant keyed by capability) and
  `db.record_approval(conn, …)`, which writes inside the caller's transaction and raises
  `ValueError` for an unknown capability or a decision the capability does not allow. A test pins
  that the schema does **not** close the vocabulary, so a returning CHECK fails and says why.
- **Deletes:** a decided proposal cannot be deleted (`BEFORE DELETE` trigger; a pending one may);
  **all deletes on `notes` are refused**. The `AFTER DELETE` FTS trigger is removed, since it could
  never fire.
- **`resulting_note_id`** is the note the proposal created or changed (for a retire, the retired
  note's id), with `CHECK ((status IN ('approved','edited','applied_without_review')) =
  (resulting_note_id IS NOT NULL))`. The note therefore exists before the proposal is flipped.

## Second review (2026-10-02): evidence and REPLACE
- **`evidence` is now `NOT NULL` and a non-empty JSON array** (CHECK: `json_valid`, then
  `json_array_length >= 1`; the length is 0 for anything that is not an array, so one test covers
  object, null and scalar). It was `NOT NULL` and `json_valid` only. Tested for 9 refused values
  and 3 accepted shapes; both halves proven to bite.
- **REPLACE bypassed the delete guards. Measured, then closed.** SQLite fires no DELETE trigger for
  the row removed to resolve an `INSERT OR REPLACE`, `REPLACE INTO` or `UPDATE OR REPLACE`
  conflict unless `recursive_triggers` is on, and this build changes no connection pragma. On a
  bare table with a `BEFORE DELETE` trigger, `INSERT OR REPLACE` overwrote the protected row and
  `UPDATE OR REPLACE` deleted its neighbour; an explicit-rowid insert replaced a row too, which
  an id check alone would not stop. So, on all three tables: a `BEFORE INSERT` guard refuses a
  reused id **or an explicit existing rowid** (`new.rowid` is -1 when none was given, measured),
  and on `notes` and `note_proposals` a `BEFORE UPDATE` guard refuses changing an id or rowid
  (`approval_log` already refuses every update). Five new triggers. **The pragma option, reported
  and not taken:** `PRAGMA recursive_triggers = ON` makes SQLite fire the delete triggers for
  REPLACE, but it is per connection, so every connection (`db.connection()` and any other tool)
  would have to set it, and it changes trigger semantics for the whole database. That is a stop of
  its own; the guards need no pragma.
- **Tested, 4 insert forms x 3 tables:** `INSERT OR REPLACE`, `REPLACE INTO`, `INSERT OR IGNORE`
  and `ON CONFLICT DO UPDATE` each refused on a reused id with the guarded row (a **decided**
  proposal, an original note, a log row) unchanged; an explicit rowid on each table; `UPDATE OR
  REPLACE` of an id and of a rowid on `notes` and `note_proposals`; the FTS index consistent after
  an attempt; ordinary inserts still work. **16 mutations, each killed:** every new trigger
  dropped and neutered while keeping its name, each rowid half removed, the rowid half of the update
  guard removed, and both evidence checks removed. (One mutant first survived: a separate
  `json_type` check was redundant with `json_array_length`, so it was removed rather than tested.)

## Built (cumulative)
- `program/memory/migrations.py`: `_v8_notes`. Its full text is in the review message.
- `program/memory/db.py`: `APPROVAL_DECISIONS` and `record_approval` (no lock, retry or timeout
  behaviour touched; it takes the caller's connection, like `_insert_chunk_row`).
- `docs/DB_SCHEMA.md`: a Notes section. `tests/test_db.py` and `tests/test_migrations.py`: the two
  existing assertions updated for the new tables and the latest version.
- `tests/test_notes_schema.py`: 105 tests, all against direct SQL.
- **A design departure approved at review:** the FTS content is the `active_notes` view (found
  while building: the content check fails once any note is retired when the content is `notes`).

## Tested
Full suite 1,595 passed, 4 skipped; `ruff` clean.
- **Forced failure, B3's method,** at two points: the original error surfaces, the version stays 7,
  **no** Notes object survives, a version-7 artifact row is untouched, a clean retry reaches 8.
- **FTS:** ten transitions with the content check after each; the sequence test with `rebuild`
  and drift detection and repair.
- **Status rule:** every non-pending insert refused; each terminal flip works; all 20 edges out of a
  decided state refused; 11 columns each frozen; a pending row can still be edited.
- **New guards, one proven-to-bite test each** (mutations run with `PYTHONDONTWRITEBYTECODE=1`,
  each by neutering the guard while keeping its name, or removing it):
  `notes` delete guard; decided-proposal delete guard (and a variant that also refuses pending
  deletes); `resulting_note_id` CHECK (removed, and one status dropped); decision CHECK
  reintroduced; `record_approval`'s decision check, capability check, constant extended with the old
  `applied`, and a `commit()` inside it; the `VACUUM` scan with a planted statement.
- Earlier mutations re-run on the new structure: every trigger neutered, the view replaced by
  `notes`, each CHECK and FK removed, `executescript` restored.

## Open, for the next review or later
- No guard on a `notes` row's **text** (direct SQL can edit it); the design puts that in the tools
  and `scripts.note`.
- `approval_log` has no foreign key on `subject_id` (it is shared across subject kinds).
