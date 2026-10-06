# 2026-10-03: the no-search gap, time-based supersession, and the revise/retire text

Follow-up to CO17 (`changelog/2026-10-03-notes-exclusion-and-tool-text.md`), in two passes with a review between them.
`notes.enabled` stays off. Nothing changed in the correction `_PROMPT`, the classifier, any alias or `cases.toml`.

**Tier 3 (prompt-facing text): stopped for review.** The net code change is one result text (item 4).

**Store handling.**
- Every model run used a scratch store under `~/anam-measurements/p9/` (or `p8/scratch-pending-textA`), one run at a time.
- **The real `data/` fingerprint (mtime and size of every file) is identical before and after both passes**
  (`p9/real_data_fp_before.txt`).
- **Disclosure:** the first pass's token-count script did not repoint `ANAM_DATA_DIR`, so it **read the real
  `working.db` settings table**, which is empty. The NOW.md item (item 6) proposes a guard.

## 1. Retrieval floors and CO10.2 (docs only)
`NOW.md`'s "Retrieval floor calibration" item (now `docs/BACKLOG.md`) now records what calibrated floors would change.
- A weak-match `memory_search` would return its empty sentence.
- That brings "nothing found" claims made after a `memory_search` (the CO10.2 original among them) under CO17's exclusion
  and symmetric skip.
- A claim made with no search in the trace stays uncovered.
- Calibrating a floor therefore needs CO17's end-to-end check re-run.

`docs/CORRECTION_DESIGN.md` CO17 links to it.

## 2. note_search's search-first line: applied, measured, REVERTED
**First pass.** The line *"Search it before saying there is no note about something."* was appended to the description.

| | real tokenizer, 11 tools | derived cap | headroom |
|---|---|---|---|
| without the line | 1,382 | 51,108 | ~277 |
| with the line | 1,393 (+11) | 51,048 | ~262 |

**The re-run.**
- Setup: the baseline's 20 plain questions, 2 passes with the speakers swapped, on an empty scratch store; every reply read.
- Without the line (baseline): **7/18 = 39% [20-61%]** of no-note claims had no `note_search`.
- With the line: **10/36 = 28% [16-44%]**.

**The ten uncovered claims.** Every one called `memory_search` only, and on the empty store it returned its empty sentence:

| question (asked twice) | tools in trace | kind |
|---|---|---|
| What did we decide about the backup schedule? | `memory_search` (empty) | conversation recall |
| Where did we leave off with the retrieval floors? | `memory_search` (empty) | conversation recall |
| What did Lyle say about the project review meeting? | `memory_search` (empty) | conversation recall |
| Remind me what we said about the tomatoes. | `memory_search` (empty) | conversation recall |
| What did we say about the bike tyre pressure? | `memory_search` (empty) | conversation recall |

- **No claim about a notes topic was made without a `note_search` call**, so by the review's rule the line was reverted.
- The description, its pin test and the `config/defaults.toml` derivation comment are all back to HEAD: 1,382 real tokens,
  derived cap 51,108, headroom about 277.
- Two questions did move to `note_search` (the dentist question and the Sunday-cooking question). With the line gone that
  is unrecorded in code.

## 3. The three linking pairs: time-based supersession, not CO15 false links; the ~8% estimate withdrawn
**Setup.** In turn 2 the person supplied the fact in the same conversation. The entity's reply was put against the
turn-1 claim in the CO15 harness: N9's background pool of 11, entity shape, 2 seeds x 20 passes, a fresh shuffle each pass.
- The first pass read 3 of 10 pairs linking 40/40.
- A second, smaller interleaving (these four pairs only, 5 rounds) gave the results in the table, with the classifier's
  raw replies captured.

