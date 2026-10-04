# 2026-10-04: docs restructure (Tier 0/1)

Documentation, comments and docstrings only. No behaviour change, nothing committed or staged, no server, no model call, no full pytest run.
Checks run: `ruff check .` (clean), `pytest --collect-only -q` (1,986 tests collected, the same count as before the change), `bash -n start.sh` (ok),
`tomllib` load of `config/defaults.toml` (parsed values identical before and after: sha256 `139f367c…`), and an AST comparison of `program/engine/ollama.py`
against HEAD (identical apart from docstrings).

## Start checks
- `git status` was clean and HEAD was `944cefb`.
- **No program or test code reads `BUILT.md`, `NOW.md` or `CLAUDE.md` as content.** 31 references exist; all are comments, docstrings or prose. Two mechanisms touch them by name:
  `program/artifacts/blocklist.py` hashes every root `*.md` and everything under `docs/`, `changelog/`, `config/` and `program/integrity/` by rule (so `ARCHITECTURE.md`,
  `docs/archive/` and `docs/measurements/` are covered with no code change), and `tests/test_blocklist.py::test_every_governance_file_is_blocked` names the root files in a
  parametrize list and skips one that is absent. Neither reads their content for any decision.

## Files changed (14)
| file | change |
|---|---|
| `CLAUDE.md` | `@BUILT.md` removed, `@ARCHITECTURE.md` added; "State of this repo" rewritten (22 lines); "19-entry" corrected to 23; "Planned architecture" replaced by "Architecture" |
| `README.md` | one line to a 55-line README |
| `NOW.md` | new Current state and Active task; B20 and B21 moved out with index entries; go-live checklist filled; decision log untouched |
| `docs/archive/NOW-closed-backlog.md` | new: B20 and B21 verbatim |
| `BUILD_PLAN.md` | a "Status:" line under each of Phases 0 to 10; one sentence in Phase 7 |
| `PROJECT.md` | "Status" paragraph replaced (two sentences) |
| `AGENTS.md` | "Working rules" section added; "Git hygiene" rewritten; prompt-facing text added to the stop list; the intro and "The loop" adjusted to match |
| `BUILT.md` | header block added; no other line changed (`git diff` shows 0 removed lines) |
| `ARCHITECTURE.md` | new: 313 lines, 573 citations |
| `docs/measurements/README.md` | new, two lines |
| `config/defaults.toml` | idle-close comment rewritten (comments only) |
| `start.sh` | usage text about the admin gate made true (text printed by `--help`; no logic) |
| `program/engine/ollama.py` | `chat()` docstring corrected |
| `changelog/2026-10-04-docs-restructure.md` | this file |

## Sizes
| | before | after |
|---|---|---|
| `CLAUDE.md` | 128 lines, 7,082 chars | 132 lines, 8,037 chars |
| `NOW.md` | 615 lines, 44,437 chars | 571 lines, 42,005 chars |
| characters `@`-imported by `CLAUDE.md` (itself plus its imports) | **432,290** (CLAUDE 7,082 + AGENTS 16,088 + PROJECT 4,718 + GUIDANCE 5,836 + NOW 44,437 + BUILT 354,129) | **163,163** (CLAUDE 8,037 + AGENTS 21,757 + PROJECT 4,733 + GUIDANCE 5,836 + NOW 42,005 + ARCHITECTURE 80,795), a 62% reduction |

`ARCHITECTURE.md` is now the largest import (80,795 characters) because every line carries its citations.

## What each edit rests on (item numbers are the brief's)
1. **CLAUDE.md.** Commands come from `pytest.ini` (`testpaths = tests`), `pyproject.toml` (ruff: line length 100, rules E, F, I), `start.sh` and `run_server.py`. The decision-log count
   is 23 (`grep -nE '^[0-9]+\. \*\*' NOW.md`). "14–16" and PROJECT.md's "14–15" are both right in their sentences (14 and 15 are self-modification and the review queue; 16 is the wipe), so CLAUDE.md now says which is which.
2. **README.md.** Script purposes are the first docstring line of each file in `scripts/`; the operator set and the not-yet-migrated set are from `tests/test_scratch_helper.py`.
3. **NOW.md.**
   - Last full suite: 1,982 passed, 4 skipped, `changelog/2026-10-03-store-not-migrating.md`, committed in `fb25f6e`; `git diff --stat fb25f6e HEAD -- program tests scripts config` is empty.
   - Dark or off by default: `tomllib` of `config/defaults.toml` gives `notes.enabled`, `moltbook.enabled` and `corrections.person_corrects_entity` all false.
   - **Moved as closed (2): B20 and B21.** Both carry "BUILT 2026-10-01 … (6652709)" and 6652709 and eb90f93 exist (`git log`).
   - **Go-live checklist.** Each line is from `BUILD_PLAN.md` Phase 10 or an open `NOW.md` item. The Docker memory cap and the Moltbook key rotation are **not in any repo doc** (`grep -rniE` over `NOW.md`, `BUILD_PLAN.md`, `ops/`, `docs/`, `changelog/`), so they are left out.
