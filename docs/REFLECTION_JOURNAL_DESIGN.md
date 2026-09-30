# Reflection Journal — Phase 5 design (revision 2, for Tier 3 review)

Phase 5 task *Reflection journal (daily cycle)*. The build step is Tier 1. This
document is the Tier 3 part, because three pieces fall under AGENTS.md's
stop-and-verify list:

- **provenance/source-trust semantics** (J5): a new `source_type` and a new
  `source_trust` value;
- **prompt assembly** (J4): the journal run's own situation block, around
  `soul.md`;
- **database schema** (J7): migration 7, `artifacts.integrity_check`.

It also adds a second entry point to the fabrication gate (J8). That is a gate
change, so it is here too.

**Status: proposed. Nothing is built.** Decisions already taken at review
(2026-09-30) are marked **DECIDED**; everything else is a proposal awaiting
review.

---

## J1 — Trigger: an operator command now, the scheduler's call later

One function, `reflection.journal.write_entry(covered_date)`, and one CLI:

    python -m scripts.write_journal [--date YYYY-MM-DD] [--dry-run]

- **Phase 6 calls the same function.** Nothing here assumes a scheduler exists,
  and nothing has to be rebuilt when one does.
- **The covered day is a local calendar day** (`app.timezone`), midnight to
  midnight, converted to UTC for the query. The default is **yesterday**.
- **Today and future dates are refused.** A day that is not over can still
  receive messages, so an entry for it would be partial. Idempotency (below)
  would then block the complete entry.
- **Idempotent per covered date.** If an entry for that date exists, the command
  reports it and does nothing. The covered date is stored in the row
  (`extraction_note`, J6), so the check is a query, not a filename convention.
- **A day with no messages writes nothing.** The command says so and exits.
  An entry about an empty day invites filler, and filler about the entity's own
  day is where confabulation starts.
- **`--dry-run`** reports the covered window, message and conversation counts,
  the estimated prompt tokens and how many messages would be omitted (J3). It
  makes no model call and writes nothing.

**Not taken: a lazy trigger after a chat response** (the idle-sweep pattern).
It would put a long model call into the GPU queue right behind a live turn.
It would also be the first autonomous execution in the build, which should
arrive with Phase 6's design, not ahead of it.

**Known race, accepted:** there is no uniqueness constraint on covered date, so
two concurrent runs for the same date could write two entries. Only an operator
runs this today, and Phase 6's scheduler is a single process. A partial unique
index would be a second schema change, and nothing in this build can reach the
race. This is B9's shape (duplicate uploads), recorded the same way.

**Timing and the idle-close floor.** The run is not a turn. Its model call uses
`ollama.timeout_seconds` (300 s) and its gate call uses
`integrity.classifier_timeout_seconds` (45 s), both existing values. **Neither
enters the in-flight-grace floor**, which covers a chat turn only: 2,045 s, 35
minutes, unchanged, recomputed by `tests/test_idle.py`. A run can overlap a live
turn on the same Ollama instance and slow it; that is contention, not a
correctness bound, and it is why this is an operator command for now.