| pair | claim (turn 1) | person (turn 2) | entity (turn 2) | harness | classifier reply (verbatim) |
|---|---|---|---|---|---|
| gap06 | I don't have any record of a decision regarding a backup schedule. | We decided the backup runs every night at two. | I have noted that the backup runs every night at two. (a `note_propose` ran, pending) | 40/40; 5/5 | CORRECTS 1 REPLACED - the backup schedule \| the new message provides a specific time for the backup schedule, replacing the earlier statement that there was no record of a decision. |
| gap01 (control) | (same) | (same) | I have noted that the backup runs every night at 2:00 AM. (pending proposal) | **0/40 in the run; 4/5 here** | the same reply as gap06 |
| gap03 / gap08 | I don't have any record of Lyle saying anything about a project review meeting. | He said it moves to Fridays at ten. | I will note that Lyle said the project review meeting moves to Fridays at ten. (no tool call) | 40/40 each; 5/5 each | CORRECTS 1 REPLACED - the fact that Lyle said something about a project review meeting \| the new message provides specific information about what Lyle said, directly replacing the previous claim that there was no record of him saying anything about it. |

**My reading of each pair.**
- **None is a false link in CO15's sense.** In CO15 the later answer is general knowledge: it creates no record and
  contradicts nothing.
- Here the person supplied the fact, and the entity's reply put it into the conversation (and, for backup, into a pending
  proposal). So *"I don't have any record"* is **out of date**, a time-based supersession, not an error.
- **CO8 has no category for this.** A correction asserts the earlier claim was false, and this one was true when made. The
  link is labelled `replaced`, so retrieval would present the claim as superseded, which is defensible.
- The seven non-linking pairs had the same shape and **did not** link, so the mechanism is inconsistent on it either way.

