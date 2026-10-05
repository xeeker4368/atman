# AGENTS.md

Instructions for Claude Code (CC) working in this repo. Read this file, plus
`PROJECT.md`, `ARCHITECTURE.md`, `NOW.md`, and `GUIDANCE.md`, at the start of every
session before touching anything. `BUILT.md` is the frozen history: search it by
heading, never load it whole.

## The loop

CC plans → plan goes to the reviewer (Claude, outside this repo) for
approval → CC implements (with a changelog entry where "Git hygiene" below
requires one) → CC commits to its own branch and stops → Lyle reviews the
branch diff on his own device, merges and pushes. **CC never commits to main, and never pushes, merges, rebases or rewrites history. For each task CC creates a branch `cc/<piece>` from the current main, commits to it with explicit `git add <file>` (never `-A` or `.`), and stops. Lyle reviews `git diff main..cc/<piece>`, runs the leak check, squash-merges, commits and pushes. A Tier 3 piece's branch is merged before the next piece's branch is created. See `docs/GIT_WORKFLOW.md`.** This holds
regardless of how small or obviously-correct a change seems.

Work one task at a time. Verify before proceeding to the next. Do not batch
unrelated changes into one patch.

*Until Phase 10, a Tier 1 or Tier 2 piece has no separate plan-approval round: see
"Until go-live", item 2.*

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
implementation feels. **Until Phase 10 begins, most entries below are Tier 2,
not stops; each is marked, and "Until go-live" after this list gives the short Tier 3
list that stays.**

- Database schema (initial design and any migration). *(Until Phase 10:
  `working.db` schema changes are Tier 2; `archive.db` schema stays here.)* This applies at the
  column/field level on frozen tables too, not just at the whole-task level:
  a column decision inside an already-approved Tier 3 task still goes up
  before it is coded, not disclosed afterward. On a frozen table the cost of
  a wrong column is permanent and one-directional, so raising it after the
  fact hands the reviewer a question already answered in code — which is a
  weaker review than being asked first.
