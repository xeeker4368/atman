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
- **HEAD:** see `git log`.
- **Last full suite on record:** 2,111 passed, 23 skipped, 0 failed, `ruff` clean, at `ulimit -n 256`, 2026-10-06, on the docs-sync branch (code identical to `8890489`); the 23 skips are the live tests and two opt-in Moltbook tests.
- **Dark or off by default** (`config/defaults.toml`, parsed with `tomllib`): `notes.enabled = false`, `moltbook.enabled = false`,
  `corrections.person_corrects_entity = false`. A local `config/local.toml` can override them; it was not read for this block.

**Merged to main** (each found with `git log --grep`):

| piece | commit | record |
|---|---|---|
| Piece 4a (small fixes) | `e9bb8bc` | `changelog/2026-10-04-piece-4a.md` |
| Piece 3.5 (lifecycle and concurrency, Tier 3) | `c676888` | `changelog/2026-10-04-piece-3.5-lifecycle.md`, `docs/DESIGN_3.5_2026-10-04.md` |
| Soul/rubric (decision #24; `A2-fabricated-save` became the tenth documented failure) | `ff839b8` | `changelog/2026-10-04-soul-rubric.md`, `docs/FABRICATION_GATE_DESIGN.md` revision 10 |
| Soul v2 (decision #25; gate re-measured, baseline reproduced) | `7f9e674` | `changelog/2026-10-05-soul-v2.md`, `docs/SOUL_AND_PROMPT_DESIGN.md` revision 6 |
| Process speed-up until go-live (`AGENTS.md` "Until go-live") | `4fd5141`, `0f0d075` | the commit messages |
| Batch 1 (3.4b local times, 3.6 part A record origins, 3.3 option B earlier-tools line, test fd hygiene) | `8890489` | `changelog/2026-10-05-batch-1.md` |

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

## Active task

**Order of work** (Lyle, 2026-10-06):
1. **This docs sync** (`cc/docs-sync`): decisions #26 to #30, the decision log and backlog moved out of this file.
2. **The web UI.**
3. **The gate change and the tools-line fix**, one branch, Tier 3, measured: decisions #28 and #30.
4. **The Notes v2 design**: decision #27 and its open questions (`docs/BACKLOG.md`, "Notes v2 implementation").
5. **Run 4.**

Not placed in this order: **piece 3.1** (classifier options at temperature 0, and correction rendering
behind a setting; `docs/FIX_PLAN_2026-10-04.md`), the previous "next", and 3.4b's re-check of run 1's
reply `7cf73d40` (F57), not done in batch 1.

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

## Go-live checklist

Every line is from `BUILD_PLAN.md` Phase 10 or from an open item in `docs/BACKLOG.md`. Nothing is ticked: none of these is done.

- [ ] Full database wipe executed and verified (decision #16; Phase 10). Phase 10's checkpoint says to confirm with Lyle before executing it.
- [ ] Go-live reset command built and tested (Phase 10, Tier 3).
- [ ] `soul.md` final wording pass (Phase 10, Tier 3, Opus).
- [ ] Final model temperature (Phase 10).
- [ ] All eval and probe harnesses run against the final pre-wipe build (Phase 10).
- [ ] Pre-go-live scripted soak test, before the wipe (Phase 10). A human-driven pass is to be added as its own line once the UI ships (Phase 10, "Revisit once the UI ships").
- [ ] Final launch config/profile (Phase 10).
- [ ] Rotate the Moltbook API key before Phase 8; it was pasted unredacted into chat once. *(Lyle, 2026-10-04.)*
- [ ] Cap Docker memory for the SearXNG container. *(Lyle, 2026-10-04.)*
- [ ] B22 closed before go-live (`archive.db` has no triggers). *(Lyle, 2026-10-04.)*
- Also open, not stated as a go-live requirement in the docs: a tested restore (the Restore CLI is in Phase 1's task list and
  not built; `BUILT.md` "Backup / restore": a backup has never been restored).
