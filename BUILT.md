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
- `[built]` **`anam/` package skeleton.** Subpackages `api/`, `api/routes/`,
  `memory/`, `engine/`, `tools/`, `integrity/`, `settings/`, `ops/`,
  `artifacts/`. `api/`, `engine/` and `memory/` now carry real code; `tools/`,
  `integrity/`, `settings/`, `ops/` and `artifacts/` are still `__init__.py`
  only.
- `[built]` **Layered configuration** (`anam/config.py`).
  `defaults.toml` → `local.toml` → `ANAM_*` env, deep-merged, read through
  call-time accessors with no module-level constants. Bad values raise
  `ConfigError` rather than silently defaulting. 10 tests, including a live
  proof that `ANAM_API_PORT` changes the port the server actually binds.
- `[built]` **FastAPI application factory** (`anam/api/app.py`) with routers
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

- `[built]` **Ollama client** (`anam/engine/ollama.py`). Non-streaming and
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

- `[built]` **History windowing** (`anam/engine/history.py`), decision #6. A
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
- `[unverified]` **Never verified end to end against a live `num_ctx`.** Nothing
  has yet sent a windowed history to Ollama and confirmed the real token count
  came in under the window — that needs task 2.2's chat endpoint and is the only
  thing that will prove the margin rather than reason about it. Tool-call traces
  are also not priced yet; `_normalise()` keeps only role and content.

## Memory / retrieval

- `[built]` **Two-database schema.** `archive.db` (append-only, frozen, two
  tables) and `working.db` (operational, 7 tables + FTS5). Defined in
  `anam/memory/schema/*.sql`; narrative in `docs/DB_SCHEMA.md`.
- `[built]` **Atomic dual write** (`anam/memory/db.py`). A message reaches both
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
  **Currently dormant** — nothing in the codebase writes concurrently yet. It
  becomes live risk at **task 2.2**, which introduces genuine concurrent write
  paths: a chat turn writing messages while idle-close's sweep or a background
  pass runs.
  **Not fixed.** Resolving it means editing `anam/memory/db.py` and choosing
  between `busy_timeout` tuning, a retry, and write serialisation — each with
  atomicity implications — so it needs its own **Tier 3** task rather than an
  incidental patch.
- `[built]` **Canonical `chunks` table** with `NOT NULL` provenance columns.
  ChromaDB and FTS5 are derived from it and rebuildable from it.
- `[built]` **Chunking + checkpointing pipeline** (`anam/memory/chunking.py`).
  Exactly two entry points, pinned by a test. Turn-preserving, size-decided
  boundaries (2500-char target, 8-turn cap). Sealed groups are embedded once and
  never rewritten; the open trailing group is deliberately not indexed. Embed
  precedes any write, so a failure leaves the store untouched — verified for
  dimension, unreachable and timeout errors.
- `[built]` **Sub-chunk splitting** (`anam/memory/splitting.py`). Prefers
  paragraph → line → sentence → whitespace boundaries, hard-cutting only as a
  last resort and always in `str` space, so multi-byte characters survive.
  Split pieces take consecutive `chunk_index` values and share
  `first_message_id`, which is how siblings are discoverable without a new column.
- `[built]` **Vector store** (`anam/memory/vectors.py`). `VectorStore` protocol,
  `ChromaVectorStore` (chromadb 1.5.9, local on-disk, cosine, one `chunks`
  collection), and `NullVectorStore` retained for tests. **Chroma is the
  default**, constructed on first use and cached per resolved data path.
- `[built]` **Dimension guard at the store boundary.** Chroma infers a
  collection's width from the first vector and enforces it thereafter, so
  `upsert()` checks against `embedding.expected_dimension` first — the collection
  can only ever be defined by a 768-wide vector. Both layers tested: ours raises
  `VectorDimensionError`, and Chroma itself refuses a mismatch
  (`InvalidArgumentError`), verified rather than assumed.
- `[built]` **Reconciliation** (`anam/memory/reconcile.py`,
  `scripts/reconcile_vectors.py`). Finds chunk rows with no vector, re-embeds,
  upserts. Idempotent, resumable, `--dry-run` and `--limit`. Verified end to end
  with real embeddings: 6 missing → 6 repaired → second run finds 0.
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
- `[built]` **Versioned migration runner** (`anam/memory/migrations.py`).
  Forward-only, transactional, records versions as part of the same transaction.
  `MIGRATIONS` is empty; version 1 is the initial schema. The archive has no
  migration path by design.
- `[built]` **Tables asserted absent**: no review queue, no self-modification
  columns, no summaries, no excluded-chunks (decisions #14, #15, #6, #1), and no
  `artifacts` or `research_candidates` — both removed at the Phase 1 checkpoint
  as later-phase work with no Phase 1 consumer. Phase 2 builds `artifacts`;
  Phase 5 designs its own research-candidate table fresh.

### Conversation lifecycle — idle-close

- `[built]` **Idle-close** (`anam/memory/idle.py`). `close_idle_conversations()`
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
- `[unverified]` **The 15 / 30 / 20-minute values are placeholders.**
  `idle_close_minutes = 15`, `in_flight_grace_minutes = 30`, floor 20. They come
  from a real measurement (~197s for one worst-case turn, from the figures under
  "Model plumbing" above) multiplied by an **assumed** 5-iteration agent loop
  that does not exist yet — giving ~15–16 minutes, of which 30 is roughly 2x.
  Tool execution time is not in the arithmetic at all, because there are no
  tools; the reference build's 300s image-generation timeout would have exceeded
  a per-iteration budget on its own. **Task 2.2 owes a re-derivation** from its
  actual iteration limit and actual tool timeouts, recorded against that task in
  `BUILD_PLAN.md` rather than only here.
- **Nothing triggers this automatically.** There is no chat endpoint, no daemon
  and no timer — the design is lazy on purpose, since conversation state only
  changes when a message arrives. The only callers today are
  `scripts/close_idle_conversations.py` and the tests. The per-request sweep
  arrives with **task 2.2**, which will pass the active conversation as
  `exclude_conversation_id` so a sweep can never close the turn that triggered
  it. Task 2.2 additionally owes persisting the user's message *before*
  generation begins: without that, an in-flight turn is indistinguishable from a
  completed one and the short window would apply mid-generation. That is a
  correctness dependency, not just crash-safety.
- **Known gap:** an abandoned turn and a running turn are indistinguishable —
  both show a user message with no reply and both wait the full grace period.
  Accepted; waiting 30 minutes to close a crashed turn costs nothing.

## Tools
- *(nothing yet)*

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
- `[built]` **Settings store** (`anam/settings/store.py`), decision #8. Typed
  read/write over that table, an in-memory cache invalidated on every write, and
  a fallback to `config.py` for any key with no row. Seven registered keys.
- `[built]` **The read path is settings-table-first, through the accessors that
  already existed.** `config.py`'s scope comment claimed this before task 1.11;
  it is now the behaviour. `config.chat_model()` and friends delegate via
  `config._settings_first()`, so every existing caller — `anam/engine/ollama.py`
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
  both stores. *No auth or role gating yet — task 1.12.*

## Development fixtures

- `[built]` **Seed corpus** (`anam/ops/seed.py`, `scripts/seed_dataset.py`) for
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

- `[built]` **Test suite** — 233 tests passing (`pytest`), `ruff check` clean.
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

- `[built]` **Backup CLI** (`anam/ops/backup.py`, `scripts/backup.py`). Captures
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
