# DRAFT for review: an AGENTS.md "Working rules" section (2026-10-03)

**Not applied to `AGENTS.md`.** Each rule binds CC. Each cites where the project already states it; a rule that
has existed only in the review conversation says so. Part B holds rules CC proposes, labelled as such.

---

## Part A: the section as drafted

### Working rules

**1. Never commit, never stage, never start the server.**
- Lyle runs every commit (`AGENTS.md` "The loop"; `CLAUDE.md` "Never commit").
- For each change CC writes a list of paths, one per line. It checks that the lists together equal the working-tree
  changes, and says where a file is shared between sets. CC never uses `git add -p`. *(Review conversation;
  `CLAUDE.md` "Git hygiene".)*
- **Never start the server** (`run_server.py`, `start.sh`, `uvicorn`). The real store is at schema version 6, and
  `init_databases()` runs at startup, so migrations 7 and 8 would apply to the real data at first startup
  (`BUILT.md` "Startup creates and migrates the databases"). *(The rule itself: review conversation, 2026-10-03.)*

**2. Point every scratch directory before importing `program.*`.**
- A script that imports `program` with the default directories reads the real store. The settings-backed accessors
  read `working.db`'s settings table (2026-10-03 occurrence, `NOW.md`).
- Call `scripts._scratch.scratch_env(name)` before any `program` import. `tests/test_scratch_helper.py` fails on a new
  script that does not. Operator tools meant to use the real store are listed there with a reason.
- Confirm the real `data/` is untouched: record a fingerprint (every file's mtime and size) before and after any model
  run (piece 8 onwards).

**3. The repository is public.**
- No keys, no account names, no real conversation text, and no raw entity replies from real conversations.
- Raw samples go in `~/anam-measurements/`, never in the repo (Moltbook fixtures README; review conversation).
- See the open question under Part C.

**4. Every new search-like tool declares `Tool.empty_result`.**
- The declaration is the exact sentence the tool returns when nothing matched (CO17, `docs/CORRECTION_DESIGN.md`).
- CO17's exclusion and symmetric skip key on it. A search tool without it is invisible to both, and nothing fails.
- Add the tool to the drift test that dispatches each real tool and requires its empty result to be detected.

**5. Measurement trust.**
- **Decorrelated, shuffled passes:**
  - interpose a different prompt between samples of one case;
  - shuffle each pass with a recorded seed;
  - never loop one prompt back to back (`AGENTS.md` "Sampling a model's behaviour").
- **Escalate anything non-unanimous:** a case that is not unanimous in five runs goes to 20 runs before its rate is
  reported, and a rate is reported with an interval (`AGENTS.md`; `NOW.md` decision #22).
- **Hand classification needs an independent reader** before it is a finding. Until then it is labelled "one reader's
  classification" (`BUILT.md` caveats on the journal and piece 8 tables). *(The independent-reader requirement: review
  conversation.)*
- A harness builds what production builds (`AGENTS.md`). Read the primary source, not a derived one (`AGENTS.md`).

**6. One model run at a time; every run resumable.**
- Ollama serves one model and samples correlate (`AGENTS.md`), so CC never runs two model-calling processes at once.
- Append each sample or pass to its raw file as it finishes. A restart skips what is done. *(Review conversation;
  every `scripts/notes_*` measurement does this.)*
- Before starting runs, CC says if model time looks longer than the limit the task gives.

**7. Report in the body; flag rather than guess.**
- Results go in the body of the reply in compact tables, and in a changelog file. Never "printed above" or "see the
  attachment". *(Review conversation.)*
- CC reports its own reading, not a recommendation to switch anything on. *(Review conversation.)*
- **Anything uncertain is flagged as uncertain**, with what would settle it, rather than resolved by a guess. A status
  claim is checked against the code before it is repeated (`AGENTS.md` "A status label is a derived artifact too").

---

## Part B: rules CC proposes (CC's, not Lyle's list)

**B1. New frozen cases are proposed first.** A proposed case is shown with its observed behaviour and added to a case
file only after review. The `documented` and `note` fields can be edited without moving the fingerprint; a change to
any fingerprinted field is a new freeze and says so. *(Review conversation, 2026-10-03; piece 7's practice.)*

**B2. Tier 3 work stops for review after it is built,** per `AGENTS.md` "Stop-and-verify checkpoints". Prompt-facing
text (tool descriptions, result texts, refusal texts) counts as Tier 3, which that list does not spell out today.

**B3. A text change is measured before it is applied, when the measurement is cheap.**
- Inject the draft in memory in a scratch process, run the relevant requests, and read the replies, before the
  committed text changes.
- The 2026-10-03 drafts show why: the draft CC wrote itself (B) was no better than the current text (7/36 against 8/36).

---

## Part C: questions for the reviewer (unsure, not decided)

1. **"No raw entity replies" vs current practice.**
   - Changelogs and `BUILT.md` routinely quote replies verbatim from *scratch* runs against invented data (for example
     *"The note about the old router has been retired."*).
   - The cold-start rule said *"no real conversation text"*. Rule 3 above follows that reading: real conversations are
     out, verbatim replies from scratch runs on invented data are in.
   - If every raw reply is meant to be out, existing changelogs carry many.
2. **Who is the independent reader?** CC cannot be its own. Is it Lyle, the reviewer, or a second CC session given
   the replies without the first reading?
3. **Rule 4 has no structural enforcement.** A new search tool that omits `empty_result` fails nothing. A test could
   require every tool whose name or description contains "search" to declare it, but that matches on spelling, the
   weakness `AGENTS.md` warns about. Not proposed for building.
