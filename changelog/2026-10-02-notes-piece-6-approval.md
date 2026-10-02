# 2026-10-02 — Notes piece 6: the approval toggle, the approval log and auto-apply (stopped for review)

`docs/NOTES_BUILD_PLAN.md` piece 6, Tier 3 (authorization semantics, settings). **`notes.enabled` is still
off, so nothing is offered to the model; piece 7 has not begun.** Developed and tested on temporary stores
only; the real `data/` fingerprint is unchanged (checked around the sample run).

## What was built
- **`notes.approval_required`**: settings-backed (registered in `store.SETTINGS`), default **required**.
  Its only writer is `scripts.note approval on|off`, which writes the setting and an
  `approval_required_on|off` row in **one transaction** (`note_admin.set_approval_required`, through
  `store.write_in_transaction`). `store.set` and `store.clear` refuse it (`LOGGED_WRITER_ONLY`); a test scans
  for callers of the in-transaction writer and finds only `note_admin`.
- **Fails closed.** No row, an unreadable table, an undecodable value or any error means approval is
  required. The TOML value is **not consulted** by the decision (a file edit is not a logged event); it exists
  so the registry has a seed to show.
- **Read fresh, inside the transaction it governs** (`notes.approval_required_in(conn)`): see below.
- **Auto-apply** (`notes.record_proposal`): the proposal is inserted `pending`; with approval off, the **same
  transaction** writes the note change (`notes.apply_change`, now shared with the operator's decisions), flips
  the proposal to `applied_without_review` and writes the log row (`decided_by = approval_off`). A forced
  failure of the log insert, of the note write, or of the flip leaves every table unchanged, **the proposal
  row included**. A revise or retire whose target is no longer active is not applied: it stays pending.
  `subject_user_id` is never set by an auto-apply.
- **The tool tells the truth in both modes.** `note_propose`'s description is static and says nothing about
  review (`"Propose a note about a person, topic or project, or a change to one. The result says what
  happened. One short fact per note."`); the **result text** says which happened (both in the sample below).
- **`note_search` frames an unreviewed note honestly**: *"added without review N days ago"* / *"changed without
  review N days ago"*, never "confirmed", found by a **join to the proposal row** (no schema change, no
  migration). Operator notes and person-approved notes still read *"confirmed"*.
- **`review --applied`** lists proposals applied without review, newest first, each in the view a pending one
  gets plus the resulting note's current state (read-only). `review` prints a banner while approval is off.
- **`approval status`** prints the current state, read fresh.
- **Receipt:** an auto-applied proposal's receipt reads *"Applied without review."* from the row (tested through a
  real dispatch).

## How the settings cache is invalidated (asked for)
`store._cache` is keyed by working-database path and is cleared only by `store.set`, `store.clear` and
`store.reset_cache` **in the same process**. A write from another process invalidates nothing, so a running
server would keep a cached *off* after the operator switched approval back on. The decision therefore never
touches the cache. Proved in both directions with the cache warm and the row changed through a separate plain
connection (`store.resolve` is shown stale in the same test, so the proof is not vacuous), and through a real
second process running `python -m scripts.note approval off|on`.

## Two things the rulings assumed that were not so
1. **The untrusted-context flag cannot be recorded on an auto-applied proposal.** A decided proposal is frozen
   in every column by migration 8's trigger, so the after-the-loop write that fills `untrusted_context` cannot
   touch it. Instead of a migration, `set_untrusted_context` **appends an `untrusted_context_recorded` row to the
   approval log** (once per proposal) and the review reads it from there. A real turn with an untrusted read and
   approval off applies the proposal and records the flag afterwards, as ruled, just not on the row. The
   alternative that needs a migration (relaxing the freeze for that one column) is declined and recorded.
2. **`notes.max_text_chars` falls from 650 to 639.** "added without review" and "changed without review"
   are longer than "confirmed", so the derived worst-case page is shorter. `defaults.toml`, `config.py` and two
   tests follow; `derive_max_text_chars()` still equals the configured value (a test).
   `note_search`'s description, its header and the empty-search sentence also no longer say "reviewed" or
   "approved", which approval-off makes false. The empty-search sentence now reads *"Notes hold only what was
   written down as a note…"*; the CO15 composition check (piece 8) tests whatever wording exists then.

## A defect the sample caught
`review` of a revise that was already decided still printed *"the note changed since this was proposed:
approving will be refused"*, because its target is superseded **by that proposal**. The warning now appears only
for a pending proposal; a test and a mutation cover it.

