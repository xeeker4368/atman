# Media & Creative — Phase 4 design of record

Clusters A (image generation) and B (creative writing). The consolidated plan and
its fourteen questions were reviewed on 2026-09-21; every decision is recorded
here, with the reasoning, so none of it has to be reconstructed from a changelog.

Sequencing: **P0** (shared) → A1a → A1b → A1c → A2 → A3, with B0 → B4 → B5 in
parallel once P0 lands.

---

## Decisions

### Q1 — storage root: a type-to-root mapping in code, no migration

`artifacts.storage_path` is relative to a root, and until Phase 4 there was one
root so the column comment could name it. There are now two, because `config.py`
keeps them apart deliberately: `artifact_dir()` for files **people hand over**,
`workspace_dir()` for the entity's **own output** — *"so the go-live wipe and the
governance blocklist can treat them differently without unpicking one
directory."*

**Both new kinds go to `workspace/`.** The split is uploads-versus-output, not
text-versus-binary, which is why a generated image goes where the writing does.

Resolved by `program/artifacts/kinds.py` — a table, the same shape
`permissions.CAPABILITIES` and `store.SETTINGS` use, so the full set is greppable
from one place. **An unregistered type raises** rather than defaulting: a wrong
root writes bytes somewhere the backup, the wipe and the blocklist do not expect,
and a silent default is how that happens without an error.

### Q2 — attribution: a context that is deliberately not an actor

**(a) A per-turn parameter named `attribution`, carrying an `AttributionContext`.**
Not an `Actor`, and the name matters as much as the type. `Actor` answers *what is
this person allowed to do* and carries a `Role`; this answers *whose record is
this* and carries no role, no permission and no authorization meaning.

Several tests across the build assert that nothing in the tool path takes an
`actor`, `role` or `user` parameter, and they protect a real property: fabrication
checks, retrieval and corrections all treat both household members identically.
An authorization object in the tool path is how that would quietly stop being
true. Attribution and authorization answer different questions; mixing them would
make the second reachable wherever the first is needed.

**Three properties hold it in place, each tested:**

* It is **declared** per tool (`Tool.takes_attribution`), not inferred from the
  handler's signature — the contract is the thing written down, the same reason
  `parameters` is declared.
* It is **never model-settable.** `Tool.__post_init__` refuses a tool that puts
  `attribution` in its `parameters`. A model that could set it could attribute a
  write to the other household member.
* It is **kept out of the recorded arguments.** `ToolResult.arguments` is what
  reaches the tool trace the fabrication gate reasons over, so a value the model
  never sent must not change that shape.

A declaring tool dispatched with no context **raises** `ToolError` rather than
degrading. There is no honest value to substitute — the caller knows whose record
it is and the model does not — and it is a wiring bug of the same class as a
duplicate registration. `turn.py` always supplies it; a test asserts that from
inside the dispatch rather than by reading two lines.

**(b) In a live turn, the person present.** They asked for the thing, so the
record belongs in their history.

**(c) With no person present, the entity's own row** in the existing `users`
table. Two things about that row were **not** trivial, exactly as the review
anticipated:

* **`users.role` is `CHECK (role IN ('admin', 'user'))`**, and `create_user`
  validates the same list in Python. A third value would need the table
  recreated, and the decision was a real row with no schema change — so the row
  takes **`user`**, on least privilege. The role is not what keeps it from being
  an account: **`password_hash` stays `NULL`, and a NULL hash never
  authenticates.**
* **`users.name` is `NOT NULL UNIQUE`**, so the row needs a string — and
  CLAUDE.md says *"the entity has no name and must not be given one — not by
  code, prompt, config, or docs."* The row carries the sentinel **`__entity__`**,
  and **never renders**.

  The first choice was `"the system"`, on the grounds that it reads as a role
  description rather than a name. **A reachability trace, asked for at review,
  killed it** — and the path is recorded here because it is not obvious:

  * `chunking._format_line()` renders every user message as
    `f"{user_name}: {content}"`, where `user_name` is
    `db.get_user(conversation["user_id"])["name"]`. **A conversation owned by this
    row would bake its name into chunk text** — which is what reaches FTS5, the
    embedding vector and the retrieved-records block of the prompt. That is
    permanent: re-chunking reproduces it, and an embedding cannot be edited after
    the fact.
  * `turn.py` passes `actor.name` into the correction classifier's prompt
    (`corrections._render`) and into the situation block's speaker field. Both are
    model-facing, and `db.get_actor()` would build a valid `Actor` from this row.

  **Neither is reachable today** — the row owns no conversation and cannot produce
  an `Actor`, because it cannot log in. But a reflection journal or a research run
  owning its own conversation is one ordinary Phase 5 task away, and the chunk-text
  path is irreversible once taken. A sentinel costs nothing now and cannot be
  un-taken later. Underscore-delimited rather than `operator`-style bare, because
  `permissions.OPERATOR_NAME` is a word a person might legitimately be called and
  this must not be.

  Three tests pin it: the sentinel's shape, that a user name really does reach
  chunk text, and that this row really would produce an `Actor` carrying it — so if
  either path ever closes, the reasoning fails loudly rather than becoming received
  wisdom.

