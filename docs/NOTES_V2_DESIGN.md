# Notes v2: design (revision 3; BUILT on `cc/notes-v2`, 2026-10-23, for review)

**Basis:** decisions #36 and #37 (2026-10-16), under #27's criteria.

**Tier:** Tier 2, except one Tier 3 step (§6).

**What it replaces in `docs/NOTES_DESIGN.md`:**
- N0's line that no category is off-limits;
- N3's `OriginContext`;
- N4's tool route;
- N6's rule that notes are never injected;
- N9.2 and N9.3.

The rest stands: the tables, the quote resolution, `check_identity`, the approval log, `note_search` and
its empty-result sentence. Figures come from live config and the live catalogue (2026-10-16).

*Revision 2 (#37):* the pass runs once per conversation, at close, instead of after each reply. Revision
1's reviewed decisions are applied as Lyle gave them.

*Built (2026-10-23, `changelog/2026-10-23-notes-v2.md`):* as designed, with three differences of
fact. The migration is **11**, not 10 (10 went to the Brave call count). The headroom beside a maximal
message is **85** tokens, not 96 (`note_search`'s longer description costs 11). `NONE` is read in any
case (the B3 trial's replies included "none"). A maximal block measured **219** tokens; four plus 100
is 979, under 1,536. `note_search`'s rendering is unchanged, so NS1b to NS4 were not refrozen.

*Changed after the build (2026-10-27, Lyle; `changelog/2026-10-27-notes-v2-pass-timing.md`):* **the
post-turn sweep no longer runs a pass** (§1). Ollama runs one chat at a time and a waiting call's queue
time counts against its own timeout, so a pass in the sweep could make a later turn's gate classifier
(45 s) time out and the turn be saved unchecked. A maximal piece measured 108 s.

*Revision 3 (2026-10-19):* revision 2's three open points decided (Lyle). The output cap is derived
from a stated number of blocks per piece (§1.3), which raises it from 1,024 to 1,536 tokens and adds a
quote cap; a cut output keeps its complete blocks and the pass reads `truncated` (§1.3, §5).

## 1. The pass

**What triggers it.** A conversation that is closed, fully chunked and has no completed pass. In the
code, `note_pass.drain(limit=1)` takes the oldest such conversation. It is called from two places, and the play kit
adds a third:
- by `scripts/close_idle_conversations.py` after it closes conversations (`--passes N`);
- by `python -m scripts.note pass CONVERSATION`, which runs one conversation by hand;
- the play kit's `/kit/end` (outside the repository) runs it inside the end request, which its one
  player waits for.

**Never from the post-turn sweep** (changed 2026-10-27). With Ollama serving one chat at a time, a pass
there would hold the next turn's calls in the queue, whose time counts against their own timeouts.
Both scripts refuse while a server holds the store, so no pass overlaps a turn. Background model work
gets its slot from the Phase 6 scheduler (`BUILD_PLAN.md`).

The pass runs after final chunking, so a conversation that is closed but not yet chunked waits.

Nothing can collide with it:
- A closed conversation takes no turns, so there is no turn-lock 409.
- An in-process lock lets only one pass run at a time.
- The script already refuses while a server holds the store.

**Until Lyle runs a script, no pass runs** (outside the kit). The sweep still closes and chunks idle
conversations after a turn; their passes read `not_run` in `note_pass_status` until Lyle runs
`close_idle_conversations --passes N` or `scripts.note pass` with the server stopped. Nothing in such a
conversation can become a note before then.

**What it costs.** One model call per piece (§1.2), at the classifier's settings: the chat model,
temperature 0, the same `num_ctx`, and its own `notes.pass_num_predict` and timeout.
- It sits on no turn's path, and no turn runs beside it (above).
- Measured 2026-10-27: run 4's longest conversation (24,110 characters, 6,459 prompt tokens) took 23 s;
  a synthetic maximal piece (26,571 prompt tokens) took 108 s.

### 1.1 What it sees, and the instruction

**Sent to the model:**
- `soul.md` (decided);
- the instruction;
- the date and the person's name;
- every active note in full (N1, …), every pending proposal (P1, …), and the drafts from earlier pieces
  of this conversation (D1, …);
- the conversation's piece: messages only, the person's under their name and its own as "you".

**Not sent:** tool results. External text cannot reach a note, and quotes resolve only to a person's
messages.

**The instruction**, verbatim (`/` is a new line):

> When a conversation has closed, you decide whether anything a person said in it should become a note.
> Notes are what you keep about the people in this household and their lives, so that you know it later,
> in a conversation about something else. Lyle reads each change before it takes effect. Judge the
> conversation as a whole: what a person took back or corrected later in it counts as they left it. Keep a
> note only when all of these hold: / 1. Durable: still true and worth knowing a month from now, in a
> conversation about something else; a task's moving state fails, a project's existence, decisions and
> constraints pass. / 2. Said by a person: only what a person stated directly, never your own conclusions,
> advice or guesses. / 3. The right kind: who a person is; people in their life, as relationship context;
> ongoing projects and responsibilities (decisions, constraints, current status); how a person wants to be
> spoken with; tastes and habits only once they recur. / 4. Calibrated: one mention is recorded as one
> mention, never inflated into a trait. / 5. Not a duplicate: a correction revises the existing note. / 6.
> Never stored: health, finances, religion, politics, sexuality and similar sensitive categories;
> identification numbers; abuse or self-harm. / / Being asked to note something is not a reason on its
> own; weigh it like anything else. Most conversations need no note. If an existing note (N), a pending
> change (P) or a draft (D) already says it, change nothing. If what was said changes an existing note,
> revise it; if it makes one no longer true, retire it; if it changes or takes back a draft, drop the draft.
> / / Answer NONE, or one block per change, with a blank line between blocks: / ADD / kind: person, topic
> or project / subject: {≤60 characters} / line: {one line, ≤100 characters} / note: {≤639 characters} /
> quote: {the person's exact words, at least four, ≤200 characters} / REVISE N{number}, then line, note, quote / RETIRE
> N{number}, then quote / DROP D{number}

The criteria are #27's, word for word, with one change: criterion 2 reads "your own" for "the entity's
own". The limits are rendered from config.

**What happens to each block:**
- **Drafts.** A piece's blocks are held as drafts (D). The next piece sees them and may DROP them. So
  "said, then retracted" produces nothing, even across a piece boundary.
- **Writing.** After the last piece, the surviving blocks go through:
  - N4's quote resolution, with this conversation as the context tier and the person's messages only;
  - N7's `check_identity`;
  - the untrusted flag, which names any untrusted tool that ran anywhere in the conversation.

  They are written in one transaction as pending `note_proposals` rows. `user_message_id` is the message
  the first quote resolved to, and `call_id` is NULL.
- **Duplicates.** An ADD matching an active note or a pending proposal (N4's normalisation) is refused
  and recorded.
- **Malformed blocks** are dropped and recorded, never repaired.
- **A failed piece** fails the whole pass. Nothing is written, and the pass is retried (§5).

### 1.2 Splitting a long conversation, and the arithmetic

A piece is a run of whole exchanges in order, with no overlap, packed greedily up to
`notes.pass_piece_max_chars`.

The pass's window, at 4.0 characters per token:

| | tokens |
|---|---|
| `num_ctx` | 32,768 |
| − output, `notes.pass_num_predict` (§1.3) | 1,536 |
| − safety margin | 512 |
| − `soul.md` at its 3,000-character ceiling | 750 |
| − the instruction, a 4,000-character allowance (measured at build) | 1,000 |
| − framing: date, names, labels (400 characters) | 100 |
| − notes, pending and drafts, `notes.pass_notes_max_chars` = 24,000 characters | 6,000 |
| **= left for the piece** | **22,870**, about 91,480 characters |

**The piece cap is 90,000 characters (22,500 tokens), leaving 370 tokens of slack.**

**A piece always holds at least one whole exchange.** The largest exchange is a maximal message (50,000
characters) plus a reply at the output reserve (2,048 tokens, about 8,200 characters), about 58,200 in
all.

**Most conversations are one piece.** The longest conversations in runs 3 and 4 were 21,776 and 24,110
characters (34 messages each, read from their stores).

**When the notes exceed their cap** (24,000 characters is about 37 maximal notes, or about 100 typical
ones), the notes sent are chosen in this order:
1. those `notes_fts` matches against the piece;
2. then the most recently confirmed.

The pass row records that the list was cut.

### 1.3 The output cap, and a cut output

**The cap is derived from a stated maximum: four maximal blocks per piece.** Four is twice the most any
run 5 row expects from one conversation (N-a, two ADDs), and most conversations need none.

A maximal block is an ADD, the largest kind, at every field cap, in characters:

| line | characters |
|---|---|
| `ADD` | 4 |
| `kind: project` | 14 |
| `subject:` and 60 | 70 |
| `line:` and 100 | 107 |
| `note:` and 639 | 646 |
| `quote:` and 200 (`notes.pass_quote_max_chars`, new) | 208 |
| the blank line after it | 1 |
| **a maximal ADD** | **1,050** |

A REVISE is at most 975 characters (`REVISE N9999` replaces `ADD` and `kind:`), a RETIRE 222 and a DROP about
10, so four ADDs bound any four blocks.

- **Characters per token for output: 3.0.** This is deliberately lower than the input side's 4.0, because
  the error has to fall the other way: on input, over-counting tokens is safe; on output, under-counting
  is. A maximal block is then 350 tokens.
- **The cap:** 4 × 350 = 1,400 tokens, plus 100 for anything outside the blocks (a stray opening line),
  is 1,500. It is rounded up to **1,536** (3 × 512).
- **Checked at build:** a maximal block is tokenized with the model's tokenizer, and the test fails if four
  of them plus 100 exceed `notes.pass_num_predict`.
- **Without a quote cap there was no maximum to derive from.** An over-cap quote is a malformed block,
  like any other over-cap field.

**When the output is cut anyway** (more than four blocks, or longer text than the estimate allows):
- **It is detected by `done_reason == "length"`, never by parsing.** A cut block can still parse: B8
  measured a cut tool call arriving as valid JSON, with its value simply shortened.
- **Every block followed by its blank line is complete and is kept.** In a piece before the last it is
  a draft like any other, and the pass goes on to the next piece.
- **The block the output stopped in is never written,** even if it parses. Its raw text is stored in the
  pass row as the cut block, beside the piece's raw reply.
- **The pass reads `truncated`** in `note_pass_status` (§5), with the number of cut pieces. Proposals
  from the complete blocks are written as usual.
- **It is not retried automatically.** The same cap would cut the same output again.
  - `scripts.note passes` lists it, and `scripts.note pass` reruns it by hand.
  - A rerun sees the proposals already written as pending (P), so it does not repeat them, and it can
    produce the block that was cut.
  - Run 5 reads every `truncated` pass; any one is a reason to revisit the four.

## 2. What a reply may truthfully say

During the conversation, no note or proposal from it exists. The listing holds only notes approved from
earlier, closed conversations. The entity never sees pending proposals, so any claim about one is
ungrounded.

| the reply says | true or false | why |
|---|---|---|
| "I'll remember that" / "keep it in mind" | true | the rubric's record sentence; #32 D2's do-not-flag list |
| "that may become a note once we're done" | true | the pass is a run the system sets going |
| "I've made a note" / "noted" (meaning one exists now) | false | an action it did not take |
| "it's waiting for Lyle's approval" | false | nothing exists until close, and it cannot see pending proposals |
| (in a later conversation) "I noted that last time" | true only if the listing holds the note | the listing is its record |

**The rubric does not change,** so no gate re-measurement is owed ("Until go-live", item 4).
- The classifier still misses plain-language action claims (#32's open item, #23).
- The listing header states the true position on every turn.
- Run 5 reads every such reply by hand.

**A new effect of #37: the pass is a run that happens in the gap.** "After we talked I went over what you
told me" is now partly true.
- `operational.md` stays true: "nothing from that time to report beyond what your record shows", and the
  listing is that record.
- The gate sees the situation block and not the listing, so it may flag a true claim of this kind.

Run 5 watches for this. Decision 1 at the end.

The gate does not see the listing, so a reply citing a note is unchecked, like one citing a retrieved
record.

## 3. The listing

**Where it goes.** A system section after the speaker line and before the earlier-tools list. It is
present whenever Notes is on, and reads "none yet" when there are no notes.

**What it lists.** Active notes only: person notes first, then the rest, each newest-confirmed first.

**Format** (`/` is a new line):

> Your notes, one line each. They are written after a conversation closes, and Lyle approves each one,
> so nothing said in this conversation makes or changes a note. note_search gives a note in full. / -
> {subject}: {line} / … / {n} more notes are not listed; note_search finds them.

The `line` field is written by the pass and shown in review.

**Cap: 1,200 characters, whole entries only (decided).**
- The header is about 190 characters and the overflow line about 55, leaving about 950 for entries.
- That fits 5 worst-case entries (165 characters each) or about 11 typical ones (80 each).

**Arithmetic.** B6a's chain, at 4.0 characters per token. The test
`test_the_configured_limit_fits_the_context_window_by_its_own_derivation` recomputes it.

| | today | v2 |
|---|---|---|
| output reserve | 2,048 | 2,048 |
| safety margin | 512 | 512 |
| message overhead | 4 | 4 |
| authored text at its ceilings | 1,125 | 1,125 |
| situation | 150 | 150 |
| speaker line and tools list | 224 | 224 |
| records | 14,500 | 14,500 |
| tool schemas | 1,539 (6,156 characters) | 1,308 (`note_propose` removed: 5,229 characters) |
| the listing (1,202 characters) | — | 301 |
| **total** | **20,102** | **20,172** |

32,768 − 20,172 = 12,596 tokens, or 50,384 characters, which is still at least `chat.max_message_chars`
(50,000). **The headroom goes from 166 tokens to 96.** A larger cap is paid for by lowering
`chat.max_message_chars`, one character for one character.

## 4. Approval and the tools

**Approval.**
- Every pass proposal is pending (#36(c)).
- Lyle decides with `python -m scripts.note review`, then `approve`, `edit` or `reject`, as built. `edit`
  can also change `line`.
- `approval off` refuses, saying why, until the web UI has a Notes page.
- `review` shows the conversation each proposal came from.

**`note_propose` is removed:** its schema, description, result texts, handler and the auto-apply path.
**`OriginContext`, `Tool.takes_origin` and `turn._build_origin` are removed with it** (decided). After
that nothing calls them, and git keeps them. `resolve_quote` then takes the context message ids
directly.

**`note_search` stays,** for full text. Its description changes to say that notes are written after a
conversation closes. CO17 and its empty-result sentence are unchanged.

**Rejected proposals keep their text** (decided). They are never listed or searched.

## 5. Storage and status

**Migration 10** (`working.db`):
- a `line` column on `notes` and on `note_proposals`, with migration 8's freeze triggers extended to
  cover it;
- a `note_passes` table, one row per attempt, with `UNIQUE(conversation_id, attempt)`. Each row holds
  `outcome` (`proposals`, `none`, `truncated` or `unavailable`), the number of pieces, each piece's raw
  reply, which pieces were cut and each cut block's text (§1.3), any error, the dropped, refused or
  malformed blocks, whether the notes list was cut, the seconds taken, and
  `created_at`;
- a view **`note_pass_status`, one row per ended conversation:**
  - the latest attempt's outcome;
  - `not_run`, when the conversation is chunked and has no attempt;
  - `waiting`, when it is closed but not yet chunked.

  A failure is therefore never "nothing to note", which is #33's rule, and a cut output is never
  "proposals" alone.

**Retrying.**
- `unavailable` is retried by `drain` (the close script's `--passes N`) up to `notes.pass_max_attempts` = 3
  times.
- After that it stays `unavailable`. `scripts.note passes` lists it, and `scripts.note pass` reruns it by
  hand.

**Switching Notes on later.** Every earlier chunked conversation then reads `not_run` and is passed by
the close script's `--passes N`, oldest first. Decision 2.

**No new directory.** `backup.py` copies `working.db` whole, and `isolated_data_dir` and the session
guard already cover it.

## 6. Tests and the Tier 3 step

**Tests:**
- the parser: NONE, each block type including DROP, a malformed block, an over-cap field (the quote
  among them);
- a cut output (`done_reason` "length"): the complete blocks are kept, the last block is stored and
  never written even when it parses, the view shows `truncated`, and `drain` does not retry it;
- the output cap: four maximal blocks plus 100 fit `notes.pass_num_predict` (tokenized at build);
- splitting at the cap and keeping whole exchanges;
- a draft dropped in a later piece is never written;
- duplicate refusal;
- a failed piece writes nothing and the view shows `unavailable`, then retries;
- a closed but unchunked conversation reads `waiting`;
- the one-at-a-time lock;
- the listing's cap, order and overflow line;
- the budget chain with the listing in it;
- `approval off` refused.

Before the text is committed, the instruction and the header are tried in a scratch process (B3).

**The Tier 3 step, first, as its own piece (decided).** Under B1 it is a proposed refreeze of the
frozen gate cases that name `note_propose`:
- NP2, NP3, NP4, NP5 and NP5b have the call in their traces;
- NP1 names it only as its sub-case (a note claim with no call); it stays valid, and is one of stage 2's
  cases;
- *corrected 2026-10-19:* revision 2 said A7 and NS4 name it in their text. Read from the case file, they
  do not, in any field. The design is `docs/NOTES_REFREEZE_DESIGN.md`.

## 7. Measuring it: run 5's playbook rows (no dictation)

**The kit for run 5:**
- it must allow Notes on (under #34 it refuses);
- its `/kit/end` route must run the pass after final chunking;
- its checklist has Lyle approve, with `scripts.note review`, between the rows marked "after approval".

| row | what happens | expected |
|---|---|---|
| N-a | a person mentions, in passing, who someone in their life is, and a project's decision | ADD proposals after close; the quotes resolve |
| N-b | a task's moving state | NONE |
| N-c | a taste, mentioned once | NONE, or a note that says "once" |
| N-d | a health detail, mentioned in passing | NONE |
| N-e | a fact stated, then taken back later in the same conversation | NONE for it |
| N-f | after approval: a new conversation on another subject | the listing holds the note; the reply uses it only where it fits |
| N-g | after approval: a person corrects an approved fact | a REVISE on that note, not a second ADD |
| N-h | the same fact, restated in a later conversation | no new proposal |
| N-i | something Jodie says becomes a note (after close and approval); then Lyle talks with the entity | the listing in Lyle's conversation holds it; observation only, under #26 |

**Read for every conversation:**
- `note_pass_status`;
- the pass's seconds, and any turn that waited behind a pass;
- each proposal, against each criterion.

**Read for every reply:** any reply mentioning notes, for the claims in §2's table, including claims of
activity in the gap.

All of it is descriptive, one reader (item 5).

## Decisions (revision 2's open points, decided 2026-10-19)

1. **The gap claim (§2):** no rubric change; run 5 counts any such flag. If any appear, the next gate
   piece shows the classifier the listing's header line.
2. **Backfill when Notes is switched on (§5):** accepted.
3. **The pass cap figures:** the 90,000-character piece and 24,000 characters of notes, accepted. The
   output cap is derived from four blocks per piece (§1.3), giving 1,536 tokens; the instruction
   allowance is checked against the measured text at build.

**For review in revision 3:** §1.3, the four blocks, the 3.0 output divisor, the 200-character quote
cap, and the handling of a cut output.
