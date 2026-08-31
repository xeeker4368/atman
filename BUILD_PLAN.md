# BUILD_PLAN.md

The master build sequence. Every decision here traces back to `NOW.md`'s
decision log — this document turns those decisions into ordered,
CC-sized work. Read `PROJECT.md`, `AGENTS.md`, and `GUIDANCE.md` before
starting any phase; this document assumes their content and doesn't
repeat it.

**How to use this:** work phases in order — later phases depend on earlier
ones' actual interfaces existing, not just their specs. Within a phase,
tasks can often be parallelized if noted. Every task lists a **Tier**
(execution autonomy, see below), a **Model** default, and whether it's a
**Checkpoint** (stop, Lyle reviews, before continuing).

**Tiers**, for reference:
- **Tier 0** — fully autonomous, whole-milestone runs, no review until done.
- **Tier 1** — autonomous execution, reviewed in batches of a few tasks.
- **Tier 2** — spec must be Lyle-approved before CC builds it (design already
  decided in `NOW.md`, so this mostly means "confirm before implementing,"
  not "design from scratch").
- **Tier 3** — hard gate. One task, plan-check loop fully intact, no
  chaining, no exceptions. Matches `AGENTS.md`'s stop-and-verify list.

---

## Phase 0 — Scaffolding

Goal: an empty-but-runnable skeleton. No memory, no model calls that matter
yet — just the shape of the app.

