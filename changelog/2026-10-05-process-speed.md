# Process speed-up until go-live, and scripts/leak_check.py (Tier 2)

**What changed.** `AGENTS.md` gains "Until go-live: the faster process", in force until Phase 10
begins and lapsing then: (1) prompt-facing text, `soul.md`, `operational.md` and `working.db`
schema are Tier 2, while auth, `db.py` locking and atomicity, the `archive.db` schema, restore, the
wipe and external effects stay Tier 3; (2) no separate design round for Tier 1 and 2; (3) docs per
piece capped at about a page of design and 30 lines of changelog; (4) a gate re-measurement only
when what the classifier sees changes; (5) the independent-reader rule and the 20-run escalation
only when a decision depends on the finding, with other numbers labelled descriptive (decision #22
annotated); (6) two lanes, if no common file and only one makes model calls; (7) a test-only change
that removes a clock, date, environment or ordering dependence is not a stop; (8) the full report
is a file and the reply ends in a merge checklist; (9) a clean Tier 0 to 2 checklist may be merged
without external review. `BUILD_PLAN.md`, `CLAUDE.md` and `docs/GIT_WORKFLOW.md` point at it.

**`scripts/leak_check.py`** scans every commit in a range (default `@{upstream}..HEAD`): added diff
lines, file paths and commit messages, against plain-text patterns in a file outside the
repository (`~/.config/anam/leak-patterns.txt` or `$ANAM_LEAK_PATTERNS`), case-insensitively. It
fails closed (exit 2) on a missing or empty file, an unresolvable range or no upstream, and on a
match prints the pattern, commit and file but never the line. The pre-push line to add by hand is
in `docs/GIT_WORKFLOW.md`; `.git/hooks` was not edited.

**Tested.** `tests/test_leak_check.py`, 11 tests, each on its own temporary repository and pattern
file. Eight mutations (messages unchecked, a missing or an empty file passing, the line printed,
only the tip checked, case-sensitive matching, removed lines counted, no upstream passing) each
fail a test; two of them missed at first, and the tests were strengthened until they bit.

**Limitations.** A binary file's content is not read (its path is printed as a warning). Plain-text
patterns only, no regular expressions. The rules in items 1 to 9 are process, so no test enforces them.
