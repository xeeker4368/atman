# 2026-09-02 — Design docs: resolved/open markers on nine open questions

**Tier 0/1 · documentation only · not gated.**
Committed in `f5ee598`, whose message describes the Phase 1 checkpoint script;
this entry covers the substantive part of that commit, the two design docs.

## Summary

`docs/RETRIEVAL_DESIGN.md` and `docs/DB_CONTENTION_DESIGN.md` both ended with an
`## Open questions for approval` section listing questions that had since been
answered — by approval, by what was actually built, or by an explicit decision
to defer. Nine questions across the two files now carry their status inline,
in the pattern `docs/SOUL_AND_PROMPT_DESIGN.md` already uses.

**Four of the five retrieval questions and all four contention questions are
marked RESOLVED. One is marked OPEN**, because it is.

## What was applied

`docs/RETRIEVAL_DESIGN.md` — heading now reads
*"Open questions — four resolved, one open (D4's lexical floor)"*:

| # | Question | Marker |
|---|---|---|
| 1 | D7's position — siblings post-fusion | **RESOLVED: yes, attached post-fusion** |
| 2 | D5's `ids=` allow-list scaling | **RESOLVED: accepted as-is** |
| 3 | D4's lexical floor — relative vs. absolute | **OPEN, not resolved** |
| 4 | D1's OR semantics | **RESOLVED: confirmed, with one consequence still open** |
| 5 | `VectorStore.query()` gaining `ids` | **RESOLVED: yes, it belongs in this task** |

`docs/DB_CONTENTION_DESIGN.md` — heading now reads
*"Open questions — all four resolved"*:

| # | Question | Marker |
|---|---|---|
| 1 | Decorator vs. a `write(fn)` helper | **RESOLVED: decorator** |
| 2 | Attempt count vs. wall-clock deadline | **RESOLVED: deadline** |
| 3 | Making `busy_timeout` configurable | **RESOLVED: yes** |
| 4 | Should `retry_on_locked` log? | **RESOLVED: yes, WARNING on both paths** |

Each marker is appended beneath the question as originally written, never in
place of it. The questions stay readable as asked, which is the point of keeping
them at all.

## Where each answer came from

**No answer was written from memory or inference.** Each was sourced from the
design doc's own body, the corresponding changelog, or the shipped code, and
several were checked against all three:

- `2026-09-01-task-1-5-hybrid-retrieval.md` names open questions 2, 4 and 5 as
  approved, in those words.
- `2026-09-02-db-write-contention-retry.md` §"Why a deadline, not an attempt
  count" is the source for contention #2, and the eight decorated functions for
  #1 — verified against `@retry_on_locked` at `program/memory/db.py:272, 330,
  348, 362, 452, 487` and `program/settings/store.py:405, 438`.
- Contention #3 and #4 were read off the code rather than any doc:
  `config.db_busy_timeout_seconds()` at the three `sqlite3.connect()` sites, and
  the two `logger.warning` calls in `retry_on_locked`.

## The one marked OPEN, and why it is not marked RESOLVED

D4's lexical floor was first drafted as *"RESOLVED: agreed, and deliberately
left open"*. That is a contradiction, and it was caught in review before it
landed. Nothing was decided about the lexical floor: the mechanism ships as an
absolute comparison because that is what an uncalibrated placeholder should be,
and whether it must become relative is a calibration-time question that this
corpus cannot answer, since `bm25()` magnitude scales with query term count.

The marker says **OPEN, not resolved** and closes with *"Do not read this as
decided."* The redundancy is deliberate — it sits in a numbered list whose other
four items begin `RESOLVED:`, and a reader skimming shapes rather than words
should not be able to mistake it.

One sourcing error was also caught and removed in review: the first draft cited
the 0.658 / 0.5567 off-topic distances as evidence under the *lexical* floor.
Those are cosine distances belonging to D4's *vector*-floor argument. The
document keeps two separate arguments for two separate floors, and the marker
now rests only on the term-count-scaling argument, which is the one that is
actually about BM25.

## Files changed

- `docs/RETRIEVAL_DESIGN.md` — heading, plus five markers.
- `docs/DB_CONTENTION_DESIGN.md` — heading, plus four markers.
- This changelog.

**Nothing else.** D1–D9 and C1–C8 received no hunks at all; the staged diff was
73 insertions and 2 deletions, and both deletions are the old heading lines.
No code, no tests, no `config/defaults.toml`, no `BUILT.md`, no `NOW.md`.

## Tests run

None, and none are possible: the change is markdown prose in two design
documents, with no code path, no assertion and no runtime behaviour behind it.
Stated explicitly rather than skipped silently, per `AGENTS.md`.

What was verified instead, by reading the diff rather than trusting the edit:
every replacement was applied through an exact-string match asserted to occur
exactly once, and `git diff --cached` was read in full to confirm the only
removed lines were the two headings.

## Known limitations

- **`docs/DB_CONTENTION_DESIGN.md` still names settings that do not exist**, and
  this change did not fix it. C4's policy table gives all three keys under a
  `db.*` namespace (lines 168–170), and C6's body text refers to
  `write_retry_attempts = 1` as the way to disable the fix (line 237). The
  shipped configuration is `database.busy_timeout_seconds`,
  `database.write_retry_deadline_seconds` and
  `database.write_retry_base_delay_seconds` — the `database.*` namespace, and
  **there is no `write_retry_attempts` setting at all**; `grep` finds that name
  only inside this document. C6's real mechanism is setting the *deadline* to 0,
  which is what `test_without_retry_contention_actually_fails` does.

  Not corrected here: the scope of this change was the open-questions markers,
  and the correction is its own not-yet-scoped task. It is recorded in this
  changelog specifically so the conversation that found it is not the only place
  it exists. Note that marking the section resolved makes the document read as
  freshly reviewed, which raises rather than lowers the chance a stale line
  inside it gets trusted.

- **Line 299 is not part of that problem.** The contention #2 question text
  quotes `write_retry_attempts = 4` because that is what was asked at the time,
  and the question is retained verbatim above its marker by design. Historical
  question text is a record of what was asked, not a claim about what exists.

- **Retrieval #4 is marked RESOLVED but carries an unresolved consequence** —
  stopword-only matches earning an RRF contribution. That is stated inside the
  marker rather than hidden by it, and it remains flagged for decision in
  `BUILT.md`. The heading's count is of questions as asked.

- **`scripts/checkpoint_queries.py` was untracked at the time this discrepancy
  was found.** `f5ee598`'s message describes adding it, but that commit contains
  only the two design docs. It was committed afterwards in `3a8785b`, alongside
  this changelog. (It was first committed on its own as `84b31b1`; that commit
  is not reachable from `main` — it was superseded — so `3a8785b` is the
  reference that resolves.)

## Follow-up

- Scope the C4 / C6 correction above as its own task.
- D4's lexical floor stays in `NOW.md`'s "Retrieval floor calibration" backlog
  entry; the marker now points at it from the design doc.

## Project Anam alignment check

Documentation only — no schema, no runtime behaviour, no entity surface.

4. Preserve raw experience? **N/A** — no data path touched.
8. Context construction inspectable? **Unchanged.**
10. Anam/entity distinction preserved? **Yes** — no entity-facing text involved.
11. Migration required? **No.**
12. Tests? **None possible**, stated above rather than skipped.
13. Core substrate changed unnecessarily? **No** — no code file was opened for
    writing.
16. Casual legacy renaming avoided? **Yes.** `reference/old-anam/` was not
    consulted.