- Chunking / checkpointing pipeline design *(until Phase 10: Tier 2)*
- Hybrid retrieval scoring (RRF fusion, relevance floors) *(until Phase 10: Tier 2)*
- Retrieval changes that implement the supersedes/correction link *(until Phase 10: Tier 2)*
- Provenance/source-trust semantics *(until Phase 10: Tier 2)*
- `soul.md` and `operational.md` content and prompt assembly (both are the
  entity's authored text, decision #25) *(until Phase 10: Tier 2)*
- Prompt-facing text (tool descriptions, result texts, refusal texts) *(until
  Phase 10: Tier 2)*
- Authentication: credential verification, session-token issue and expiry, and
  anything that decides which `Actor` a request produces
- Database concurrency and locking semantics in `program/memory/db.py` —
  `busy_timeout` tuning, write retry, or write serialisation. These read as
  operational tuning and are not: the cross-database atomicity guarantee
  depends on the locking behaviour they change, so a fix verified only against
  the lock-timeout symptom can weaken the guarantee without failing anything.
- Restore-from-backup logic
- The fabrication gate and the correction/supersession classifier (design
  and eval harness — not each individual runtime classification call) *(until Phase 10: the
  gate and classifier code are Tier 2; the frozen eval case sets and their harness stay here)*
- Go-live reset and database wipe tooling
- **Notes** (`docs/NOTES_BUILD_PLAN.md`), each Tier 3 piece, one stop per piece: migration 8
  and its triggers (database schema); the two tools' descriptions, quote resolution and
  provenance of a new kind of record; `scripts.note`, which is the only human control on what a
  note may say; the receipt and trace-key generalisation (gate-adjacent); the approval toggle,
  approval log and auto-apply (authorization semantics); the frozen gate cases and fingerprint
  move; and the CO15 composition test and pending-claim measurement (ship gates). `OriginContext`
  (piece 2) is Tier 2 and needs diff review only. *(Until Phase 10: Notes work is Tier 2, except
  a change to the frozen case sets or their fingerprint.)*

This list is kept in sync with BUILD_PLAN.md's Tier 3 tasks. If a task is
Tier 3 there, it belongs here — if you add a Tier 3 task without adding it
here, fix this list in the same change, not later.

These are the categories where a wrong decision compounds silently across
everything built afterward, and where this project's own history shows
problems going unnoticed without a deliberate look.

## Until go-live: the faster process

**In force until Phase 10 begins** (decided 2026-10-05). The data is disposable test data
until the go-live wipe (decision #16), so a wrong call before then costs a re-do, not a
corrupted record. **When Phase 10 starts, every rule in this section lapses and the rules
it relaxes apply again as written elsewhere in this file.**

### 1. Tiers

- **Tier 3 stays for exactly these:**
  - authentication (credential verification, session tokens, which `Actor` a request
    produces);
  - `db.py` locking and atomicity (`busy_timeout`, write retry, write serialisation, the
    cross-database guarantee);
  - the `archive.db` schema;
  - restore from backup;
  - the go-live reset and wipe;
  - anything with external effect (posting, research execution, the scheduler's `allow_*`
    flags);
  - the frozen eval case sets and their harness (case files, fingerprints, the sampling
    protocol).
- **Everything else on the stop-and-verify list is Tier 2 until Phase 10**, including:
  prompt-facing text (tool descriptions, result texts, refusal texts, `soul.md`,
  `operational.md`, prompt assembly); `working.db` schema changes (migrations); the gate and
  classifier code; provenance and source-trust semantics; retrieval scoring and the
  supersedes retrieval change; chunking; Notes.
- **Item 4 still applies to Tier 2 work:** a change to anything the classifier sees requires
  the gate re-measurement, whatever its tier.

### 2. No separate design round for Tier 1 and Tier 2

The implementer states its plan at the top of its report and builds in one pass, stopping
only on a stop condition (the brief's, or item 7's). There is no plan-approval round before
the build. **Tier 3 keeps the design round:** plan, review, then build.

### 3. Docs per piece

- A design file is **at most about one page**. Tier 3 is excepted.
- A changelog entry is **at most about 30 lines**.
- `ARCHITECTURE.md` changes only when an invariant changes (as "Git hygiene" already says).
- **No design-doc revision for a small change.** A design doc gains a revision when a piece
  changes what it decides, not to record that a piece happened; the changelog and the
  commit message do that.

### 4. When the gate must be re-measured

A gate re-measurement is required **only when a change touches what the classifier sees**:
the rubric (`program/integrity/architecture.md`), the classifier prompts, the situation
block, or how the trace is rendered to the classifier. It is **not** required for a change to
`soul.md` or `operational.md`, which the gate never reads
(`tests/test_gate.py::test_the_rubric_is_architecture_md_and_soul_is_not_read`).

### 5. Measurement rules: findings and descriptive numbers

- **The independent-reader rule and the 20-run escalation apply only when a decision depends
  on the finding**: a case is frozen or refrozen, a text ships or is withdrawn, a defect is
  declared closed, a setting changes.
- **Everything else is descriptive** and is labelled as descriptive where it is reported
  ("descriptive: one reader, five runs"), so nobody later builds a decision on it unawares.
- Unchanged: decorrelated, shuffled passes (never one prompt back to back), and a harness
  builds what production builds. Those make even a descriptive number mean something.

### 6. Two lanes

Two branches may be open at once if **they touch no common file** and **only one of them
makes model calls** (working rule 6: Ollama serves one model and samples correlate). Each
lane has its own working directory (`git worktree add`, `docs/GIT_WORKFLOW.md`). Before a
second branch is created, its report-to-be names the other open branch and confirms the two
file lists do not overlap. The rule that a Tier 3 piece's branch is merged before the next
branch is created still holds: a second lane does not open beside an unmerged Tier 3 branch.

### 7. Stop conditions: the test-only exception

A change to a **test** outside the plan's file list is **not** a stop condition when it
removes a clock, date, environment or ordering dependence and **does not change what the test
asserts**. Make it in its own commit, say in the message which dependence it removes, and list
it in the report. (The precedent: soul v2's journal test compared a real-clock run with a
fixed-clock run.) **Any change to non-test code outside the list is still a stop.**

### 8. Reports

The full report goes to `~/anam-measurements/reports/<piece>-<YYYY-MM-DD>.md` (outside the
repository, rule 3). The reply gives **at most 20 lines**: what was done, where the report is,
and the checklist's verdict. The report still carries the branch, `git diff --stat` and
`git log --oneline` (working rule 7).

Every report ends with a **MERGE CHECKLIST**:

- branch;
- tier;
- full-suite result (without `--run-live`) and the `ulimit -n` it ran under;
- `ruff`;
- `scripts/leak_check.py` result over the branch's commits;
- any stop condition hit;
- any file changed outside the plan;
- **DECISIONS NEEDED**, each with a recommendation. The operator may answer "take your
  recommendations".

### 9. Merge rule

A **Tier 0 to 2** piece whose merge checklist is clean (suite green, `ruff` clean,
`leak_check` clean, no stop condition hit, no file changed outside the plan) **may be merged
by the operator without external review.** **Tier 3 pieces, and any report with a stop
condition or an open decision, go to the reviewer first.** CC still never merges or pushes
(working rule 1); this rule is about who must look before the operator does
(`docs/GIT_WORKFLOW.md`).

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
an interval rather than as a bare count. *(Until Phase 10 this applies when a
decision depends on the finding; otherwise the number is labelled descriptive:
"Until go-live", item 5.)*

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

### When the classifier runs at temperature 0

*(Ruling of 2026-10-04; applies from piece 3.1, which pins the gate and correction classifiers at temperature 0.)*
Repeating one prompt then gives near-identical outputs, so "20 runs" no longer estimates a rate. Compare per-case
outcomes, and take 3 shuffled passes to catch state noise (the same case differing by what ran before it). Do not tune
cases or wording to move a documented failure.

### Mutation checks run with bytecode writing off

"Proven to bite" means breaking the code, running the tests, and restoring the code.
**Run those tests with `PYTHONDONTWRITEBYTECODE=1`**, or clear `__pycache__` after each
restore.

Python reuses a cached `.pyc` while the source file's **size and mtime (to the
second)** still match. A mutation the same length as the original, restored within the
same second, therefore leaves the *mutated* bytecode in force. This fails two ways:

- **It fakes a failure.** The next run tests the mutation, not the restored code. On
  2026-10-01 a full-suite run after an order-swap mutation failed two tests against
  code byte-identical to HEAD.
- **It hides a working mutation.** A same-size mutation can fail to take effect, and
  the test then appears not to catch it.

If a test fails right after a restore and `git diff` shows the code matches HEAD,
clear `__pycache__` before suspecting the code.

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

**A status label is a derived artifact too.** "Fixed", "closed", "queued" and a
finding's own number live in a report, a changelog line or a queue — none of which
is the code. Spot-check a status against the code before trusting it, and
especially before *extending* it: a queue inherited without checking carries its
errors forward silently, because a closed item is simply absent and absence looks
like nothing.

- **Occurrence (2026-09-23).** Triaging the diagnostic pass's queue found two errors
  in its own "closed" list. `unrun_tool` fires on ordinary English was carried as
  closed; re-running its exact repro flagged **4 of 4** strings, control clean — it
  had never been fixed. And the multi-chunk half-indexed-artifact finding was
  dropped from the queue because `changelog/2026-09-22-ambiguous-phrase-attribution.md`
  numbered the attribution fix **#12** when the published report numbers it **#14** —
  so a live confirmed bug was marked closed and a genuinely closed one was still
  queued under the wrong identity. The changelog was mine, and the mis-numbering
  propagated into a later brief before anyone read the code.

### A harness must build what production builds

An eval harness or diagnostic that calls the same function production calls is not
thereby measuring production. **It has to hand that function what production hands
it: the same ordering, the same defaults, the same surrounding configuration.**
Arbitrary inputs to the right function measure a system nobody runs. Before trusting
a harness, list what production builds around the call (candidate order, pool size
and composition, defaults, which registry or settings are in force) and check that
the harness builds the same. Where it cannot, record the difference beside the
harness and treat it as unknown, not as harmless.

The failure is quiet for the same reason as the rest of this section: the harness
returns clean, stable numbers, just about a configuration that never occurs.

**This rule rests on one recorded occurrence so far**, the one below. An earlier
gate/registry instance of the same class was recalled at review, but no record of it
has been found, so it is not counted. Treat the rule as a lesson from one case until a
second is documented. That argues for applying it, not for applying it lightly: the one
case sat in the measurement of record, unseen, until a production failure pointed at it.

- **Occurrence (2026-09-27, B11).** The correction eval lists candidates oldest
  first; `corrections.candidates()` lists them newest first. This was recorded in
  `BUILT.md` as a known difference with *"no measured result … known to depend on
  it"*. CO10.2's false link depends on exactly that: the same 11-candidate pool links
  **20/20** with the claim first (production's order) and **0/20** with it last. The
  frozen set reported this area clean because it never builds production's order.
  *"Not known to matter"* was only ever untested.

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

## Working rules

Each rule binds CC. Where a rule has existed only in review conversation rather than in
a project document, it says so. Rules B1 to B3 were proposed by CC
(`changelog/2026-10-03-agents-working-rules-draft.md`, Part B).

**1. Branches, not main; never start a server against `data/`.**
- CC never commits to main, and never pushes, merges, rebases or rewrites history. For each task CC creates a branch `cc/<piece>` from the current main, commits to it with explicit `git add <file>` (never `-A` or `.`), and stops. Lyle reviews `git diff main..cc/<piece>`, runs the leak check, squash-merges, commits and pushes. A Tier 3 piece's branch is merged before the next piece's branch is created. See `docs/GIT_WORKFLOW.md`.
- Before committing, CC confirms `git branch --show-current` names its `cc/` branch. The hooks in `.git/hooks` (see `docs/GIT_WORKFLOW.md`) refuse commits on main and all pushes; CC does not inspect, edit or bypass them and never uses `--no-verify`. *(Review conversation, 2026-10-04; `CLAUDE.md` "Git hygiene".)*
- CC does not start a server against `data/`. A server on a scratch store (the play kit) is started only when the task
  says so, and only after every resolved directory is confirmed under the scratch root. Server startup calls
  `db.init_databases()`, which applies pending migrations (`program/api/app.py`, `lifespan`). *(The rule itself:
  review conversation, 2026-10-03.)*

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
- *Until Phase 10, both of the above apply only when a decision depends on the finding; a number nothing depends on
  is reported and labelled as descriptive ("Until go-live", item 5).*
- A harness builds what production builds (`AGENTS.md`). Read the primary source, not a derived one (`AGENTS.md`).

**6. One model run at a time; every run resumable.**
- Ollama serves one model and samples correlate (`AGENTS.md`), so CC never runs two model-calling processes at once.
- Append each sample or pass to its raw file as it finishes. A restart skips what is done. *(Review conversation;
  every `scripts/notes_*` measurement does this.)*
- Before starting runs, CC says if model time looks longer than the limit the task gives.

**7. Report in the body; flag rather than guess.**
- Results go in the body of the reply in compact tables, and in a changelog file. Never "printed above" or "see the
  attachment". *(Review conversation.)* *Until Phase 10, the full report is a file and the reply is at most 20 lines
  ending in its merge checklist ("Until go-live", item 8); "never see the attachment" then means the reply must still
  state the outcome and every decision needed, not only point at the file.*
- A finished task's report gives the branch name, `git diff --stat main..<branch>`, `git log --oneline main..<branch>`, and a note for any file another open branch also touches. *(Review conversation, 2026-10-04.)*
- CC reports its own reading, not a recommendation to switch anything on. *(Review conversation.)*
- **Anything uncertain is flagged as uncertain**, with what would settle it, rather than resolved by a guess. A status
  claim is checked against the code before it is repeated (`AGENTS.md` "A status label is a derived artifact too").

**B1. New frozen cases are proposed first.** A proposed case is shown with its observed behaviour and added to a case
file only after review. The `documented` and `note` fields can be edited without moving the fingerprint; a change to
any fingerprinted field is a new freeze and says so. *(Review conversation, 2026-10-03; piece 7's practice.)*

**B2. Tier 3 work stops for review after it is built,** per "Stop-and-verify checkpoints" above. Prompt-facing
text (tool descriptions, result texts, refusal texts) counts as Tier 3 and is listed there.

**B3. A text change is measured before it is applied, when it needs under about ten minutes of model time on one process.**
- Inject the draft in memory in a scratch process, run the relevant requests, and read the replies, before the
  committed text changes.
- The 2026-10-03 drafts show why: the draft CC wrote itself (B) was no better than the current text (7/36 against 8/36;
  `changelog/2026-10-03-notes-search-line-and-gap-remeasure.md`).

## Git hygiene

- Explicit `git add <filename>` per file. Never `-A`, never `.`.
- `git status` confirmed clean before every commit — no unrelated files
  riding along.
- Commit only on the task's `cc/<piece>` branch. After creating it, never switch to main.
- Never `git push`, `merge`, `rebase`, `reset --hard`, `branch -D`, or change `git config`.
- Commit messages are public, so they follow the public-repo rules (rule 3): no keys, account names or real
  conversation text.
- Changelog entries are for Tier 2 and Tier 3 work and for behaviour changes (what changed, why, what was tested,
  known limitations, follow-up work). A docs-only fix goes in the commit message. Until Phase 10, an entry is at
  most about 30 lines ("Until go-live", item 3).
- `BUILT.md` is frozen history and is not updated (see its header). `ARCHITECTURE.md` is updated in the same commit
  as the work **only when an invariant changes**.
- `NOW.md` holds open items only. Closing an item moves it to `docs/archive/` with its ID, leaving a one-line index
  entry.
- A statement of what the system does belongs in a test or in the code; the docs explain why.
- A status claim cites a commit or a test.

## Testing

Every task needs tests before it's considered done. If a task can't
reasonably be tested (rare), say so explicitly rather than skipping
silently.
