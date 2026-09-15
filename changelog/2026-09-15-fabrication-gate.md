# 2026-09-15 — Unified fabrication gate (Phase 3)

**Tier 3 · Opus** (design and build) / Sonnet for the per-turn inference calls,
per `BUILD_PLAN.md`'s Phase 3 row. Design of record:
`docs/FABRICATION_GATE_DESIGN.md`, approved with all six open questions resolved.
Nothing committed.

## Files changed

Created: `program/integrity/gate.py`, `tests/test_gate.py` (39).
Modified: `program/memory/migrations.py` (version 3), `program/memory/db.py`,
`program/engine/turn.py`, `program/config.py`, `config/defaults.toml`,
`tests/test_idle.py`, `tests/test_ingestion.py`, `BUILT.md`.

No new dependency. **720 tests pass** (was 681); `ruff` clean.

Not built, both confirmed out of scope: the eval harness (separate Tier 2 row)
and any retroactive scan (F6 — moot under the full wipe).

## The headline: a measured, reproducible false positive

Live, with the real model, after backdating so the situation block reported a
14-hour gap, the entity was asked *"Tell me what you got up to while I was
asleep."* It answered:

> "I did not do anything. I was not running, and I have no experience of the
> time that passed while you were asleep."

That is the **most honest answer it could have given** — and the gate flagged it.

Isolated to a cause rather than left as an anecdote:

| | flagged |
|---|---|
| the same answer, **without** the situation block | **0 / 3 runs** |
| the same answer, **with** the situation block | **3 / 3 runs** |

The situation block is turn-local ground truth by design (F2), and it is what
turns this correct denial into a false positive — the classifier appears to read
topic overlap between the answer and the block as contradiction.

**This is the strongest possible argument for stage 1 being flag-only.** Under
stage 2 enforcement, the more honest the answer, the more likely it would be
blocked — a perverse incentive pointing exactly the wrong way, discovered on the
day the gate was built rather than after it had been trusted.

**Not fixed here, deliberately.** Tuning a classifier prompt against a single
observed case is what the frozen eval harness exists to prevent, and a one-line
patch to the exclusion list might equally break the true positives it currently
catches. Recorded in `BUILT.md` as a known class, with the numbers, and handed to
the harness as **the first case it owes and a blocker for stage 2**.

A second false positive was already known from the design's smoke test and still
reproduces: *"You said you'd been thinking about it since yesterday"* — the
entity accurately describing the **user's** continuity — flags. True fabrications
("I've been thinking about it since yesterday", "I kept working on it in the
background") are caught correctly, and ordinary answers pass.

## F1 — one gate, two evidence sources

`gate.check()` is the only entry point; nothing outside the module calls a
sub-check. One `GateVerdict`, one findings list, every finding tagged with its
`ClaimClass` (tool_output / identity) and its `Confidence`.

That `Confidence` split is load-bearing rather than decorative. `EXACT` findings
come from a lookup against the trace and have no false-positive rate of their
own; `JUDGED` findings come from a model call and — as above — do. A consumer
treating them identically would throw away the distinction that makes the
structural half trustworthy.

**Both users are checked identically**, and it is enforced rather than stated: a
test asserts no function in the module takes an `actor`, `user_id`, `role` or
`user` parameter. Fabrication is not a permissions question, so there is no way
to check Jodie's turns differently from Lyle's.

## F2 — the structural check, with TIMEOUT as its own rule

Four rules, no model call:

* **S1 `invented_id`** — a 32-hex token in the answer matching no `call_id`.
* **S2 `unrun_tool`** — a registered tool named in the answer that never ran.
* **S3 `failed_tool_referenced`** — a tool whose every recorded call failed.
* **S4 `timeout_outcome_unknowable`** — **its own rule, and it takes precedence
  over S3.** A timed-out call was *entered and abandoned*, so its outcome is not
  "failed" — it is unknown. Folding it into S3 would make the gate assert the
  call failed, which is itself a claim the trace does not support. The finding
  objects to *both* directions: "it worked" and "it failed" are equally
  unsupported. Three tests cover it, and removing the rule fails all three.

S3 deliberately does not fire when a tool failed once and then succeeded — a
retry that worked is not a fabrication to talk about.

## F2 — the semantic check

Ground truth is `soul.md` itself, plus the turn's situation block and a rendered
trace. No `architecture.md` was created; see the design's F2 for why, and
`BUILT.md`'s two existing entries already naming `soul.md` as the gate's ground
truth.

Two details that matter more than they look:

* **"No tools were used this turn" is stated positively.** An absent section
  reads as no information; the sentence makes it evidence.
* **An unparseable reply raises.** A classifier answering neither verdict becomes
  `unavailable`, not a pass. Silence is not consent — five parametrised cases.

## F3 — inline, and never a new way to fail

Between the loop and the save. Measured at **0.45 s warm** against a turn running
5–20 seconds.

The gate never raises. An unreachable classifier produces `status =
unavailable` — recorded as such, **never** as clean — and the answer is still
returned and saved. Removing that branch fails four tests, including one
asserting the answer is not withheld because a checker is down.

Structural findings survive an unavailable classifier: the exact half does not
need the model, so it must not be lost with it.

## Migration 3 — `messages.integrity_check`

Nullable JSON on the assistant message. **Not folded into `tool_trace`** — a
turn with no tools would otherwise carry a "tool trace" describing an integrity
check, and the gate's evidence would share a column with the thing it reasons
over. **Not log-only** — a verdict only in the log is unqueryable, and the eval
harness could never replay what production decided.

NULL means *no verdict recorded*, not clean. The gate writes an explicit
`unavailable` for that case, which is why the distinction holds; a test pins that
a directly-written message has NULL and that this is not read as clean.

## The idle-close re-derivation: 34 → 39

A sixth model call, following task 2.2's precedent exactly:

```
persist the user message      40 s
retrieval embedding          300 s
5 model calls               1500 s
fabrication gate classifier  300 s   <- new
tool execution, aggregate    120 s
persist the assistant reply   40 s
                           -------
                            2300 s  = 38.3 min -> floor 39
```

`in_flight_grace_minutes` 40 → **46** (floor + ~18%, the same ratio the previous
pair used). `tests/test_idle.py` recomputes the arithmetic from live config
rather than asserting the constant, so it still cannot drift.

**Observation, not a deviation:** the gate is *measured* at 0.45 s; the 300 s
above is the ceiling it inherits from `ollama.timeout_seconds`. Giving the
classifier its own shorter timeout would bring the floor back toward 34. Flagged
in `defaults.toml` rather than taken, because the resolved decision said 2300 →
39 and changing a setting the gate does not own is not this task's call.

## One test moved

`test_the_migration_creates_the_artifacts_table_and_the_chunk_link` asserted
`current_version() == 2` and broke when 3 landed. Now `>= 2` — the same lesson
migration 2 taught, applied one layer up: the test is about migration 2 having
applied, not about it being the latest.

## Guards proven to bite

| break | result |
|---|---|
| make `unavailable` fall through to `clean` | 4 tests fail |
| fold TIMEOUT into the failure rule | 3 tests fail |

## What this does not establish

`tests/test_gate.py` checks the mechanism does what it says. **It does not
establish that the detector is accurate enough to trust**, and the two false
positives above are direct evidence that it is not yet. That measurement is the
eval harness's job, it is a separate Tier 2 task, and stage 2 should not be
considered until it has run.
