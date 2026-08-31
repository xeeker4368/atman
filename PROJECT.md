# PROJECT.md

## What this is

A persistent, local AI entity running on a Mac mini for a two-person household.
Lyle is the operator/admin. Jodie is a household user with chat, image
generation, and creative-space access, no settings access.

This is a **hobby project**. No launch date, no finish line, no fixed
requirement set. It is continuously developed and added to. Do not use
launch-gating or deadline language anywhere in this project (docs, task
briefs, commit messages, conversation).

## Core thesis

Can a coherent, emergent sense of self arise from accumulated, provenanced
memory over time? The substrate (retrieval, storage, model plumbing) is
deliberately kept boring and pinned so that accumulated memory remains the
one interesting variable. KISS is non-negotiable at the substrate level.
Complexity in the substrate is not the same thing as richness in the entity.

## Status

Fresh build. The prior implementation (`xeeker4368/Anam`) is kept at
`reference/old-anam/` for consultation only — see `AGENTS.md` for the rule
governing that folder. Nothing in this repo is a copy of that code; it is a
from-scratch build informed by lessons already learned there.

The database in this build is disposable test data. It will be wiped before
go-live, without exception, unlike the prior project's more cautious
partial-preservation stance. Do not build any "preserve genuine history"
carve-out into the wipe tooling for this iteration.

## Stack

- Python / FastAPI backend
- Ollama, local model (chat + embeddings)
- ChromaDB (vector retrieval) + SQLite FTS5/BM25 (lexical retrieval), fused
  via RRF
- Frontend: **hybrid**. React (clean, single-source-of-truth state — no
  competing pollers, no duplicate state machines) for the live chat
  interface. Plain server-rendered forms for the admin settings panel — it
  has no complex client state and doesn't need a SPA.
- SearXNG (self-hosted, local HTTP) backing `web_search` / `web_fetch`
- ComfyUI backing image generation

## People and roles

- **Lyle** — admin/operator. Full settings access via the admin panel
  (loopback-gated). Holds all commit authority.
- **Jodie** — household user. Chat, image generation, creative-space access.
  No settings access, ever. Her conversations are legitimate source material
  for the periodic research-mining pass (see `GUIDANCE.md`), but she cannot
  trigger a research run herself.
- **Claude (reviewer/architect)** — plans, reviews, pushes back, writes task
  specs.
- **Claude Code (CC)** — implements. Never commits unilaterally.

## In scope for this build

Core substrate, memory integrity (fabrication detection + correction/
supersession), tool registry (`memory_search`, `web_search`/`web_fetch`,
file/PDF ingestion), media (image generation, creative writing), bounded
research (manual + entity-self-flagged + periodic-mined candidates, all
human-approved before execution), reflection journal, scheduler, backup/
restore, admin settings panel (live-editable capability flags, model
selection, temperature, API keys with per-setting verification), multi-user
attribution, eval/probe harnesses, go-live hardening.

## Explicitly deferred (not in this build)

- **Self-modification** and its only consumer, the **review queue**. No
  integration seam is being left for either — see the decision log in
  `NOW.md` (entries 14–15) for the reasoning. When self-mod is picked back
  up, it gets its own full design
  pass, including a real answer to the sandboxing/execution question that
  was never resolved in the prior implementation.
- **iMessage** integration, all stages.
- **Vision, self-image, avatar/self-representation** — blocked on you
  acquiring the actual hardware (camera). Revisit once that exists.
- **Public internet exposure.** LAN-only for this build.
- Working Theories, Interpretation Trace Runtime, Temporal Runtime Headers
  (beyond the elapsed-time statement — see `GUIDANCE.md`), Web Source
  Runtime, orchestrator/contradiction-detection agent. Prove-need-first,
  not committed.

## Standing principles carried forward

- Personality is observed, not assigned. No personality sliders, no
  injected traits, no telling the entity what it is like.
- Provenance is sacred. Raw experience is never edited or deleted to
  "correct" it — see the supersession mechanism in `GUIDANCE.md`.
- The entity is allowed genuine discretion, including refusal, within
  actions that carry no external risk (e.g., declining to share a piece of
  creative writing). This is a real capability, not a compliance
  performance.
- Verify against live state (code, database, actual running behavior), not
  against documentation summaries, whenever the two could conflict.
