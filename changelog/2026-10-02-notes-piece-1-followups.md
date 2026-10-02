# 2026-10-02 — Notes piece 1 follow-ups: frozen inactive notes, rowid pins, FK pin, REPLACE audit

Review rulings after piece 1 was committed. **Migration 8 was edited in place**, which is allowed
only because it has never run on real data: **read-only `SELECT MAX(version) FROM schema_version`
on the real `data/working.db` returned 6** (versions 1–6; the file's mtime unchanged by the read).
That is neither of the two cases the instruction named (7: edit in place; 8: add migration 9).
Version 6 means migrations 7 and 8 are both still pending there, so editing 8 in place is the same
situation as the "7" case. (The soak copy used for the journal runs is at 7: migration 8 existed
only after those runs.)

## Built
- **`notes_inactive_is_frozen`**: a `BEFORE UPDATE ON notes` trigger, `WHEN old.status <> 'active'`,
  refusing every change to a retired or superseded note, including a direct `UPDATE` back to
  `active`. Retiring and superseding are updates *from* `active` and still work.
- **Three `AFTER INSERT` triggers refusing a negative rowid**, found while pinning the next item:
  `new.rowid` is -1 in a `BEFORE INSERT` trigger when none is given, so an **explicit rowid of -1
  could not be told from none and slipped past the REPLACE guards**. Refusing negative rowids after
  the insert aborts the whole statement (a REPLACE's deletion included), so no row can ever sit at
  -1 to be replaced.
- Tests (`tests/test_notes_schema.py`, 105 -> 145):
  - every column of a retired and of a superseded row frozen (15 assignments each, a no-op update
    included); an active note can still be edited, superseded and retired; a frozen note stays out
    of the index and the index stays consistent;
  - **`new.rowid` is -1 in a BEFORE INSERT trigger when none is given**, pinned on the real table
    with a temporary trigger (and 4242 when explicit);
  - **the connection helper's `foreign_keys` setting is what refuses a bad reference**: the same
    five bad inserts into `notes` and `note_proposals` are refused through `db.connection()` and
    **accepted through a plain `sqlite3` connection** (SQLite's default is off). The guarantee is
    the helper's, and any code that opens its own connection loses it;
  - negative rowids refused on all three tables, for -1 and -5.
- Two existing FTS tests changed, because the new trigger makes editing or reactivating a
  retired note impossible by design.

## Mutations (PYTHONDONTWRITEBYTECODE=1), each killed
Frozen trigger: never fires, covers retired only, covers superseded only, also refuses active, dropped.
Negative-rowid triggers: each dropped and neutered (6). `foreign_keys` pragma turned off in
`db._configure`. One equivalent mutant is recorded, not hidden: changing the guards' `<> -1` to
`<> 0` survives, because with negative rowids refused nothing can sit at -1, so that clause is a
harmless optimisation; the pin test documents the assumption it rests on.

## The REPLACE audit (report only)
`grep -rnE "OR REPLACE|REPLACE INTO|ON CONFLICT" program scripts ops` finds three hits:
- `program/settings/store.py:528`: `INSERT INTO settings … ON CONFLICT(key) DO UPDATE SET …`.
  Table `settings`. This is an upsert, which runs UPDATE triggers, not REPLACE's silent delete.
- `program/memory/migrations.py:458` and `:619`: **comments** in the Notes migration.

**None targets `chunks`, `messages` or any FTS-backed table.** Recorded in `BUILT.md` as a latent
gap, with this grep as evidence. Two things the audit also shows, for the record: `chunks_fts_delete`
is an `AFTER DELETE` trigger and a `REPLACE INTO chunks` would skip it exactly as the Notes delete
guards were skipped; and **`archive.sql` has no triggers at all**, so the archive's append-only rule
is a code convention, with nothing in the schema stopping an `UPDATE`, `DELETE` or `REPLACE` of a
message. Neither is changed here.

## Tested
Full suite 1,653 passed, 4 skipped; `ruff` clean.
