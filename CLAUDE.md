# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md
@PROJECT.md
@GUIDANCE.md
@NOW.md

`BUILT.md` is the frozen history of how the build got here (about 354,000 characters). It is **not
imported**: search it by heading (`## Core platform`, `## Tools` and so on), never load it whole.
`ARCHITECTURE.md`, `docs/DECISIONS.md` and `docs/BACKLOG.md` are not imported either (size).

**Before changing a subsystem, read its section of `ARCHITECTURE.md` (invariants, each cited to a test)
and the decisions in `docs/DECISIONS.md` that touch it.**

## State of this repo

Phases 0 to 4 of `BUILD_PLAN.md` are built and Phase 5 is in progress; `NOW.md` ("Current state") has the
details and `BUILD_PLAN.md` has a "Status:" line under each phase. It is a git repository on `main`.
`reference/old-anam/` is the prior build (reference-only, see below).

Commands that exist (from `pytest.ini`, `pyproject.toml`, `start.sh`, `run_server.py`, `scripts/`):
- `venv/bin/python -m pytest`: the tests (`testpaths = tests`). Tests marked `live` are skipped and no test may reach the real Ollama.
- `venv/bin/python -m pytest --run-live`: also runs the live tests. Needs the services up and nothing else using the model; run it only when the task says so.
- `ruff check .`: lint (line length 100, rules E, F, I).
- `python run_server.py [--debug] [--port N]` or `./start.sh [--lan]`: start the backend.
- `scripts/`: operator and measurement scripts; `README.md` says what each is for. A script that imports
  `program` against a scratch store calls `scripts/_scratch.py` `scratch_env(name)` first.

Hard rules: **CC never commits to main and never pushes or merges; it commits to its own `cc/<piece>` branch and stops** (`docs/GIT_WORKFLOW.md`). **CC does not start a server against `data/`.**

Doc map: `AGENTS.md` how CC works; `PROJECT.md` what and why; `GUIDANCE.md` behavioural principles; `NOW.md`
current state, the active task, the decision index and the go-live checklist; `docs/DECISIONS.md` the
decision log; `docs/BACKLOG.md` open items; `ARCHITECTURE.md` current invariants, each cited to a test or
code; `BUILD_PLAN.md` the phases and their status; `BUILT.md` frozen history; `docs/` design docs;
`docs/archive/` closed items; `docs/measurements/` future measurement detail; `docs/GIT_WORKFLOW.md` the branch and merge procedure; `changelog/` dated task records.

The package name `tir/` and the "Tír" naming belong to the old build; do not carry either into new code.

## Naming and language discipline

- The project is **Project Anam**. Anam is the substrate, not the entity.
- **The AI entity is not given a name** — not by code, prompt, config, or docs —
  and `soul.md` says so. Since decision #24 it **may choose one for itself**;
  nobody else chooses for it. Never write "Anam said" / "Anam thinks": that
  collapses the substrate/entity distinction.
- **No launch-gating or deadline language anywhere** — docs, task briefs, commit
  messages, or conversation. This is a continuously developed hobby project with
  no finish line (`PROJECT.md`).
- Personality is observed, never assigned. No traits, no sliders, no "you are
  like X" framing.

## The reference folder is a trap worth naming

`reference/old-anam/` is a complete, working prior implementation — full FastAPI
backend, React frontend, ~70 test files, and years of design docs. `AGENTS.md`
permits consulting it **only when a task explicitly points at it**, and forbids
copying code from it. Two failure modes to watch for:

1. It quietly becoming the default answer to "how should this be built." The
   rebuild exists to shed inherited complexity, not to transcribe old files into
   new paths.
2. **Its docs contradict this build's decisions.** `reference/old-anam/` carries
   its own `CLAUDE.md`, `AGENTS.md`, `CODING_ASSISTANT_RULES.md`, `NORTH_STAR.md`,
   `CONSTRAINTS.md`, and `NOW.md` — and a nested `CLAUDE.md` can be auto-loaded
   when a file under that directory is read. Those describe the *old* project's
   rules and status. This repo's root docs win, always. Concretely, the old docs
   still treat self-modification, the review queue, and partial data preservation
   as live; here they are deferred or abolished (`docs/DECISIONS.md` entries 14 and 15 for self-modification and the review queue, 16 for the wipe).

## Decisions that are already made

`docs/DECISIONS.md` holds a 32-entry decision log (entries 1 to 32; `NOW.md` indexes them) covering what was settled before and
during the build. **Treat every line there as DECIDED** — implement against it rather than
relitigating it, and if a task seems to require deviating, stop and flag it
instead of deciding silently. The ones most likely to be reinvented by accident:

