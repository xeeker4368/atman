# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md
@PROJECT.md
@GUIDANCE.md
@NOW.md
@BUILT.md

## State of this repo

**Fresh build. No code exists yet.** As of this writing the repo contains only
the five canonical docs imported above, `reference/old-anam/`, and an empty
`venv/` (Python 3.14, pip only). It is **not yet a git repository** — `git init`
has not been run.

Consequences for a session starting here:

- There are no build, test, lint, or run commands yet. Do not invent them, and
  do not copy the old project's (`./start.sh`, `python -m pytest`,
  `python -m tir.admin`) into a task brief as if they applied — that package
  does not exist in this build.
- `BUILT.md` is the authority on what actually exists. Everything in `PROJECT.md`
  under "In scope" is *planned*, not present. When adding the first real code,
  update `BUILT.md` in the same commit, and record commands here once they exist.
- Nothing here is a copy of the prior implementation. The package name `tir/` and
  the "Tír" naming belong to that old build; do not carry either into new code.

## Naming and language discipline

- The project is **Project Anam**. Anam is the substrate, not the entity.
- **The AI entity has no name and must not be given one** — not by code, prompt,
  config, or docs. Never write "Anam said" / "Anam thinks"; that collapses the
  substrate/entity distinction.
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
   as live; here they are deferred or abolished (`NOW.md` entries 14–16).

## Decisions that are already made

`NOW.md` holds a 19-entry decision log covering everything settled before code
exists. **Treat every line there as DECIDED** — implement against it rather than
relitigating it, and if a task seems to require deviating, stop and flag it
instead of deciding silently. The ones most likely to be reinvented by accident:

- **Unified fabrication detector** — one detector for both tool-output
  fabrication and identity-claim fabrication. Not two systems.
- **Corrections are model-judged, not keyword-matched.** A correction links to
  what it corrects via a `supersedes` relationship and retrieval must respect it.
  Raw experience is never edited or deleted to fix it — corrections layer on top.
  Needs its own frozen eval case set before production trust, same bar as the
  fabrication gate.
- **Elapsed-time statement must be paired with the no-experience statement in
  `soul.md`.** Stating "it has been 14 hours" without the explicit note that the
  gap held no experience, continuity, or thought is the exact confabulation
  pattern the prior build produced. The pairing is not optional flavor.
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

## Planned architecture (none of it built yet)

Python/FastAPI backend · Ollama for local chat + embeddings · ChromaDB vectors
plus SQLite FTS5/BM25 lexical, fused via RRF · SearXNG (local HTTP) behind
`web_search`/`web_fetch` · ComfyUI behind image generation.

The frontend is deliberately **hybrid**: React for the live chat interface,
rebuilt around one coordinated state machine — no scattered `useState`, no
competing pollers, no duplicate state machines, which is the specific failure the
old build hit. Plain server-rendered forms for the admin settings panel, which
has no complex client state. Admin settings are loopback-gated and never exposed
to Jodie.

Substrate stays boring on purpose: accumulated memory is meant to be the only
interesting variable. KISS is non-negotiable at the substrate level, and
complexity in the substrate is not the same thing as richness in the entity.

## Working rules that bite

- **Never commit.** CC plans → the reviewer (Claude, outside this repo) approves →
  CC implements with a changelog entry → Lyle reviews the diff and commits.
  This holds regardless of how small or obviously-correct the change is.
- One task at a time, verified before the next. Do not batch unrelated changes.
- **Stop and wait for review** after: database schema (initial or migration),
  provenance/source-trust semantics, `soul.md` content and prompt assembly,
  restore-from-backup logic, the fabrication gate and correction/supersession
  classifier, and go-live reset / wipe tooling.
- **Check, don't assert.** If a claim about system state is directly checkable —
  a config value, a database row, whether a process actually died, whether a
  service is actually running — run the command. `ollama ps`, direct SQL, and
  process inspection are cheap; being wrong about system state is not. Verify
  against live code and behavior over any doc, including `BUILT.md`, then fix
  the doc.
- Git hygiene when staging for Lyle: explicit `git add <filename>` per file,
  never `-A`, never `.`; `git status` clean before a commit.
- Every task needs tests. If one genuinely can't be tested, say so explicitly
  rather than skipping quietly.
- Default to Sonnet. Opus only when a task brief names it — schema design,
  retrieval scoring/RRF weighting, provenance semantics, `soul.md` wording.
