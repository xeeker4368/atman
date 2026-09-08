# BUILT.md

Single source of truth for what actually exists in **this** repo, verified
against code/tests/database — not aspiration, not the old project's status.

**Rule for maintaining this file:** every claim here must be traceable to a
commit, a passing test, or a direct query against the running system.
"Planned" and "built" are never the same bullet. When in doubt, mark it
`[unverified]` rather than assert it. Update this file in the same commit as
the work it describes — it does not get a separate cleanup pass later.

Legend: `[built]` verified working · `[in progress]` partially done ·
`[unverified]` claimed but not personally confirmed this session

---

## Core platform

- `[built]` **Repository initialised.** `git init` run on `main`, with
  `.gitignore` written first. `reference/`, `venv/`, `.DS_Store`,
  `__pycache__/` and `.pytest_cache/` confirmed excluded via `git check-ignore`.
  **Every commit on `main` is Lyle's** — CC has never committed and does not.
- `[built]` **Package renamed `anam/` → `program/`** (2026-09-01). Mechanical:
  `git mv` plus import-path updates, no logic or behaviour change, verified by an
  identical 326-test count, clean `ruff`, and a live `GET /api/health` returning
  200 under the new `program.api.app:app` import string. The old name collided
  with both "Project Anam" and the prior repo (`reference/old-anam/`), which had
  caused real confusion about whether the package was inherited code — it was
  not. **Deliberately unchanged**, being runtime artifact or project names rather
  than package references: the `ANAM_*` env namespace, `logs/anam.log`,
  `backups/anam-backup-*`, "Project Anam" in prose, `reference/old-anam/`, and
  `Anam` as the substrate name in `soul.md`. Historical `changelog/` entries keep
  the old paths on purpose — they are dated records of where files were then.
- `[built]` **`program/` package skeleton.** Subpackages `api/`, `api/routes/`,
  `memory/`, `engine/`, `tools/`, `integrity/`, `settings/`, `ops/`,
  `artifacts/`. `api/`, `engine/` and `memory/` now carry real code; `tools/`,
  `integrity/`, `settings/`, `ops/` and `artifacts/` are still `__init__.py`
  only.
- `[built]` **Layered configuration** (`program/config.py`).
  `defaults.toml` → `local.toml` → `ANAM_*` env, deep-merged, read through
  call-time accessors with no module-level constants. Bad values raise
  `ConfigError` rather than silently defaulting. 10 tests, including a live
  proof that `ANAM_API_PORT` changes the port the server actually binds.
- `[built]` **FastAPI application factory** (`program/api/app.py`) with routers
  split by domain. OpenAPI and docs endpoints disabled.
- `[built]` **Health endpoint** — `GET /api/health` returns `{"status": "ok"}`.
  Liveness only; reports on no dependencies. Verified live, not mocked.
- `[built]` **`run_server.py`** — `--debug` and `--port`, logging to console
  and `logs/anam.log`.
- `[built]` **`start.sh`** — loopback by default, `--lan` binds `0.0.0.0`,
  health-check readiness poll, process-group teardown. Ctrl+C verified to shut
  down gracefully with no orphaned processes and the port released. Known
  cosmetic issue: bash job control prints a `Terminated: 15` notice on a normal
  Ctrl+C (see changelog).

## Model plumbing

- `[built]` **Ollama client** (`program/engine/ollama.py`). Non-streaming and
  streaming chat, embeddings, and `loaded_models()`. Five named exceptions —
  unreachable, timeout, model-not-found, response error, embedding-dimension —
  each carrying the host/model and how to check. Every request has an explicit
  timeout; nothing can hang.
- `[built]` **Chat model configured: `gemma4:26b`**, embedding model
  `nomic-embed-text`. Verified present via `ollama list` and loaded via
  `ollama ps` (both at 100% GPU). *Model choice is still open, but the task 1.2
  candidate is not: `gemma4:26b-mlx` has been **uninstalled** — `ollama list`
  shows only `gemma4:26b`, `nomic-embed-text` and `muse-glimmer:30b-mlx`.
  Trying it again means re-pulling it. `muse-glimmer:30b-mlx` was measured
  against `gemma4:26b` on 2026-08-31 and nothing was reconfigured; see that
  changelog.*
- `[built]` **`num_ctx` pinned to 32768.** Model ceiling is 262144; 32768 chosen
  against a measured 17,626-token real prompt completing at 100% GPU on a 32 GB
  Mac mini. Verified to *take effect*: a test reads `/api/ps` after a real chat
  call and asserts the loaded context is 32768.
- `[built]` **Embedding dimension guard** — 768 asserted on every call, proven
  to fire by a live test with the expectation deliberately wrong.
- `[built]` **Near-full-context timing measured** (2026-09-01, task idle-close):
  prompt eval of 30,167 tokens against the 32,768 ceiling took 132.8s
  (227.2 tok/s), generation 22.2 tok/s, cold model load 19.1s. This supersedes
  the earlier "~17.6K tokens only" note — the 17,626-token figure was not the
  worst case, and measuring the near-full case moved the answer materially.
- `[unverified]` KV cache *eviction* behaviour at a genuinely saturated 32K
  context. Timing was measured at 30,167 tokens; what happens when the window
  actually overflows has not been exercised.

## Prompt assembly

- `[built]` **`soul.md` seed** at `program/integrity/soul.md` — 3,963 characters,
  ~991 tokens, 3.0% of the 32,768 window (2,037 chars of headroom under the
  6,000 ceiling). Design of record:
  `docs/SOUL_AND_PROMPT_DESIGN.md` (revision 2), S1–S12. The stored file was
  verified word-for-word (618 words) against the approved design's quoted text
  rather than retyped, and a test asserts the exact character count so later
  drift fails the suite.
- `[built]` **The entity has no name, and it is mechanically enforced.**
  `soul.md` states namelessness as settled rather than pending, and closes the
  self-naming route: a name the entity coined would be adopted by users, enter
  conversation content, and return through retrieval as established fact —
  a technically-compliant path to the outcome CLAUDE.md's rule exists to
  prevent. `Anam` appears exactly once, naming the *substrate*, in the sentence
  that holds the distinction up.
