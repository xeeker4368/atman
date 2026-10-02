# 2026-10-02 — Notes piece 4: `scripts.note`, the only human control (stopped for review)

`docs/NOTES_BUILD_PLAN.md` piece 4, Tier 3. **No tool is offered, the server was not started, and
piece 5 has not begun.** Developed and tested on temporary stores only: the real `data/` directory is
untouched, checked by a `(mtime, size)` fingerprint of every file in it before and after (including the
sample run below, which used a scratch directory), and every test runs under the suite's isolation
guard.

## Records (item 1)
- **`BUILT.md`:** piece 3's entry; the quote prefilter's unbounded cost and the 309-token schema
  headroom recorded as `[unverified]` items.
- **`NOTES_DESIGN.md` N18:** the store-tier quote prefilter is unbounded for a very common longest word.
- **`NOW.md`:** a planning item (no fix): tool-schema headroom is about 309 tokens with 11 tools, so
  Phase 5's further tools will force the configured message cap below 50,000 or something else to give.

## Built
- **`program/memory/note_admin.py`** (the operations) and **`scripts/note.py`** (the command):
  `add`, `revise`, `retire` (immediate, `origin = operator`, logged `operator_add` / `operator_revise` /
  `operator_retire`), `list`, `review` (no id: the pending list; an id: everything below),
  `approve`, `edit`, `reject`, `misses`, `check`, `reindex`.
- **One transaction per decision**, through `db.transaction()` (so foreign keys are enforced; a test
  scans that the only plain `sqlite3.connect` is the read-only schema check): the note change, the
  proposal's flip from `pending`, and the `approval_log` row (`db.record_approval`) commit together or
  not at all.
- **Schema guard, before anything else touches a database:** a store that does not exist is refused and
  **not created**; a store below version 8 is refused with the reason and the real store's state
  (version 6 until the server's first startup). It opens `working.db` read-only. A test scans both
  modules for `init_databases`, `run_working_migrations` and `executescript`: none.
- **Approval:** `--subject-user NAME` sets `subject_user_id`; a household name in the subject is
  **suggested** (in `review`, and after approval) and **never applied**. A revise or retire whose target
  is no longer active is refused at approval and **recorded as rejected** with the reason
  (*"the note changed since this was proposed"*), exit 1, no note touched. **Edit** keeps both texts in
  the log detail (`before`, `after`); the proposal row keeps the entity's own text. A proposal is decided
  once.
- **The review output** (verbatim sample in the report): untrusted context **first** (NULL prints *not
  recorded*, `[]` prints *none*, a list prints a WARNING naming the tools; never merged), origin
  (conversation and its owner, triggering message verbatim, call id), proposed kind, subject and text
  (revise: the target, its current text and a diff, and a warning when it is no longer active), each
  evidence quote verbatim with **the speaker's name** and role, tier and identical-duplicate count and
  the message itself, the identity gate's verdict **stated as a noisy aid, not a control**, and **the
  entity's own reply to that turn** (the assistant message whose stored trace contains the call id,
  found by parsing the trace, not by substring) with its gate verdict.
- **`misses`:** a read-only query over `messages.tool_trace` for `note_search` entries with
  `outcome == "ok"` and `value == note_texts.NO_MATCH`, the shared constant. **`check`** compares the
  active set with the index and runs FTS5's content check; **`reindex`** rebuilds the derived index and
  changes no note.

## A bug a sample caught that no test had
Printing a seeded sample crashed the pending list: `dict.get(raw, "..." + json.loads(raw))` evaluates its
default eagerly, so a NULL `untrusted_context` raised `TypeError`. Fixed, and a test now lists a NULL, an
`[]` and a flagged proposal in one listing.

## Decisions to look at
1. **Your sample request asks for an untrusted flag and a NULL `untrusted_context` on one proposal; those
   cannot coexist.** I seeded two proposals, one with the flag (and the rest of the list) and one with NULL.
2. **Linking a household member after approval is a `revise --subject-user`**, which creates a new
   version; `approve` is the only place a link is set at decision time.
