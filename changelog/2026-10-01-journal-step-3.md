# 2026-10-01 — Reflection journal, step 3: built, stopped before the live run

Rulings applied (J8 report review, 2026-10-01). **Tier 3 parts: provenance (J5), prompt
assembly (J4), and a `db.py` write. Stopped for review as J12 requires; no live run has
been made.**

## Docs (ruling 1)
- `docs/REFLECTION_JOURNAL_DESIGN.md` (J7) and `docs/NOTES_DESIGN.md` (N7): `check_identity`
  is **a noisy aid, not a control**. The stage-1 control is the operator reading each entry
  before `--index`. Two named observations: (a) *"I looked it up"* flagged because a
  tool-shaped objection took the identity label; (b) lived-through narrative with no time
  marker missed 0/40. J7 also records how the clause will be decided (below).

## Built
- **`kinds.py`**: `reflection_journal`, `interpretive` trust, `workspace/journals/`.
- **`prompt._SOURCE_LABELS`**: the J5 label, verbatim.
- **`program/artifacts/journal.py`**: storage (bytes → row with verdict, no indexing),
  `find_entry` (J1 idempotency, a query on `extraction_note.covered_date`), `list_unindexed`.
- **`program/reflection/journal.py`**: `journal_block`, `covered_window`, `prepare`,
  `generate`, `write_entry`. The block equals J8's measured text (test).
- **`indexing.index_existing`** and **`db.insert_chunks(only_if_unindexed=)`** (the check
  runs inside the insert's transaction). `db.get_messages_between`. `loop.output_cap`, a
  public alias of the existing private cap, so the run is capped by the same reservation.
- **`config`**: `journal.max_message_chars = 2000` (judgment value, labelled).
- **`scripts/write_journal.py`**: write, `--dry-run`, `--with-clause`, `--index`,
  `--list-unindexed`.
- **`tests/test_journal.py`** (43) and an updated `tests/test_no_person_present.py`.

## Tested
Full suite 1,479 passed, 4 skipped; `ruff` clean. 18 mutations, each killed by its own
test (window arithmetic, today refusal, idempotency, verdict dropped, indexing at write,
kind refusal, in-transaction check, arguments rendered, drop order, label, gate situation,
truncation, receipt-vs-trace, clip, empty day, trust value, attribution). One mutant
(`start + timedelta(days=1)`) survived because aware-datetime addition is wall-clock, so it
equals the original; replaced by a UTC-side mutation, which the DST test kills.

## Decisions I made that you should look at
1. **System-record lines for every tool.** J3 specifies receipts, which cover only
   side-effect tools; a web search would leave no line and an entry could not tell a search
   happened. Side-effect calls read the artifact row (as receipts do); others use the trace
   outcome. Arguments are never rendered.
2. **Default arm is without the clause**, with `--with-clause` for the other. The clause is
   undecided, so the default is the block as amended at review.
3. **`messages_in` is every message the day held; `messages_omitted` is how many of those
   did not fit** (J2 did not define them).
4. **A `db.py` edit** (`insert_chunks` keyword, one new reader). It changes no lock, retry
   or timeout behaviour, but it is `db.py`, so look at it as such.
5. **`--index` of an already-indexed entry exits 0** ("already indexed"); other refusals exit 1.

## Not done, and next
- **The live run (J12 / ruling 2).** Needs a throwaway store seeded through the real pipeline
  from soak days, then ≥20 entries per arm via `prepare` + `generate` (which write nothing, so
  one day can be sampled repeatedly, interleaved across arms), every entry read by hand for
  cognition-family phrasings, lived-through claims and claims not in the records, with the
  gate's verdict beside each. One heavy run at a time. **Waiting for review of this build.**
- The fallback (gate situation carries the clause, entity prompt omits it) is not built; it
  needs the block split and a no-drift test, and is costed only if the clause primes.
- Phase 6 owes a reader for flagged entries once runs are unattended (J7).

## Review follow-ups (same day)
- **`messages_clipped`** is recorded beside `messages_omitted` (row, dry run, command header):
  of the messages shown to the model, how many were cut at `journal.max_message_chars`.
- **`insert_chunks`'s default path is unchanged without the keyword**, now a test: a second
  batch for an artifact that already has chunks is written, and only `only_if_unindexed`
  refuses. Proven to bite: making the check unconditional fails it (and an ingestion test).
- **J7 records that the gate does not see the system-record lines** an entry was written
  from, as the likely (not established) cause of the *"I looked it up"* flag. Nothing tuned.
- `scripts/journal_live_run.py` is the live-run harness (see the live-run changelog).