The row is created **lazily** on first use rather than in `init_databases()`, so
an empty store stays empty — every test run builds a store, and a user row
appearing in all of them would change what existing tests see. `UNIQUE` on `name`
makes it safe under two concurrent first writes.

**Residual, not closed here:** an operator could set a password on that row with
`scripts/set_password.py`, after which it could authenticate and every route behind
`require_actor` would accept it. Closing it needs an auth-side guard — refusing
credential operations and token issue for the reserved id — which falls under
`AGENTS.md`'s authentication checkpoint, so it is its own Tier 3 change.
**Tracked as a named item in `NOW.md`'s backlog**, not left in a changelog.

### Q3 — provisional provenance vocabulary

`creative_writing` and `generated_image` as both `artifact_type` and
`source_type`, `source_trust = firsthand`, registered in `kinds.py` in the same
provisional shape as `chunking.py`'s `conversation`/`firsthand` and `ingest.py`'s
`file`/`secondhand` pairs. **Task 1.7 owns this vocabulary and has not landed** —
`working.sql` names a `program/memory/provenance.py` that does not exist — so
these sit here for 1.7 to collect.

`firsthand` because the entity wrote it: its own output, not a report of someone
else's. **The fabrication gate's usual reading of that label does not apply here:
fiction is not a truth-claim**, so a story saying something untrue about the world
is not the gate's concern. Nothing reads the value in any case —
`test_source_trust_does_not_change_ranking` rewrites every chunk's trust and
asserts ranking is byte-identical — so this is a record, not a lever.

### Q4 — `enabled` only; no `approval_required` mechanism here

`approval_required` means *does this output need human review before it takes
effect*. A generated image has **no external effect** — it lands in the local
store — and `GUIDANCE.md` reserves those flags for *"fully unattended,
no-human-in-the-loop execution."* So the second axis here would gate nothing: the
**"gate mounted on nothing"** anti-pattern `ingest.py` already refused when it
declined to register an upload capability. The general two-axis mechanism is
deferred to Moltbook, where posting has a real external effect.

> **This reasoning assumes image generation stays live-turn-only.** If it is ever
> wired into an autonomous session, the argument above stops holding — unattended
> execution is precisely the case the flags exist for — and **this decision must
> be revisited rather than inherited silently.**

### Q5, Q7 — ANSWERED by measurement (2026-09-21): 4-step Lightning

A1b measured SDXL base at default settings taking **125–133 s with `gemma4:26b`
resident**, which exceeds `agent.tool_budget_seconds = 120` outright. Q7 was reopened
conditionally and the SDXL Lightning LoRA measured on the same checkpoint:
**4 steps — cold 30.5 s, warm 12.8 s, contended 51–58 s (48% of the turn budget)**;
8 steps — warm 23.1 s, contended 65–68 s.

**So no constant moves.** `agent.tool_budget_seconds` stays 120 and
`IN_FLIGHT_GRACE_FLOOR_MINUTES` stays 35. No async mechanism, no memory-unload step,
no step-count compromise on the base model.

Three things recorded because they are easy to lose:

* **The honest speedup is 2.3×, not 8.4×.** Warm-to-warm is 8.4×; the operative
  comparison is contended-to-contended, because the chat model has just answered.
* **The contention penalty is additive, not proportional**: +18–25 s at 20 steps,
  **+38–45 s at 4 steps**. With the chat model resident `vram_free` is 2.1 GiB and
  SDXL is paged back in per generation. **A faster sampler cannot shrink it** — it is
  memory, not compute — so this is the floor on any single generation while both
  models coexist.
* **Lightning requires euler / sgm_uniform / cfg 1.0.** Anything else measures a
  misconfigured sampler rather than the LoRA.

*Which step count ships is Lyle's call from the sample images, not a latency
question: both fit.*

### Q15 — `image_generate` exposes NO `negative_prompt`, and the revisit trigger is cfg

**DECIDED at review 2026-09-21.** At **cfg 1.0** the sampler skips the unconditional
branch, so there is no second pass for a negative prompt and **any value supplied
would be silently ignored.** A parameter that is accepted and does nothing is the
*"gate mounted on nothing"* shape one layer down from the one `ingest.py` refused when
it declined to register an upload capability — and worse here, because the model would
have no way to notice its instruction had no effect.

This is also half of why Lightning is fast: per-step cost falls from 5.25 s to ~3.2 s
precisely *because* that branch is skipped. The absent parameter and the speed are the
same fact.

> **The trigger is a cfg change, not a phase.** If a future checkpoint or
> configuration ever raises cfg above 1.0, the negative prompt becomes functional and
> exposing it should be reconsidered **at that point**. Until then it stays absent.
> Do not inherit this as "images never take a negative prompt" — inherit it as "cfg
> 1.0 means a negative prompt cannot work."

### Q6 — answered: no unload step