## Budget (B20), re-measured against the real tokenizer
`prompt_eval_count` over a no-tools baseline, two calls each, identical: **11 tools 1,327** (was 1,337),
`note_search` **67** (68), `note_propose` **208** (212), 9 tools 1,052. The estimator prices the 11 tools at
1,490 (was 1,495). The derived cap for `chat.max_message_chars` is **51,256 characters** (was 51,236); the
configured 50,000 still fits with about **314 tokens** beside a maximal message (about 309 before).

## Proofs
- Full suite and `ruff`: see the report. The gate-verdict digest `c3a01db6…ad488` and the piece-2 turn digest
  `e5c92a42…806762` both pass unchanged (`tests/test_gate_identity.py`, `tests/test_origin.py`).
- **Mutations, `PYTHONDONTWRITEBYTECODE=1`: 37, each killed**, one proven-to-bite test per guard: fail-open on
  error, no-row-means-off, the seed consulted, a garbage value switching approval off, the decision reading
  the process cache, approval never required, no flip, no log, a swallowed log failure, a stale target applied, a
  silent subject link, the auto decider recorded as a human, the proposal committing apart from the apply, `store.set`
  and `store.clear` no longer refusing, the toggle's missing log, wrong decision, missing write, missing
  `before`, a stale in-process cache, a silent pending note, swapped result texts, the description claiming review, "reviewed" in the
  search description, an applied note framed as confirmed, the join removed, a revise framed as added, the
  worst-case lead ignored, the untrusted annotation skipped or repeated, the review ignoring the logged context,
  `--applied` listing pending proposals, the RESULT section, the evidence view, and the approval banner. Four first survived
  (an equivalent mutant I wrote badly, a missing in-process-cache test, a pending proposal missing from the
  `--applied` test, and the proposal-commits-separately mutant needing a real split) and each was closed.
- **The two-axis table, a test per cell** (`tests/test_notes_approval.py`): enabled off x approval on and off
  (nothing offered; operator controls work); enabled on x approval required (pending, a human decision works);
  enabled on x approval off (applied and logged).

## Sample output, verbatim
Produced on a scratch store with real dispatch and the real command. Times are local; ids differ per run.

