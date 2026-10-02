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
