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
  *Not yet committed — the first commit is Lyle's.*
- `[built]` **`anam/` package skeleton.** Subpackages `api/`, `api/routes/`,
  `memory/`, `engine/`, `tools/`, `integrity/`, `settings/`, `ops/`,
  `artifacts/` — all empty apart from `api/`.
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
  `ollama ps`. *Model choice is open — `gemma4:26b-mlx` may be faster on this
  hardware; see the task 1.2 changelog.*
- `[built]` **`num_ctx` pinned to 32768.** Model ceiling is 262144; 32768 chosen
  against a measured 17,626-token real prompt completing at 100% GPU on a 32 GB
  Mac mini. Verified to *take effect*: a test reads `/api/ps` after a real chat
  call and asserts the loaded context is 32768.
- `[built]` **Embedding dimension guard** — 768 asserted on every call, proven
  to fire by a live test with the expectation deliberately wrong.
- `[unverified]` KV cache behaviour at a genuinely full 32K context. Measured to
  ~17.6K tokens only.

## Memory / retrieval

- `[built]` **Two-database schema.** `archive.db` (append-only, frozen, two
  tables) and `working.db` (operational, 7 tables + FTS5). Defined in
  `anam/memory/schema/*.sql`; narrative in `docs/DB_SCHEMA.md`.
- `[built]` **Atomic dual write** (`anam/memory/db.py`). A message reaches both
  stores in one transaction over an `ATTACH`ed connection, or neither. Proven by
  `test_failed_write_leaves_neither_store_touched`, which forces a failure
  between the two inserts. Both databases pinned to `DELETE` journaling — WAL
  would break cross-database atomicity.
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
- `[built]` **Vector-store seam** (`anam/memory/vectors.py`). `VectorStore`
  protocol plus `NullVectorStore`. *Default is the null store until task 1.4 —
  chunks are **lexically retrievable only**, and `ChunkingResult.vectors_indexed`
  reports 0 rather than letting that read as success.*
- `[unverified]` Reconciliation of chunks missing a vector — specified in the
  task 1.3 design, lands with 1.4.
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

## Tools
- *(nothing yet)*

## Media
- *(nothing yet)*

## Research / reflection
- *(nothing yet)*

## Scheduling
- *(nothing yet)*

## Admin / settings

- `[built]` **`settings` table** with a CHECK-constrained `value_type`.
  *No store or cache yet — task 1.11. `anam/settings/` is still empty.*

## Artifacts
- *(nothing yet — the `artifacts` table moves to Phase 2, where the ingestion
  design drives its shape)*

## Users / households

- `[built]` **`users` table** in both stores, with `role` (`admin` | `user`,
  CHECK-constrained) and `password_hash` in working. Created atomically across
  both stores. *No auth or role gating yet — task 1.12.*

## Eval / observability

- `[built]` **Test suite** — 109 tests passing (`pytest`), `ruff check` clean.
- `[built]` **Store-isolation guard skeleton** (`tests/conftest.py`). Captures
  real paths at import before any test can patch them; `StoreIsolationViolation`
  derives from `BaseException` so `except Exception` blocks cannot swallow it.
  Now exercised: the `isolated_data_dir` fixture redirects the data directory
  per test, and a full suite run creates no `data/` directory in the repo.
  Task 1.4 extends it to the vector store.
- `[built]` **Live-integration tests against the real Ollama instance** — 17
  tests, 0 mocked transports. Failure paths use real injection (a closed port; a
  socket that accepts and stalls). They skip rather than fail without Ollama, and
  a skip is visible in pytest output where a mock would look like a pass.

## Go-live readiness
- *(nothing yet)*

---

## Explicitly not built (see PROJECT.md "Explicitly deferred")

Self-modification, review queue, iMessage, vision/self-image/avatar, public
internet exposure, Working Theories, Interpretation Trace Runtime, Temporal
Runtime Headers (beyond elapsed-time statement), Web Source Runtime,
orchestrator/contradiction-detection agent.