4. **BUILD_PLAN.md statuses** are derived from `BUILT.md` and from named tests: Phase 1 not built: Restore CLI, loopback check; Phase 2: the `db.py` contention task is partly done; Phase 3 stops at stage 1 (decision #23); Phase 5 has no research code (`tests/test_db.py` asserts no `research_candidates` table, and no `allow_*` flag exists); Phases 6 to 10 not started (tests and file listing named in each line).
5. **PROJECT.md.** The sentence "Nothing in this repo is a copy of that code" was replaced by "`AGENTS.md` forbids copying code from it", which is checkable.
6. **AGENTS.md.** Rules 1 to 7 and B1 to B3 are from `changelog/2026-10-03-agents-working-rules-draft.md`, Part C not added.
   - (i) In rule 3 I removed the clause "and no raw entity replies from real conversations" and the "See the open question under Part C" bullet. I read "the sentence about raw entity replies" as that clause; it is the only one.
   - (ii) Rule 1's server clause is yours, followed by one checked fact: server startup calls `db.init_databases()` (`program/api/app.py`, `lifespan`). I **dropped** the draft's "the real store is at schema version 6" because I did not open `data/` to verify it.
   - (iii) B3 says "when it needs under about ten minutes of model time on one process". Its supporting numbers (7/36 against 8/36) are in `changelog/2026-10-03-notes-search-line-and-gap-remeasure.md`, now cited.
   - The "(Review conversation)" provenance labels in the draft are carried unchanged; I cannot verify them.
7. **BUILT.md.** Header only. The "82%" is 354,129 of 432,290.
8. **Comment fixes.**
   - (a) `start.sh`: `program/api/routes/` holds auth, chat, health and upload; `grep -rn "request.client\|is_loopback" program` finds the web_fetch and ComfyUI address checks, no gate.
   - (b) `config/defaults.toml`: the floor is 35 (`IN_FLIGHT_GRACE_FLOOR_MINUTES`, 2,045 s) and the grace is 41, pinned by `tests/test_idle.py::test_the_floor_is_recomputed_from_the_loops_own_limits` and `test_the_shipped_grace_is_above_the_floor_in_every_layer`. I also corrected the paragraph above it, which still said the classifier term was 300 s and "could" be shortened; it is 45 s (`integrity.classifier_timeout_seconds`).
   - (c) `grep -rn prompt_eval_count program scripts` finds only the docstring; `eval_count` is read in `loop.py` and `journal.py` (to word a truncated-reply error) and in `scripts/measure_classifier_budget.py`.
9. **ARCHITECTURE.md.** Checker (`/tmp/arch/check.py`, not in the repo): a citation resolves if the file exists, a test name appears in `pytest --collect-only -q` (parametrize suffix stripped), and a non-test name is **defined** in its file (`def`, `class` or assignment).
   - **Result: 573 citations, 0 unresolved.** Along the way it caught: 2 wrong test names in the first section and 5 in the Tools section (replaced after looking up the real names), and one function cited as `backup` that is `create_backup`. One line I dropped because its cited test did not support it ("Moltbook is off in every test"). Three absence lines ("no TLS", "no admin route", "settings validate type only") were moved to "Known unverified" because a test cannot cite an absence.
   - **What the checker does not prove:** that a cited test actually demonstrates the claim. I chose each test by its name and read a few bodies; the rest rests on the names.
10. `docs/measurements/README.md`: two lines. No measurement table or changelog was touched.

## Commit sets (order: A, E, B, C, D, so `ARCHITECTURE.md` exists before anything imports or names it)
Lists are in the scratchpad as `commit-docs-{A-comments,B-agents,C-now,D-entry,E-architecture}.txt`. Every changed file is in exactly one set; none is shared.
- **A** `config/defaults.toml`, `program/engine/ollama.py`, `start.sh`
- **B** `AGENTS.md`
- **C** `NOW.md`, `docs/archive/NOW-closed-backlog.md`
- **D** `BUILD_PLAN.md`, `CLAUDE.md`, `PROJECT.md`, `README.md`
- **E** `ARCHITECTURE.md`, `BUILT.md`, `changelog/2026-10-04-docs-restructure.md`, `docs/measurements/README.md`

## Could not verify (left out or flagged)
- The real store's schema version (not opened). `config/local.toml` overrides (not read).
- Closed-looking `NOW.md` items left in place because they name **no commit**: the entity `users` row (marked CLOSED 2026-09-24; candidate `9a616cc`), the supersession-description item (Resolved 2026-09-30 by B12; candidate `d63206e`), and the scratch-helper item (built 2026-10-03; candidate `fb25f6e`, but it has a "Not done" part). Say if you want them moved.
- Phase 0's gate is derived from `BUILT.md` entries, not re-run.
- The Docker memory cap and the Moltbook key rotation (not in the repo docs).

## Left in place on purpose
The whole decision log; every open backlog item in full; `BUILT.md`'s content; all other docstrings the external review flagged (`resolve_time_window` and the rest); `NOW.md`'s first paragraph.

## Adjacent problems (reported, not fixed)
- `NOW.md` still says in two open items that "the journal is not built yet" (the corrections-do-not-reach-journal-chunks item and the `messages.integrity_check` item); the journal was built 2026-10-01.
- `NOW.md` line 3 says it is "overwritten each session"; git history now does that job. The decision-log preamble still says "before code exists".
- `BUILD_PLAN.md` Phase 1 and 2 rows keep placeholder numbers (idle-close 30/20) that the code replaced; historical text, left.
- `ARCHITECTURE.md` shares a name with `program/integrity/architecture.md` (the gate's rubric); the header says so.
- The citation checker lives only in `/tmp`; a copy under `scripts/` would let the next change re-run it.
- `scripts/correction_eval.py` and `scripts/fabrication_eval.py` are in `tests/test_scratch_helper.py`'s not-yet-migrated set: run without scratch environment variables they read the real settings table (the README now says to use scratch).
- `ARCHITECTURE.md` at 80,795 characters is the biggest import; if you want it smaller, the citations are the cost, not the claims.