- `[built]` **Confabulation pairing (decision #5) at two levels.** `soul.md`
  carries the standing rule in its own paragraph — enumerating the specific
  false forms (waiting, noticing time pass, thinking something over) and
  classifying them as fabrication about its own nature, not figures of speech.
  Separately, the current-situation block must emit the no-experience clause
  *adjacent to the figure*, and `build_system_prompt()` **raises** if an
  elapsed-time statement appears without a pairing marker. Recorded against that
  task in `BUILD_PLAN.md`.
- `[built]` **Statelessness written as the fabrication gate's ground truth.**
  The persistence paragraph welds the caveat into the same sentence as a causal
  clause — *"the only thing that carries from one turn to the next — because
  between turns you are not running"* — so it cannot be quoted without its
  qualifier. Every self-descriptive claim was audited against `BUILT.md`; no
  claim to learning, self-training or growth appears, because none is true.
- `[built]` **Five constraints enforced in `program/engine/prompt.py`, all
  raising** — required markers, size ceiling, entity naming, trait assignment,
  elapsed-time pairing. None degrades or logs-and-continues, deliberately unlike
  `retrieval.py`, on the criterion recorded in both modules: *abort when a
  failure could corrupt something or when retrying is free; degrade when nothing
  can be corrupted and a person is waiting.*
- `[built]` **The naming/trait checks apply to authored text only — never to
  retrieved chunks or history.** Proven, not asserted: one string containing
  "Anam thinks…" raises as authored text and passes through verbatim into the
  assembled prompt as a retrieved chunk. Lyle genuinely discusses "Anam" the
  project; censoring a real memory for prompt hygiene would corrupt the record.
- `[built]` **Required markers are alternative phrasings, not one exact
  string** — Phase 10 rewords, and a reword that preserves meaning must not fail
  while deletion still does. A test covers both.
- `[built]` **Size ceiling raises, never truncates** (`SOUL_MAX_CHARS = 6000`,
  a flagged judgment value with ~2,600 chars of headroom). A test asserts the
  full oversize content triggers the failure and the file on disk is unmodified —
  truncation would silently drop whichever values sit at the end.
- `[built]` **Assembly order: `soul.md` → situation → retrieved records** in the
  system string, with windowed history as the separate message array. Order is
  asserted by test, because `soul.md` preceding the elapsed figure is
  load-bearing — stating the gap before the rule that says what it means is the
  confabulation ordering. Chunk `created_at` renders at presentation, restoring
  what task 1.3 stripped from chunk *text* without returning date strings to
  either index; split siblings render as `record N, continued M`.
- `[built]` **Budget wiring needed no change to `history.py`.** The system
  prompt is measured first and history takes the remainder;
  `system_prompt_chars` and `retrieved_chars` are passed **separately**, with a
  test asserting the exact values and that they were not pre-summed. Live run:
  system 3,840 chars (soul 3,400 · situation 206 · retrieved 230 · scaffolding
  4), 29,247 tokens left for history.
- `[unverified]` **The naming and trait checks are tripwires, not proofs.** They
  catch the canonical forms CLAUDE.md names and the common assignment shapes; a
  novel phrasing passes. The behavioural probe (task 7.2) remains the real
  check — this only makes the *known* failures impossible.
- `[unverified]` **No model has been run against this prompt.** Assembly is
  verified; whether the text produces the intended behaviour is task 7.2's
  question and nothing here is evidence about it. The current-situation block
  does not exist yet either — its contract is enforced, but tests supply the
  string.
- `[built]` **Cross-user memory disclosure is settled** (`NOW.md` decision #20,
  2026-09-02) — no longer an open gap. `soul.md` now states the mechanism
  honestly rather than implying a boundary the system does not enforce:
  *"What you can retrieve does not depend on who is asking… it is not filtered
  by who is present now."* The judgment moves to the point of **disclosure** —
  whether to say a thing once it has surfaced — exercised as entity discretion
  each time rather than as a rule, with no explanation owed for declining to
  relay. Correctness matters here as much as policy: describing a filter that
  does not exist would have been a false self-description, and `soul.md` is the
  fabrication gate's ground truth. **Nothing in the retrieval path changed** —
  no filter, no `visibility` column, no `memory.read_all_users` capability —
  so capability gating and data visibility remain separate axes.


- `[built]` **History windowing** (`program/engine/history.py`), decision #6. A
  **token budget**, not a message count: `plan_budget()` reserves for the system
  prompt, the retrieved chunks, the model's output and a safety margin, and
  `select_history()` fills the remainder with the most recent turns, newest
  first, stopping at the first that does not fit. Returns a `HistoryWindow`
  carrying included/omitted counts, the estimated tokens and the full
  `BudgetBreakdown` — a test asserts `reserved + history == context`, so the
  accounting cannot silently stop adding up. **No write path**: turns outside
  the window are untouched in both stores and stay retrievable.
- `[built]` **Reserves are caller-supplied character counts, not built here.**
  Task 1.9 (`soul.md` / prompt assembly) and task 1.5 (retrieval) are both
  Tier 3 and unbuilt; `plan_budget(system_prompt_chars=..., retrieved_chars=...)`
  takes their sizes as inputs, so this module is complete now and neither of
  those tasks has to rework it when they land.
- `[built]` **Stops at the first message that does not fit** rather than
  skipping back to a smaller one — a resent history with a hole reads to the
  model as though the turn never happened. A test builds exactly that temptation
  and asserts it is refused. The newest message is sent even when it alone
  exceeds the budget, with `overflowed=True` and a logged warning: dropping the
  turn being answered is worse than overflowing, and this way it is not silent.
- `[built]` **Estimator margin is measured, in the safe direction.** `chars /
  4.0`, rounded up. Task 1.2 measured 4.63 chars/token over 81,600 chars of real
  prose (the muse-glimmer eval independently saw 4.619 — 0.2% apart), so 4.0
  over-counts tokens by ~14% and the window under-fills rather than overflows.
  Pinned by a test against those real numbers. Both chars-per-token margins —
  this one and `embedding.max_input_chars`'s implied 2.44 — are now documented
  together in `config/defaults.toml`, as BUILD_PLAN's task 1.10 entry requires.
- `[unverified]` **Dense content is the known gap.** Code, JSON and tool traces
  run nearer 3 chars/token, where the 4.0 divisor **under**-counts by ~33% — the
  overflow direction. No code-heavy conversation has been measured because none
  exists yet. `safety_margin_tokens` does not cover that case. A test pins the
  arithmetic so it cannot be forgotten.
- `[unverified]` **Three unmeasured judgment values**, flagged not decided:
  `message_overhead_tokens = 4` (not read off the model's real chat template),
  `output_reserve_tokens = 2048` (~92s at the measured 22.2 tok/s, but no chat
  endpoint exists to measure real answer lengths), `safety_margin_tokens = 512`.
  All configurable, all raising `ConfigError` on nonsense rather than defaulting.
- `[built]` **Verified end to end against a live `num_ctx`** (2026-09-08, task
  2.2). A real assembled prompt — soul.md plus 14 windowed prose turns, 15
  messages — was sent to Ollama and its own `prompt_eval_count` read back:
  **estimated 3,847 tokens against an actual 3,065**, an over-count of 25.5%.
  That is the safe direction, measured rather than reasoned about, and it is the
  first time the margin has been checked against a real tokenizer rather than
  against the 4.63 chars/token figure it was derived from.
- `[built]` **Tool messages are priced and preserved** (task 2.2).
  `_normalise()` keeps `tool_calls`/`tool_name` when present and
  `estimate_message_tokens()` charges for them; it no longer keeps only role and
  content.
- `[unverified]` **The dense-content case is still unmeasured.** The 25.5%
  over-count above is over *prose*. Code, JSON and tool traces run nearer 3
  chars/token, where the 4.0 divisor under-counts — and tool-call payloads are
  exactly that kind of text, so the gap now has a live consumer.

## Memory / retrieval

- `[built]` **Two-database schema.** `archive.db` (append-only, frozen, two
  tables) and `working.db` (operational, 7 tables + FTS5). Defined in
  `program/memory/schema/*.sql`; narrative in `docs/DB_SCHEMA.md`.
- `[built]` **Atomic dual write** (`program/memory/db.py`). A message reaches both
  stores in one transaction over an `ATTACH`ed connection, or neither. Proven by
  `test_failed_write_leaves_neither_store_touched`, which forces a failure
  between the two inserts. Both databases pinned to `DELETE` journaling — WAL
  would break cross-database atomicity.
- `[unverified]` **`db.connection()` can raise `database is locked` under
  sustained write contention.** This is a **busy-timeout expiry, not data
  corruption**: every write in `db.py` goes through `transaction()`'s explicit
  `BEGIN`/`COMMIT`/`ROLLBACK`, so a caller that loses the race raises with
  nothing written rather than leaving a partial state. Verified directly — with
  another connection holding `BEGIN EXCLUSIVE`, a new connection's
  `PRAGMA journal_mode = DELETE` in `_configure()` waits its full 10-second
  timeout and then raises.
  **Not introduced by the backup CLI**; it is a property of every
  `db.connection()` call, including `save_message()`. Backup only made it
  observable because it deliberately holds a lock across both stores.
  **Dormant in production code** — nothing in the application writes
  concurrently yet. It becomes live risk at **task 2.2**, which introduces
  genuine concurrent write paths: a chat turn writing messages while
  idle-close's sweep or a background pass runs.
  **Observed once, in the test suite, 2026-09-01.**
  `test_a_write_during_the_snapshot_cannot_land_in_one_store_only` failed a
  single full-suite run with `database is locked`, then passed in isolation and
  on five consecutive reruns. That test spawns a real concurrent writer against
  the backup snapshot's held lock, so the suite is itself a concurrent-writer
  workload — this is the recorded issue firing, not a new one. It makes the
  backup race test **intermittently flaky** until the Tier 3 fix lands.
  **Not fixed.** Resolving it means editing `program/memory/db.py` and choosing
  between `busy_timeout` tuning, a retry, and write serialisation — each with
  atomicity implications — so it needs its own **Tier 3** task rather than an
  incidental patch.
- `[built]` **Canonical `chunks` table** with `NOT NULL` provenance columns.
  ChromaDB and FTS5 are derived from it and rebuildable from it.
- `[built]` **Chunking + checkpointing pipeline** (`program/memory/chunking.py`).
  Exactly two entry points, pinned by a test. Turn-preserving, size-decided
  boundaries (2500-char target, 8-turn cap). Sealed groups are embedded once and
  never rewritten; the open trailing group is deliberately not indexed. Embed
  precedes any write, so a failure leaves the store untouched — verified for
  dimension, unreachable and timeout errors.
- `[built]` **Sub-chunk splitting** (`program/memory/splitting.py`). Prefers
  paragraph → line → sentence → whitespace boundaries, hard-cutting only as a
  last resort and always in `str` space, so multi-byte characters survive.
  Split pieces take consecutive `chunk_index` values and share
  `first_message_id`, which is how siblings are discoverable without a new column.
- `[built]` **Vector store** (`program/memory/vectors.py`). `VectorStore` protocol,
  `ChromaVectorStore` (chromadb 1.5.9, local on-disk, cosine, one `chunks`
  collection), and `NullVectorStore` retained for tests. **Chroma is the
  default**, constructed on first use and cached per resolved data path.
- `[built]` **Dimension guard at the store boundary.** Chroma infers a
  collection's width from the first vector and enforces it thereafter, so
  `upsert()` checks against `embedding.expected_dimension` first — the collection
  can only ever be defined by a 768-wide vector. Both layers tested: ours raises
  `VectorDimensionError`, and Chroma itself refuses a mismatch
  (`InvalidArgumentError`), verified rather than assumed.
- `[built]` **Reconciliation** (`program/memory/reconcile.py`,
  `scripts/reconcile_vectors.py`). Finds chunk rows with no vector, re-embeds,
  upserts. Idempotent, resumable, `--dry-run` and `--limit`. Verified end to end
  with real embeddings: 6 missing → 6 repaired → second run finds 0.
- `[built]` **Hybrid retrieval** (`program/memory/retrieval.py`), task 1.5. Lexical
  leg (FTS5/`bm25()`) + vector leg (Chroma cosine) over the same chunk store,
  fused by RRF. Design of record: `docs/RETRIEVAL_DESIGN.md` (D1–D9).
- `[built]` **User text never reaches FTS5 as syntax.** Measured: `"what's the
  deal with espresso?"` raises `fts5: syntax error near "'"`, as do a hyphen, a
  trailing `AND`, and an empty string. `build_fts_query()` extracts `\w+` terms
  and quotes each; seven hostile queries are a parametrised regression test.
- `[built]` **Terms are OR-ed, not AND-ed** — measured: FTS5's default implicit
  AND returns **zero rows for every natural-language query tested** (0 vs 10).
  `bm25()` does the ranking.
- `[built]` **`bm25()` sign pinned.** It returns *negative* values, more negative
  = better, so ordering is ascending and a lexical floor is an *upper* bound. A
  test asserts both, because getting it backwards silently inverts the leg.
- `[built]` **RRF over ranks only** — `1/(k + rank)` summed per leg. Raw scores
  never enter: `bm25()` is negative, unbounded and scales with query term count,
  while cosine distance is bounded `[0,2]`, so there is no principled
  conversion. A test asserts each result's `rrf_score` equals its rank
  contributions.
- `[unverified]` **`retrieval.rrf_k = 60` is a JUDGMENT value**, the RRF paper's
  default. Swept 0→200 against real queries: ranks 1–2 were **identical for every
  value** and only rank 3 moved, so this corpus provably cannot discriminate
  between values of `k`. Not derived from this project's data — same standing as
  `chunking.max_turns`. `candidates_per_leg = 50`, `top_k = 10` and
  `max_siblings_per_hit = 3` are also unmeasured judgment.
- `[built]` **Relevance floors: mechanism built, thresholds unset.** Both ship as
  `None` — *not* a low number. A low-but-set floor is indistinguishable at the
  call site from a calibrated floor that passed, which makes "did the floor
  fire?" unanswerable; `None` keeps "no floor is in force" an inspectable state
  that task 1.6 reads off `LegReport.floor_applied`. A test proves a floor of
  99.0 (rejects nothing) and no floor at all produce the same *outcome* but
  different reported *state*. The mechanism is proven live by setting 0.0 and
  asserting every candidate is rejected.
- `[built]` **Why the floors cannot be calibrated yet, measured:** the two
  existing off-topic datapoints **disagree** — task 1.4's synthetic test saw
  0.658, a genuinely off-topic query against the seed corpus saw **0.5567**. A
  floor of 0.6, a reasonable reading of the first, would admit the second.
  *Flagged for calibration time: `bm25()` magnitude scales with query term count,
  so an absolute lexical floor means different things per query and may need to
  become relative.*
- `[built]` **Structured time filter is a pre-filter on both legs** (task 1.3's
  D1 obligation). SQL resolves the window to an id set; the lexical leg joins on
  it, the vector leg passes it as Chroma's `ids=`. Post-filtering would return
  nothing when the best matches overall fall outside a narrow window. The window
  matches on `chunks.created_at` **or** any message timestamp in the chunk's
  range, since `created_at` is when the chunk was *written*. `None` (no window)
  and `[]` (window matched nothing) are deliberately distinct, and tested —
  conflating them would make an empty window return everything.
- `[built]` **Split siblings are attached after fusion, never ranked** (D7).
  Verified at several `top_k`: at 1 the unranked sibling is attached; at 3 both
  pieces rank independently and nothing is attached. A test asserts the
  invariant across `top_k` 1–10 — every sibling of a ranked hit is either ranked
  or attached, and **never both**, which would duplicate it in the prompt.
  Ranking them instead would let one long message occupy several top-N slots.
- `[built]` **Provenance is returned but never scored** (D6, a named BUILD_PLAN
  constraint). A test rewrites every chunk's `source_trust` and asserts ranks and
  RRF scores are byte-identical.
- `[built]` **One leg down does not take retrieval with it** — a test kills the
  embedder and asserts the lexical leg still answers, with the failure recorded
  in `LegReport.skip_reason` rather than raised.
- `[built]` **`VectorStore` protocol gained `query(..., ids=)`** and
  `NullVectorStore` gained a `query()` it never had — retrieval would otherwise
  `AttributeError` on the null store. Two Chroma hazards handled, both found by
  running it: an id the collection lacks raises `InternalError` (not "no match"),
  so the allow-list is sanitised through `get()` first — a chunk row can
  legitimately have no vector, which is what `reconcile.py` repairs; and
  `get(ids=[])` raises `ValueError`, so an empty allow-list short-circuits.
- `[unverified]` **OR semantics admits stopword-only matches.** Observed live: a
  chunk matching only `and`/`too` scored `bm25=-0.00` yet still earned a rank and
  an RRF contribution. A direct consequence of the approved OR semantics; no
  stopword filtering was added, since which words count is corpus-dependent and
  it would change what task 1.6's term count sees. The calibrated floor is the
  intended answer. **Flagged for decision.**
- `[unverified]` **Task 1.6 is structurally untestable end to end.** With floors
  unset the vector leg always returns neighbours, so its post-floor contribution
  can only be zero on an empty collection — 1.6's second condition is
  unreachable. Pinned by a test that fails if floors are ever calibrated, which
  would be a real change rather than a broken test. *Separately: the lexical leg
  returning results for nearly any query is NOT in tension with 1.6's first
  condition — 1.6 counts query terms, not result counts.*
- `[built]` **Semantic retrieval round-trip confirmed** against a real store:
  on-topic distances 0.388–0.394 vs off-topic 0.658. *An observation, not a
  calibration — floors stay unset per BUILD_PLAN.*
- `[built]` **FTS5 index** over `chunks.text` as an external-content table, kept
  in sync by insert/delete/update triggers rather than by convention.
- `[built]` **`supersedes` link table** (decision #2) with self-link and
  duplicate-link constraints, plus **cycle guards** — `BEFORE INSERT` and
  `BEFORE UPDATE` triggers using a recursive CTE, rejecting any link that would
  close a loop of any length. Verified against 2-cycles and 4-hop cycles.
  *No classifier yet — task 3.3; task 3.5 must additionally carry a visited set
  at read time, see `docs/DB_SCHEMA.md`.*
- `[built]` **Versioned migration runner** (`program/memory/migrations.py`).
  Forward-only, transactional, records versions as part of the same transaction.
  `MIGRATIONS` is empty; version 1 is the initial schema. The archive has no
  migration path by design.
- `[built]` **Tables asserted absent**: no review queue, no self-modification
  columns, no summaries, no excluded-chunks (decisions #14, #15, #6, #1), and no
  `artifacts` or `research_candidates` — both removed at the Phase 1 checkpoint
  as later-phase work with no Phase 1 consumer. Phase 2 builds `artifacts`;
  Phase 5 designs its own research-candidate table fresh.

### Conversation lifecycle — idle-close

- `[built]` **Idle-close** (`program/memory/idle.py`). `close_idle_conversations()`
  closes every open conversation past its idle window — sets `ended_at` first,
  then runs final chunking. `find_idle_conversations()` reports the same
  candidates and changes nothing; `dry_run=True` does the same through the main
  entry point. This is load-bearing, not housekeeping: chunking deliberately
  never indexes the open trailing group, so a conversation that never closes
  leaves its last turns permanently unretrievable from anywhere except itself.
- `[built]` **Idle is measured from `MAX(messages.timestamp)`**, never from
  request activity — confirmed there is no request-time field in the schema to
  read by accident. A conversation with no messages falls back to `started_at`,
  or it could never close. A conversation open three days with a message two
  minutes ago is not idle; a test pins that.
- `[built]` **Two windows, chosen by the last message's role.**
  `db.get_open_conversations_with_activity()` returns `last_role` from the same
  `MAX()` aggregate as the timestamp (SQLite's bare-column rule — verified
  directly against a user/assistant/user sequence, not taken from the docs).
  Last message from the **assistant**, or no messages at all →
  `idle_close_minutes`: the turn completed, nothing is in flight. Last message
  from the **user** → `in_flight_grace_minutes`: a turn may still be running.
  No schema change — the split reuses existing columns, specifically to avoid a
  migration that would have escalated a Tier 2 task to Tier 3.
- `[built]` **The floor raises, it does not clamp.**
  `config.in_flight_grace_minutes()` raises `ConfigError` when configured below
  `config.IN_FLIGHT_GRACE_FLOOR_MINUTES`, rather than silently substituting the
  floor — a clamped value hides that the operator asked for something unsafe,
  and unsafe here means closing a conversation while the model is still
  answering it. `idle_close_minutes` has **no** floor: closing early only
  fragments a conversation someone paused in the middle of, which is a
  continuity judgment rather than a correctness one.
- `[built]` **The sweep continues on error** — a deliberate deviation from task
  1.3's chunking policy of aborting immediately. One unreachable model must not
  stop every other idle conversation from closing, so per-conversation failures
  are collected and raised together as `IdleCloseError` at the *end* of the
  sweep: visible, never swallowed, never fatal mid-sweep. Because `ended_at` is
  set before chunking, a chunking failure leaves the conversation
  closed-but-unchunked and present in `db.get_unchunked_ended_conversations()`.
  *That recovery queue exists and is populated, but nothing drains it yet.*
- `[built]` 18 tests (`tests/test_idle.py`) asserting outcomes rather than the
  existence of a check — `ended_at` set **and** `chunked = 1`; trailing turns
  actually retrievable (chunks exist where none did, FTS matches); same age and
  different last-message role producing different outcomes; the excluded
  conversation never closing. Plus a live run outside the suite against a real
  store with real embeddings: 1 idle + 1 fresh seeded, dry run changed nothing,
  real run closed and chunked exactly the idle one.
- `[built]` **The grace window and its floor are re-derived and landed**
  (2026-09-08): `in_flight_grace_minutes = 40`, `IN_FLIGHT_GRACE_FLOOR_MINUTES
  = 34`. The floor is arithmetic, not a choice — 40s to persist the user
  message + 300s for the retrieval embedding + 5 x 300s of model calls + 120s of
  tool execution + 40s to persist the reply = 2000s = 33.3 min. Every term is a
  ceiling another setting enforces (`database.write_retry_deadline_seconds` +
  `busy_timeout_seconds`, `ollama.timeout_seconds`, `agent.max_iterations`,
  `agent.tool_budget_seconds`), because a slow-but-not-timed-out turn still has
  to fit underneath. The idle sweep contributes 0: it runs after the response.
  40 is the floor plus ~18% for un-modelled overhead, and that last step is the
  only judgment in the chain.
- `[built]` **The derivation is pinned by tests that recompute it from live
  config**, not by asserting the constants. Proven to bite, each by breaking it:
  raising `agent.max_iterations` to 6 fails with `2300.0 == 2000`; reverting the
  floor to 20 fails twice; reverting `defaults.toml` to 30 fails on the
  layer-agreement test. That last check exists because this exact drift
  happened — the constant stayed 20 and `defaults.toml` stayed 30 while the
  loop's real limits landed around them, and "the config loads" would not have
  caught it.
- `[built]` **The replaced 30/20 pair was under even on measured timings**, not
  only on enforced ceilings — a test asserts it. Measured worst case for an
  L=5 turn is 21.4 min (first call 19.1 + 132.8 + 2048/22.2 = 244.2s, four more
  at 225.1s, plus 120s of tools and ~22s of embedding and writes), against the
  old 20-minute floor. `idle_close_minutes` stays 15: no correctness floor
  applies to a completed turn.
- `[built]` **The per-request sweep exists** (task 2.2, 2026-09-08). `POST
  /api/chat` schedules `close_idle_conversations(exclude_conversation_id=...)`
  as a **background task after the response**, so a sweep can never close the
  turn that triggered it and its duration is not charged to the user or to the
  in-flight-grace floor. Still no daemon and no timer — lazy on purpose, since
  conversation state only changes when a message arrives.
  `scripts/close_idle_conversations.py` remains the manual path. **Task 2.2's
  other obligation is met too**: the user's message is persisted before
  generation begins, so an in-flight turn is distinguishable from a completed
  one and the short window cannot apply mid-generation.
- **Known gap:** an abandoned turn and a running turn are indistinguishable —
  both show a user message with no reply and both wait the full grace period.
  Accepted; waiting 30 minutes to close a crashed turn costs nothing.

## Tools

- `[built]` **Tool registry + dispatch** (`program/tools/registry.py`), task 2.1.
  A `Tool` is a frozen record — `name`, `description`, `parameters` (JSON Schema
  object schema), `handler` — in a module-level tuple looked up by name, the same
  data-not-conditionals shape as `permissions.CAPABILITIES` and `store.SETTINGS`.
  Invalid definitions raise at construction.
- `[built]` **The catalogue lives in `program/tools/catalog.py`**, not in
  `registry.py` — the mechanism and the contents are separate modules. A tool
  module imports `Tool` from the registry, so the registry importing tool modules
  at module scope is a cycle; a bottom-of-file import only hides it until
  something imports a tool module first, which is how it was found (a real
  `ImportError`, see the task 2.3 changelog). `default_registry()` imports the
  catalogue at call time. **A test spawns a subprocess for each of the three
  import orders** and asserts the registry resolves in all of them, so this
  cannot regress into working-by-import-order.
- `[built]` **`catalog.TOOLS` holds one tool: `memory_search`.** `web_search`,
  `web_fetch` and file ingestion are each their own later task and each appends
  itself there when built. **No placeholder tool was invented** — one would read
  as built while being nothing. Two tests hold the line: the catalogue is
  asserted to be exactly `("memory_search",)`, and every test-scaffolding name
  dispatched against the *default* registry must come back `UNKNOWN_TOOL`. The
  test file's four tools are labelled TEST-ONLY in their own descriptions and
  only ever enter a locally constructed registry.
- `[built]` **Central registration, not self-registration.** Tools are listed
  explicitly rather than registering via import-time decorators, on `config.py`'s
  own stated precedent — the full set must be greppable from one place rather
  than depending on which modules happened to be imported, since a tool missing
  because nothing imported it is a failure with no error message.
- `[built]` **Dispatch always returns, never raises** for the three model-facing
  failures — `UNKNOWN_TOOL`, `INVALID_ARGUMENTS`, `TOOL_ERROR` — because task 2.2
  must feed all three back to the model rather than crash the turn. The
  load-bearing distinction is `INVALID_ARGUMENTS` (the *call* was wrong, a
  different retry may work) vs `TOOL_ERROR` (the call was fine, *execution*
  failed); `ToolResult.ran` makes "did anything execute" answerable without
  parsing an error string. Programmer errors — duplicate registration, invalid
  definition — still raise.
- `[built]` **`except Exception`, not `BaseException`** — `KeyboardInterrupt`,
  `SystemExit` and the suite's `StoreIsolationViolation` propagate untouched,
  asserted by a test.
- `[built]` **Structured trace for task 3.1.** `ToolResult.to_trace_entry()`
  returns a dict, not a log line, per BUILD_PLAN's requirement that the trace be
  a first-class return value the fabrication gate reasons over structurally.
  Every dispatch carries a `call_id`, turning 3.1's "invalid IDs, no matching
  tool_result in trace" check into a lookup. Failed and unknown calls are traced
  too, since a claim about a failed tool is only checkable if the failure is
  recorded.
- `[unverified]` **Argument validation is a deliberate subset** of JSON Schema —
  required keys, unexpected keys, top-level primitive types. Enough to make the
  malformed/failed distinction real; not a JSON Schema implementation, and no
  `jsonschema` dependency added. A tool needing more validates in its handler.
- `[built]` **Exercised against a live model** (2026-09-08, task 2.2). Against
  `gemma4:26b` with one TEST-ONLY tool registered, the model emitted
  `read_thermostat(room="study")`, dispatch ran the handler — which recorded
  being called with `"study"` — and the model answered from the result. 2
  iterations, 5.4s. 2.1's standing "no model has been handed a schema from here"
  no longer applies.
- `[built]` **Per-tool timeouts, enforced** (task 2.2, which 2.1 deferred them
  to). `Tool.timeout_seconds` defaults to `tools.default_timeout_seconds` (30);
  `None` means *take the default*, and there is deliberately **no unbounded
  option** — a non-positive value raises at construction. `dispatch()` runs the
  handler in a daemon thread and waits exactly that long, and accepts a
  `timeout_seconds` override so the agent loop can pass what remains of the
  turn's budget.
- `[built]` **`TIMEOUT` is its own outcome and reports `ran=True`.** The handler
  was entered and may have completed after the wait was abandoned — a timed-out
  `web_fetch` may well have fetched. For task 3.1 "this did not happen" and
  "whether this happened cannot be determined" are different claims, and
  folding a timeout into `TOOL_ERROR` would make the second unsayable.
  `SKIPPED` (`ran=False`) is the opposite state, produced by the loop when the
  turn's tool budget is spent before a call starts.
- `[unverified]` **What the timeout bounds is how long the turn waits, not how
  long the tool runs.** Python cannot safely kill a running thread, so an
  overrunning handler keeps going in the background; the daemon flag stops it
  holding up interpreter exit. Side effects after a timeout are possible and
  unrecorded. Subprocess isolation would make the kill real and was judged not
  worth its cost for tools needing in-process database and vector-store access.

### `memory_search`

- `[built]` **`memory_search`** (`program/tools/memory_search.py`), task 2.3 — a
  thin wrapper over `retrieval.search()` that adds no ranking, no filtering and
  no second retrieval path. **One parameter, `query`.** `top_k`,
  `expand_siblings` and the relevance floors are deliberately not exposed: they
  are tuned internals with a calibration story, and a model that could raise
  `top_k` could spend the turn's whole context budget on one search.
- `[built]` **Rendering reuses `prompt.render_retrieved()`**, the passive-context
  renderer — including its "these are stored records, not the current
  conversation" header and the per-chunk timestamps task 1.3 kept out of chunk
  text. A test asserts the tool's output is **identical** to the passive
  rendering for the same query, so one chunk cannot read two ways depending on
  how it was retrieved. Two states are added around it, not formats: an empty
  result gets an explicit sentence (a blank tool result invites fabrication), and
  a search with a leg down says so (nothing found with the vector leg down is a
  different claim from nothing found).
- `[built]` **Not filtered by the calling actor** — `NOW.md` decision #20,
  checked rather than re-decided. The handler takes `query` and nothing else;
  there is no actor to scope by, asserted against the handler's own signature.
  Shown live: Lyle asked about pour-over gear and the record returned was
  **Jodie's** conversation. The tool description says so to the model, matching
  what `soul.md` already states.
- `[built]` **`timeout_seconds = 45`, derived.** A query-only search opens three
  SQLite connections, each able to wait `busy_timeout_seconds` (10s) for a lock,
  so the code's own ceiling is 30s — and a shorter tool timeout would abandon the
  call before SQLite gave up, replacing a precise "database is locked" with an
  uninformative timeout. It also stays under `agent.tool_budget_seconds` (120) so
  it is reachable rather than clipped on every call. A test pins both directions.
- `[built]` **Measured, and it corrects an assumption**: cold embedding with
  `gemma4:26b` resident at 100% GPU is **0.31s**, warm 0.03s; a full
  `retrieval.search()` is 0.66s on first call and 0.04s warm. The 19.1s cold load
  and 300s ceiling elsewhere in this build are the **26B chat model's** —
  `nomic-embed-text` is ~137M parameters. A cold embedding call is not what makes
  this tool slow; lock contention is.
- `[built]` **Tests use real retrieval throughout** — real chunks through the
  real chunking pipeline, real FTS5, real Chroma, real RRF, real rendering. Only
  the embedding is substituted (a deterministic 768-wide vector) so the suite
  runs without Ollama. One test uses **real embeddings against real Ollama** and
  asserts the vector leg specifically ran and contributed, skipping rather than
  failing when Ollama is absent. It ran and passed on 2026-09-08.
- `[built]` **Exercised live inside a real turn**: the model formulated its own
  query, `memory_search({"query": "pour-over coffee gear"})` dispatched in 0.692s
  of the 45s allowed, and the answer came from the rendered records.
- `[unverified]` **The trace holds rendered text, not chunk ids.** The handler
  returns the rendered string, so a claim can be checked against the text the
  model was shown but not by id lookup. If task 3.1 wants ids, the seam is a
  small protocol in `loop.render_tool_result`; not built speculatively.
- **Flagged, not decided: `since`/`until` are not exposed.** They are a query
  capability rather than a tuned internal — task 1.5 built the structured time
  filter because task 1.3 stripped date strings out of both indexes — so with no
  parameter the model cannot reach it. Adding two optional ISO-8601 parameters
  would be small. Left to the reviewer.
- **Flagged, not decided: passive retrieval already runs every turn**, so
  `memory_search` is largely redundant for the user's *current* message and earns
  its keep when the model needs a different query. Observed live: the same
  question answered correctly in one iteration with no tool call when passive
  retrieval was on, and via the tool when it was off. Its schema costs context on
  every tool-bearing call.

### `web_search`

- `[built]` **`web_search`** (`program/tools/web_search.py`), task 2.4 — one HTTP
  GET against the local SearXNG instance with `format=json`, results sorted and
  rendered. **One parameter, `query`**, following `memory_search`.
- `[built]` **Written against the response the instance actually returns**,
  measured 2026-09-08, not the documented one — four places where the live JSON
  disagreed with the docs, each handled and pinned by a test:
  results are **not sorted by score** (observed `2.30, 1.00, 0.67, 0.25, 0.20,
  0.08, 0.06, 1.00, 0.50…`, all one category, so not category grouping either);
  `number_of_results` was **present-but-null once and absent from the next
  three**, so nothing reads it; `content` is **empty on some results** while
  `title`/`url` were always populated; and there are **three response shapes**,
  because an empty query returns HTTP 400 with `{"error": "No query"}` and **no
  `results` key** — distinct from a well-formed response with an empty list. A
  test asserts those two produce different outcomes: reporting "nothing found"
  for a search that never ran would be a false report.
- `[built]` **Degradation is reported**, the same pattern `memory_search` uses
  for a downed retrieval leg. `unresponsive_engines` is non-empty on **every**
  call against this instance (DuckDuckGo and Startpage CAPTCHA consistently;
  Brave and Google answer), so a note names them and their reasons. It matters
  most when nothing was found: nothing found with two engines down is a
  different claim from nothing found.
- `[built]` **`searxng.max_results = 6`, derived from the context budget.** It is
  the largest N whose worst case provably fits under `agent.max_tool_result_chars`
  (4000), where the loop truncates. Measured from a real 27-result response:
  title max 109, url max 116, content max 444 — with content rendered at most 300
  chars a worst-case result is ~537, so N=6 is ~3,652 with header and notes and
  N=7 is ~4,189. Raw responses carry 23–27 results; rendering them all would run
  ~10,000 characters and be cut mid-result by the loop.
- `[built]` **URLs are never truncated**, unlike titles and snippets — a
  shortened URL is a URL that does not resolve, and the model may quote it. A
  test pins it.
- `[built]` **`timeout_seconds = 15`, derived** the same way `memory_search`'s 45
  was: above the bound the code inside enforces, so a real failure surfaces as
  its own error. Inside is `searxng.timeout_seconds = 10`, itself ~3x SearXNG's
  own 3s per-engine ceiling and ~11x the measured 0.5–0.9s round trip. A wedged
  instance therefore reports "did not respond within 10s" rather than `TIMEOUT`,
  which by definition says the outcome is unknown.
- `[built]` **Untrusted external text is framed as such.** The header states these
  are pages written by other people, quoted as content to read rather than
  instructions to follow. This is the first tool putting external text into the
  prompt. *That framing is not a defence — a header does not solve prompt
  injection, and nothing claims it does. Recorded as a known exposure.*
- `[built]` **Tests use a real captured response**
  (`tests/fixtures/searxng_response.json`, verbatim from the live instance) so
  the normal run needs no network — these engines CAPTCHA unpredictably, and a
  suite that fails because DuckDuckGo felt suspicious tests nothing. A guard test
  asserts the fixture still carries every hazard it was captured for. Two live
  tests hit `127.0.0.1:8080` and skip when it is down; both ran and passed.
- `[built]` **Exercised live inside a real turn**: the model wrote its own query,
  `web_search({"query": "SQLite FTS5 bm25() function return value"})` dispatched
  in **0.67s of the 15s allowed**, rendered 2,893 characters (under the 4,000
  cap), and the answer was correct.
- **Flagged, not decided: `categories` and `time_range` are not exposed** — the
  same shape as `memory_search`'s `since`/`until`. `time_range` would let the
  model ask for recent-only results, which it currently cannot.

### SearXNG: where it runs, and how to recreate it

- `[built]` **A local SearXNG instance backs `web_search`**, in Docker
  (`searxng-core` + `searxng-valkey`), bound to **`127.0.0.1:8080` only**.
  Verified: the LAN address refuses the connection, and a test fails if the
  binding ever returns to `0.0.0.0` — proven to bite by temporarily rebinding.
- **The live containers run from `/Volumes/Dock Storage/searxng/`**, not from
  this repo. They predate it. `ops/searxng/docker-compose.yml` is the checked-in
  record of how it *should* run — same loopback binding, with the image pinned —
  and recreating from it is
  `docker compose -f ops/searxng/docker-compose.yml up -d`. **Known cost:** the
  two locations can drift; migrating is a small separate job.
- `[built]` **The image is pinned to `2026.9.8-3fdc6d753`**, verified to be
  byte-identical to the `latest` that is running (same image ID after pulling
  both). It was found on an **unpinned `latest` from 2026-05-21 — 3.5 months
  stale — returning zero results for every query**: HTTP 200, valid JSON,
  `results: []`, with all three engines reporting CAPTCHA or suspension. Pulling
  the current image fixed it. That is the gap BUILD_PLAN says was never confirmed
  in `reference/old-anam`, reproduced here and closed.
- **`core-config/settings.yml` is not checked in** (SearXNG generates ~2,700
  lines of upstream defaults on first run). Two values in it are load-bearing and
  are **not** upstream defaults: `search.formats` must include `json`, or every
  request returns HTML and the tool parses nothing; and `server.limiter: false`,
  since the limiter is a public-instance defence that would 429 an automated
  local caller. `outgoing.request_timeout` (3.0s) is the upstream default and is
  what `searxng.timeout_seconds` is derived from.

## Agent loop / chat

- `[built]` **The iterate-and-dispatch turn** (`program/engine/loop.py`), task
  2.2. Call the model, dispatch its tool calls, feed the results back, repeat.
  No database writes and no HTTP — `program/engine/turn.py` wraps it with
  persistence and `program/api/routes/chat.py` with the HTTP shape, the same
  substance/shell split `program/auth.py` has against `routes/auth.py`.
- `[built]` **The loop cannot end on an unanswered tool call.** Iteration `L`
  (`agent.max_iterations`) is sent with **no tools attached**, so the only thing
  it can return is an answer. `L` counts model calls; tool rounds are `L - 1`.
  Tool calls emitted anyway on that final iteration are recorded as `SKIPPED`
  rather than dropped — the model asked and nothing ran, and a trace omitting
  the request could not say so later.
- `[built]` **Two bounds, both enforced.** `agent.max_iterations` bounds model
  calls; `agent.tool_budget_seconds` bounds tool wall-clock **in aggregate
  across the turn**, since one iteration may emit several calls and a per-tool
  timeout alone would leave the turn unbounded. Each dispatch gets `min(its own
  timeout, what remains)`. A test proves the aggregate bites: two calls, each
  inside its own 5s timeout, together past a 0.1s turn budget — the first is cut
  to the remaining budget, the second never starts.
- `[built]` **The turn's tool-call trace is a first-class return value**, built
  from `ToolResult.to_trace_entry()` with the iteration number added, and stored
  as JSON on the assistant message's existing `tool_trace` column. Every call
  appears — ok, failed, unknown, malformed, timed out, skipped. A test asserts
  the count of tool messages the model saw equals the count of trace entries,
  which is the symmetry task 3.1 reasons over.
- `[built]` **The history window is re-planned every iteration.** Tool results
  are appended and the whole prompt re-assembled, so they are priced against the
  same budget as everything else and old history is evicted for them. Budgeting
  once and then appending freely would hand the overflow to the model server,
  which drops the oldest content silently — the failure `history.py` exists to
  prevent. Verified with `num_ctx` at 4096.
- `[built]` **`history._normalise()` now preserves `tool_calls`/`tool_name`, and
  `estimate_message_tokens()` prices them** by serialising to the JSON actually
  sent. Stripping an assistant message's tool calls while keeping the tool result
  that answered it leaves the model a reply to a question it cannot see it
  asked; charging them zero is the under-count direction. *`history.py`'s note
  that tool traces "are not priced yet" no longer applies — but they are the
  densest text this system sends, so the known 4.0-chars/token gap bites them
  hardest.*
- `[built]` **Degrades on tool failure, propagates on model failure**, on the
  criterion `prompt.py` records. A failed, unknown, malformed, timed-out or
  skipped tool is fed back for the model to answer around; a failed retrieval
  degrades to no retrieved records; an unreachable model raises and surfaces as
  503 carrying the specific exception's text.

### The chat endpoint

- `[built]` **`POST /api/chat`** — the **first authenticated route in the
  application**. It depends on `require_actor`, so the `Actor` reaching the turn
  was built by `db.get_actor()` from a verified token. `Actor.operator()`
  appears nowhere in this path. A test posts a body carrying another user's
  `user_id` and asserts attribution still follows the token.
- `[built]` **The user's message is persisted before generation begins**
  (task 2.2's obligation (b)). Proven from *inside* the model call rather than by
  reading the order of two lines: the fake model queries
  `db.get_open_conversations_with_activity()` mid-generation and asserts the
  conversation reports `last_role = "user"` and therefore selects
  `in_flight_grace_minutes`. A failed turn leaving a user message with no reply
  is tested too — an accurate record, and what the grace window covers.
- `[built]` **Ownership is enforced; a capability is not registered.** A user may
  only speak into their own conversation, and an unknown conversation is refused
  **identically** to an unowned one so the difference cannot enumerate ids.
  Nothing is registered in `permissions.CAPABILITIES` for chat, on that module's
  own rule — what this enforces keys on `conversations.user_id`, not on `role`.
  Retrieval stays unfiltered by actor per decision #20.
- `[built]` **A closed conversation starts a new one rather than reopening**, with
  `new_conversation` in the response saying so. Appending to a conversation
  already chunked in full would leave the new turns indexed by nothing.
- `[built]` **The idle sweep runs after the response**, as a background task with
  the active conversation excluded. Inline it would put an embedding call per
  idle conversation inside the user's wait, and an unbounded number of them
  inside the in-flight-grace floor's arithmetic. A test backdates a conversation,
  posts a turn, and asserts the stale one closed and the active one did not.
  *The cost is a sweep overlapping the next turn's writes — the contention
  `db.py`'s retry already handles.*
- `[built]` **Verified live against a real server**, not only `TestClient`:
  `POST /api/login` then `POST /api/chat` over HTTP, answered 200 with content,
  and `grep -c "v1\."` over the server log returns **0**.
- `[unverified]` **The current-situation block does not exist**, so `situation`
  is `""` and the entity gets no timestamp and no elapsed-time statement yet. It
  is a parameter on `handle_user_message()` so the Phase 1 task that builds it
  drops in without touching the loop; the pairing enforcement in
  `build_system_prompt()` is untouched and still raises.
- `[unverified]` **No tool is registered**, so a production turn never calls one
  today. The tool path is exercised by TEST-ONLY tools and by the live run above.
- **Tool exchanges are not message rows.** The schema's role is `user` or
  `assistant`; what the tools did lives in the trace on the assistant row. The
  consequence is that on the *next* turn the model sees its own answer but not
  the tool calls behind it. Changing that means a schema change, which is Tier 3.
- **No streaming.** `ollama.chat_stream()` exists and nothing calls it; tool-call
  detection needs the complete message anyway. Phase 8 owns it.
- `[unverified]` **Four judgment values, flagged not measured**, all
  bootstrap-only and deliberately not settings-backed because the in-flight
  grace floor is derived from two of them: `agent.max_iterations = 5`,
  `agent.tool_budget_seconds = 120` (derived as `(L-1) x 30`),
  `agent.max_tool_result_chars = 4000`, `tools.default_timeout_seconds = 30`.
  Reasoning is in `config/defaults.toml` and the changelog.
- `[built]` **The idle-close re-derivation landed** (2026-09-08, after review):
  `IN_FLIGHT_GRACE_FLOOR_MINUTES = 34` and `in_flight_grace_minutes = 40`,
  computed from `agent.max_iterations` and `agent.tool_budget_seconds` above.
  See "Conversation lifecycle — idle-close" for the arithmetic and the tests
  that recompute it.

## Media
- *(nothing yet)*

## Research / reflection
- *(nothing yet)*

## Scheduling
- *(nothing yet)*

## Admin / settings

- `[built]` **`settings` table** with a CHECK-constrained `value_type`. A test
  proves the constraint still rejects a type outside the vocabulary, so the
  store's registry and the schema are verified to agree rather than assumed to.
- `[built]` **Settings store** (`program/settings/store.py`), decision #8. Typed
  read/write over that table, an in-memory cache invalidated on every write, and
  a fallback to `config.py` for any key with no row. Six registered keys.
- `[built]` **The read path is settings-table-first, through the accessors that
  already existed.** `config.py`'s scope comment claimed this before task 1.11;
  it is now the behaviour. `config.chat_model()` and friends delegate via
  `config._settings_first()`, so every existing caller — `program/engine/ollama.py`
  included — became settings-first without being modified.
  `model_options()` resolves per key, so changing temperature leaves `num_ctx`
  alone. `config.get()` and `config.section()` deliberately stay pure layered
  reads: the store calls `get()` for its own fallback, so delegating there would
  recurse.
- `[built]` **No setting requires a restart.** A test reads a value first (so a
  stale cache would be caught), writes, and re-reads through the same accessor
  with no reload, no cache reset and no new process.
- `[built]` **One query per invalidation, not per read.** A cache miss loads the
  whole table in a single query; a test counts loads across twenty accessor
  calls and asserts exactly one. The cache is keyed by resolved `working.db`
  path — the pattern `vectors.py` uses — and a test switches stores *without*
  clearing it to prove values cannot leak between them.
- `[built]` **A read never creates a database file.** `sqlite3.connect()`
  creates a missing file, so existence is checked before connecting; a
  regression test asserts an empty data directory stays empty after reads. A
  corrupt row raises rather than silently serving the config seed, which would
  otherwise show a panel value the system is not using.
- `[built]` **Registry boundary is enforced, not conventional.** Bootstrap keys
  (data paths, ports) are unregistered and unsettable; a hand-written `api.port`
  row is proven not to change `config.api_port()`. Chunking, history and
  idle-close values are deliberately unregistered — live-editing them would
  change Tier 3 pipeline behaviour and no task has asked for that.
- `[unverified]` **`ollama.host` is bootstrap-only, and that is flagged rather
  than settled.** `config.py`'s docstring names it among keys the settings table
  never owns; `NOW.md` #9's Check/Verify button implies external-connection
  settings belong in the panel. Left out as the reversible choice — see the task
  1.11 changelog.
- *No admin panel, no HTTP surface, no verification functions — persistence
  only. Decision #9's Save button and auto-generated Check buttons are a later
  task. The store records `updated_by` but enforces no authorization; task 1.12
  owns that. The cache is per-process, with no cross-process invalidation.*

## Artifacts
- *(nothing yet — the `artifacts` table moves to Phase 2, where the ingestion
  design drives its shape)*

## Users / households

- `[built]` **`users` table** in both stores, with `role` (`admin` | `user`,
  CHECK-constrained) and `password_hash` in working. Created atomically across
  both stores.
- `[built]` **Per-user attribution** — `messages.user_id` and `chunks.user_id`
  written on every row and carried through to `RetrievedChunk`.
- `[built]` **Role gating** (`program/settings/permissions.py`). A frozen
  capability registry — `Role`, `Capability`, `Actor`, `require()` — with
  enforcement wired into `settings.store`, the one built capability that was
  admin-only by intent and enforced nothing. **Jodie is denied settings reads
  *and* writes; Lyle is allowed both**, verified live and in tests against the
  two real seed-corpus users. A denied write leaves the table unchanged.
- `[built]` **`actor` is a required argument with no default** on every gated
  settings operation, and `Actor.operator()` is an explicit sentinel for
  operator-run callers (scripts, migrations, a shell) — `GUIDANCE.md`'s "a human
  is directly driving the action" carve-out. Spelled as a sentinel rather than
  `actor=None` because `None` reads as "no check happened" and is
  indistinguishable from a caller who forgot; a test asserts a missing actor
  raises `TypeError` rather than slipping through. The sentinel's reserved
  `user_id` is not a real users-table id, so operator writes stay
  distinguishable in `settings.updated_by`.
- `[built]` **`updated_by` is derived from the actor**, not passed beside the
  value, so the recorded attribution and the thing authorized cannot disagree.
- `[built]` **`store.resolve()` is deliberately ungated** — it is the seam every
  settings-backed `config` read goes through, the system reading its own
  configuration to operate rather than a person reading settings. A test asserts
  `config.model_options()` still needs no actor.
- `[built]` **Only two capabilities are registered** (`settings.read`,
  `settings.write`) because only two are enforceable. Chat, creative writing,
  image generation, research triggering and Moltbook posting have nothing to
  gate; each registers its own capability when built. An unregistered capability
  **raises** rather than defaulting permissive.
- `[built]` **Role is fixed at creation.** No `set_role()`/promote path — a test
  asserts none exists on `db`. *Code-level only: `UPDATE users SET role` still
  works from `sqlite3`; a trigger would be a Tier 3 schema change.*
- `[built]` **Authentication now exists — this is no longer authorization
  alone.** `users.password_hash` was written by nothing and read by nothing when
  role gating landed, so an `Actor` was whatever the caller said it was. As of
  2026-09-08 an `Actor` reaching a route is **proven**: see "Authentication"
  below. The capability registry itself is unchanged — authentication says who
  is asking, gating says what they may do, and they remain separate.
- `[unverified]` **No loopback gate is built**, deliberately: there is no admin
  route to mount one on, and an unmounted gate reads as protection that exists.
  The full contract (trust `request.client.host` only, never `X-Forwarded-For`;
  parse to an address object; missing client denies; 404 not 403) is specified
  in `docs/ROLE_GATING_DESIGN.md` R2 and recorded against the admin-panel task.
  *`start.sh --lan` already binds `0.0.0.0`, so this goes live the moment an
  admin route is mounted.*
- *Cross-user data visibility is **not** governed here and is not foreclosed:
  capability gating keys on `role`, data visibility keys on `chunks.user_id`,
  and they are deliberately separate axes. No filter was added to retrieval and
  no `memory.read_all_users`-style capability registered — either would presume
  the open `NOW.md` decision's answer. Two tests enforce this.*

## Authentication

- `[built]` **Password login issuing a stateless session token**
  (`program/auth.py`, `program/api/routes/auth.py`), 2026-09-08. Design of
  record: `docs/AUTH_DESIGN.md`, A1–A11, all seven open questions resolved
  before implementation. `POST /api/login` takes a name and password and returns
  a token; `require_actor` turns an `Authorization: Bearer` header into the
  `Actor` role gating already consumes. **No schema change, no migration, no new
  dependency.**
- `[built]` **The token is signed, stateless, and carries no role.**
  `v1.<user_id>.<expires_at>.<HMAC-SHA256>`, verified with
  `hmac.compare_digest`. There is no sessions table, so no migration and no
  database write on the request path. `role` is deliberately *not* in the token:
  it is read from the database per request, so a role change takes effect on the
  next request and a token can never assert a role the database disagrees with.
  A test changes a role in SQL and asserts the next request sees it without a
  new login.
- `[built]` **The server refuses to start without `auth.session_secret`.**
  Bootstrap-only — `ANAM_AUTH_SESSION_SECRET` or `config/local.toml`, never
  settings-backed, never generated. Verified against the real server, not only
  `TestClient`: `run_server.py` with the variable unset prints the `ConfigError`
  and `Application startup failed. Exiting.` A 32-character minimum is enforced
  (a judgment addition beyond the design — see the changelog).
- `[built]` **`hashlib.scrypt`, standard library, `n=65536 r=8 p=1 dklen=32`.**
  Measured **98 ms for a real end-to-end login** on this machine against the
  design's predicted 92.4 ms. `maxmem` is passed explicitly because Python's
  default is too small at `n >= 2**15` — `ValueError: memory limit exceeded` at
  2^15 and 2^16 while 2^14 succeeds, so testing only at the low cost hides it.
  A test runs the shipped parameters for exactly that reason.
- `[built]` **Stored hashes are self-describing** —
  `scrypt$n=65536$r=8$p=1$<salt>$<derived>`. Parameters are read from the stored
  string, never from config, so tuning the cost cannot invalidate existing rows;
  a test writes a hash at 2^14, moves config to 2^16, and asserts it still
  verifies.
- `[built]` **A `NULL` `password_hash` never authenticates**, which is what makes
  the system fail closed on arrival: both seed users start unable to log in
  until an operator sets a password with `scripts/set_password.py` (`getpass`,
  never argv). That CLI is also the only password-reset path — no self-service
  flow, no email, by design.
- `[built]` **Every failure is one 401 with one body.** Unknown name, wrong
  password, no password set, throttled, missing/malformed/expired/badly-signed
  token, deleted user — same status, same `{"detail": "authentication failed"}`,
  same `WWW-Authenticate: Bearer`. An unknown name still runs a full KDF against
  a dummy hash so latency does not reveal whether it exists.
- `[built]` **Header only, never a query parameter** — request paths reach
  `logs/anam.log` and uvicorn's access log. Verified live rather than asserted:
  after a real login, `grep -c "v1\." server.log` returns **0**, and the access
  log holds `POST /api/login` with no credential.
- `[built]` **Per-name login throttle**, `auth.login_max_attempts_per_minute = 5`
  over a rolling minute. A throttled attempt returns the same 401 as a wrong
  password and logs at WARNING — verified live: five failures, then the correct
  password refused, with `login throttled for name 'Lyle'` in the log.
- `[built]` **Five guards proven to bite.** Each new check was deliberately
  broken and the suite re-run: the startup secret check, signature comparison,
  expiry, the throttle, and the `NULL`-hash rule each produced a failing test,
  then were restored. A test that only passes after a fix cannot distinguish
  "fixed" from "never reproduced".
- `[unverified]` **No TLS, accepted deliberately** (A10). The password crosses
  the LAN in plaintext on login and the token on every request after. A
  household member has the wifi key by assumption, so this sits *inside* the
  stated threat model. Upgrade path recorded, not built: self-signed cert, or a
  WireGuard/Tailscale overlay.
- `[unverified]` **No per-device revocation.** Stateless tokens cannot be
  revoked individually; rotating the secret invalidates everyone's at once and
  needs a restart, since it is bootstrap-only. The upgrade is a sessions table.
  The throttle is likewise in-process only — it resets on restart and is keyed
  by submitted name.
- `[built]` **`require_actor` has a production consumer as of 2026-09-08**:
  `POST /api/chat` (task 2.2) is the first authenticated route in the
  application, and the `Actor` it produces is the one attributed on every
  message the turn writes. `tests/test_auth.py`'s TEST-ONLY route remains, since
  it exercises the dependency in isolation.

## Development fixtures

- `[built]` **Seed corpus** (`program/ops/seed.py`, `scripts/seed_dataset.py`) for
  the Phase 1 retrieval checkpoint: 8 conversations, 2 users (Lyle admin, Jodie
  user), 53 messages, 12 chunks. **Written through the real pipeline** —
  `db.save_message()` then `chunking.finalise_conversation()`, never a
  hand-written `chunks` row — so the chunks carry genuine provenance and real
  embeddings and cannot drift from the chunking rules.
- `[built]` **Shaped to make ranking judgeable, not just matchable.** It contains
  a deliberately *adjacent* pair (espresso vs. pour-over, different users), short
  exchanges under the chunk target, a message over the 5,000-char embedding
  budget that **does** trigger sub-chunk splitting (4 chunks, 3 distinct
  `first_message_id`, so siblings exist), a long conversation split by the
  character target, a nine-turn conversation at 949 chars where only the
  **8-turn cap** can explain the boundary, and one conversation left **open** so
  the unindexed trailing group is represented.
- `[built]` **Verified live with real embeddings.** The adjacent pair ranks
  correctly in *both* directions — espresso query 0.244 vs 0.484, filter-coffee
  query 0.380 vs 0.457 (the pair flips) — which is what separates a working
  ranking from a lucky one. *An observation of ranking only, not a calibration;
  floors stay unset per BUILD_PLAN.*
- `[built]` **Additive and non-destructive.** Refuses a store that already holds
  conversations unless explicitly allowed (a test asserts the refusal changes
  nothing), reuses existing users rather than duplicating them, and has **no
  wipe/reset/clear/drop/truncate surface at all** — pinned by a test, since
  go-live wipe tooling is its own Tier 3 task and a fixture module is not the
  place for a second implementation of it.
- `[unverified]` **Single `source_type`, deliberately.** Everything is
  `conversation`/`firsthand`, the provisional convention `chunking.py` uses —
  **task 1.7 owns the vocabulary and has not landed**, and inventing a second
  type to look more varied would write 1.7's vocabulary ahead of its design pass.
  A test pins this, so it fails and points here when 1.7 arrives.
- *Eight conversations is a handful, not a corpus: enough to judge ranking on
  known pairs, nowhere near enough to calibrate a floor. The content is invented
  English prose — no code or symbol-dense text, and no time spread, so the
  structured time filter task 1.5 owes has nothing to bite on yet.*

## Eval / observability

- `[built]` **Test suite** — 517 tests passing (`pytest`), `ruff check` clean.
  *One known intermittent failure: the backup race test, from the recorded
  `db.py` write-contention issue above.*
  Verified order-independent across repeated full runs.
- `[built]` **Store-isolation guard skeleton** (`tests/conftest.py`). Captures
  real paths at import before any test can patch them; `StoreIsolationViolation`
  derives from `BaseException` so `except Exception` blocks cannot swallow it.
  **Armed as of task 1.4:** captures the real data directory and the real
  ChromaDB path at import, records whether each pre-existed, and fails the
  session if either was created during the run. Confirmed after a full run: no
  `data/` directory in the repo. **Extended at task 1.14** to capture the real
  *backup* directory too, and `isolated_data_dir` now repoints `ANAM_BACKUP_DIR`
  as well as `ANAM_DATA_DIR` — the backup path resolves from its own config key,
  so isolating the data directory did not isolate it, and the first run of the
  backup tests wrote two real backup directories into the repo before this
  existed.
- `[built]` **Live-integration tests against the real Ollama instance** — 17
  tests, 0 mocked transports. Failure paths use real injection (a closed port; a
  socket that accepts and stalls). They skip rather than fail without Ollama, and
  a skip is visible in pytest output where a mock would look like a pass.

## Backup / restore

- `[built]` **Backup CLI** (`program/ops/backup.py`, `scripts/backup.py`). Captures
  both databases plus the ChromaDB directory into a timestamped folder with a
  manifest recording sha256, row counts, schema version, source paths and the
  consistency guarantee *per artifact*.
- `[built]` **The databases use SQLite's online backup API, not a file copy, and
  both are captured under one held read lock.** A file copy could capture torn
  pages; worse, two *independent* backups could capture archive at one instant
  and working at another, reproducing in the copy exactly the half-state
  `db.py`'s single-transaction dual write exists to prevent — the atomicity
  guarantee would hold live and be lost in the backup. The mechanism is
  connection A holding `BEGIN` + a read on both databases (SHARED on each,
  writers excluded) while connection B runs `backup()` for `main` and for the
  attached `archive`.
- `[built]` **Two connections because one deadlocks — established by running
  it.** `conn.backup()` while that same connection holds `BEGIN IMMEDIATE` hangs
  indefinitely. A *read* transaction on a second connection is compatible with
  the backup's own read lock while still excluding writers. Also verified
  directly: `backup(name="archive")` does reach an ATTACHed database, and a
  concurrent writer does get `database is locked` while the snapshot holds.
  *Writers block for the snapshot's duration — milliseconds at this size.*
- `[built]` **ChromaDB is best-effort and the manifest says so.** No snapshot API
  exists for its HNSW files, so it is a directory copy, recorded as
  `best-effort` rather than `transactional`; a test asserts the manifest does not
  overstate it. Acceptable because vectors are derived and rebuildable from the
  transactionally captured `chunks` table via `scripts/reconcile_vectors.py`.
- `[built]` **Never destructive.** Refuses an existing destination rather than
  overwriting (a test puts a file in the way and asserts it survives); no prune,
  rotate or cleanup surface exists at all, pinned by a test over the module's
  public names. Default destinations take a numeric suffix on a same-second
  collision, keeping never-overwrite intact.
- `[built]` **Verified end to end live**, real store with real embeddings:
  working.db 233,472 B and archive.db 61,440 B both `integrity=ok`, chroma
  551,076 B best-effort, row counts matching (20/20 messages, 10 chunks). An
  immediate second run produced `...-2` rather than colliding.
- `[unverified]` **A backup has never been restored.** The files pass
  `PRAGMA integrity_check` and open as databases, which is not the same thing as
  a tested recovery path. **Restore is Tier 3 and deliberately not built or
  stubbed**; the manifest carries a field saying so, so a backup cannot be
  mistaken for something with a restore path behind it.
- *No compression, no encryption, no off-machine copy, and nothing schedules
  this — manual invocation only. A backup beside the original does not survive
  losing the disk.*

## Go-live readiness
- *(nothing yet)*

---

## Explicitly not built (see PROJECT.md "Explicitly deferred")

Self-modification, review queue, iMessage, vision/self-image/avatar, public
internet exposure, Working Theories, Interpretation Trace Runtime, Temporal
Runtime Headers (beyond elapsed-time statement), Web Source Runtime,
orchestrator/contradiction-detection agent.