| Task | Tier | Model |
|---|---|---|
| Write `.gitignore` **before** running `git init` — must cover `__pycache__/`, `.pytest_cache/`, `.DS_Store`, and `reference/` (per the reference-folder rule in `AGENTS.md`). This has to exist before the first commit, not added afterward, or unwanted files already have history to clean up. | 0 | Sonnet |
| `git init`, first commit — includes `.gitignore` itself; scaffold otherwise empty is fine | 0 | Sonnet |
| Repo structure — package named `anam/` (the project's own codename, same role `tir/` played in the reference build; does not conflict with the entity staying unnamed — this names the package, not the entity), plus `config/`, `tests/`, `workspace/` | 0 | Sonnet |
| Layered config: `defaults.toml` → `local.toml` → env override, matching the pattern already proven in `reference/old-anam/` | 0 | Sonnet |
| `requirements.txt`, FastAPI app skeleton, `run_server.py`, health-check endpoint | 0 | Sonnet |
| `start.sh` (local + `--lan` flags) | 0 | Sonnet |

**Gate:** server starts, responds to a health check, config loads correctly
from all three layers with env override proven by a test.

---

## Phase 1 — Core Substrate

Goal: the memory system exists and is queryable. This is the highest-risk
phase in the whole build — most of its tasks are Tier 2/3.

| Task | Tier | Model |
|---|---|---|
| SQLite archive (durable) + working (operational) DB schema | **3** | **Opus** |
| Ollama integration: chat + embedding calls, `num_ctx` pinned (confirm value against actual model in use — verify live via `ollama ps`, don't assume) | 1 | Sonnet |
| Chunking + checkpointing pipeline, embedding-dimension guard, sub-chunk splitting for over-length input (known failure mode in `reference/old-anam` — design it out from the start, don't reproduce it) | **3** | **Opus** |
| ChromaDB integration + FTS5 setup | 1 | Sonnet |
| Hybrid retrieval: BM25 + vector, RRF fusion, relevance floor on both legs (a bare "top-K regardless of match quality" retrieval is a known bug class — build the floor in from day one) | **3** | **Opus** |
| Degenerate-query handling: when the lexical query collapses to ≤1 meaningful term AND the vector leg contributes zero chunks post-floor, treat as no reliable match rather than returning weak hits | 2 | Sonnet |
| Provenance/source metadata on every chunk (`source_type`, `source_trust`) — **metadata only, confirm it is never wired into ranking** unless a future decision explicitly changes that | **3** | **Opus** |
| `soul.md` seed + prompt assembly (`build_system_prompt`) | **3** | **Opus** |
| Current-situation block: current timestamp (existing good pattern) + elapsed-time-since-last-message (new, decision #5) + the confabulation-prevention pairing text in `soul.md` (decision #5, `GUIDANCE.md`) | 2 | Sonnet |
| History windowing: token-budget cutoff for in-context history (decision #6). **Token estimate: character-based (~4 chars/token), no tokenizer dependency.** Margin direction matters more than size: erring high (assuming fewer tokens than reality) under-fills the window — the omitted history stays retrievable via `memory_search`, a non-event. Erring low (assuming more tokens fit than actually do) means the model server silently drops the overflow, losing the oldest content with nothing raised — the worse failure mode. Bias the estimate toward erring high. This is the same ratio problem the embedding-input truncation work hits (dense content runs nearer 3 chars/token than 4) — document both margins together and keep them consistent rather than letting them diverge by accident. | 1 | Sonnet |
| Settings persistence: DB-backed settings table + in-memory cache, invalidated on write (decision #8) | 1 | Sonnet |
| Multi-user schema + per-user attribution, admin vs. household role gating (loopback check) | 2 | Sonnet |
| Backup CLI | 0 | Sonnet |
| Restore CLI (atomic, verified) | **3** | Sonnet |
| Build a small seeded dataset (a handful of varied conversation/chunk
  records) for the Phase 1 checkpoint below to actually run queries against | 1 | Sonnet |

**Notes:**
- **Config ownership:** TOML/env provide bootstrap defaults only, read the
  first time the system runs before the settings table (task: "Settings
  persistence") has a row for a given key. Once the admin panel writes a
  value, the DB table is authoritative at runtime — no task should read
  from both at request time.
- **Retrieval floor constants:** do not copy the reference build's specific
  numbers (0.40 distance floor, -2.5 BM25 threshold, 50-chunk minimum).
  Those were measurements against that corpus, not universal values. Build
  the floor as a configurable mechanism; leave actual thresholds permissive/
  unset until there's real data here to calibrate against. **Known
  consequence, accepted deliberately:** with the vector floor permissive,
  the degenerate-query rule (task 1.6) structurally cannot fire yet — there's
  nothing for it to reject. The task 1.15 checkpoint report must state this
  explicitly ("ranking validated; floor-rejection and degenerate-query
  behavior not yet exercised — floors are intentionally uncalibrated
  pending real usage data") rather than imply rejection behavior was
  verified when it wasn't.
- **Governance-file blocklist, generalized:** match by resolved directory,
  not an enumerated filename list — a filename list silently fails to
  cover any governance file added in a later phase (this already bit task
  2.8 once, when `anam/integrity/architecture.md` arrived in Phase 3 after
  the blocklist shipped in Phase 2). Put `soul.md` under the same
  directory-based rule if it isn't already, so this doesn't recur for
  whatever governance file gets added next.
- **Direct reference access, exception to the reference-only rule:** for
  the schema task and the hybrid retrieval task specifically, consult
  `reference/old-anam/`'s actual implementation directly (not just when a
  task text points at it) as a cross-check — these are the two
  highest-risk tasks in the build. Everywhere else in this plan, the
  standard reference-only rule in `AGENTS.md` still applies.

**Checkpoint before proceeding to Phase 2:** Lyle reviews schema, retrieval
scoring approach, and `soul.md` content directly. Run a handful of real
queries against the seeded dataset above and confirm retrieval **ranks**
sensibly. Floor-rejection and degenerate-query behavior are **not** in
scope for this checkpoint — floors are intentionally uncalibrated (see
notes above) and cannot be exercised yet. Don't let this checkpoint imply
more was verified than actually was.

---

## Phase 2 — Tools

Goal: the entity can act, not just talk.

| Task | Tier | Model |
|---|---|---|
| Tool registry + dispatch framework | 1 | Sonnet |
| **Agent loop**: the actual iterate-and-dispatch turn — call model, check
  for tool calls, dispatch, feed results back, repeat until a terminal
  response or iteration limit. Nothing in Phase 0/1 builds this; the
  registry alone doesn't run a conversation turn. Mechanical wiring of a
  well-understood pattern, not a novel design decision. **The turn's tool-call
  trace must be a first-class return value the fabrication gate reasons
  over structurally — not debug/log output.** This is what makes
  structural tool-output fabrication detection possible (checking a claim
  against what actually happened in the trace) rather than the reference
  build's weaker pattern-matching-on-prose approach. | 1 | Sonnet |
| `memory_search` tool | 1 | Sonnet |
| Stand up local SearXNG instance; `web_search` tool against it | 1 | Sonnet |
| `web_fetch` tool (public HTTP/HTTPS only, no localhost/private network access) | 1 | Sonnet |
| **Live validation that SearXNG actually returns real results** — this was never confirmed working in `reference/old-anam`; don't inherit that gap silently | 1 | Sonnet |
| File/artifact ingestion: upload endpoint, text + PDF content extraction (decision #11), other types metadata-only | 1 | Sonnet |
| Governance-file ingestion blocklist (`soul.md`, project docs can't be ingested as normal memory) | 1 | Sonnet |

**Gate:** each tool independently tested with a real call; `web_search`
confirmed against a live SearXNG response, not just a mocked test.

---

## Phase 3 — Memory Integrity

Goal: the system can catch and handle its own errors about itself.

| Task | Tier | Model |
|---|---|---|
| Unified fabrication gate — covers tool-output fabrication (structural: invalid IDs, no matching tool_result in trace — checkable directly) and identity-claim fabrication (semantic: no structural marker exists, since "I've been thinking about that since yesterday" is ordinary English carrying a false continuity claim). Identity-claim detection must be **model-judged**, not keyword/marker-based — a classifier call comparing a candidate statement against a ground-truth description of the system's actual architecture (stateless per call, no persistent working memory, no self-training, memory only via retrieval) and flagging contradictions. This can share one classifier framework with the correction/supersession classifier below (two prompt variants of one mechanism), keeping the "unified detector" framing intact at the mechanism level, not just the tool-output level. (decision #1) | **3** (design) / Sonnet (runtime calls) | **Opus** to design and build; **Sonnet** for the classifier's actual per-turn inference calls |
| Fabrication-gate eval harness — frozen test cases, covering both fabrication classes | 2 | Sonnet |
| Correction/supersession classifier: model-judged detection of "this message corrects that prior claim" (decision #2) | **3** (design) / Sonnet (runtime calls) | **Opus** to design and build; **Sonnet** for runtime inference calls |
| Correction/supersession eval harness — frozen real-correction and near-miss cases | 2 | Sonnet |
| Retrieval updated to respect `supersedes` links | **3** | Sonnet |

**Checkpoint:** review both eval harnesses' actual pass/fail behavior before
trusting either mechanism live. This is explicitly the same discipline
`reference/old-anam` used for its fabrication gate — don't skip the
measurement step just because the design is decided.

---

## Phase 4 — Media & Creative

| Task | Tier | Model |
|---|---|---|
| ComfyUI backend integration | 1 | Sonnet |
| `image_generate` tool, config-gated | 1 | Sonnet |
| Generated-image artifact storage + metadata indexing | 1 | Sonnet |
| Creative writing tool: writes to `workspace/`, registers as `artifact_type: creative_writing`, indexed with its own `source_type`, no gate (decision #10) | 1 | Sonnet |
| Wire creative-writing availability into autonomous/background session mode, not just live conversation (decision #10) | 1 | Sonnet |
| Entity-refusal support: `soul.md` language establishing declining to share creative work is legitimate (decision #10, `GUIDANCE.md`). This is the second of three `soul.md` touches across the build (Phase 1, 4, 10) — falls under AGENTS.md's "soul.md content and prompt assembly" checkpoint category, same as the other two. **The general discretion principle (entity may decline; personality observed, not assigned) is written in Phase 1's `soul.md` task; this task adds only the creative-work-specific clause on top of it, scoped to the feature that now exists.** | **3** | **Opus** |

**Gate:** generate one real image, write one real piece of creative content
in a live conversation and one in a simulated autonomous session; confirm
both index correctly and the entity can decline a share request in a live
test.

---

## PAUSE — Account usage check-in

Not a technical gate — a deliberate stop. Check `/status` and take stock of
where usage stands before committing to the remaining phases (Research,
Scheduling, Eval, Moltbook, UI, Go-Live). Decide pacing for the rest of the
build from here rather than assuming the plan continues in one sitting.

---

## Phase 5 — Research & Reflection

| Task | Tier | Model |
|---|---|---|
| Bounded research execution (manual-trigger path) | 2 | Sonnet |
| Entity self-flag tool: creates a research candidate tied to `source_conversation_id`/`source_message_id` (decision #3) | 1 | Sonnet |
| Periodic mining job: scans recent conversations **across all users, including Jodie's** (decision #3, #17), proposes candidates | 1 | Sonnet |
| Propose-vs-execute authorization: proposing never gates; executing checks `allow_*` **unless** a human is directly driving the action (decision #4) | **2** | Sonnet |
| Reflection journal (daily cycle) | 1 | Sonnet |
| Source trace collection + ingestion blocklist for trace files | 1 | Sonnet |

**Gate:** run one real manual research task end-to-end; confirm a
self-flagged and a mined candidate both land in the same review surface
correctly, with correct source attribution.

---

## Phase 6 — Scheduling

| Task | Tier | Model |
|---|---|---|
| Nightly tick / scheduler core | 1 | Sonnet |
| launchd automation for the scheduler | 0 | Sonnet |
| Confirm `allow_*` flags gate exactly the unattended path, nothing else (decision #4) | 2 | Sonnet |

**Gate:** scheduler fires on schedule in a test window; confirm it respects
flags correctly and a manual override still works independent of them.

---

## Phase 7 — Eval & Observability

| Task | Tier | Model |
|---|---|---|
| Retrieval eval harness (frozen regression cases) | 0 | Sonnet |
| Behavioral probe harness | 0 | Sonnet |
| Raw-gemma control arm (same probe, no memory, for comparison) | 1 | Sonnet |
| Backend test suite coverage pass | 0 | Sonnet |

Can run in parallel with Phases 4–6 once Phase 1–3 interfaces are stable.

---

## Phase 8 — Moltbook

*(Moved ahead of UI — the UI phase needs Moltbook's backend capability to
already exist so it has something real to build a toggle for.)*

| Task | Tier | Model |
|---|---|---|
| Read-only Moltbook tools (feed, search, profile, posts-by-author, etc.) | 1 | Sonnet |
| Posting capability behind the enabled/approval-required toggle (decision #12) | 2 | Sonnet |
| Rate limit enforcement (posts/day hard ceiling, decision #12) | 1 | Sonnet |

**Gate:** confirm read-only path works live; confirm posting stays fully
inert until you explicitly enable it — verify by checking the flag state
directly, not just by trusting the UI.

---

## Phase 9 — Frontend / UI

| Task | Tier | Model |
|---|---|---|
| React chat interface: single coordinated state machine (reducer-based), streaming responses, tool-call/artifact rendering — explicitly avoid the competing-poller pattern from `reference/old-anam` (decision #7) | 1 | Sonnet |
| Server-rendered admin settings panel: capability toggles, model selection, temperature, API keys (decision #7) | 1 | Sonnet |
| Save-button UX: appears on change, commits + goes live immediately (decision #9) | 1 | Sonnet |
| Check/Verify button framework: auto-rendered for any setting with a registered verification function (decision #9) | 1 | Sonnet |
| Wire verification functions for each external-connection setting (SearXNG URL, ComfyUI, any API keys) | 1 | Sonnet |
| Moltbook capability UI: enabled/approval-required toggles + rate-limit field, wired against the real backend built in Phase 8 (decision #12) | 1 | Sonnet |
| Mobile/responsive pass | 1 | Sonnet |

**Gate:** full device test on your actual hardware — desktop and mobile —
before calling this phase done. This is exactly the category of thing only
a human can actually verify by looking at it.

---

## Phase 10 — Go-Live Readiness

*(Deliberately sequenced after UI, per your instruction — go-live isn't
meaningful until the full system, UI included, is actually usable.)*

| Task | Tier | Model |
|---|---|---|
| Go-live reset command | **3** | Sonnet |
| Final model temperature | 2 | Sonnet |
| `soul.md` final wording pass | **3** | **Opus** |
| Full database wipe execution (decision #16 — no partial-preservation exception) | **3** | Sonnet |
| Final launch config/profile | 1 | Sonnet |
| Run all eval/probe harnesses against the final pre-wipe build | 0 | Sonnet |

**Checkpoint:** this is the point of no return for this build's test data.
Confirm explicitly with Lyle before executing the wipe.

---

## Deferred — not in this plan

Self-modification + review queue, iMessage (all stages), vision/self-image/
avatar (blocked on camera hardware — revisit `BUILT.md` and this plan once
acquired), Working Theories, Interpretation Trace Runtime, Temporal Runtime
Headers beyond the elapsed-time statement, Web Source Runtime,
orchestrator/contradiction-detection agent, public internet exposure.

When any of these get picked up, they get their own planning pass — don't
retrofit them into this document.