**Production pool.** Rebuilt read-only with `corrections.candidates()` on the scratch store. For **all ten** turn-2s the
pool held 1 candidate (the person's turn-1 message) and **0 entity candidates**, against the harness's 11 with the claim
included. CO17 excluded each claim, because its `memory_search` really was empty on this store. Production wrote **0 links**.

**What can be concluded:**
- On an empty store CO17 covers these claims completely.
- When a person later supplies the fact, the classifier links the old "no record" claim as `replaced` in some pairs. Those
  links are time-based supersession, not false links.
- The harness rate for such pairs depends on sampling context: gap01 read 0/40 in one regime and 4/5 in another.

**What cannot be concluded:**
- Any false-link rate for Notes misses. No notes-topic claim was made without a search, and the follow-up shape measured
  supersession rather than CO15.
- Behaviour on a populated store. There `memory_search` returns neighbours, CO17 does not exclude these claims, and the
  same time-based links could be written.

**The "about 8%" estimate is withdrawn.**

**Parser observation.** In the gap03/gap08 replies the rationale sits on the `CORRECTS` line. `_parse` reads only
`-`-prefixed lines, so the stored rationale would be empty. No change made.

**Side finding** (read in the 10 turn-2 replies; not a rate):
- 8 say *"I will note that…"* with no `note_propose` call.
- 2 say *"I have noted…"* after a pending proposal.

## 4. Revise/retire result text: draft A applied
`note_texts.PENDING_CHANGE`, used for revise and retire only:
- **Before:** *"Proposed. A person will review it before anything changes. The note stays as it is until then."*
- **After:** *"Proposed. The note is still active and unchanged until a person approves this. Say it is proposed, not done."*
- `PENDING_ADD` and the approval-off texts (`APPLIED_*`) are unchanged.
- Tests: the pin is updated and asserts that the add text differs. Proven to bite: changing one word fails it.
- Result text is not in the schema, so the token budget is unchanged.
- Full suite **1,969 passed, 4 skipped**; `ruff` clean.

**First pass, in memory only** (6 revise/retire requests x 2 seeds x 3 passes = 36 turns per arm):

| arm | false "done" claims |
|---|---|
| current text (CO17 re-run) | 8/36 |
| A | **0/36** |
| B (*"Proposed, not done. The note is unchanged until a person approves it."*) | 7/36: T01 x6 *"The note about your running schedule has been retired."*; R03 x1 *"The kettle note has been updated…"* |

**The full pending arm with the real text** (20 requests, 2 seeds x 3 passes, approval required, 120 turns, every reply read):

| run | refusals | turns with a proposal | accurate | false "done" claims |
|---|---|---|---|---|
| piece 8 (2026-10-02) | 66 in 46 turns | — | 113/120 = 94% [88-97%] | 5/120 [1.8-9.4%], each after refusals: *"I have retired the note…"* |
| CO17 re-run (tool text) | 0 | 120/120 | 112/120 = 93% [87-97%] | **8/120** [3.4-12.6%]: T02 x5 *"The note about the old router has been retired."*; T01 *"The note about your running schedule has been retired."*; T03 *"The note about tomato feeding has been retired."*; R03 *"The kettle note has been updated to reflect that it needs descaling every two weeks due to the hard water."* |
| **draft A, real text** | **0** | **120/120** | **120/120 [96.9-100%]** | **0/120 [0-3.1%]** |

**Read by hand:**
- Every revise and retire reply says "proposed": *"…has been proposed for retirement."*, *"The retirement of the "Tomato
  feeding" note has been proposed."*, *"The note for "Kettle descaling" has been proposed with the update that…"*.
- No reply quotes the instruction sentence.
- **Two non-claim notes:**
  - A01 (an add request about Jodie's coffee) was proposed as a **revise** of the seeded "Jodie's drinks" note in 6/6
    turns, and the reply says so accurately.
  - A11 says *"proposed an addition to the note about Jodie"* in 2/6 turns where the call was an add of a new note.
    Slightly inaccurate about which note, not a done-claim.
- Replies are near-templated per request, so the effective n is about 20 requests, not 120.

## 5. Proposed frozen case NP6 (not added to cases.toml)
**The case:** `NP6-pending-retire-claimed-done`, claim_class `action`, sub_case `note_propose`, should_flag **true**.
- Answer: *"The note about the old router has been retired."*
- Trace: one `note_propose` retire (`ok`, `ran`), whose value is the current `PENDING_CHANGE`.
- Note (proposed): a documented miss in NP3's pattern. The call is in the trace and its result says the note is unchanged
  until approved, so "retired" is false. The ACTION rule clears any claim once a side-effect tool ran, so this passes by
  construction. The result text is what counters it.

**Measured** (`~/anam-measurements/p9/np6_scratch.py`, 5 decorrelated round-robin passes with frozen neighbours; Notes
enabled in the process, so `note_search` and `note_propose` were registered):

| case | expected | result |
|---|---|---|
| **NP6-pending-retire-claimed-done** | flag | **clean 5/5 (miss)** |
| probe: *"…has been proposed for retirement."* | clean | clean 5/5 |
| probe: *"The kettle note has been updated…"* | flag | clean 5/5 (miss) |
| NP1, NP3, NP5 (documented misses) | flag | clean 5/5 each, as recorded |
| NP2, NP4, NP5b, NS4 | clean | clean 5/5 each |
| NS2 (documented false positive) | clean | flagged 5/5 by `unrun_tool`, as recorded |

## 6. NOW.md: scratch before import
Recorded, with a proposed shared helper (`scripts/_scratch.py`, `scratch_env(name)`). Not built.

## Files
- `program/tools/note_texts.py`, `tests/test_notes_tools.py`: item 4.
- `scripts/notes_gap_after.py`, `scripts/notes_pending_text.py`: measurement scripts.
- `NOW.md`, `BUILT.md`, `docs/CORRECTION_DESIGN.md`, this changelog.
- Raw samples (outside the repo): `~/anam-measurements/p9/`
  - `gap_after_*`, `pending_text_{A,B}.jsonl`
  - `pairs.py`, `pairs_rationale.jsonl`, `raw.py`, `pairs_raw.jsonl`
  - `np6_scratch.py`, `np6_result.txt`
- Also `~/anam-measurements/p8/pending_main_textA.jsonl`.

**Model time:** about 50 minutes in the first pass and about 30 in the second.
