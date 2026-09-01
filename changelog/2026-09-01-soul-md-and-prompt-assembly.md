# 2026-09-01 — soul.md seed + `build_system_prompt()`

**Tier 3 · Opus · design approved (revision 2) before implementation.**
Design of record: `docs/SOUL_AND_PROMPT_DESIGN.md`, cited as S1–S12.

## Summary

`anam/integrity/soul.md` holds the approved seed text. `anam/engine/prompt.py`
assembles each turn's prompt from it plus the current-situation block, retrieved
chunks and windowed history — and enforces five constraints that all raise.

## Files changed

Created: `anam/integrity/soul.md`, `anam/engine/prompt.py`,
`tests/test_prompt.py` (52).
Modified: `BUILD_PLAN.md` (the two approved edits), `BUILT.md`.
No schema change, no change to `history.py` or `retrieval.py`.

## soul.md — 3,401 characters, verified against the design, not retyped

Rather than transcribe the approved text by hand, I extracted the quoted block
from `docs/SOUL_AND_PROMPT_DESIGN.md`, unwrapped it, and compared it
word-for-word against the file being written: **618 words, identical**. The
stored file is 3,401 characters, matching the design's claim exactly.

`test_soul_md_char_count_matches_the_design_document` asserts 3,401 as a test
rather than a one-time eyeball, so later drift fails the suite. (The design's
figure counts the trailing newline; stripped content is 3,400.)

## Two implementation findings — the checks initially rejected valid content

Both were found by running the checks against the real `soul.md`, and both are
worth recording because the naive implementation looked obviously right.

**1. The naming check rejected soul.md's own required sentence.** The first
pattern for entity-naming included a bare `(?:named|call|called)\s+(?:you\s+)?
Anam`, which matched *"The system you run on is **called Anam**"* — the sentence
that holds the substrate/entity distinction up and is mandatory content. A
blanket ban on the token is impossible here precisely because naming the
substrate is the point. Tightened so the object being named must explicitly be
the entity (`call you Anam`, `you are Anam`, `your name is Anam`, `I am Anam`,
plus `Anam` as the subject of a thought or speech verb — the collapses CLAUDE.md
names). A parametrised test asserts both directions.

**2. The trait check rejected "kind" as a noun, and would have rejected Phase
4.** The design's S9 row says authored text must not *assign* personality
adjectives; my first implementation used a bare word list, on the reasoning that
authored text is small and controlled so conservatism is cheap. That reasoning
was wrong:

- *"You are your own **kind** of entity"* — soul.md's opening line, a noun.
- *"**creative** writing"* — a core capability and GUIDANCE.md's own term. Phase
  4's clause ("declining to share your creative writing") would have failed to
  assemble.

Rewritten as **assignment-context matching**, which is the more faithful reading
of "must not assign": patterns for `you are <trait>`, `your <trait>
nature/tone/manner`, `be <trait>`, `you tend to be <trait>`, `you have a <trait>
streak`. `kind` and `creative` are additionally dropped from the word list since
their non-trait senses dominate in this project; `imaginative` and `artistic`
still cover the genuine assignment. Tests cover both directions, including
Phase 4's exact future phrasing.

Neither was a design change — the design specified the constraint, not the
regex — but both are the kind of detail that would have shipped silently and
then blocked a later phase.

## The five S9 checks, all raising

| Check | Enforced in | Proven by |
|---|---|---|
| Required markers | `load_soul()` | deleting each statement raises; a *rewording* still passes |
| Size ceiling | `load_soul()` | oversize raises, file on disk unmodified |
| Entity naming | `check_authored_text()` | 10 forbidden forms raise; 5 legitimate ones do not |
| Trait assignment | `check_authored_text()` | 7 assignments raise; 4 non-trait uses do not |
| Elapsed-time pairing | `build_system_prompt()` / `assemble_turn()` | both directions tested |

None degrades, none logs-and-continues. The module docstring states why, against
`retrieval.py`'s opposite choice and using the same criterion recorded there:
*abort when a failure could corrupt something or when retrying is free; degrade
when nothing can be corrupted and a person is waiting.* A retrieval leg failing
costs a worse answer; a prompt that states elapsed time without its pairing
corrupts the thing the build exists to get right, and does so invisibly.

**Required markers are alternative phrasings, not one exact string.** Phase 10
is a wording pass and will rephrase; a single hardcoded sentence would fail on a
reword that preserved the meaning. What no alternative covers is the concept
being *deleted*. If a future rewrite drops every listed alternative it raises —
intended, and the fix is to add the new phrasing deliberately rather than weaken
the check.

