# AGENTS.md

Instructions for Claude Code (CC) working in this repo. Read this file, plus
`PROJECT.md`, `BUILT.md`, `NOW.md`, and `GUIDANCE.md`, at the start of every
session before touching anything.

## The loop

CC plans → plan goes to the reviewer (Claude, outside this repo) for
approval → CC implements with a changelog entry → Lyle reviews the diff on
his own device → Lyle commits. **CC never commits.** Ever, regardless of how
small or obviously-correct a change seems.

Work one task at a time. Verify before proceeding to the next. Do not batch
unrelated changes into one patch.

## The reference folder

`reference/old-anam/` contains a prior implementation of this project. It is
**reference-only**:

- Use it to verify exact schemas, constants, thresholds, and behavior when a
  task description points at it (e.g., "match the RRF fusion approach used
  in `reference/old-anam/tir/memory/retrieval.py`").
- **Do not copy code from it directly.** Write fresh implementations. The
  point of this rebuild is to fix known issues and avoid inherited
  complexity, not to transcribe old files into new paths.
- If a task doesn't explicitly reference it, don't consult it — don't let it
  quietly become the default source of truth for how something "should" be
  built.

## Model selection

Default to Sonnet. Use Opus only when a task is explicitly marked for it —
schema design, retrieval scoring/RRF weighting, provenance semantics,
`soul.md` wording, or anything else the task brief calls out by name. Don't
leave Opus on by default; it draws meaningfully more from the shared usage
pool for work that doesn't need it.

## Stop-and-verify checkpoints

The following categories of work require an explicit pause for Lyle's
review before continuing to the next task, regardless of how confident the
implementation feels:

- Database schema (initial design and any migration). This applies at the
  column/field level on frozen tables too, not just at the whole-task level:
  a column decision inside an already-approved Tier 3 task still goes up
  before it is coded, not disclosed afterward. On a frozen table the cost of
  a wrong column is permanent and one-directional, so raising it after the
  fact hands the reviewer a question already answered in code — which is a
  weaker review than being asked first.
- Chunking / checkpointing pipeline design
- Hybrid retrieval scoring (RRF fusion, relevance floors)
- Retrieval changes that implement the supersedes/correction link
- Provenance/source-trust semantics
- `soul.md` content and prompt assembly
- Restore-from-backup logic
- The fabrication gate and the correction/supersession classifier (design
  and eval harness — not each individual runtime classification call)
- Go-live reset and database wipe tooling

This list is kept in sync with BUILD_PLAN.md's Tier 3 tasks. If a task is
Tier 3 there, it belongs here — if you add a Tier 3 task without adding it
here, fix this list in the same change, not later.

These are the categories where a wrong decision compounds silently across
everything built afterward, and where this project's own history shows
problems going unnoticed without a deliberate look.

## Verification discipline

When a claim about system state could be checked directly (a config value,
a database row, whether a process actually died, whether a service is
actually running), **check it** — run the command, query the database,
don't assert from memory or from what a comment says. `ollama ps`, direct
SQL queries, and actual process inspection are cheap; being wrong about
system state is not.

Prefer verifying against running code and live behavior over trusting
`BUILT.md` or any other doc, if the two ever seem to disagree — then fix the
doc.

## Git hygiene

- Explicit `git add <filename>` per file. Never `-A`, never `.`.
- `git status` confirmed clean before every commit — no unrelated files
  riding along.
- A changelog entry accompanies every substantive change (what changed, why,
  what was tested, known limitations, follow-up work).
- Update `BUILT.md` in the same commit as the work it describes.

## Testing

Every task needs tests before it's considered done. If a task can't
reasonably be tested (rare), say so explicitly rather than skipping
silently.
