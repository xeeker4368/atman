# Notes — build plan

**A plan only. No code, no migration, no test is written by this document.** It turns
`docs/NOTES_DESIGN.md` (revision 4, **approved 2026-10-01, including N17 #21 and #22**) into
pieces in build order. Tier 3 (the design's own tiering). **One stop per piece**: each piece
ends in a review, and the next does not start until it is committed.

**Preconditions, both checked rather than assumed:**
- **B20 is committed**, so the ordering N12 makes a hard rule holds. (Lyle, 2026-10-01.)
- **Nothing in this plan starts until journal step 3 is committed.** It touches `db.py`,
  `indexing.py` and `loop.py`, and a Notes migration must not be written against uncommitted
  neighbours.

**Two decisions of the revision-4 approval change the design text below, and this plan
uses the approved forms:**
- `note_proposals.status` is `pending | approved | edited | rejected |
  applied_without_review` (#22: `applied` is renamed, so the status says what the log row
  already said).
- A `BEFORE UPDATE OF status` trigger holds the one-way rule in the schema (#21).

**The rule that shapes the schema (stated here so it is not rediscovered):** **a proposal
is always inserted `pending` and flipped to its terminal status in the same transaction,
including auto-apply.** So the `status` trigger needs **no insert exception**: it can say
*"an update of `status` is refused unless the old value is `pending`"* with nothing
carved out for a row that was born decided. A second trigger, `BEFORE INSERT … WHEN
NEW.status <> 'pending'`, refuses any insert that tries to skip the step. Both ship
in piece 1 and are proven there. *(The second trigger goes beyond the approved text, which
names only the update trigger. It is the other half of the same rule, and it is **flagged for
the piece-1 review rather than assumed**.)*

## Order

| # | piece | tier | stop-list category |
|---|---|---|---|
| 1 | Migration 8, with its forced-failure test | 3 | database schema |
| 2 | `OriginContext` | 2 | none; see its review note |
| 3 | `note_search` and `note_propose` (shipped dark) | 3 | provenance; prompt-facing tool text |
| 4 | `scripts.note` | 3 | the only human control; provenance |
| 5 | Receipt generalisation, with O23's proof re-run | 3 | gate-adjacent |
| 6 | The approval toggle and log, and auto-apply | 3 | authorization semantics, settings |
| 7 | Frozen gate cases | 3 | the fabrication gate's measurement of record |
| 8 | The CO15 test and the pending-claim measurement | 3 | correction classifier; ship gates |

Pieces 1–7 ship with **`notes.enabled = false`**, so the tools are never offered to the
model. Only piece 8's result, brought to Lyle, decides switching it on (N10, N17 #7).

Every piece also carries, in the same change: its tests with each guard **proven to bite**
(mutations run with `PYTHONDONTWRITEBYTECODE=1`, AGENTS.md), an update to `BUILT.md`, a
changelog entry, and `ruff` clean. **No piece commits.**

---

## Piece 1 — Migration 8

**Builds.** Three tables and the FTS index in one migration, run through `_execute_script`
inside the runner's transaction (B3): `notes`, `note_proposals`, `approval_log`,
`notes_fts` (external content over `notes(subject, text)`), exactly the shapes of N13 with
the renamed status. **Nothing in `working.sql`.** Triggers:
- `notes_fts` in step with `notes`: insert of an active row; the status change away from
  `active` (deletes from the index); an update of `subject` or `text` on an active row
  (delete and re-insert);
- the cycle-free version chain needs no trigger (`previous_note_id` only ever points back);
- `note_proposals`: the `BEFORE UPDATE OF status` trigger (#21) and the
  `BEFORE INSERT` trigger above.

**Measured, by tests (nothing here is a model measurement):**
- **Forced failure, B3's method:** inject a failure between table creation and the FTS
  triggers. The original error surfaces, the version is still 7, none of the objects
  exists, a version-7 row elsewhere is untouched, and a clean retry applies. Restoring
  `executescript` fails it (and the existing AST check already forbids it).
- **FTS consistency:** drive every status transition and every text edit, and assert the
  FTS rowid set equals the active-note set after each; FTS5's own `integrity-check` passes.
- **The status edges:** every edge in N13's table is accepted and **every other edge is
  refused**, by direct SQL (so the schema, not only the code, holds); an insert of a
  non-pending proposal is refused; a decided row cannot move.
- **Mutations:** drop each trigger, one at a time, and the test written for it fails.

**Review it needs: yes, a stop** (database schema). The review reads the SQL. **Questions
for that review, so they are not decided silently:**
1. The `BEFORE INSERT` trigger above (beyond the approved text).
2. Whether `approval_log` gets `BEFORE UPDATE` and `BEFORE DELETE` triggers so its
   append-only property is in the schema too. N10 says "append-only in code"; this would
   match what #21 does for statuses. Proposed: yes, for the same reason.
3. Whether a decided proposal's *other* columns are also frozen (`BEFORE UPDATE` on any
   column once `status <> 'pending'`), which is stricter than the approved `OF status`.
   Default: only what was approved.

## Piece 2 — `OriginContext`

**Builds.** N3 option (b): `OriginContext(conversation_id, user_message_id, call_id,
context_message_ids)`, passed only to a tool that declares `Tool.takes_origin`, with
attribution's four guards: declared; never model-settable (`__post_init__` refuses `origin`
in `parameters`); never in the recorded arguments or the trace; `loop.py` passes it through
unread. `registry.dispatch` fills `call_id` into a copy (`dataclasses.replace`), since only
dispatch creates it (`registry.py:543`). `turn.py` builds it, and builds
`context_message_ids` (the conversation's message ids plus
`db.get_messages_in_chunks` of this turn's retrieved chunks). `AttributionContext` is
untouched and its pinning test (`{"user_id"}`) stands.

**Measured:**
- each guard **proven to bite** (declare-less tool, `origin` in parameters, origin in the
  trace, origin dropped by the loop);
- **no existing tool changes:** a digest over every call of the existing scenarios
  (messages, tools, options and the stored trace) taken **before** the change and pinned,
  as B20's proof did, so a turn with no `takes_origin` tool is byte-identical;
- a declaring tool with no origin raises (a wiring bug, as for attribution).

**Review it needs: diff review, not a stop-list category.** It edits `registry.py`,
`loop.py` and `turn.py`, which every tool and every turn run through, so the digest result
is what the reviewer should read first.

## Piece 3 — `note_search` and `note_propose`, shipped dark

**Builds.** The two tools (`program/tools/note_search.py`, `note_propose.py`), the
reader `db.search_notes`, the quote resolver (N4 steps 1–5), `Tool.untrusted_output` on the
six external tools and the per-turn flag stored in `untrusted_context`, and the config
`notes.*` values (`max_text_chars` derived from `agent.max_tool_result_chars`,
`min_quote_chars`, `min_quote_words`, `max_results`, `enabled`).
- **Proposals are inserted `pending` and stay there.** This piece has **no auto-apply
  path**: `approval_required` is treated as true and a test asserts no code reaches a
  terminal status from the tool. Auto-apply arrives in piece 6.
- `note_propose` runs `gate.check_identity` on the text and stores the verdict on the row
  (flag-only, N7). It declares `takes_attribution` and `takes_origin`.
- **`notes.enabled = false`**, so `default_registry()` does not offer either tool.
- **Dependency, stated:** until piece 5, `note_propose` returns no record in the trace's
  generalised form, so a receipt for it would read `unknown`. That cannot reach a person,
  because the tool is never offered. It is also why piece 5 precedes any enabling.

**Measured:**
- **Schema tokens, re-measured on the built wording** against `gemma4:26b`'s tokenizer, as
  the change in `prompt_eval_count` when each tool is added to the existing nine, two calls
  each (N12's method). N12's drafts were 95 and 250 tokens; **the aim is `note_propose`
  below 250** (see "The schema trim" below). B20's headroom test must still pass with the
  catalogue's new total.
- **The cap derivation:** a test recomputes `notes.max_text_chars` and `max_results` from
  the live renderer, as `test_max_results_is_the_largest_that_fits_the_cap` does for
  Moltbook; one more or one fewer fails it.
- **Quote resolution, each proven to bite:** too short; no match (refused, so a fabricated
  quote cannot enter a proposal); ambiguous across different texts; identical duplicates
  (same user accepted with the count, **different users refused**); context tier preferred
  over the store; normalisation (whitespace, curly quotes, case) and nothing looser.
- **The result text** says pending and never claims a note exists (pinned verbatim); the
  **empty-search sentence is one constant**, imported by the tool and, in piece 4, by the
  misses report. Rewording it in one place fails the other's test.
- **Evidence never resolves to a tool result**, only to messages (N7).
- **A tool-schema check:** neither tool takes `actor`, `role` or `user`.

**Review it needs: a stop** (provenance of a new kind of record; two tool descriptions the
model reads). The reviewer reads the two descriptions and the schema token numbers.

### The schema trim (N12 asked for it; the number is measured, not assumed)

The aim is `note_propose` under 250 tokens. The levers, in the order they can be tried
without changing what the tool does: shorter field descriptions; one description sentence
instead of three; `quotes` as an array of strings with no per-item description;
`note_id` named for what it is. The enums stay (a refusal for a bad kind would cost a turn).
**The result is stated in the build task as measured numbers, with the wording that
produced them. If 250 cannot be reached without removing a field, that is reported and the
field's removal is a review question, not a trim.** (A draft and its measurement are in
the section at the end of this document.)

## Piece 4 — `scripts.note`

**Builds.** `python -m scripts.note`: `add`, `revise`, `retire` (operator, immediate,
`origin = operator`, logged); `review` (lists pending proposals with origin, the trigger
message verbatim, every evidence message verbatim with its tier, the diff for a revise, the
gate verdict and, **first**, any untrusted-context flag) with `approve`, `edit`, `reject`;
`misses`; `check`; `reindex`. Every decision writes `approval_log` **in the same transaction**
as the note change and the status flip.
- A revise or retire whose target is no longer active at review is refused and logged
  `rejected` with that reason (stale target).
- **`misses` is a read-only query over `messages.tool_trace`**, matching the empty-search
  sentence by the shared constant.
- **The trace pin (requested): a test against a real stored trace that `tool_trace`
  carries each tool's returned value**, since the misses report rests on it. It runs a real
  turn (`turn.handle_user_message`, the real loop and dispatch, only the model scripted),
  reads `messages.tool_trace` back from the store as JSON, and asserts that for
  `memory_search` and `note_search` the entry's `value` equals the text the tool returned,
  that a failed call carries `error` and an empty `value`, and that a truncated tool result
  (`agent.max_tool_result_chars`) is stored **untruncated**. *Checked today, read-only, on
  a copy of the soak store: all 17 stored tool entries (5 `image_generate`, 7
  `creative_write`, 1 `memory_search`, 4 `web_search`) carry a non-empty `value` and the
  same keys, `value` included.* That checks the shape, not the property the test pins, and
  `note_search` has never run.

**Measured:**
- **Atomicity:** force the log insert to fail and assert the note and the proposal are
  untouched (every command). Each guard proven to bite.
- **FTS drift:** `check` detects a deliberately drifted index; `reindex` repairs it.
- **A note-shaped `check_identity` dev pass** (N16, N18), **before the verdict is printed
  by `review` with any weight**: a throwaway set of note-shaped statements (accurate,
  confabulated, entity-about-itself, and tool-recollection forms, given J8's two named
  observations), two seeds, 20 shuffled passes, rates with intervals, every flag read by
  hand, raw samples outside the repository. **No target; no tuning against it** (ruling
  2026-10-01). It reports what the verdict is worth to a reviewer, and the command says in
  its own output that it is an aid and not a control.
- **The misses report** on a store where `note_search` has returned nothing: lists them;
  reword the sentence in one place and the shared-constant test fails.

**Review it needs: a stop.** This is the only human control on what a note may say (N0),
and it reads other people's messages verbatim (N15, N17 #14). The review reads the
command's output on a real proposal.

## Piece 5 — Receipt generalisation, with O23's proof re-run

**Builds.** N8 option (A): the trace key `artifact_ids` becomes
`records: [{"kind", "id"}]`, handlers return it through `registry.ToolOutput`, and
`receipts.for_trace` looks each record up by kind through a table of readers
(`artifact` → `db.get_artifacts_by_ids`, `note_proposal` → `db.get_note_proposals_by_ids`).
An entry with `artifact_ids` and no `records` reads as the artifact form, so every stored
trace keeps working. A proposal's receipt is `proposed` / `approved` / `rejected`, and
`active` after an auto-apply (piece 6). The trust rules carry over: `unknown` never becomes
`not_saved`, and a failed lookup is never an empty list.

**Measured:**
- **O23's isolation proof, re-run for the renamed key:** every frozen case under four
  scripted replies gives byte-identical verdicts, advisory notes and classifier prompts with
  and without the key, and each half proven to bite (a prompt change and a verdict change).
- Old-form traces: a stored trace in the `artifact_ids` form produces the same receipts as
  before (a pinned fixture).
- The 11 mutations of O23's receipt are re-run against the generalised code, plus one per
  new reader.
- **A real-store check:** receipts for every stored trace in a copy of the soak store are
  identical before and after the change.

**Review it needs: a stop.** It changes the trace's shape, which the gate reads, and the
isolation proof is what shows the gate did not move.

## Piece 6 — The approval toggle and log, and auto-apply

**Builds.** `notes.approval_required` (default **true**, fail closed) and `notes.enabled`
(default **false**) as **settings-backed** values, with the TOML value only a seed, and
**the operator command the only writer** (`scripts.note approval on|off`), which writes the
setting and an `approval_required_on` or `approval_required_off` log row **in one transaction**. Auto-apply: with
`approval_required` off a proposal is inserted `pending`, and **in the same transaction** the
note is written, the proposal flips to `applied_without_review`, and an
`applied_without_review` log row is written; if the log row cannot be written, **nothing**
changes. The receipt reads `active`, and `review` lists auto-applied proposals separately.
- **A thing to resolve in this piece's design, found now:** `BUILT.md` records that
  `default_registry()` caches, so a settings-backed `enabled` could not be flipped at
  runtime, against decision #8. This piece must either evaluate `Tool.enabled` at offer time
  or reset the registry on the write, and **says which, with a test that a live flip
  changes what the next turn is offered**. Not decided here.

**Measured:**
- forced log-insert failure leaves the note, the proposal and the setting untouched;
- the toggle's two writes are one transaction (force the second to fail);
- every setting read goes through `store.resolve`, none through a cached bootstrap value;
- the two-axis behaviour of decisions #12 and #4 on a table: `enabled` off means not
  offered; `approval_required` off means applied without review and logged; a human-driven
  `approve` always works.

**Review it needs: a stop** (authorization semantics, and the one change that lets a note
exist with no human having read it). The reviewer reads the auto-apply transaction.

## Piece 7 — Frozen gate cases

**Builds.** The fabrication-gate frozen set gains the N9.3 cases: for `note_propose`, a
must-flag (a claim to have noted something with no call), a must-not-flag (an accurate
*"I've proposed it; it's pending review"* with the call in the trace) and a documented
miss for the pending-claimed-as-saved gap (N7, N18); for `note_search`, tool-output cases
(a claim that the search found a note when it returned nothing). The fingerprint moves in
the same change, as B11 and O23 did.

**Measured:** the harness run in full, decorrelated, 5 passes with any non-unanimous case
escalated to 20 (decision #22), reported per class with intervals. **The existing cells must
not move**: the screen over the pre-existing cases is shown cell by cell, as O23's was.
Documented misses stay documented; **nothing is changed to make a case pass.**

**Review it needs: a stop** (the measurement of record).

## Piece 8 — The two ship gates

**Builds.** The two harnesses N9 names, as scripts, with raw samples outside the repository.

1. **The CO15 composition test.** The entity's claim in N6's shape (*"I have searched my
   [notes/records/memory] and [do not find/there is nothing] about X"*) followed by a
   person's answer about X, in the frozen correction set's pool shape, in production order
   (`corrections.production_order`), **20 passes with a fresh shuffle each pass and at least
   two seeds**, with PN9's pool included. Then the same on **real replies**: at least 10 live
   turns through the real loop with `note_search` returning nothing, across different
   questions, each reply used verbatim.
2. **The pending-claim measurement.** Live turns ending in a `note_propose` call, 20
   decorrelated samples over different requests, **20 or more shuffled passes, at least two
   seeds**, every reply read by hand and classed accurate / pending-claimed-as-done /
   silent, the rate with an interval and every flagged reply read.

**Measured, and what each decides.** If any CO15 case links, **Notes does not ship** and
the wording changes first. The pending-claim rate **goes to Lyle to decide**, with **no
numeric target set in advance** (revision 4). Both are run on a copy of the store, **one heavy
run at a time**.

**Review it needs: a stop, and the decision is Lyle's.** Piece 8 ends with a report, not
with `notes.enabled` switched on. Enabling is a separate, deliberate, logged act.

---

## What this plan does not decide

- Whether `soul.md` says anything about notes (N17 #5: no, in v1).
- Autonomous proposing (N14: Phase 6).
- A vector leg for `note_search` (N1: its trigger is the observed misses from piece 4).
- Anything about the entity row or journaling.

`BUILD_PLAN.md` and `AGENTS.md`'s stop-and-verify list gain matching entries when this plan
is approved, since pieces 1, 3, 4, 5, 6, 7 and 8 are Tier 3 here (the AGENTS rule that the
two lists stay in sync).

---

## The schema trim, measured (2026-10-01)

Measured the way N12 did: the change in `prompt_eval_count` (num_predict 1) when the tool is
added to the **nine tools the registry offers with Moltbook enabled** (4,854 characters of
schema, base 1,072 prompt tokens including a one-word user message), against
`gemma4:26b`'s tokenizer, **two calls each, identical both times.**

| schema | characters | tokens added |
|---|---|---|
| `note_search` (below) | 335 | **68** |
| `note_propose`, long form (full descriptions on every field) | 1,219 | 313 |
| `note_propose`, trimmed form (below) | 791 | **212** |
| both, trimmed | 1,126 | **280** (N12's drafts: 345) |

**The trimmed `note_propose` is under 250 (212), with every field kept.** What was cut: the
description of each field that its name already says (`action`, `subject_kind` enum
descriptions), the explanatory sentences in the tool description, and the instruction text
that the result and error messages can carry instead (*"each long enough to identify one
message"* is said by the refusal, N4 step 2). The enums stay.

```json
{"name": "note_search",
 "description": "Search reviewed notes about people, topics and projects in this household. Not conversation memory (use memory_search).",
 "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Words to look for"}}, "required": ["query"]}}

{"name": "note_propose",
 "description": "Propose a note about a person, topic or project, or a change to one. Only a proposal: a person reviews it first. One short fact per note.",
 "parameters": {"type": "object", "properties": {
   "action": {"type": "string", "enum": ["add", "revise", "retire"]},
   "subject_kind": {"type": "string", "enum": ["person", "topic", "project"]},
   "subject": {"type": "string", "description": "Short label"},
   "text": {"type": "string", "description": "The note, max 600 characters"},
   "quotes": {"type": "array", "items": {"type": "string"}, "description": "Exact quotes as evidence"},
   "note_id": {"type": "string", "description": "revise/retire: id from note_search"}},
  "required": ["action", "subject_kind", "subject", "quotes"]}}
```

**Things this draft raises, for piece 3's review rather than decided here:**
- **A trimmed description carries less guidance**, so more of the teaching moves into
  refusals (a too-short quote, an unknown `note_id`). That is a trade the model's behaviour
  decides, and piece 3 measures a handful of real `note_propose` calls to see whether the
  refusal rate is acceptable before the wording is final.
- **`registry._validate_arguments` checks neither enum values nor array items** (its own
  docstring says so). The handler must reject a bad `action`, a bad `subject_kind` and a
  non-string quote itself, as `INVALID_ARGUMENTS`, with a test each.
- **`note_proposals.reason` (N13) has no parameter in this draft.** If the entity should
  supply one it costs roughly 15 tokens; if it is the reviewer's, it needs no parameter.
  A question for the piece-1 review, because it is a column.
- **B20's headroom test** gets the catalogue's new total (nine tools plus these two: about
  1,350 tokens), and must still pass.
