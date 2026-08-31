# 2026-08-31 — DB_SCHEMA.md: correct test references, close the coverage gap

**Tier 0/1 · not gated.**

## Summary

`docs/DB_SCHEMA.md` cited two different test names for what read as one
guarantee. Both tests are real and cover genuinely different things, but the
document attributed the wrong one to two bullets and claimed coverage that did
not exist for three more. Corrected the document and closed the gap.

## What the grep actually showed

Both names are real, in `tests/test_db.py`:

| test | line | covers |
|---|---|---|
| `test_later_phase_tables_are_absent` | 68 | `artifacts`, `research_candidates` |
| `test_deferred_features_have_no_tables` | 85 | `review_items`, `review_queue`, `self_mod*`, `behavioral_guidance*` |

So neither name was wrong — they are two tests for two different kinds of
absence, and the document had simply mixed up which paragraph belonged to which.

**But the header sentence was wrong in a second, worse way.** It read "each is
asserted by a test (`test_deferred_features_have_no_tables`)" above a list of
seven bullets. Checking each against the actual assertions:

| bullet | was actually asserted by |
|---|---|
| `review_items` | `test_deferred_features_have_no_tables` |
| self-modification-shaped | `test_deferred_features_have_no_tables` |
| `summaries` | **nothing** |
| `excluded_chunks` | **nothing** |
| `channel_identifiers` | **nothing** |
| `artifacts` | `test_later_phase_tables_are_absent` |
| `research_candidates` | `test_later_phase_tables_are_absent` |

Three of seven had no test at all. The document asserted a guarantee that was
not there — the same overclaiming pattern the Phase 1 checkpoint scoping exists
to prevent, in a different place.

## Resolution: two tests, each paragraph naming its own

Kept both tests rather than merging them, because they mean different things and
merging would lose that:

- **Out of this build entirely** — review queue, self-modification, summaries,
  excluded chunks, channel identifiers. Not coming back within this build.
- **Deferred to a later phase of this build** — `artifacts` (Phase 2/4) and a
  research-candidate table (Phase 5). Expected back, just not designed here.

A single test would have flattened "never" and "not yet" into one assertion, and
the distinction is the thing a future reader most needs.

Both docstrings now name the other test and state the distinction, so the two
cannot drift back into looking redundant.

## Closing the coverage gap

Extended `test_deferred_features_have_no_tables` to cover `summaries`,
`excluded_chunks` and `channel_identifiers`, each with a one-line comment naming
the decision behind it.

This is slightly more than the literal ask, which was to make the document
consistent. The reasoning: the document already claimed these were asserted, so
the honest options were to make the claim true or retract it. Making it true was
three lines and leaves the stronger guarantee. Say the word if you would rather
the claim had been retracted instead and the test left alone.

## Files changed

- `docs/DB_SCHEMA.md` — "Deliberately absent" split into two labelled groups,
  each naming the test that covers it. The trailing one-line paragraph that
  named `test_later_phase_tables_are_absent` is gone; that test is now named in
  its own group's heading.
- `tests/test_db.py` — three names added to the forbidden list; both docstrings
  rewritten to state the distinction.
- This changelog.

## Tests run

- `python -m pytest` — **54 passed**, unchanged count (no new test functions,
  three new assertions inside an existing one).
- `ruff check .` — clean.
- **Verified every test name the document cites actually exists**, by name and
  line, rather than trusting the edit: all three resolve
  (`test_journal_mode_is_delete_on_both` at 115,
  `test_deferred_features_have_no_tables` at 85,
  `test_later_phase_tables_are_absent` at 68).
- **Verified the three new assertions actually bite.** Injected each forbidden
  table into `working.sql` in turn, confirmed
  `test_deferred_features_have_no_tables` fails each time, and restored the
  schema. A guard that passes when violated is worse than no guard, and a
  three-line addition to a list is exactly the kind of change that can be
  silently inert. `git diff` confirms `working.sql` is byte-identical afterward.

## Known limitations

- The self-modification check is a substring match (`self_mod`, `selfmod`,
  `behavioral_guidance`) rather than an exact-name list — deliberate, since a
  seam is likelier to arrive under an unexpected name, but it means a self-mod
  table named something else entirely would pass. Now stated in the document
  rather than only implied by the code.
- These tests assert absence in `main` only. Nothing checks the archive for
  unexpected tables beyond `test_archive_has_exactly_two_tables`, which pins the
  exact set and so covers it by construction.

## Follow-up

None. Task 1.1 remains at its checkpoint awaiting sign-off; 1.2 not started.

## Project Anam alignment check

Documentation and test-coverage only; no schema change, no runtime behaviour, no
entity surface touched.

11. Migration required? **No** — `working.sql` is byte-identical, verified.
12. Tests? **Yes** — three assertions added and each independently verified to
    fail on violation.