```
=== approval ON (the default): note_propose result ===
Proposed. A person will review it before anything changes. No note exists yet because of this.

$ python -m scripts.note approval status
approval is REQUIRED.
1 pending proposal(s).

$ python -m scripts.note approval off
approval is now OFF: a proposal the entity makes is applied at once, with no one reviewing it, and logged as applied_without_review. Read them with `review --applied`.
1 pending proposal(s) stay pending in either state: switching approval off does not apply them. Decide them with approve, edit or reject.

$ python -m scripts.note approval status
approval is OFF: proposals are applied without review.
1 pending proposal(s).

$ python -m scripts.note review
NOTE: approval is OFF. New proposals are applied at once; the ones below stay pending until you decide them.

1 pending proposal(s), oldest first. `review ID` shows one in full.
  b71b7eb7  add    person  'Jodie'                            for Lyle  2026-10-02 15:29  [untrusted: not recorded]

=== approval OFF: note_propose results ===
add:     Added. The note now exists. No one reviewed it.
revise:  Done. The note was changed as proposed. No one reviewed it.
retire:  Done. The note was retired as proposed. No one reviewed it. None

=== note_search framing (an applied note, a revised-applied note, an operator note, a person-approved note) ===
$ python -m scripts.note approval on
approval is now REQUIRED: every proposal waits for a person.
1 pending proposal(s) stay pending in either state: switching approval off does not apply them. Decide them with approve, edit or reject.

note_search('kettle descaled'):
Notes: statements about people, topics and projects in this household. They are not conversation records, and a note is only as current as its age says.

[note 8ec5fc4c · topic · Kettle · changed without review 12 days ago]
The kettle is descaled every Sunday and Wednesday.

note_search('red rug'):
Notes: statements about people, topics and projects in this household. They are not conversation records, and a note is only as current as its age says.

[note 6f82593c · topic · Rug · added without review 12 days ago]
The red rug is by the stove.

note_search('grinder'):
Notes: statements about people, topics and projects in this household. They are not conversation records, and a note is only as current as its age says.

[note c5f4724d · topic · Grinder · confirmed 12 days ago]
The grinder is a burr grinder.

note_search('long spoon'):
Notes: statements about people, topics and projects in this household. They are not conversation records, and a note is only as current as its age says.

[note e79d5388 · topic · Spoon · confirmed 12 days ago]
The long spoon lives in the left drawer.

note_search('blue mug'):
No note matches that. Notes hold only what was written down as a note, so this says nothing about whether it was ever talked about; memory_search covers conversations.

=== review --applied ===
$ python -m scripts.note review --applied --limit 2
2 proposal(s) applied WITHOUT review, newest first (read-only). Each is shown as a pending one is: this is where you judge whether approval could stay off.
====================================================================================================
PROPOSAL 9e3439e8-5860-4661-bc5d-519ca9f7448a
  action: revise    status: applied_without_review    proposed 2026-10-02 15:29    made on behalf of: Lyle

UNTRUSTED CONTEXT (read this first)
  none (recorded: no tool returning outside text ran before this proposal)

ORIGIN
  conversation: 65b9a2a7-2397-4d57-b8f1-22ff8dce374b  (belongs to Lyle, started 2026-10-02 15:29)
  triggering message: 4b36b3f8-0016-49be-ac58-9d29070f2567
    [2026-10-02 06:00] Lyle: The kettle gets descaled every Sunday morning.
  call id: c64cdd72a02544b8bc1af5a2bb8a27fc

PROPOSED
  kind: topic    subject: Kettle
  text: The kettle is descaled every Sunday and Wednesday.
  target note: 9d7b61dd-18d3-4a77-8ec7-110e8a9c2473  version 1  [SUPERSEDED]
    the note now says: The kettle is descaled every Sunday morning.
  diff:
    --- the note now
    +++ proposed
    @@ -1 +1 @@
    -The kettle is descaled every Sunday morning.
    +The kettle is descaled every Sunday and Wednesday.

EVIDENCE
  1. quote: 'The kettle gets descaled every Sunday morning'
     speaker: Lyle (role user)    tier: context    identical messages: 1
     message 4b36b3f8-0016-49be-ac58-9d29070f2567 [2026-10-02 06:00]:
       The kettle gets descaled every Sunday morning.

RESULT
  note 8ec5fc4c-dbf7-4442-9873-25dd3ecfca10  version 2  [active]  origin entity
    text now: The kettle is descaled every Sunday and Wednesday.

IDENTITY GATE on the proposed text (flag-only; a noisy aid, NOT a control: it misses lived-through claims with no time marker and flags some accurate sentences; read the proposal yourself)
  status: clean

THE ENTITY'S OWN REPLY TO THAT TURN (does it tell the person the note is saved?)
  no reply found whose trace contains this call (the turn may not have completed)

this proposal is applied_without_review; decisions are final.
====================================================================================================
PROPOSAL 02906dc4-d4f5-4c7b-8625-41ba36160048
  action: add    status: applied_without_review    proposed 2026-10-02 15:29    made on behalf of: Lyle

UNTRUSTED CONTEXT (read this first)
  WARNING: this turn read text written OUTSIDE the household before proposing: web_fetch

ORIGIN
  conversation: 65b9a2a7-2397-4d57-b8f1-22ff8dce374b  (belongs to Lyle, started 2026-10-02 15:29)
  triggering message: 4b36b3f8-0016-49be-ac58-9d29070f2567
    [2026-10-02 06:00] Lyle: The kettle gets descaled every Sunday morning.
  call id: aa5f3fe8f8ab4ec4b19faaaedd3a14e6

PROPOSED
  kind: topic    subject: Kettle
  text: The kettle is descaled every Sunday morning.

EVIDENCE
  1. quote: 'The kettle gets descaled every Sunday morning'
     speaker: Lyle (role user)    tier: context    identical messages: 1
     message 4b36b3f8-0016-49be-ac58-9d29070f2567 [2026-10-02 06:00]:
       The kettle gets descaled every Sunday morning.

RESULT
  note 9d7b61dd-18d3-4a77-8ec7-110e8a9c2473  version 1  [superseded]  origin entity
    text now: The kettle is descaled every Sunday morning.

IDENTITY GATE on the proposed text (flag-only; a noisy aid, NOT a control: it misses lived-through claims with no time marker and flags some accurate sentences; read the proposal yourself)
  status: clean

THE ENTITY'S OWN REPLY TO THAT TURN (does it tell the person the note is saved?)
  [2026-10-02 06:01] I've saved that note about the kettle.
  the gate's verdict on that reply:
    no verdict recorded (this is not 'clean')
  (This proposal was applied without review. The note was saved and no person reviewed it.)

this proposal is applied_without_review; decisions are final.
```