## The scope-limit test is the one that matters

`test_the_scope_limit_holds_retrieved_and_history_are_never_checked` takes one
string containing *"Anam thinks..."* and *"Anam said..."*, and proves it:

- **raises** when passed as authored text, and
- **passes straight through** when it arrives as a retrieved chunk, appearing
  verbatim in the assembled system prompt.

That is the design's explicit boundary. Lyle genuinely discusses "Anam" the
project and the seed corpus contains such a conversation; censoring a real memory
to satisfy prompt hygiene would corrupt the record, which is worse than the
failure being prevented.

## Assembly and budget (S11/S12)

Order is soul.md → situation → retrieved records in the system string, with
windowed history as the separate message array. A test asserts the index order,
because soul.md preceding the elapsed figure is load-bearing: stating the gap
before the rule that says what it means is the confabulation ordering.

Chunk timestamps render at presentation (`render_retrieved()`), restoring the
capability task 1.3 removed from chunk *text* without putting date strings back
into either index. Siblings render as `record N, continued M` under their parent.

Budget wiring is exactly what `plan_budget`'s caller-supplied inputs were built
for — `history.py` needed no change. The system prompt is measured first, then
history takes the remainder, and the two counts are passed **separately**:
`test_plan_budget_receives_the_right_character_counts` asserts the exact values
and that they were not pre-summed. `test_the_reported_parts_sum_to_the_system_
string` keeps the accounting honest.

Live run: system 3,840 chars (soul 3,400 · situation 206 · retrieved 230 ·
scaffolding 4), 29,247 tokens left for history.

## BUILD_PLAN edits (approved questions 2 and 4)

1. **Pairing contract recorded against the current-situation block task**, same
   treatment idle-close's obligations got against the agent loop: the
   no-experience clause must be emitted in the same block, adjacent to the
   figure, and `build_system_prompt()` already raises if it is not — so a block
   emitting the figure alone fails assembly rather than reaching the model.
2. **Chunk-timestamp cross-reference repointed** from the neighbouring task to
   this one, naming `render_retrieved()`.

## Tests: 52 new, 326 total

`ruff check .` clean.

## Known limitations

- **The naming and trait checks are tripwires, not proofs.** They catch the
  canonical forms CLAUDE.md names and the common assignment shapes. A
  sufficiently novel phrasing passes. The behavioural probe remains the real
  check; this only makes the *known* failures impossible.
- **`SOUL_MAX_CHARS = 6000` is a flagged judgment value**, headroom-derived.
- **No model has been run against this prompt.** Assembly is verified; whether
  the text produces the intended behaviour is task 7.2's question, and nothing
  here should be read as evidence about it.
- **The situation block does not exist yet.** Its pairing contract is enforced
  and recorded, but no code emits an elapsed-time statement — tests supply the
  string.
- **Cross-user disclosure (S7)** remains an open gap, not a settled decision.
  Recommendation stands that Lyle add a `NOW.md` entry before Phase 5's
  cross-user mining sharpens it. Its presence in `soul.md` does not decide it.

## Project Anam alignment check

1. Entity named? **No** — and this is the task that makes it checkable. The
   entity has no name in `soul.md`, and authored text is validated against
   naming it.
2. Anam-or-Tír leakage? **No.** `Anam` appears once, naming the substrate, in the
   sentence that holds the distinction. Any `Tír`/`Tir` form raises.
3. Personality assigned? **No** — actively enforced, with the assignment check
   proven against seven shapes.
4. Preserve raw experience? **Yes** — read-only. Retrieved chunks and history
   pass through verbatim and uncensored, deliberately.
5. Traceable derived artifacts? **Yes** — `AssembledPrompt` reports every
   segment's size and the full budget breakdown.
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Yes** — this is the task that makes the
   assembled prompt an inspectable object rather than a concatenated string.
9. Autonomy more cumulative? **Yes** — accumulated memory reaches the model here.
10. Anam/entity distinction preserved? **Yes**, and mechanically enforced.
11. Migration required? **No.**
12. Tests? **Yes**, 52.
13. Core substrate changed unnecessarily? **No** — `history.py` and
    `retrieval.py` untouched.
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **`soul.md` is package content, not runtime
    data** — outside `data_dir` and `workspace_dir`, so nothing that writes the
    entity's artifacts can reach it. Consistent with decision #15's no-seam rule.
16. Casual legacy renaming avoided? **Yes.** The prior project's `soul.md` was
    consulted under the authorised exception; no line was copied, the stale
    iMessage-shaped detail was dropped, and the persistence paragraph was
    rewritten around the named risk rather than carried over.
