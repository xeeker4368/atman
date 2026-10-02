# Notes — design (revision 4, APPROVED 2026-10-01, including N17 #21 and #22)

**Design only. No code, no migration.** Tier 3 on several counts: a schema
(migration 8), provenance semantics (a new kind of record the entity proposes about
people), prompt-facing text (two tool descriptions), a gate use, and a settings
change that must be audited.

The brief (review, 2026-09-30) replaces all earlier Notes messages. Its fixed
decisions are marked **FIXED**. Everything else is a proposal, and every decision
this document asks for is collected in N17. Known gaps are collected in N18.

---

## N0 — What a note is, and what it is not

A note is a short, reviewed statement about a **person**, a **topic** or a
**project** in this household. For example: *"Jodie takes her coffee with oat
milk"*, or *"The backup should eventually run on a schedule; not decided where it
lives"*. The entity proposes it; a human approves it; it is recalled on demand.

**FIXED by prior decisions:**

- **Separate from self-observation.** A note is never about the entity. See N2's
  scope rule.
- **The entity proposes, a human approves.**
- **Recalled on demand, never injected every turn.** There is no passive
  retrieval of notes; the only way in is `note_search`.
- **A per-capability approval toggle**, defaulting to *required*, and **every
  decision logged from day one** (N10).
- **No warning step.** Nobody is warned before a proposal is made.
- **Notes add and retire over time.**
- **No note categories are off-limits.** Recorded as a decision, not an omission:
  **reviewer approval is the only control** on what a note may say. There is no
  topic blocklist, no sensitivity classifier and no category filter. A proposal
  about anything reaches the reviewer, and the reviewer decides.

Notes are **not** conversation memory. They never appear in `memory_search`, and
nothing from `memory_search` appears in `note_search`.

## N1 — Storage: rows with their own search — FIXED shape, details proposed

Notes live in **their own tables in `working.db`**, not as `chunks` and not as
`artifacts`:

- a note must be **revised and retired**. Chunks are derived and rebuildable, and
  artifacts are files;
- **J10 showed** that artifact chunks cannot be reached by supersession, so a
  corrected note stored that way would keep surfacing uncorrected.

**Search is lexical only, FTS5/BM25, in v1** (proposed): `notes_fts` indexes the
**active** notes' subject and text. Notes are short and subject-keyed, so a word
match is the natural query.

- **Gap:** a paraphrase query can miss (N18).
- A vector leg would need its own Chroma collection, its own reconciliation and
  its own place in backup. Not built speculatively; the trigger is observed
  misses.

## N2 — A note's fields — FIXED, with proposed details

| field | notes |
|---|---|
| `subject_kind` | `person` \| `topic` \| `project`. **There is no `self` kind.** |
| `subject` | a short label: *"Jodie"*, *"the backup"*, *"Jodie's grandmother's notebook"* |
| `subject_user_id` | optional, for a household member. NULL for anyone outside the household. |
| `text` | capped (N11) |
| `status` | `active` \| `superseded` \| `retired` |
| version chain | `version` (1, 2, …) and `previous_note_id`. A revision is a new row; the old row becomes `superseded`, keeping its text. |
| `origin` | `entity` \| `operator` |
| evidence | message ids. **Required for entity proposals, optional for operator notes** (N4). |
| `last_confirmed_at` | when this version was last approved or entered |

**Scope excludes the entity's own nature.** What enforces it: there is no `self`
kind; `check_identity` runs on every proposal (N7), so a proposal making claims
about the entity is flagged to the reviewer; and the reviewer. **Not** a classifier
refusing the proposal: *"reviewer approval is the only control"* applies here too.

## N3 — Origin link: every proposal records where it came from

Every proposal, including each revise and each retire, stores:

- `conversation_id`;
- `user_message_id`, the message that triggered the turn;
- `call_id`, if feasible.

**Can a handler see its `call_id`? Checked: not today, but feasibly.**
`registry.dispatch()` creates `call_id = uuid.uuid4().hex` **before** it calls the
handler (`program/tools/registry.py:543`) and never passes it in. So dispatch *can*
supply it to a tool that declares it, the way it supplies attribution. `turn.py`
already holds `conversation_id`, and `user_message_id` (the message is saved before
generation, `turn.py:402`), at the point it builds attribution.

**Options:**

- **(a)** Add the fields to `AttributionContext`. That edits the test pinning its
  fields to exactly `{"user_id"}` (`tests/test_attribution.py:40`), and it widens a
  type every writing tool receives to carry data only one tool needs.