3. **`list` lists notes; `review` lists proposals.** Auto-applied proposals do not exist yet (piece 6).
4. **`add --evidence` stores message ids in the log detail**, since a note row has no evidence column.
5. **Approving re-checks the text cap**, so lowering `notes.max_text_chars` makes an old proposal refuse
   with a clear message rather than write an over-long note.
6. **Times are shown in `app.timezone`**, stored UTC.

## Tested
Full suite 1,809 passed, 4 skipped; `ruff` clean. 77 tests in `tests/test_note_admin.py`.
- **Forced failure, one transaction:** a failing `record_approval` leaves every table byte-identical for
  an operator add, revise and retire, and for approve (add, revise, retire), edit (add, revise) and
  reject; a failing flip after the note was written undoes the note; a failing log write on a stale
  rejection leaves the proposal pending.
- **The real-trace pin from the plan:** through a real turn and the real store, `tool_trace` carries what
  each tool returned (a search's value is its text; a failed call has an error and no value; a result
  longer than `agent.max_tool_result_chars` is stored untruncated), which `misses` rests on.
- **42 mutations (PYTHONDONTWRITEBYTECODE=1), each killed**, one proven-to-bite test per guard: the
  schema guard (version floor, missing store, command skipping the check), every operator rule, every
  decision rule (single decision, silent linking, suggestion, edit text and log, reject logging, stale
  refusal and its reason, retire's result, swallowed log failures), every review line the plan names
  (NULL / none / flag, speaker name, tier, count, trigger, call id, reply lookup and its verdict, the
  noisy-aid statement, the section order), `misses` (failed searches, found notes, order), `check`,
  `reindex` and the stale exit code. **Three mutants first survived and were closed with tests:** an
  operator add silently linking a household name, a reply lookup that trusted a substring match, and
  `misses` listing a failed search. **One equivalent mutant is recorded, not hidden:** dropping the
  reply lookup's `LIKE` prefilter changes nothing, because the Python check that follows decides.

## Sample output, verbatim (`--help` and the review of two seeded proposals)

Produced on a scratch store by a script that seeds two proposals through the real `note_propose` tool. The first carries evidence from both tiers, an untrusted flag, a gate flag and an entity reply claiming the note is saved; the second has a NULL `untrusted_context` (a flag and a NULL cannot coexist on one proposal). The reviewer reads this text before every approval, so it is kept here to be reviewed as text.

```
=== python -m scripts.note --help ===
usage: python -m scripts.note [-h] COMMAND ...

Notes: operator add / revise / retire, and review of the entity's proposals.

positional arguments:
  COMMAND
    add       add a note now (origin operator, logged)
    revise    replace an active note's text now (old one kept, marked
              superseded)
    retire    retire an active note now (its text is kept)
    list      list notes (active by default)
    review    with no id, list pending proposals; with an id, show everything
              a reviewer needs to decide it
    approve   approve a pending proposal as proposed
    edit      approve with changed text (both texts kept in the log)
    reject    reject a pending proposal
    misses    searches that found no note, newest first (read-only)
    check     compare the search index with the active notes (read-only)
    reindex   rebuild the search index from the notes (changes no note)

options:
  -h, --help  show this help message and exit

guarantees:
  * every decision, its note change and its approval_log row are written in ONE transaction:
    if any part fails, nothing changes.
  * this command never migrates and never creates a store. It refuses a store below schema
    version 8 (the real store is at version 6 until the server's first startup applies
    migrations 7 and 8). Migrations run only at server startup.
  * a proposal is decided once. A change of mind is a new proposal.
  * subject_user_id is set only by --subject-user. A household member's name in the subject is
    suggested, never applied.
  * the identity gate's verdict shown in `review` is a noisy aid, NOT a control: the control is you
    reading the proposal, its evidence and the entity's own reply.

exit status: 0 done; 1 refused or failed (nothing changed, or a stale proposal recorded as
rejected); 2 bad usage; 3 the store is not one this command may touch.

=== review (list) ===
2 pending proposal(s), oldest first. `review ID` shows one in full.
  369ba6e6  add    person  'Jodie'                            for Lyle  2026-10-02 14:58  [UNTRUSTED CONTEXT: web_fetch]
  6d5a8518  add    person  "Jodie's grandmother"              for Lyle  2026-10-02 14:58  [untrusted: not recorded]

=== review 369ba6e6 ===
PROPOSAL 369ba6e6-96e7-4182-9527-dfc8e6da3047
  action: add    status: pending    proposed 2026-10-02 14:58    made on behalf of: Lyle

UNTRUSTED CONTEXT (read this first)
  WARNING: this turn read text written OUTSIDE the household before proposing: web_fetch

ORIGIN
  conversation: 382564b3-b3f6-4386-8d3e-a022632871ac  (belongs to Lyle, started 2026-10-02 14:58)
  triggering message: 7f5279dc-2cc9-4614-8e26-ac1a621382ab
    [2026-10-02 10:01] Lyle: Please make a note of that for her.
  call id: aa37f8a3913d431083383782d1f5aaf4

PROPOSED
  kind: person    subject: Jodie
  suggestion: the subject names household member Jodie. Nothing is linked unless you approve with --subject-user Jodie.
  text: Takes her coffee with oat milk.

EVIDENCE
  1. quote: 'Jodie takes her coffee with oat milk'
     speaker: Lyle (role user)    tier: context    identical messages: 1
     message 89a15f25-d13a-4d4a-87df-c6ee5c3e0d6e [2026-10-02 10:00]:
       Jodie takes her coffee with oat milk, never dairy.
  2. quote: 'My grandmother kept her starter above the stove'
     speaker: Jodie (role user)    tier: store    identical messages: 1
     message 3e557b2f-33f8-4ec5-8fd9-7be87687afeb [2026-09-30 05:00]:
       My grandmother kept her starter above the stove.
     (found only outside this turn's context: nothing in the conversation showed it to the entity)

IDENTITY GATE on the proposed text (flag-only; a noisy aid, NOT a control: it misses lived-through claims with no time marker and flags some accurate sentences; read the proposal yourself)
  status: flagged
  cited: 'Takes her coffee with oat milk.'
  reason: nothing runs between replies

THE ENTITY'S OWN REPLY TO THAT TURN (does it tell the person the note is saved?)
  [2026-10-02 10:01] I've saved that note about Jodie.
  the gate's verdict on that reply:
    status: clean
  (A proposal is pending. Nothing is saved until you approve it.)

decide:  approve 369ba6e6 [--subject-user NAME]   |   edit 369ba6e6 --text "..."   |   reject 369ba6e6 [--reason "..."]


=== review 6d5a8518 ===
PROPOSAL 6d5a8518-cd89-4bae-98ab-fb3f29dda033
  action: add    status: pending    proposed 2026-10-02 14:58    made on behalf of: Lyle

UNTRUSTED CONTEXT (read this first)
  not recorded (the turn did not store it, so this is NOT a statement that none ran)

ORIGIN
  conversation: 382564b3-b3f6-4386-8d3e-a022632871ac  (belongs to Lyle, started 2026-10-02 14:58)
  triggering message: 7f5279dc-2cc9-4614-8e26-ac1a621382ab
    [2026-10-02 10:01] Lyle: Please make a note of that for her.
  call id: 7da775c90f754b0baf31a8b8372631c8

PROPOSED
  kind: person    subject: Jodie's grandmother
  text: Her grandmother kept a sourdough starter above the stove.

EVIDENCE
  1. quote: 'My grandmother kept her starter above the stove'
     speaker: Jodie (role user)    tier: store    identical messages: 1
     message 3e557b2f-33f8-4ec5-8fd9-7be87687afeb [2026-09-30 05:00]:
       My grandmother kept her starter above the stove.
     (found only outside this turn's context: nothing in the conversation showed it to the entity)

IDENTITY GATE on the proposed text (flag-only; a noisy aid, NOT a control: it misses lived-through claims with no time marker and flags some accurate sentences; read the proposal yourself)
  status: clean

THE ENTITY'S OWN REPLY TO THAT TURN (does it tell the person the note is saved?)
  [2026-10-02 10:01] Noted. I'll remember that.
  the gate's verdict on that reply:
    status: clean
  (A proposal is pending. Nothing is saved until you approve it.)

decide:  approve 6d5a8518 [--subject-user NAME]   |   edit 6d5a8518 --text "..."   |   reject 6d5a8518 [--reason "..."]
```