Neither model evicts the other. `gemma4:26b` stayed resident at 17 GB / 100% GPU
through a generation and answered in 5.4 s afterwards. **The cost is swap** — the swap
file grew 2 GB → 10 GB with 9.2 GB used — which is sustained SSD pressure rather than
a correctness problem, and the number to re-measure if a third resident model appears.

### Superseded: Q5, Q6 as originally left open until A1b measures

Whether generation fits inside a turn, and whether the chat model and an image
model can share 32 GB. **No async "check later" mechanism and no memory-unload
step is to be built speculatively** — only if measurement shows it is needed.
Recorded because `agent.tool_budget_seconds` feeds
`IN_FLIGHT_GRACE_FLOOR_MINUTES = 35`, so a change there moves a Tier 3 floor.

### Q7 — SDXL base

Ungated, fits the memory budget with real headroom alongside `gemma4:26b`, no
licence friction.

### Q8 — `ops/comfyui/`

README, pinned commit, frozen requirements file — the recreate record SearXNG's
compose file provides, in the form a native install allows.

### Q9 — "private" means not proactively announced

Retrieval stays unfiltered per decision #20; **no retrieval-time exclusion is
built.** Discretion applies at the point of surfacing, the same pattern as
cross-user disclosure — and with the same standing: a prompted tendency, not an
enforced boundary.

### Q10, Q11 — reduced scope for task 5; `workspace/` prerequisites stay in B0

Task 5 becomes a **provable property** — the tool runs with no conversation and no
live turn — rather than a wiring into a session mode that does not exist. Building
one would be R2's *"an unmounted gate is worse than an absent one."*

### Q12 — no additional gate on image prompts, as a considered decision

Not an implied analogy from decision #10: stated. The underlying model's own
safety behaviour is the only gate. Images generated here have no external effect,
nothing irreversible, and land in a local store on a household machine — the same
risk profile #10 reasoned about for creative writing. If image generation ever
acquires an external surface (Moltbook, a public share), this is revisited with
Q4.

### Q13 — text confirmation plus path and id, in the gate criteria

The entity has no vision (deferred, blocked on camera hardware) and there is no
frontend until Phase 8, so a successful generation returns a **text confirmation
with the artifact's path and id**, verifiable on disk. The full visual experience
is Phase 8's. **This belongs in A2/A3's gate criteria explicitly**, so "generate
one real image" is not read as "see one real image."

### Q14 — the prompt is what gets embedded

An image carries no text, so the **prompt** is the indexable text: it makes the
image findable by what was asked for. `extraction_status = metadata_only`, on the
no-text-layer-PDF precedent — a file that was never *read* must not look read.

---

## Built at P0 (2026-09-21)

`program/attribution.py`, `program/artifacts/kinds.py`, `Tool.takes_attribution`
and the dispatch parameter, threading through `loop.run_turn`, `turn.py` supplying
the person present, `db.entity_user_id()`, and `ingest.py` reading its root and
vocabulary from the registry rather than keeping its own copies.

**Still unbuilt:** every tool. P0 is the shared floor both clusters stand on; no
image is generated and nothing is written to `workspace/` yet.

### Proven by breaking it

| broken | what failed |
|---|---|
| attribution leaked into `supplied` | the trace-shape test and the arguments test |
| the `parameters` guard removed | the model-settable test |

### The regression the review required

`memory_search`, `web_search` and `web_fetch` are asserted to dispatch
identically with and without attribution — compared on the **envelope** (outcome,
recorded arguments, `ran`, `timeout_seconds`, trace key set), plus a registry-wide
check that no existing tool declares `takes_attribution` and no existing handler
could accept it even by accident.

The tools' own `value` and `error` are deliberately excluded from that comparison:
two live calls to a search engine legitimately return different results, and a
test that failed for that reason would train the next reader to ignore it.


---

## Built at B5 (2026-09-21) — Q10's reduced scope, delivered as a property

Decision #10 asks for creative writing in autonomous and background sessions. **No
session mode exists**, so B5 proves the path works with nobody present rather than
wiring into something that cannot happen.

**It already worked.** P0 put an `AttributionContext` rather than an `Actor` in the tool
path and built `AttributionContext.for_entity()` for exactly this case, so with no
users, no conversation, no turn and no actor the tool dispatches, stores, indexes and
retrieves. Nothing in `program/` changed; `tests/test_no_person_present.py` (9) is the
evidence.

**The load-bearing detail is a foreign key.** `artifacts.user_id` and `chunks.user_id`
both `REFERENCES users(id)` with `PRAGMA foreign_keys` on, so `for_entity()` creating
the entity row lazily is what makes the whole path possible — breaking it fails 6 of
the 9 tests. That was not obvious from the design and is worth carrying forward: any
future writer attributed to the entity depends on the same row existing.

**The absence is asserted.** A test fails if a `scheduler`, `reflection`, `autonomous`,
`session` or `daemon` module appears under `program/`, so "B5 built no mode" stays a
checked fact. When Phase 5 or 6 builds one, that test is what points at the change.

### What remains of decision #10

The `soul.md` entity-refusal clause — Tier 3 / Opus, its own thread, and the one part of
creative writing a tool cannot provide. Everything mechanical is built.