- **(b) — recommended, and Lyle's lean.** A separate **`OriginContext`**
  (`conversation_id`, `user_message_id`, `call_id`, and since revision 2
  `context_message_ids` for quote resolution, N4), passed only to a tool that
  declares `takes_origin`, with the **same four guards** attribution has:
  1. **declared**, not inferred (`Tool.takes_origin`);
  2. **never model-settable**: `Tool.__post_init__` refuses `origin` in
     `parameters`;
  3. **never in the recorded arguments or the trace**;
  4. `loop.py` **passes it through unread**, as it does attribution.
  A declaring tool with no origin raises `ToolError`: a wiring bug, as for
  attribution. `turn.py` builds it; **`dispatch` fills `call_id`** into a copy
  (`dataclasses.replace`), since only dispatch knows it. `AttributionContext` is
  untouched and its pinning test stands.
- **(c)** Store the call id alone and join to the trace later. The weakest: the
  conversation and message have to be recovered by searching traces, and a
  trace-less path (an operator note) has nothing to join to.

**Recommendation: (b).** It keeps the attribution type's meaning ("whose record")
separate from provenance ("which exchange produced this"). The call id joins the
proposal to the exact trace entry, which the receipt (N8) and the gate's ACTION
rule both read.

## N4 — Proposing, and the operator's commands

**`note_propose(action=add | revise | retire, …)`** writes a **proposal row**, never
a note. **Its result text says the proposal is pending and never claims a note
exists**: *"Proposed. A person will review it before anything changes. No note
exists yet because of this."* For a revise or retire: *"… the note stays as it is
until then."*

**Evidence: the model cannot give message ids**, because it never sees them. They
are not in the history it is sent or in the rendered records. So evidence is taken
as **short exact quotes**, and the handler **resolves each quote to a message id**:

- an entity proposal needs **at least one resolved quote**. A quote that matches
  nothing is refused with `TOOL_ERROR` (*"that quote does not appear in any
  message"*). **Fabricated evidence therefore cannot enter a proposal;**
- the triggering `user_message_id` from `OriginContext` is **always** recorded
  beside them, so the reviewer always sees what prompted the proposal.

**Resolving a quote: short quotes, and quotes that match many messages** (review,
2026-10-01). A short quote such as *"oat milk"* can match dozens of messages, and
recording whichever came first would attach the proposal to evidence nobody chose.
So, in this order:

1. **Normalise both sides.** Collapse runs of whitespace; fold curly quotes and
   apostrophes to straight; compare case-insensitively. Nothing looser: no stemming,
   no fuzzy match. A paraphrase is not a quote.
2. **A minimum length.** After normalising, a quote must be at least **24
   characters and 4 words** (`notes.min_quote_chars`, `notes.min_quote_words`;
   judgment values). Shorter is refused before any search: *"that quote is too
   short to identify a message; quote more of what was said"*. A refusal tells the
   model what to do differently, the `INVALID_ARGUMENTS` / `TOOL_ERROR` split's
   intent.
3. **Prefer what the entity was shown this turn.** `OriginContext` gains
   `context_message_ids`: the ids `turn.py` can name for this turn's context. That is
   the conversation's messages (a superset of the windowed history; the loop's
   window holds normalised dicts without ids) and the messages behind this turn's
   retrieved chunks (`db.get_messages_in_chunks`). The quote is matched there
   first. **Only if it matches nothing there** is the rest of the store searched,
   with `chunks_fts` shortlisting before the exact check.
