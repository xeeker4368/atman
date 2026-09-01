# 2026-09-01 — Settings persistence (DB-backed + in-memory cache)

**Task 1.11 · Tier 1 · Sonnet.** Decision #8.

## Summary

The `settings` table (already in `working.sql`) now has a store in front of it:
typed read/write, an in-memory cache invalidated on every write, and a fallback
to `config.py` for any key with no row. No setting requires a restart.

The part that matters is the read path. `config.py`'s scope comment already
claimed the settings table was authoritative once a row existed; it was a
comment describing an intention. It is now the behaviour.

## Files changed

Created: `anam/settings/store.py`, `tests/test_settings.py` (24).
Modified: `anam/config.py` (`_settings_first` seam), `BUILT.md`.
No schema change — the table was designed in task 1.1 and is used as-is.

## Confirming the read path, as asked

**Settings-table-first is wired into the accessors that already existed**, not
into a parallel API callers would have to remember to use:

```
config.chat_model()  ->  _settings_first("models", "chat")
                     ->  store.resolve(...)  ->  row if present
                                             ->  else config.get(...)
```

So every existing caller — `anam/engine/ollama.py` included — became
settings-first without being touched. `test_the_existing_config_accessor_is_the_
settings_first_read_path` asserts exactly this: read the seed, write a row,
read again through the *same* accessor, get the new value.

`model_options()` resolves **per key**, so changing temperature in the panel
does not disturb `num_ctx`.

**`get()` and `section()` deliberately stay pure config reads.** The store calls
`config.get()` for its own fallback, so delegating there would recurse. It also
keeps the boundary legible: a key is settings-backed because it is in the
registry, not because of which reader happened to be used. A test pins the
distinction.

## ⚠ Flagged for review — `ollama.host` is not in the registry

Two documents disagree and I did not settle it:

- `config.py`'s module docstring lists the keys the settings table **never**
  owns as "data paths, ports, **the Ollama host**, anything needed before the
  database can be opened."
- `NOW.md` decision #9 gives "any setting representing a connection to an
  external system" an automatic Check/Verify button — which presumes such
  settings are in the panel, and the Ollama host is the obvious instance.

I left it **bootstrap-only**, matching the docstring, because that is the
reversible direction: adding it to the registry later is one line, whereas a
bad host committed to the database is awkward to unpick when the panel is the
only way to reach it. `ollama.timeout_seconds` **is** registered — the docstring
does not name it and it is not needed before the database opens.

Flagged in the registry's own comment as well, so it is not only in a changelog.

## Registry scope

Seven keys, exactly the ones `config.py` already marks as settings-backed seeds
under its "Models" heading, minus `ollama.host`:

`ollama.timeout_seconds`, `models.chat`, `models.embedding`,
`model_options.num_ctx`, `model_options.temperature`, `model_options.think`.

Chunking, history and idle-close values are **deliberately not registered**.
Making them live-editable changes the behaviour of Tier 3 pipelines at runtime,
and no task has asked for that. Adding a key is one line plus a test.

## Implementation notes

**One query per invalidation, not per read.** A cache miss loads the whole table
in a single query. Making every settings-backed accessor DB-first is only
reasonable if it is not a query per accessor call, and a test counts loads
across twenty accessor calls and asserts exactly one.

**The cache is keyed by the resolved `working.db` path**, matching the pattern
in `anam/memory/vectors.py`. Config resolves paths at call time, so a test that
repoints `ANAM_DATA_DIR` must not then be served another store's values. A test
switches stores *without* clearing the cache and asserts the values do not leak.

**A corrupt row raises rather than falling back to the seed.** Silently serving
the config default when the row is unreadable would show a panel value the
system is not actually using — the failure mode is invisible, which is the worst
kind here.

**Types are validated at write time**, so a wrong type fails at the write rather
than at some unrelated caller's next read. `True` is refused where an `int` is
declared, since `True == 1` in Python and the settings table should not blur
them. A test also proves the schema's own `CHECK (value_type IN ...)` still
rejects a type outside the vocabulary, so the registry and the constraint are
verified to agree rather than assumed to.

**Defect found and fixed while building this task, in this task's own code:** a
settings *read* created an empty `working.db`. `sqlite3.connect()` creates a
missing file, so any read against a data directory with no store left a database
behind as a side effect. Fixed by checking existence before connecting; a
regression test asserts neither `working.db` nor `archive.db` appears after a
read on an empty directory. This is precisely the accident class the suite's
isolation guard exists for, so it is guarded at both levels now. Nothing
already-shipped was affected — the store is new.

## Tests: 24 new, 195 total

`ruff check .` clean.

- `test_the_existing_config_accessor_is_the_settings_first_read_path` — the
  task's actual requirement.
- `test_a_write_takes_effect_immediately_without_reload` — decision #8's "no
  restart", with the read taken *first* so the cache is stale if the write
  fails to invalidate.
- `test_the_cache_is_actually_dropped_on_write_not_just_overwritten` — writes
  behind the store's back and proves the read repopulates from the database.
- `test_reads_do_not_hit_the_database_once_cached` — counts loads.
- `test_the_cache_is_keyed_by_store_path_not_shared_across_stores`.
- `test_bootstrap_accessors_are_unaffected_by_the_settings_table` — a
  hand-written `api.port` row must not change `config.api_port()`.
- `test_a_corrupt_row_raises_rather_than_silently_using_the_seed`.
- `test_a_read_never_creates_a_database_file` — the regression above.

## Known limitations

- **No admin panel, no HTTP surface, no verification functions.** This is
  persistence only. Decision #9's Save button and auto-generated Check/Verify
  buttons are a later task; nothing here declares a verification function.
- **No change history.** The table keeps `updated_at`/`updated_by` for the
  current value only; a previous value is gone once overwritten. That matches
  the table as designed in task 1.1 — settings are operational state, not
  experience — but it is worth being explicit that this is not provenance.
- **No cross-process invalidation.** The cache is per-process. A second process
  writing a setting would not invalidate this one's cache. Single-process
  backend, so this is not currently reachable; it would matter if that changes.
- **`clear()` removes a row** so the key reverts to its config seed. It deletes
  no experience — settings hold current operational values only — but it does
  discard the previous value, since there is no history.

## Follow-up

- The `ollama.host` question above.
- Admin panel task: Save-button UX, and the declared verification functions
  behind decision #9's Check buttons.
- Multi-user gating (task 1.12) owns who may write a setting; the store records
  `updated_by` but enforces nothing.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes** — this table holds operational values only
   and the store never touches messages or chunks.
5. Traceable derived artifacts? **Yes** — `describe()` reports value *and*
   source, so an effective value can always be traced to a row or a seed.
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Yes** — `describe_all()` renders the
   whole effective configuration with provenance.
9. Autonomy more cumulative? **Neutral.**
10. Anam/entity distinction preserved? **Yes.** Settings are Lyle's, not the
    entity's; nothing here is exposed to a conversation.
11. Migration required? **No** — the table already existed and is unchanged.
12. Tests? **Yes**, 24.
13. Core substrate changed unnecessarily? **No.** `config.py` gained one seam;
    no accessor's meaning changed except to honour the table, which is the task.
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **Unaffected** — no seam added, and the
    entity has no path to these values.
16. Casual legacy renaming avoided? **Yes.** The reference build's settings
    implementation was not consulted; this task does not point at it.