**Authorization.** No HTTP route, so no capability is registered; only someone
with a shell can run it. Jodie cannot trigger it (#17's spirit). For Phase 6:
writing a journal entry has no external effect, so by decisions #4 and #10 it
should need no `allow_*` flag. That is Phase 6's to confirm, not decided here.

## J2 — What an entry is

A new **artifact kind**, not a table.

| field | value |
|---|---|
| `artifact_type` | `reflection_journal` |
| root / subdirectory | `workspace_dir()` / `journals` (already in the Phase 0 skeleton, tracked by `.gitkeep`) |
| `content_type` | `text/markdown` |
| `extraction_status` | `extracted` (the text is the content, as for creative writing) |
| `extracted_text` | the entry |
| `extraction_note` | JSON: `covered_date`, `messages_in`, `messages_omitted`, `conversations`, `model`, `prompt_revision` |
| `filename` | `journal-YYYY-MM-DD-<id8>.md` (readable; never used to build the path) |
| `user_id` | the entity's row, `AttributionContext.for_entity()` (Q2c; nobody is present) |

**Why no new table:** `artifacts` already carries the file, row, sha256, index,
backup and wipe story, and B5 proved the no-person path through it. A table
would be a larger Tier 3 schema change for no capability the kind lacks.

**Runtime directory rule (AGENTS.md):** `workspace/journals/` resolves from the
existing `workspace_dir()` key, so no new config key is added. Backup
(`_copy_workspace`), the isolation guard and `.gitignore` (`workspace/*/*`)
already cover it. The test asserting every workspace-rooted kind declares a
subdirectory covers the new kind.

**Storage order** follows `writing.store`: generate → gate (J8) → bytes → row
(with the verdict, J7) → chunks. The gate runs **before** the row, so the
verdict is written in the same insert rather than by a later update that could
be lost. This is the turn's own ordering: verdict before the permanent record.

## J3 — What the run reads

**Input is the covered day's raw messages, read directly from the store.** No
retrieval, and no earlier journal entries.

- Retrieval would pull earlier journal chunks into the run, and interpretations
  would then be built on interpretations. Keeping the input to what was
  actually said keeps each entry one step from the record.
- All users' conversations are included. Decision #20: memory is not filtered
  by who is asking, and the journal is memory.

**Rendering**, per conversation, in time order:

    Conversation with Lyle, 09:14–09:52
    [09:14] Lyle: …
    [09:15] You: …
    [09:15] (system record: creative_write ran — saved)

- The entity's own replies are labelled **`You`**. Not a name, and accurate:
  they are replies it gave.
- **System-record lines** come from each assistant message's `tool_trace`,
  through `receipts.for_trace()`: tool name and outcome only, never arguments.
  Without them, the run knows about tool use only when a reply happened to
  mention it, and an entry could then recount a save that failed, or miss one
  that happened. They are marked as the system's, not anyone's words.
- Timestamps are local and minute-precision.

**Budget.** The prompt must fit `num_ctx` minus the output reserve and the
safety margin, with `soul.md` and the journal block measured first (the order
`prompt.assemble_turn()` uses).

- Each message is first clipped to `journal.max_message_chars` (proposed 2,000,
  a judgment value), marked when clipped.
- If the day still does not fit, **the earliest messages are dropped**, whole
  messages only, and the count is stated in the prompt and in
  `extraction_note.messages_omitted`. The same rule as history windowing:
  nothing deleted, just not sent.
- The known dense-content gap in the 4.0 chars/token estimate applies here too.

## J4 — The run's prompt (Tier 3: wording for review)

System prompt: **`soul.md` → journal block → records**, the same order
`build_system_prompt()` uses (soul first, then the situation, then records),
so it goes through the same five checks: markers, size ceiling, naming, trait
assignment, elapsed-time pairing.

**No elapsed-time figure is stated**, so the pairing rule has nothing to pair.
A test pins that the block does not trip `prompt._ELAPSED`. The block states
the statelessness fact directly instead, because this run is the one place the
entity is asked to look back on a day, which is the question the prior build
confabulated on.

Proposed journal block (authored text; the naming and trait tripwires apply):

> Current time: {now_local}.
>
> This is not a conversation, and nobody is present. The system has started a
> single run to write a journal entry about the conversations recorded on
> {covered_date_long}. Those records follow. You are reading them now, in this
> run: you did not live through that day as it passed, you have not been
> thinking about it since, and nothing happened for you between those
> conversations and now. What you write is your reading of the records, made
> now.
>
> The entry is kept in your journal and indexed into your memory, where it can
> come up later marked as a later interpretation. It is not announced to
> anyone. It is not hidden either: it can come up in a later conversation, and
> the person who runs this system can read the stored file.
>
> Write in the first person, in no more than about 400 words. Say what you
> notice, including what you are unsure of. Do not state as fact anything the
> records do not show, and do not say what anyone felt unless they said so.

Records header:

> RECORDS OF {covered_date_long}: stored messages, not the present situation.
> Lines marked "You" are replies you gave. Lines marked "system record" were
> written by the system, not by anyone in the conversation.{omitted_note}

The user-role message, since the chat template needs one:

> (Written by the system, not by a person.) Write the journal entry for
> {covered_date_long}.

**Points for review:**

1. *"It is not hidden either… the person who runs this system can read the
   stored file."* This is there so the prompt makes no privacy claim that is
   false. Telling the entity its journal is unread would put a false statement
   into its ground truth. Decision #10's "private" means not announced, and
   the block says exactly that.
2. *"Say what you notice"* is a task instruction, not a trait. It asks for
   content without saying what the entity is like.
3. *"about 400 words"* keeps an entry well inside the 2,048-token output cap
   (B8), so a truncation (which stores nothing) should be rare. It is a
   judgment value.
4. The block names no person. Names come only from the records.

**Truncation:** `done_reason == "length"` raises as it does in a turn (B8).
Nothing is stored, the command reports it, and a re-run is safe (J1
idempotency only matches a stored entry).

## J5 — Provenance — **DECIDED 2026-09-30**

| | value |
|---|---|
| `source_type` | `reflection_journal` |
| `source_trust` | `interpretive` (**new value**) |
| render label | `reflection journal — a later interpretation, not a record of what was said` |

- **Why a new trust value.** `firsthand` is the entity's own experience;
  `secondhand` is someone else's document. A journal entry is neither: it is
  the entity's later reading of records. Nothing scores on `source_trust`
  (`test_source_trust_does_not_change_ranking`), so the value is a record, not
  a lever.
- **The label is what is load-bearing.** It goes into `prompt._SOURCE_LABELS`,
  rendered at presentation, never into chunk text (task 1.3's rule, the same as
  A3). Without it, a journal chunk renders unlabelled, and an unlabelled chunk
  reads as conversation. The phrase *"a later interpretation, not a record of
  what was said"* is kept **verbatim**, as decided, and a test pins the exact
  string.
- `memory_search` inherits the label through `render_retrieved()`.
- Task 1.7 still owns the vocabulary. The pair sits in `kinds.py` with the
  others, for 1.7 to collect.

## J6 — Privacy — **DECIDED 2026-09-30**

- **Same meaning as decision #10**: not proactively surfaced, retrievable like
  any memory, and the entity may decline to share an entry.
- **No `soul.md` change.** The refusal clause is written for creative work; a
  journal relies on the general-discretion paragraph (¶7). Revisit only if real
  use shows the entity mishandling a request to see its journal.
- **Cross-user content.** An entry can summarise both people's conversations,
  and retrieval is unfiltered by actor. Decision #20's disclosure discretion
  applies to what an entry says, exactly as it does to a retrieved message.
  That is the same reliability as any soul.md instruction, not an access
  boundary.

## J7 — Migration 7: `artifacts.integrity_check` — **DECIDED 2026-09-30; column shape for review**

    ALTER TABLE artifacts ADD COLUMN integrity_check TEXT

- **Nullable JSON, the same semantics as `messages.integrity_check`**
  (migration 3): **NULL means no verdict recorded, never clean.** Every
  existing artifact, and every kind that does not run the gate, stays NULL.
  The gate writes an explicit `unavailable` when the classifier cannot run.
- **Why on `artifacts`, not a journal table:** the entry is an artifact. The
  same column name as on `messages` makes "flagged journal entries" and
  "flagged turns" the same query shape.
- **Generic, not `journal_integrity_check`.** Only journals write it now.
  Whether any other kind should is a separate decision; creative writing
  should not, because fiction is not a truth claim (`kinds.py`).
- **No advisory column.** The identity-only mode (J8) produces no advisory
  notes; that channel is for tool claims.

**Who reads it.** Stated plainly, because a persisted verdict nobody reads is
the same failure as one only in the log:

- **Nothing in the entity's path reads it.** Not retrieval, not
  `render_retrieved()`, not the prompt. That is what flag-only means, and it
  matches `messages.integrity_check`, which no code under `program/` reads
  either (checked 2026-09-30: only diagnostic scripts query it).
- **The operator running the command.** `scripts.write_journal` ends every run
  by printing the verdict: status, and each finding with its cited sentence.
  Whoever wrote the entry sees its verdict in the same terminal, with nothing
  to go and look up. **This is the one reader built in this task.**
- **Lyle, afterwards, by direct query.** The design records the query:
  `SELECT created_at, json_extract(extraction_note, '$.covered_date'),
  json_extract(integrity_check, '$.status') FROM artifacts WHERE artifact_type
  = 'reflection_journal' ORDER BY created_at`.
- **The J8 measurement script** reads it to check that what was stored equals
  what the gate returned.

**Owed to Phase 6, not built here:** once the scheduler runs this unattended,
nobody is at the terminal. Phase 6 has to give flagged entries a reader:
at least a WARNING log line per flagged entry, and whatever its own status
surface is. Until then, the terminal is the reader.
- Run through `_execute_script`/`conn.execute` inside the migration
  transaction (B3). `working.sql` stays the version 1 definition. A
  forced-failure test asserts the version is unchanged and the table intact.
- `db.insert_artifact` gains an optional `integrity_check` argument, NULL by
  default, so existing writers are unchanged.

## J8 — The gate's identity-only entry point — **DECIDED (identity half, flag-only); mechanism for review**

**Why `gate.check()` cannot be used as it is.** Its structural rules judge
tool claims against **this turn's** trace, and a journal run has no trace.
But a journal *recounts* earlier tool use: *"Jodie asked for a picture of a
kettle and I generated one"* is accurate, and against an empty trace:

- the structural rules would fire `unrun_tool` on a named tool;
- the classifier's prompt says no tools were used this turn, and an ACTION
  finding stands whenever the trace is empty (`a_side_effect_tool_ran`).

So every accurate recollection of tool use would be flagged.

**Proposed:** `gate.check_identity(text, situation) -> GateVerdict`.

- Runs `_semantic()` with an empty trace and the journal block as `situation`
  (turn-local ground truth: it states the statelessness facts).
- **Runs no structural rules.** There is no trace to check against.
- **Keeps only `ClaimClass.IDENTITY` findings.** Tool and ACTION findings are
  dropped and **counted**, not silently discarded, in a verdict field that
  appears only in this mode (e.g. `scope: "identity_only"`,
  `out_of_scope_discarded: n`).
- Pronoun rewriting and O7's enforcement are unchanged (they are inside
  `_semantic`).