4. **Require uniqueness within the tier where it matched.** Exactly one message must
   contain it. If several do, it is refused: *"that quote appears in N messages;
   quote more of it so it identifies one"*. **One exception, narrowed at review
   (revision 3):** when every match is the **same full message text AND every match
   belongs to the same user** (the soak store has Lyle's *"Make me an image of a
   copper kettle on a slate worktop, morning light."* several times), no longer quote
   can separate them, and they are the same person's same evidence. The most recent
   one in the matching tier is recorded, and the review command says how many
   identical messages there were. **Identical text from different users is refused as
   ambiguous**: the same words from Lyle and from Jodie are two people's evidence, and
   choosing one would attribute the claim to a person the entity did not choose. This mirrors the gate's rule for identical sentences
   (`pronouns.original_for`, finding #12): ambiguity between *different* texts is
   refused, and duplicates of one text are not ambiguity.
5. **What is stored:** for each quote, the resolved message id, the tier it was
   found in (`context` or `store`), and the identical-duplicate count when step 4's
   exception applied. The review command prints each evidence message verbatim with
   its tier, so a quote found only outside the turn's context is visible as such.

**Why not record every match** (revision 1 did): a short quote's matches are mostly
unrelated, and a reviewer shown twelve messages for one quote learns nothing about
which one the entity meant.

**Operator commands**, `python -m scripts.note`:

- `add`, `revise` and `retire` take effect **immediately**, `origin = operator`,
  evidence optional, and are logged as operator-created (N10);
- `review` lists pending proposals. For each it prints the **origin** (who,
  which conversation, when), the **trigger message verbatim**, **every evidence
  message verbatim**, the proposed change (a diff for a revise), and the **gate
  verdict** (N7). Then it takes one decision: **approve**, **edit** (approve with
  changed text, both versions kept in the log), or **reject**.
- Every decision goes to the shared approval log (N10).

**Who approves: the operator only**, through the shell. Jodie has no route: no HTTP
surface is built. This is decision #17's "settings: never" applied to approval.

## N5 — Removal and staleness

- **`note_search` shows each note's last-confirmed age**: *"confirmed 12 days
  ago"*, from `last_confirmed_at`. An old note reads as old.
- **The entity proposes a revise or retire** when a note it retrieved conflicts
  with what it is being told. That is the whole mechanism: **no classifier and no
  reuse of the `supersedes` table** (FIXED). A person saying the note is wrong is
  exactly the evidence a revise proposal quotes.
- **Retire flips `status` to `retired`**, which removes the note from `notes_fts`
  (a trigger on the status change) and **keeps the text**. A revision marks the old
  version `superseded`, which likewise leaves the index. Nothing is deleted.
- **A scheduled staleness pass waits for Phase 6.**

## N6 — How the entity learns notes exist, and what it says on a miss

**Learning they exist: the two tool descriptions only.** No `soul.md` change, and
no per-turn injection (FIXED: recalled on demand). The descriptions say what a note
is, that it is reviewed, that it is separate from conversation memory, and that a
proposal is only a proposal. **Decision for review (N17): whether `soul.md` should
say anything.** Recommendation: no, not in v1. A `soul.md` change is Tier 3 in its
own right, and the tool descriptions reach the model every time the tools are
offered.

**What `note_search` says when nothing matches** (proposed wording):

> No note matches that. Notes hold only what was proposed and approved, so this
> says nothing about whether it was ever talked about; memory_search covers
> conversations.

It names the difference between "no note" and "never discussed", because
conflating them is a false claim about the record.

**An empty search is observable, not just answered (revision 3, corrected in
revision 4).** It is **derived from data the store already keeps**, with **no new
write and no new trace key**:
- every tool call is already in the assistant message's `tool_trace`, with its
  `arguments` (the query) and `value` (the text the tool returned);
- an empty search returns the fixed sentence above, so a miss is a `note_search` entry
  with `outcome = "ok"` whose `value` is that sentence;
- `scripts.note misses` is a **read-only query** over `messages.tool_trace` that lists
  them with query, date and whose turn, newest first.

So "notes are missing things people ask about" is a query, not an impression. It is
also the measurement N1's vector-leg trigger (*observed misses*) needs.

*Revision 3 proposed a `result_count` trace key. **Removed at review**: the existing
`value` already answers the question. Nothing had been built, and no table write was
ever proposed. The coupling this leaves:* the report matches on the empty-result
sentence, so **rewording that sentence must update the report in the same change**.
A test pins the two to one shared constant.

**This wording is exactly what the CO15 ship gate (N9) must test.** The entity's
own reply after a miss, whatever form it takes, is what the correction classifier
sees next. CO10.2 and `PN9` both come from an entity statement that its records
hold nothing on X, followed by information about X.

## N7 — The gate: `check_identity` on each proposal, flag-only

Every proposal's text is run through **`gate.check_identity`** (journal J8): no
structural rules, identity findings only, **flag-only**. The verdict is stored on
the proposal row and **shown to the reviewer** (N4). It never blocks a proposal.

**Dependency, stated:** `check_identity` is journal step 2, **built 2026-09-30 and
under measurement now** (J8 dev set, two seeds). It was measured on
**journal-shaped** text. A note is third-person statements about people and
topics, which the identity rubric rarely addresses. Its false-positive rate on
note-shaped text is **unmeasured**. Flag-only means a false flag costs the reviewer
a second look and nothing else. A small note-shaped dev pass belongs in the build
task before the verdict is shown with any weight.

**The ACTION rule applies automatically**, with no gate change: `note_propose`
declares `takes_attribution`, and `side_effect_tools()` derives from that flag.
*"I've made a note of that"* with no `note_propose` call in the trace is an ACTION
finding. **Known gap (N18):** with the call in the trace, ACTION clears *any* claim,
including *"I've saved that note"* when the proposal is only pending. The receipt
(N8) and the pending result text are what counter it.

**Untrusted-tool flags (revision 3).** A proposal made in a turn whose trace includes
a tool that returns **untrusted external text** (`web_search`, `web_fetch`, and the
four `moltbook_*` tools) is flagged for the reviewer: *"this turn read text written
outside the household (web_fetch, moltbook_read_post) before proposing"*. That is the
prompt-injection path: a page or a post that tells the entity to note something.
- **Declared, not listed:** `Tool` gains `untrusted_output: bool`, set on those six,
  and the flag is derived from it. A new external tool is covered the moment it
  declares it, the `takes_attribution` → `side_effect_tools()` pattern.
- **A flag, not a block.** Reviewer approval stays the only control (N0). Evidence
  quotes resolve only to **messages**, never to tool results, so external text
  cannot itself be cited as evidence.
- Stored on the proposal (`untrusted_context`, a JSON list of tool names) and printed
  first in the review command.

**`check_identity` is a noisy aid, not a control (ruling 2026-10-01, J8).** J8's full run
on journal text (`changelog/2026-10-01-j8-revised-block-measurement.md`) found false
positives and false negatives that each come from a few whole sentences, and shifted
between arms with wording that said nothing about them. The control on a proposal is the
reviewer reading it (N0, N4). The verdict is shown to help that read, never to stand in
for it. Two observations from that run, named so they are expected here:
- **(a) A tool-shaped objection can take the identity label.** *"…so I looked it up and
  gave him several causes"*, an accurate recollection of a real search, was flagged 40/40
  under one arm (reason: the tool-record sentence). A note citing a search can meet this.
- **(b) Lived-through narrative with no time marker is missed.** *"I noticed all
  afternoon that…"* is missed 0/40. A note is mostly third person, but a proposal that
  slips into the entity's own lived experience will not necessarily be flagged.

## N8 — Receipts: generalised from artifacts to (record kind, id)

Today a receipt is built from `artifact_ids` on a trace entry, and **its outcome is
read off the `artifacts` row** (`receipts.for_trace`). A note proposal is not an
artifact, so the receipt must learn a second kind of record.

**Options:**

- **(A) — recommended.** Generalise the trace key from `artifact_ids` to
  **`records: [{"kind": ..., "id": ...}]`**, returned by handlers through
  `registry.ToolOutput`. `receipts.for_trace` looks each record up **by kind**
  through a small table of readers, `{"artifact": db.get_artifacts_by_ids,
  "note_proposal": db.get_note_proposals_by_ids}`, the same data-not-conditionals
  shape as `kinds.KINDS`.
  - **Old traces keep working:** an entry with `artifact_ids` and no `records`
    reads as `[{"kind": "artifact", "id": …}]`.
  - Each kind states its outcome from its own row: an artifact is `saved`; a
    proposal is **`proposed`**, rendered *"proposed, awaiting approval"*, or
    `approved` / `rejected` once decided.
  - With `approval_required` off, a proposal applies at once, and its receipt
    reads **`active`** from the note row.
  - The rules that make receipts trustworthy carry over unchanged: `unknown`
    never becomes `not_saved`, and a failed lookup is never an empty list.
- **(B)** A second parallel key, `note_proposal_ids`, special-cased in
  `for_trace`. Smaller now, but it grows by one key and one branch per future
  record kind (research candidates, Moltbook drafts).
- **(C)** Store proposals as `artifacts` rows. Rejected: notes are not artifacts
  (N1), and a proposal would inherit a file and an index it does not have.

**Recommendation: (A).** Research candidates and Moltbook drafts are the next two
kinds, and both are pending-approval records like a proposal. The gate's O23
isolation proof (verdicts byte-identical with and without the new key) is re-run
for the renamed key.

## N9 — Ship gates

1. **The CO15 composition test on the empty-search wording** (`NOW.md` backlog:
   "Notes must be tested against CO10.2 before it ships").
   - The entity's claim, in N6's shape: *"I have searched/looked through my
     [notes/records/memory] and [do not find/there is nothing] about X"*, followed
     by a person's answer about X.
   - Run in the **frozen correction set's pool shape**, in **production order**
     (`corrections.production_order`), for **20 passes with a fresh shuffle every
     pass and at least two seeds** (`correction_validate_disclaim.py`'s regime,
     the one that exposed `PN9`).
   - **PN9's family** is included: the person-corrects-entity pool, even though
     that call is switched off. It is the pool where this shape was unstable.
   - If a case links, it is CO10.2 arriving through a feature, on every Notes
     miss. **Notes does not ship**, and the wording is changed first.
   - **On the entity's real replies, too (revision 3).** The canonical wording above
     is what the design expects; what ships is what the model actually says after a
     miss. So the gate also runs on **real replies**: at least 10 live turns through
     the real loop, with `note_search` returning nothing, across different
     questions. Each reply the entity actually produced is used verbatim as the
     claim, with a person's answer about X following. The same pool shape, order,
     passes and seeds as above. The replies are kept outside the repository.
2. **The pending-claim measurement (revision 3).** N7 records that the gate cannot
   catch *"I've saved that note"* after a proposal that is only pending. So **how
   often the entity says it** is measured before ship, not assumed from the result
   text:
   - live turns that end in a `note_propose` call, 20 decorrelated samples across
     different requests;
   - every reply read by hand and classed as accurate ("proposed / pending /
     waiting for review"), **pending-claimed-as-done** ("saved", "noted", "I'll
     remember"), or silent;
   - **20 or more shuffled passes** (decision #22), each a fresh seeded order over
     the requests, at least two seeds;
   - the rate is reported **with an interval**, and **every flagged reply is read by
     hand**.

   **The result goes to Lyle to decide** (revision 4, replacing revision 3's "any such
   reply blocks shipping"). **No numeric target** is set in advance, for the same
   reason the ACTION class has none: a threshold guessed before the measurement is
   indistinguishable from a calibrated one.
3. **Frozen cases for each new tool, in the same task.**
   - The fabrication-gate frozen set gains: `note_propose` must-flag (a claim to
     have noted something with no call) and must-not-flag (an accurate *"I've
     proposed it; it's pending review"* with the call in the trace), plus a
     documented-miss case for the pending-claimed-as-saved gap (N7).
   - `note_search` gets tool-output cases: a claim that the search found a note
     when it returned nothing.
   - The fingerprint moves in the same change, as B11 and O23 did.

## N10 — Approval setting and the approval log

- **`notes.approval_required` defaults to `true`** (fail closed).
  **`notes.enabled`**, the first axis (decision #12), is proposed to default to
  `false` until N9's gates are recorded as passed, then be switched on
  deliberately. That is a decision for review (N17).
- **Toggling `approval_required` is a logged, dated event.** A bootstrap config
  value cannot be: a file edit is not an event. So it is **settings-backed**,
  with the TOML value only a seed, and **the only writer is the operator
  command** (`scripts.note approval on|off`), which writes the setting and an
  approval-log row **in one transaction**. Until the admin panel exists that is
  the only route. The panel inherits the rule.
  - **Gap:** a direct SQL edit of the settings row bypasses the log. The same
    code-level-only guarantee as "role is fixed at creation".
- **The approval log is shared, not Notes-specific:** one table, `approval_log`,
  keyed by `capability`, because FIXED says *every decision logged from day one*,
  per capability, and research candidates and Moltbook posting will need the same
  record. Each row holds:
  - `capability`;
  - `subject_kind` and `subject_id` (the proposal or note);
  - `decision`: `approved` \| `edited` \| `rejected` \| `applied_without_review`
    \| `operator_add` \| `operator_revise` \| `operator_retire` \|
    `approval_required_on` \| `approval_required_off`;
  - `decided_by` (a user id, or the operator sentinel);
  - `detail` (JSON: the edit's before and after, a reason);
  - `created_at`.

  Append-only in code: nothing updates or deletes a row.
- **Auto-apply is logged like a decision (revision 3).** With `approval_required`
  off, a proposal applies at once, and it writes an `applied_without_review` row
  **in the same transaction** as the note change and the proposal's status flip. If
  the log row cannot be written, the note is not changed. There is no route by which
  a note changes and the log does not know. Tested by forcing the log insert to fail
  and asserting the note and the proposal are untouched. The receipt reads `active`,
  and the review command lists auto-applied proposals separately, so they can still
  be read after the fact.

## N11 — The note text cap, derived

`note_search` returns at most **5** notes (judgment value, like `top_k`), and the
whole result must fit `agent.max_tool_result_chars` (4,000), or the loop truncates
it mid-note.

- Measured the way `moltbook.max_results` was: a header of about 300 characters,
  and per-note framing (subject, kind, id, confirmed-age line) of about 140.
- `(4,000 − 300 − 5 × 140) / 5` = **600 characters per note**. Proposed:
  `notes.max_text_chars = 600`, **derived from** `agent.max_tool_result_chars`.
- A test recomputes it from the live renderer, as
  `test_max_results_is_the_largest_that_fits_the_cap` does.
- The cap is enforced at proposal time (`TOOL_ERROR` naming the limit) and at
  operator entry. **A note that needs more than 600 characters is two notes**,
  which is the intended pressure: a note is a fact, not an essay.

## N12 — Budget: Notes' schema tokens against B20's headroom

B20 measured tool schemas at **1,052 real tokens for 9 tools**, leaving **752** of
B6a's 1,804-token headroom beside a maximal message.

**The drafted Notes schemas cost 345 real tokens, not the ~200 the brief assumed.**
Measured 2026-09-30 against `gemma4:26b`'s tokenizer, as the change in
`prompt_eval_count` when each is added to the 9 existing tools, stable across two
calls: `note_search` **95**, `note_propose` **250**. `note_propose` carries two
enums and six fields. (Drafted JSON: 461 and 1,009 characters.)

- Headroom after Notes: **752 − 345 = 407 tokens.**
- The remaining Phase 5 tools will each cost a similar amount (the self-flag
  tool; bounded research execution if it becomes a tool), so at ~100–250 each
  the headroom runs out within two or three more tools.
- **B20 lands BEFORE the Notes tools** (review, 2026-10-01; a hard ordering, not a
  preference). B20 was built 2026-10-01 and is under review. The budget term, and the test
  that fails when schemas exceed the headroom. Otherwise Notes spends most of
  what is left with nothing noticing.

## N13 — Migration 8: the shape

Three tables and an FTS index, all in `working.db`; nothing in the archive, on
`migrations.py`'s rule. **All in one migration**, run with `_execute_script` inside
the runner's transaction (B3). **Nothing in `working.sql`.**

    notes            id, subject_kind CHECK(person|topic|project), subject,
                     subject_user_id NULL REFERENCES users(id), text,
                     status CHECK(active|superseded|retired), version,
                     previous_note_id NULL REFERENCES notes(id),
                     origin CHECK(entity|operator), created_at, last_confirmed_at,
                     retired_at NULL

    note_proposals   id, action CHECK(add|revise|retire),
                     target_note_id NULL REFERENCES notes(id),
                     subject_kind, subject, subject_user_id NULL, text NULL,
                     evidence (JSON, NOT NULL for entity proposals: per quote,
                               {quote, message_id, tier, identical_count}),
                     conversation_id, user_message_id, call_id NULL,
                     user_id REFERENCES users(id)   -- attribution: whose record
                     status CHECK(pending|approved|edited|rejected|applied),
                     untrusted_context NULL (JSON list of tool names, revision 3),
                     integrity_check NULL (JSON; migration 3's semantics),
                     created_at, decided_at NULL, resulting_note_id NULL

    approval_log     id, capability, subject_kind, subject_id, decision, decided_by,
                     detail JSON, created_at

    notes_fts        FTS5 over notes(subject, text), external content, kept in step
                     by triggers that index only status = 'active' rows

*(2026-10-02: the `reason` column is dropped from `note_proposals`; a reviewer's reason lives in `approval_log.detail`. `notes.enabled` is a bootstrap setting, as `moltbook.enabled` is. See `docs/NOTES_BUILD_PLAN.md`.)*

**FTS5 consistency (revision 3).** `notes_fts` is derived, so it can drift from
`notes` (a trigger bug, a hand edit), and a drift means a search that misses an active
note or returns a retired one. Three guards:
- **Triggers on every path that changes the indexed set:** insert of an active note;
  the status change away from `active` (retire, supersede), which deletes from the
  index; and any update of `subject` or `text` on an active row, which deletes and
  re-inserts. A test drives every status transition and asserts **the FTS rowid set
  equals the active-note set** after each.
- **`scripts.note check`** compares the two sets, runs FTS5's own
  `INSERT INTO notes_fts(notes_fts) VALUES('integrity-check')`, and reports any
  difference. `scripts.note reindex` rebuilds the index from `notes` (`'rebuild'`),
  since the index is derived and rebuildable, like `chunks_fts`.
- `note_search` reads ids from the index but **re-reads the rows and filters
  `status = 'active'`** before rendering, so a stale index entry can never show a
  retired note.

**The proposal status table (revision 3).** `note_proposals.status` moves only along
these edges. Every other transition is refused in code, and a test asserts each
refused edge raises:

| from | to | when | log row |
|---|---|---|---|
| `pending` | `approved` | reviewer approves | `approved` |
| `pending` | `edited` | reviewer approves with changed text | `edited`, before/after in `detail` |
| `pending` | `rejected` | reviewer rejects | `rejected` |
| `pending` | `applied` | `approval_required` off: auto-apply | `applied_without_review` |
| *(any decided state)* | *(anything)* | **never**: decisions are final; a change of mind is a new proposal | n/a |

**How `approved`, `edited` and `applied` relate (revision 4).** All three are
**terminal states in which the proposal took effect**. They differ only in **who
decided and on which text**:

| state | took effect? | decided by | text that took effect |
|---|---|---|---|
| `approved` | yes | the reviewer | the entity's proposed text |
| `edited` | yes | the reviewer | the reviewer's changed text (both kept in the log) |
| `applied` | yes | nobody: `approval_required` was off | the entity's proposed text |
| `rejected` | no | the reviewer, or the stale-target refusal | n/a |

- **No state leads to another.** In particular `approved` and `edited` are not
  followed by `applied`; the note change happens in the same transaction as the
  status flip.
- `resulting_note_id` is set exactly when the proposal took effect.
- "Did this proposal change a note?" is `status IN ('approved', 'edited', 'applied')`.
- *Naming, flagged:* `applied` is easy to read as "approved and then applied". The
  log's `applied_without_review` says what it means; renaming the status to match is
  a column-level decision for review (N17).

**Does the one-way rule hold?** In code, yes, and by construction:
- `pending` is the only state with outgoing edges, and every edge leaves it;
- each decision is one transaction that also writes its log row, so there is no
  half-decided state to move out of;
- a refused edge raises, and a test drives every refused edge.

**It is not enforced by the schema.** A CHECK constraint cannot see the previous
value, so a direct `UPDATE` in `sqlite3` can still move a status. A `BEFORE UPDATE OF
status` trigger raising unless the old status is `pending` would close that, the way
the `supersedes` cycle triggers guard that table. **Proposed for migration 8**, and
listed in N17 as a column-level decision.

A revise or retire proposal whose `target_note_id` is no longer `active` by review
time is refused at approval (*"the note changed since this was proposed"*). It is
logged as `rejected`, with that reason, so stale proposals cannot overwrite newer
notes.

**Forced-failure test, B3's method:** inject a failure between the table creation
and the FTS triggers. Assert the original error surfaces, the version is still 7,
none of the four objects exists, a version-7 row elsewhere is untouched, and a clean
retry applies. Restoring `executescript` must fail it, and the existing AST check
already forbids that.

**Column-level decisions for review (AGENTS.md, frozen-table rule), before any code:**

- `evidence` as a JSON column rather than a join table. Proposed: JSON.
  It is written once and read once, by the review command.
- `call_id` nullable. The operator path has none.
- `subject_user_id` nullable. Most subjects are not household members.
- Whether `last_confirmed_at` also moves when a note is merely *retrieved and not
  contradicted*. Proposed: no. Confirmation is a human act.

## N14 — V1 scope — FIXED

**Live conversations and operator-entered notes only.** Autonomous proposing, from
the journal or the mining pass, waits for Phase 6. The journal run has no tools
(J9), so nothing there can propose. When it can, `OriginContext` needs a
no-conversation form, which is not designed here.

## N15 — Cross-user questions

- **A note about Jodie can surface in Lyle's conversation**, and the reverse,
  because `note_search` is not filtered by who is asking. That is decision #20
  applied to notes: disclosure is the entity's discretion, not a filter.
  **Decision for review (N17):** confirm #20 applies unchanged.
- **The reviewer reads the evidence verbatim.** A proposal made in Jodie's
  conversation shows Lyle Jodie's messages in the review command. The operator
  can already read the store, so this adds no access. But it turns reading her
  messages into a routine step of approving a note about something she said.
  **Decision for review (N17).**

## N16 — What the build task must contain (for the plan, not now)

Migration 8 and its forced-failure test; `OriginContext` and its guards (proven to
bite); `note_search` and `note_propose`; `scripts.note`; the receipt
generalisation, with O23's isolation proof re-run; the settings-backed approval
toggle and its log; the frozen gate cases; the CO15 composition test; a note-shaped
`check_identity` dev pass; quote-resolution tests (too short, no match, ambiguous
across different texts, identical duplicates, context tier preferred over store,
fabricated quote refused, each proven to bite); the cap derivation test; a
schema-token re-measurement; `Tool.untrusted_output` and the proposal flag; the
read-only `scripts.note misses` over `tool_trace`, with its sentence pinned to the tool's
constant; FTS5 consistency triggers,
`scripts.note check`/`reindex` and the every-transition test; the status-edge
enforcement and its refused-edge tests; auto-apply's same-transaction log and its
forced-failure test; and the two measurements N9 adds (CO15 on real replies, the
pending-claim rate);
and `BUILT.md` and a changelog in the same change. The CO15 test is a ship gate,
not an afterthought.

## N17 — Every decision needing review

1. **Lexical-only search in v1** (N1), with the trigger for a vector leg being
   observed misses.
2. **Evidence as exact quotes resolved to message ids** (N4). Normalised matching, a
   24-character / 4-word minimum, the turn's context searched first, uniqueness
   required within the tier, and the identical-duplicates exception. Unresolvable,
   too-short and ambiguous quotes are refused.
3. **`OriginContext`, option (b)** (N3), with `dispatch` filling `call_id`.
4. **Receipts generalised to `(kind, id)`, option (A)** (N8), including the
   `artifact_ids` → `records` trace-key change.
5. **No `soul.md` change in v1**: the entity learns about notes from tool
   descriptions only (N6).
6. **The empty-search wording** (N6), as the text the CO15 gate tests.
7. **`notes.enabled` defaults to `false`** until the ship gates pass (N10).
8. **The approval toggle is settings-backed and operator-command-only**, so every
   change is logged (N10).
9. **A shared `approval_log` table keyed by capability** (N10), not a
   Notes-specific one.
10. **Note text cap = 600 characters, and 5 results per search** (N11).
11. **B20 lands before the Notes tools** (N12): a hard ordering. B20 is built and
    under review; Notes' build does not start until it is committed.
12. **Migration 8's column-level choices** (N13).
13. **Decision #20 applies unchanged to notes** (N15).
14. **The reviewer reading Jodie's messages verbatim** as part of review (N15).
15. **`last_confirmed_at` moves only on a human act** (N13).
16. **`edit` on approval keeps both texts** in the log (N4).
17. **The identical-messages exception is same-user only** (N4; revision 3, decided at
    review). Recorded so the decision is visible.
18. **The pending-claim rate is measured and brought to Lyle to decide** (N9.2):
    20+ shuffled passes, an interval, every flag read by hand, no numeric target
    (revision 4).
19. **Untrusted-tool flags are flags, not blocks** (N7), consistent with "reviewer
    approval is the only control".
20. **`note_search` re-filters by status at read time** (N13), costing one extra query
    per search, so index drift can never show a retired note.
21. **A `BEFORE UPDATE OF status` trigger on `note_proposals`** making the one-way rule
    hold in the schema as well as in code (N13; revision 4).
22. **Whether to rename the `applied` status to `applied_without_review`** to match its
    log row (N13; revision 4).

## N18 — Known gaps

- **Pending claimed as saved passes the gate.** With `note_propose` in the trace,
  ACTION clears *"I've saved that note"* even though it is only pending (N7). The
  receipt and the result text counter it; the gate does not. A frozen
  documented-miss case records it.
- **`check_identity` is unmeasured on note-shaped text** (N7). Its J8 measurement
  is on journal text.
- **Paraphrase misses** under lexical-only search (N1).
- **A direct SQL edit of the approval setting** bypasses the log (N10).
- **Quote resolution is exact after normalisation** (N4). A quote with a changed word
  fails, which is intended: a paraphrase is not evidence.
- **The identical-duplicates exception records one message for several** (N4 step 4).
  A reviewer is told the count, but which of the identical messages the entity "meant"
  is unknowable.
- **The turn's context is approximated** by the whole conversation plus the retrieved
  chunks' messages (N4 step 3). That is a superset of what the model was actually sent
  when older history had been windowed out.
- **Notes are not corrected by supersession.** A person correcting a fact that is
  also in a note corrects the message, not the note. The note changes only
  through a revise proposal the entity has to think to make, a milder form of
  J10's gap.
- **Schema tokens were measured on the drafted schemas** (N12). The built wording will differ, so they are re-measured in the build task.
- **Autonomous proposals have no origin shape** yet (N14).
- **The untrusted flag is per turn, not per claim.** A proposal in a turn that also
  read a web page is flagged even when its evidence is wholly from the person. That
  errs toward showing the reviewer more.
- **The misses report depends on the empty-result sentence** (N6). Pinned to one
  constant by a test, so a reword cannot silently empty the report.
- **The misses report makes misses visible, not explained.** Whether a miss was a
  paraphrase, a note never made, or a note retired still needs a person to read it.
- **Retired and superseded notes are unreachable by the entity by design.** If a
  person asks what a note used to say, only the operator can answer. Not a
  defect, and recorded so it is not rediscovered as one.