- **Unified fabrication detector** — one detector for both tool-output
  fabrication and identity-claim fabrication. Not two systems.
- **Corrections are model-judged, not keyword-matched.** A correction links to
  what it corrects via a `supersedes` relationship and retrieval must respect it.
  Raw experience is never edited or deleted to fix it — corrections layer on top.
  Needs its own frozen eval case set before production trust, same bar as the
  fabrication gate.
- **An elapsed-time statement must be paired with the gap statement** (decision
  #5 as amended by #25): apart from any run the record shows, nothing was running
  in the gap, so there is nothing else from it to report. The standing statement
  is in `program/integrity/operational.md` and the block repeats it beside the
  figure. Stating "it has been 14 hours" alone is the exact confabulation pattern
  the prior build produced. The pairing is not optional flavor.
- **Two-axis capability gating** — `enabled` and `approval_required` are
  orthogonal, and authorization keys on **propose vs. execute**, not on who
  triggered it. `allow_*` flags exist for exactly one case: fully unattended,
  no-human-in-the-loop execution.
- **History windowing is token-budgeted**, not fixed-message-count. Nothing is
  deleted or summarized; older turns just stop being resent and stay retrievable.
- **No self-modification seam anywhere.** Not deferred-but-stubbed — absent. The
  review queue goes with it, since self-mod was its only consumer.
- **Full database wipe before go-live, no carve-outs.** Do not build a
  "preserve genuine history" exception into the wipe tooling; this build's data
  is disposable test data throughout.

## Architecture

Built: Python/FastAPI backend (`program/`) · Ollama for local chat + embeddings · ChromaDB vectors plus SQLite
FTS5/BM25 lexical, fused via RRF · SearXNG (local HTTP) behind `web_search`/`web_fetch` · ComfyUI behind image
generation. `ARCHITECTURE.md` lists the current invariants.

Not built: the frontend (planned as **hybrid**: React for the live chat interface, rebuilt around one coordinated
state machine, with no scattered `useState` and no competing pollers, which is the failure the old build hit; plain
server-rendered forms for the admin settings panel) and the admin loopback gate that panel needs (`BUILD_PLAN.md`
Phase 9). Admin settings are to be loopback-gated and never exposed to Jodie.

Substrate stays boring on purpose: accumulated memory is meant to be the only
interesting variable. KISS is non-negotiable at the substrate level, and
complexity in the substrate is not the same thing as richness in the entity.

## Working rules that bite

- **Branch, never main.** CC plans → the reviewer (Claude, outside this repo) approves → CC implements on a `cc/<piece>` branch (with a changelog entry where `AGENTS.md` "Git hygiene" requires one) and commits there → Lyle reviews `git diff main..cc/<piece>`, squash-merges and pushes. CC never commits to main and never pushes, merges, rebases or rewrites history, except the rebase after a merge in `docs/GIT_WORKFLOW.md` (decision #32 D6). This holds regardless of how small or obviously-correct the change is.
- One task at a time, verified before the next. Do not batch unrelated changes.
- **Stop and wait for review** after: database schema (initial or migration),
  provenance/source-trust semantics, `soul.md` and `operational.md` content and prompt assembly,
  restore-from-backup logic, the fabrication gate and correction/supersession
  classifier, and go-live reset / wipe tooling.
  **Until Phase 10 begins, `AGENTS.md` "Until go-live" relaxes this:** Tier 3 is only
  authentication, `db.py` locking and atomicity, the `archive.db` schema, restore, the wipe,
  external effects, and the frozen eval case sets and their harness; the rest is Tier 2.
- **Check, don't assert.** If a claim about system state is directly checkable —
  a config value, a database row, whether a process actually died, whether a
  service is actually running — run the command. `ollama ps`, direct SQL, and
  process inspection are cheap; being wrong about system state is not. Verify
  against live code and behavior over any doc (`BUILT.md` is frozen history; fix
  `ARCHITECTURE.md` or `NOW.md` instead), then fix the doc.
- Git hygiene when committing on a `cc/` branch: explicit `git add <filename>` per file,
  never `-A`, never `.`; `git status` clean before a commit.
- Every task needs tests. If one genuinely can't be tested, say so explicitly
  rather than skipping quietly.
- Default to Sonnet. Opus only when a task brief names it — schema design,
  retrieval scoring/RRF weighting, provenance semantics, `soul.md` wording.
