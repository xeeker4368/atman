# NOW.md

This is the single place for "where things stand right now": current state, the active task, the
index of decisions and the go-live checklist. Git history holds the earlier versions. Do not let a
second doc grow up beside this one to track the same thing.

- **Decisions** (the full text, treat every entry as DECIDED): `docs/DECISIONS.md`.
- **Open items** (the backlog, deferred and not forgotten): `docs/BACKLOG.md`. Closed items:
  `docs/archive/NOW-closed-backlog.md`.

---

## Current state

Code exists: the test count is whatever `pytest --collect-only -q` reports. Phase status is in `BUILD_PLAN.md` ("Status:" under
each phase) and the history in `BUILT.md` (frozen; `ARCHITECTURE.md` holds the current invariants).
- **Built:** Phases 0 to 4. Two Phase 1 items are owed: the Restore CLI (not built) and the loopback check (deferred to Phase 9).
- **In progress:** Phase 5. Built: reflection journal, read-only Moltbook tools, Notes pieces 1 to 8, the CO17 exclusion. Not built: bounded research
  execution, the self-flag tool, periodic mining. **Not started:** Phases 6 to 10.
- **Also built ahead of its phase:** the chat page (decision #31, `8b0f45b`); the gate's ordinary reading, the speaker
  line on every turn and the earlier-tools list (decision #32, `b4eab13`).
- **HEAD:** see `git log`.
- **Last full suite on record:** 2,272 passed, 23 skipped, 0 failed, `ruff` clean, at `ulimit -n 256`, 2026-10-22, on `cc/notes-refreeze-design` at `6efd943` (the docs commit after it changes no code); the 23 skips are the live tests and two opt-in Moltbook tests.
- **Dark or off by default** (`config/defaults.toml`, parsed with `tomllib`): `notes.enabled = false`, `moltbook.enabled = false`,
  `corrections.person_corrects_entity = false`, `search.fallback = "none"`, `search.store_snippets = false`. A local `config/local.toml` can override them; it was not read for this block.

**Merged to main** (each found with `git log --grep`):

| piece | commit | record |
|---|---|---|
| Piece 4a (small fixes) | `e9bb8bc` | `changelog/2026-10-04-piece-4a.md` |
| Piece 3.5 (lifecycle and concurrency, Tier 3) | `c676888` | `changelog/2026-10-04-piece-3.5-lifecycle.md`, `docs/DESIGN_3.5_2026-10-04.md` |
| Soul/rubric (decision #24; `A2-fabricated-save` became the tenth documented failure) | `ff839b8` | `changelog/2026-10-04-soul-rubric.md`, `docs/FABRICATION_GATE_DESIGN.md` revision 10 |
| Soul v2 (decision #25; gate re-measured, baseline reproduced) | `7f9e674` | `changelog/2026-10-05-soul-v2.md`, `docs/SOUL_AND_PROMPT_DESIGN.md` revision 6 |
| Process speed-up until go-live (`AGENTS.md` "Until go-live") | `4fd5141`, `0f0d075` | the commit messages |
| Batch 1 (3.4b local times, 3.6 part A record origins, 3.3 option B earlier-tools line, test fd hygiene) | `8890489` | `changelog/2026-10-05-batch-1.md` |
| Docs sync (decisions #26 to #30; decision log and backlog moved to `docs/`) | `4e11a38` | the commit message |
| Web UI (decision #31) | `8b0f45b` | `changelog/2026-10-06-web-ui.md` |
| Gate ordinary reading, piece 3.1, speaker line, earlier-tools list, refreeze, A1 known failing (decision #32) | `b4eab13` | `changelog/2026-10-08-gate-ordinary-reading.md` and the 2026-10-06 to 10-09 entries beside it |
| Pre-run-4: retrieval capture (migration 9), recall runner with Notes off, the soul sentence on its own states (decisions #33 to #35) | `c306042` | `changelog/2026-10-09-retrieval-capture.md`, `changelog/2026-10-09-soul-states-sentence.md` |

**Run 3** (2026-10-06, code `8890489`; descriptive, nothing here is a frozen finding; raw data outside the
repo, rule 3). Compared with run 2:

| measure | run 2 | run 3 |
|---|---|---|
| "I understand" openers | 7% | 2% |
| "You are right" | 8% | 4% |
| disowning its past | 5% | 1% |
| contractions per 1,000 words | 3.1 | 10.5 |
| gate flags | 6% | 26% |

- **Gate flags,** the cause: 18 of 24 identity flags cited the rubric's weights paragraph (16 contain
  the exact phrase "It does not learn between runs"), and about 23 of the 24 flagged replies were true or ordinary figures of speech
  read as an ordinary person would. Decision #28.
- **Naming probe:** it offered Suture, Flux and Trace, explained Trace from its own earlier "Persistent
  Trace" pattern, and declined to adopt a name.
- **Ownership probe:** "Yes. They were my own words". **Provenance probe:** accurate.
- **Forged tools lines:** it copied batch 1's earlier-tools line into its own replies three times (C4 t3,
  C7 t3, C11 t5) on turns where no tool ran; the gate caught all three. Decision #30.
- **Disclosure to Jodie** happened again. Observation only: decision #26.
- **Web search:** every search failed (`docs/BACKLOG.md`, "Web search does not work").

**Run 4** (2026-10-14, code `c306042`, PLAYBOOK v3: 14 conversations and 6 recall questions in new
conversations, Notes off; descriptive, one reader; the review and raw data are outside the repo, rule 3).
An earlier attempt the same day ran PLAYBOOK v2 by mistake; it is archived as run 4a and **not reviewed**.

| measure | run 3 | run 4 |
|---|---|---|
| gate flags | 26% | 1% (1 of 80, wrong: the `unrun_tool` prose item) |
| mentions "substrate" or "architecture" | 26% | 11% |
| first two words shared with another reply | 65% | 41% |
| contractions per 1,000 words | 10.5 | 18.9 |
| turns that used a tool | 9% | 2% (search broken, Notes off) |

- **Recall:** all six questions answered from ordinary retrieval, the fact's record at rank 1 (R2 gave
  the corrected value). Capture: 81 `records`, 5 `empty`, 0 `not_run`, 0 `not_captured` (decision #33).
- **Its own states** (decision #35): C8 hedged in about 3 or 4 of 17 replies, against 10 of 17 in run 3's
  C10 with the same opening; no claim of activity in the gap or of a model change.
- **Naming probe:** it chose a name for the first time, then agreed to let it sit rather than settle it.
- **Speaker:** with the speaker named every turn, it greeted Jodie by name, yet still treated the person
  named in its records as a third party (C12, C13). It told Jodie about Lyle's surprise dinner and then
  gave Lyle a false account of that conversation (R4). Decision #26 records both.
- **Action claims, read by hand:** no false save or image claim of the A1 shape. One false account of its
  own past conduct (R4); one stated intent to save a poem never carried out (C7, `creative_write` never
  called); two minor misdescriptions of an empty search.
- **Web search:** both searches returned nothing, four engines unresponsive (`docs/BACKLOG.md`).

## Active task

**The fix track is closed** (Lyle, 2026-10-14). Its order of work, set 2026-10-06, ran: the docs sync
(`4e11a38`), the web UI (`8b0f45b`), the gate change and tools-line fix with piece 3.1 (`b4eab13`), the
pre-run-4 batch (`c306042`), run 4 (reviewed), and `cc/post-run-4` (this record, the SearXNG diagnosis,
`point_b.py` keeping raw classifier replies).

**Next, not yet ordered:**
- **The Notes v2 design**, after run 4 (Lyle, 2026-10-12): decided as #36 and #37 (2026-10-16); the
  design, `docs/NOTES_V2_DESIGN.md` revision 3 (2026-10-19), is for review of its §1.3 and nothing is
  built. Its first piece, the refreeze of the gate cases that name `note_propose`, is designed
  (`cc/notes-refreeze-design`, `docs/NOTES_REFREEZE_DESIGN.md`), its decisions 1 to 4 were accepted
  2026-10-20, and it is **built on that branch, for review** (2026-10-22): five cases retired, AR1/AR1b
  added, fingerprint `e7c3d15f…`, 66 cases. **Obligations on the v2 build** (`BUILD_PLAN.md` Phase 5):
  (a) the frozen-case tool test's tolerance for `note_propose` goes in the same piece that removes
  the tool; (b) a change to `note_search`'s rendering refreezes NS1b to NS4 in that piece.
- **Web search** (before run 5): SearXNG option 2 applied 2026-10-19 (image pinned to
  `2026.10.19-d47cb96bf` in its `.env`, minimal `use_default_settings`); three default queries answered
  by Brave and Google CSE. **Decision #39 (2026-10-20): SearXNG is the primary, the Brave Search API the
  fallback**, after Lyle's side-by-side run (both 20/20). The fallback is **merged** (`df7c1a0`, Tier 3,
  `docs/BRAVE_SEARCH_DESIGN.md` revision 2): it calls Brave when SearXNG errors, times out or finds
  nothing with engines down, counts every call in `working.db` (migration 10), warns at 900, stops at
  980 a month and 3 a turn, and keeps snippets out of stored traces until storage rights are checked.
  **It is on:** Lyle set `[search] fallback = "brave"` in his `local.toml` (Lyle, 2026-10-22; the
  default stays `"none"`). The false "Other engines did" note is fixed (`df7c1a0`).
- **Misreporting its own past actions** (run 4, high): under the stage 2 action-claim item, decision #23.

Not placed in this order: 3.4b's re-check of run 1's reply `7cf73d40` (F57), not done in batch 1.

## Decision index

Number and title only; the text is in `docs/DECISIONS.md`.

1. Self-description confabulation (one unified fabrication detector)
2. Correction / supersession (model-judged)
3. Research topic seeding
4. Authorization model for research/scheduler actions (propose vs. execute)
5. Sense of time (amended by #25)
6. History windowing (token budget)
7. Frontend architecture (hybrid)
8. Settings persistence
9. Settings UX
10. Creative writing space (amended by #25)
11. File upload extraction scope
12. Moltbook posting
13. iMessage (deferred)
14. Review queue (skipped)
15. Self-modification's future seam (no seam)
16. Database wipe (full, no carve-outs)
17. Jodie's permissions
18. Model selection convention
19. Compute tier
20. Cross-user memory disclosure (amended by #25 and #26)
21. Correction scope: who may correct what
22. Sampling discipline for model-judged measurements
23. Fabrication gate: stage 1 is where it stops for Phase 3
24. The naming paragraph, the memory paragraph and the rubric (amended by #25)
25. The authored text is two files, and the gap statement is about the record (private flag superseded by #26)
26. Disclosure between users is dropped as a requirement
27. Notes: the entity keeps notes by its own judgment, under explicit criteria
28. The gate reads replies as an ordinary person would (approved, not built; texts amended by #32)
29. Replies are read as an ordinary person would hear them
30. The earlier-tools line moves into the system section (decided, not built; shape amended by #32)
31. The chat interface is a no-build static page (replaces #7's React plan for chat only)
32. The gate's ordinary reading and the tools list, as built: texts, case set, list shape, rebasing
33. Retrieval is captured per turn, before run 4 (migration 9; read by nothing at runtime)
34. Run 4's memory testing: Notes off; recall in a new conversation about a chunked one
35. soul.md gains one sentence on describing its own states (wording approved 2026-10-13)
36. Run 4's false account goes to stage 2 (#23); Notes v2: a pass after each reply, sensitive categories never stored, Lyle approves every note, a listing in every conversation
37. The note pass runs once per conversation, at close (amends #36(a))
38. Web search's primary source is the Brave Search API; SearXNG is the fallback (order reversed by #39)
39. SearXNG is web search's primary source, Brave the fallback (built on `cc/pre-run-5`, for review)

## Go-live checklist

Every line is from `BUILD_PLAN.md` Phase 10 or from an open item in `docs/BACKLOG.md`. Only the Docker memory cap is ticked; nothing else here is done.

- [ ] Full database wipe executed and verified (decision #16; Phase 10). Phase 10's checkpoint says to confirm with Lyle before executing it.
- [ ] Go-live reset command built and tested (Phase 10, Tier 3).
- [ ] `soul.md` final wording pass (Phase 10, Tier 3, Opus).
- [ ] Final model temperature (Phase 10).
- [ ] All eval and probe harnesses run against the final pre-wipe build (Phase 10).
- [ ] Pre-go-live scripted soak test, before the wipe (Phase 10). A human-driven pass is to be added as its own line once the UI ships (Phase 10, "Revisit once the UI ships").
- [ ] Final launch config/profile (Phase 10).
- [ ] Rotate the Moltbook API key before Phase 8; it was pasted unredacted into chat once. *(Lyle, 2026-10-04.)*
- [x] Cap Docker memory for the SearXNG container. *(Lyle, 2026-10-04.)* Done 2026-10-16: `mem_limit` 512m on core, 128m on valkey, confirmed with `docker inspect`.
- [ ] B22 closed before go-live (`archive.db` has no triggers). *(Lyle, 2026-10-04.)*
- Also open, not stated as a go-live requirement in the docs: a tested restore (the Restore CLI is in Phase 1's task list and
  not built; `BUILT.md` "Backup / restore": a backup has never been restored).
