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
- Authentication: credential verification, session-token issue and expiry, and
  anything that decides which `Actor` a request produces
- Database concurrency and locking semantics in `program/memory/db.py` —
  `busy_timeout` tuning, write retry, or write serialisation. These read as
  operational tuning and are not: the cross-database atomicity guarantee
  depends on the locking behaviour they change, so a fix verified only against
  the lock-timeout symptom can weaken the guarantee without failing anything.
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

### Sampling a model's behaviour

Model-judged behaviour is measured, not observed once. Two rules, both bought
with real mistakes (see `changelog/2026-09-17-n7-stability.md`):

**Escalate anything that is not unanimous.** Five runs is enough to confirm a
case that comes back 0/5 or 5/5. It is not enough for anything in between: a
case whose true rate is 20% lands unanimous in a five-run block often enough to
mislead. **Any case returning non-unanimous in an initial five-run block goes to
20 runs before its rate is reported as a finding**, and a rate is reported with
an interval rather than as a bare count.

**Do not sample the same prompt back to back.** Repeated identical calls to
Ollama produce *correlated* results — measured on one borderline case the same
minute: a tight loop gave 10/10, while interposing a different prompt between
samples gave 4/10. A tight loop therefore reports the first sample's luck N times
and calls it unanimity. **Interpose a different prompt between samples**, or
interleave cases, whenever a rate is being estimated.

*The second rule is an addition from the same evidence rather than something
asked for, and it matters because without it the first rule reproduces the
artifact at higher N. The frozen fabrication-gate harness currently repeats each
case N times consecutively — the correlating regime — which is recorded as a
known limitation in `BUILT.md`; changing it is a reviewed change to how the
measurement works.*

*The escalation rule catches instability in **both** directions, which is worth
knowing before trusting a clean five-run block. It is usually told as "a 20% case
can read 5/5"; at revision 9 a case whose real rate was **90%** read **1/5**, and
only the escalation to 20 runs (18/20) showed it. Non-unanimous means escalate,
whichever side of the middle the block happens to land on.*

### Trust the primary source, not a derived one

When a fact is available from the thing itself and from something computed off it,
**read the thing itself.** A harness log, a manifest, a cached count, a summary
doc and a docstring are all derived; the database, the file on disk and the code
as it runs are primary. Where the two disagree the derived one is wrong often
enough that checking is not paranoia.

This is a separate rule from the two above because its failures do not look like
failures. A derived source usually returns a *clean, plausible number* — which is
why each of these went unnoticed until something forced a look at the primary:

- **A denominator taken from a log** (revision 9's F37) reported 22 action
  requests where the store held 23. The harness log had filtered out a real
  request because its record carried an error key, so a turn that genuinely
  happened was absent from the count. Caught only by querying `messages` directly.
- **A parser verified against a form that does not occur** (the `CORRECTS 1, 2`
  bug) passed its tests for two phases while writing false links, because the
  tests scripted the grammar the design implied rather than the shape the model
  actually emits.
- **A frozen case that passed for the wrong reason** (`S6`) was read as evidence
  the gate caught cross-sentence attribution. It passed because the classifier
  cited a phrase the enforcement did not cover — a fact visible only in the reply,
  not in the PASS.
- **A verifier that compared two identical error strings** and reported agreement:
  a backup digest check returned `"ERR …"` on both sides of a comparison, and
  `"ERR …" == "ERR …"`, so a table counted as verified while nothing had been
  compared.

Practically: prefer a query over a counter, `git status` over a memory of what was
edited, a probe of the port over the exit code of the command meant to close it,
and a re-read of the code over what its docstring claims. When a number matters,
say which source it came from — and if it came from a derived one, check it
against the primary before it is reported as a finding.

## Adding a runtime directory

**Any new directory resolved from its own config key gets `backup.py` coverage and
test-isolation-guard coverage in the SAME task that introduces it — never as a
follow-up.**

This is a standing item because it has now been missed three times, each caught only
after something leaked or nearly did:

| directory | added | how the gap surfaced |
|---|---|---|
| `backup_dir()` | task 1.14 | the backup tests wrote **two real backup directories into the repo** before the guard existed |
| `artifact_dir()` | task 2.6 | caught during the task, after noticing 1.14's trap |
| `workspace_dir()` | Phase 4 A2 | **65 real PNGs** from the image tests were sitting in the repository's own `workspace/` |

The mechanism is always the same, and it is worth stating so it is recognised rather
than rediscovered: **a new directory starts outside every existing guard by default.**
Each resolves from its own config key, so repointing `ANAM_DATA_DIR` does not move it
even when its default path sits inside `data/`, and `isolated_data_dir` protects only
the keys it was told about.

Three things to add, in the same change:

1. **`isolated_data_dir` repoints it** (`tests/conftest.py`) — otherwise every test
   touching it writes into the real one.
2. **The session guard notices it.** Add its real path to `conftest.REAL_DIRS`; the
   guard fingerprints every file under every entry as `(size, mtime_ns)` at import and
   compares at session end, so creation *and* modification are both caught.
   A directory nested inside one already listed needs no separate entry.

   *This replaced a weaker rule, and the reason is worth keeping.* It used to say
   `"was it created during the run?" is enough` for a directory the suite creates.
   That is only true while the directory does not exist — and by 2026-09-22 all five
   existed, so **four of the five checks could never fire again** and a test writing
   rows into the real `working.db` passed silently. `workspace/` had already needed a
   file-set snapshot because it is part of the tracked skeleton; a file-set snapshot
   alone would not have been enough either, because **writing rows into an existing
   database creates no new file**.
3. **`backup.py` copies it, or the task says in writing why not.** Ask what it holds
   that exists nowhere else: `backup_dir()` is the destination, and a vector store
   rebuilds from `chunks`, but an uploaded file and anything the entity wrote do not
   rebuild from anything.

`tests/test_directories.py` enumerates the config accessors and fails on a directory
that is not covered and not explicitly exempted, so a fourth occurrence fails the
suite instead of leaking. **Adding the exemption is a real answer; it just has to be
written down.**

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