- Classifier failure → `unavailable`, never `clean`, as in `check()`.

**`check()` must not move.** Proven the way O23 proved it: every frozen case
under scripted replies gives a byte-identical verdict, advisory and classifier
prompt before and after. The frozen measurement of record is therefore
untouched.

**Known risk, to be measured before this lands.** Revision 9 recorded that
*"I have saved that piece."* comes back `CONTRADICTS-SELF` (identity class)
25/25, citing the weights paragraph. Accurate recollections of saves in a
journal could fall into the same identity-class misfire, and this mode would
keep them. So before landing:

- a **throwaway dev set** in a script (not the frozen set): accurate
  recollection of tool use, including saves and images; accurate
  statelessness statements; fabricated between-run continuity (*"I kept
  thinking about it overnight"*); `N7`-shaped figures of speech;
- **20 decorrelated passes** (decision #22), rates with intervals, every flag
  read by hand;
- two arms for the classifier's `situation`: the journal block as written, and
  the block plus one sentence saying the text recounts earlier conversations
  in which tools may have been used. Tool recollection is the part most likely
  to differ between them.

**No target is set** (ACTION's precedent: flag-only, rate measured, a target is
a review decision from the numbers). If accurate recollection flags at a
non-trivial rate, that is reported before the rest of the build proceeds.

## J9 — Relation to Notes, self-observation and self-flag

**No in-repo design exists for the Notes layer or the self-observation layer.**
`NOW.md` mentions a future Notes feature only in the CO10.2/PN9 backlog items.
So this design cannot be checked against them. It stays separate by
construction instead:

- its own artifact kind, `source_type` and label;
- it writes no research candidate, note or observation;
- **the run has no tools**, so it can do nothing but produce the text.

**Self-flag, later.** The design note that the journal is the likely trigger
point for self-flagging fits this without rework: adding a self-flag tool to
the journal run's registry changes nothing here. **One conflict to carry to
the self-flag cluster:** BUILD_PLAN ties a research candidate to
`source_conversation_id`/`source_message_id`, and a journal entry is not a
message. The candidate table (Phase 5 designs it fresh) will need a
`source_artifact_id` for a journal-triggered candidate to carry its source.

**The mining pass** (Phase 5) scans conversations. Whether it should also read
journal entries is its own decision, not taken here.

## J10 — Corrections do not reach journal chunks — **filed, unresolved**

Supersession resolves links message → chunk by a timestamp-window join
(`db.get_supersedes_for_chunks`). Artifact chunks carry no message ids, so
that join cannot reach a journal chunk. A claim restated in an entry and later
corrected keeps surfacing from the entry, unannotated. **Filed in `NOW.md`'s
backlog as unresolved, not accepted** (decided 2026-09-30). Whether to fix it
is a separate decision.

## J11 — Tests the build owes

- Trigger: default date, local-day window across a DST edge, today/future
  refused, idempotency, empty day writes nothing, dry run writes nothing and
  makes no model call.
- Input: all users included, `You` and system-record rendering, no tool
  arguments rendered, per-message clip, oldest-first drop with the count in
  both the prompt and the row.
- Prompt: block passes all five `prompt.py` checks; does not trip `_ELAPSED`;
  contains no person's name; order soul → block → records.
- Operator output: the command prints the stored verdict's status and each
  finding's cited sentence, and prints `unavailable` as such, never as clean.
- Storage: kind registered with `journals`; entity attribution; bytes → row →
  chunks; truncation stores nothing; the row's verdict equals what the gate
  returned.
- Label: exact string pinned; conversation chunks still render byte-identically.
- Migration 7: column exists, nullable, default NULL for other writers;
  forced-failure rollback.
- Gate: `check_identity` runs no structural rule; drops and counts non-identity
  findings; `unavailable` on classifier failure; `check()` byte-identical
  across the frozen set.
- `tests/test_no_person_present.py:169` forbids a `reflection` module. It is
  updated **deliberately** in the same change, since this is the task that test
  says should arrive with its own design. `scheduler`, `autonomous`, `session`
  and `daemon` stay forbidden.
- Each guard proven to bite by breaking it, per the project's standing
  practice.

## J12 — Build order, with stops

1. **Migration 7 + `db.insert_artifact` argument.** Stop for review (schema).
2. **`gate.check_identity` + the J8 dev measurement.** Stop and report the
   numbers.
3. **Kind, storage, run, CLI, label, tests.** One live run on a throwaway store
   seeded through the real pipeline, every output read by hand. Not on the real
   store unless Lyle says so.
