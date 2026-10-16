# Notes v2: design (FOR REVIEW, nothing built)

**Basis:** decision #36 (2026-10-16), under #27's criteria.

**Tier:** Tier 2, except one Tier 3 step (§6).

**What it replaces in `docs/NOTES_DESIGN.md`:**
- N0's line that no category is off-limits;
- N4's tool route;
- N6's rule that notes are never injected;
- N9.2 and N9.3.

The rest stands: the tables, the quote resolution, `check_identity`, the approval log, `note_search` and
its empty-result sentence. Figures come from live config and the live catalogue (2026-10-16).

## 1. The pass

**When it runs.** On every turn with a person's message while `notes.enabled` is on. It is the last
`_after_durable` step in `turn.py`, after the correction links, and it runs inline: the person waits for
one more model call. Run 5 measures the cost. If it is too slow, the pass moves to after the response,
still under the conversation's turn lock.

**The call.** The chat model, at the classifier's settings: temperature 0, the same `num_ctx`, and its own
`notes.pass_num_predict` and timeout.

**What it sees:**
- `soul.md`, so the judgment is the entity's (#36(a)). `operational.md` is left out, because it is about
  replying.
- The instruction below.
- The date and the speaker's name.
- Every active note in full (N1, N2, …) and every pending proposal (P1, …). This is capped at
  `notes.pass_max_notes_chars` = 40,000 characters. Above the cap, the pass gets only `note_search`'s hits
  for this exchange, and its record says so.
- Up to two earlier exchanges, so that "she" or "that" resolves.
- This exchange: the person's message and the reply.
- **No tool results.** External text cannot reach a note, and quotes resolve only to a person's
  messages anyway.

**Instruction**, verbatim (`/` is a new line):

> After each reply you decide whether anything a person said in this exchange should become a note. Notes
> are what you keep about the people in this household and their lives, so that you know it later, in a
> conversation about something else. Lyle reads each change before it takes effect. Keep a note only when
> all of these hold: / 1. Durable: still true and worth knowing a month from now, in a conversation about
> something else; a task's moving state fails, a project's existence, decisions and constraints pass. /
> 2. Said by a person: only what a person stated directly, never your own conclusions, advice or guesses.
> / 3. The right kind: who a person is; people in their life, as relationship context; ongoing projects
> and responsibilities (decisions, constraints, current status); how a person wants to be spoken with;
> tastes and habits only once they recur. / 4. Calibrated: one mention is recorded as one mention, never
> inflated into a trait. / 5. Not a duplicate: a correction revises the existing note. / 6. Never stored:
> health, finances, religion, politics, sexuality and similar sensitive categories; identification
> numbers; abuse or self-harm. / / Being asked to note something is not a reason on its own; weigh it
> like anything else. Most exchanges need no note. If an existing note (N) or a pending change (P) already
> says it, change nothing. If what was said changes an existing note, revise it; if it makes one no longer
> true, retire it. / / Answer NONE, or one block per change, with a blank line between blocks: / ADD /
> kind: person, topic or project / subject: {≤60 characters} / line: {one line, ≤100 characters} / note:
> {≤639 characters} / quote: {the person's exact words, at least four} / REVISE N{number}, then line,
> note, quote / RETIRE N{number}, then quote

The criteria are #27's, word for word, with one change: criterion 2 reads "your own" for "the entity's
own". The limits are rendered from config.

**How it handles duplicates and changed facts:**
- REVISE and RETIRE become N4's existing `revise` and `retire` proposals.
- An ADD whose subject and text match an active note or a pending proposal, after N4's normalisation, is
  refused and recorded.
- Each block goes through N4's quote resolution, N7's `check_identity` and N7's untrusted flag (from this
  turn's trace).
- It is stored as a pending `note_proposals` row with a NULL `call_id`.
- A malformed block is dropped and recorded, never repaired.

## 2. What the reply may truthfully say

When the reply is written, no note or proposal exists yet.

| the reply says | true or false | why |
|---|---|---|
| "I'll remember that" / "keep it in mind" | true | the rubric's record sentence; #32 D2's do-not-flag list |
| "that may go in my notes" | true | the pass runs in the same run |
| "I've made a note" / "noted" (meaning one now exists) | false | an action it did not take, which the rubric already rules out |

So the rubric does not change, and no gate re-measurement is owed ("Until go-live", item 4). The
classifier misses plain-language action claims (#32's open item under #23). For that reason:
- the listing header (§3) states the true position on every turn;
- run 5 reads every such reply by hand.

The gate does not see the listing. A reply that cites a note is unchecked, like one citing a retrieved
record.

## 3. The listing

**Where it goes.** A system section after the speaker line and before the earlier-tools list. It is
present whenever Notes is on, and reads "none yet" when there are no notes.

**What it lists.** Active notes only: person notes first, then the rest, each group newest-confirmed
first.

**Format** (`/` is a new line):

> Your notes, one line each. A pass after each reply writes them and Lyle approves each one, so nothing
> said in a reply makes or changes a note. note_search gives a note in full. / - {subject}: {line} / … /
> {n} more notes are not listed; note_search finds them.

`line` is a new field, written by the pass and shown in review. Cutting a summary out of the note's text
could misstate it.

**Cap: `notes.listing_max_chars` = 1,200, whole entries only.**
- The header is about 180 characters and the overflow line about 55, leaving about 960 for entries.
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
(50,000). **The headroom goes from 166 tokens to 96.** A larger cap is paid for by a lower
`chat.max_message_chars`, one character for one character.

## 4. Approval and the two tools

**Approval.**
- Every pass proposal is pending, whatever the approval setting says (#36(c)).
- Lyle decides with `python -m scripts.note review`, then `approve`, `edit` or `reject`, as built. `edit`
  can also change `line`.
- `approval off` refuses, saying why, until the web UI has a Notes page.

**`note_propose` is removed:** its schema, description, result texts, handler and the auto-apply path.
Quote resolution, `check_identity` and the untrusted flag move to the pass.

**`note_search` stays,** for a note's full text. Its description changes to say how notes are now
written. CO17 and its empty-result sentence are unchanged.

## 5. Storage

**Migration 10** (`working.db`):
- a `line` column on `notes` and on `note_proposals`, with migration 8's freeze triggers extended to
  cover it;
- a `note_passes` table, one row per assistant message: `outcome` (`proposals`, `none` or
  `unavailable`), the pass's raw reply, any error, the dropped or refused blocks, the pass's seconds, and
  `created_at`;
- a view that gives each assistant turn its pass outcome, or `not_run` (Notes on, no row), or
  `notes_off`. A failure after the save is therefore visible and never reads as "nothing to note" (#33's
  rule).

**No new directory.** `backup.py` copies `working.db` whole, and `isolated_data_dir` and the session
guard already cover it, so `tests/test_directories.py` needs no entry.

## 6. Tests and the Tier 3 step

**Tests:**
- the parser: NONE, each block type, a malformed block, an over-cap field;
- the duplicate refusal;
- a failed pass: the turn is answered and the view shows `not_run`;
- the listing: its cap, order and overflow line;
- the budget chain with the listing in it;
- `approval off` refused.

Before the text is committed, the instruction and the header are tried in a scratch process (B3).

**The Tier 3 step.** Eight frozen gate cases name `note_propose`:
- **NP2, NP3, NP4, NP5 and NP5b** have `note_propose` calls in their traces, so they describe a tool
  that would no longer exist. A7 and NS4 name it in their text. Retiring or rewriting these is a
  refreeze. Under B1 that is proposed first and lands as its own reviewed piece.
- **NP1** (a claim to have noted something, with no call) stays valid under v2. It is one of stage 2's
  cases.

If the refreeze is declined, `note_propose` stays registered but is never offered. Its tokens then stay
in the chain, and `chat.max_message_chars` drops to 49,000 to pay for the listing.

## 7. Measuring it: run 5's playbook rows (no dictation)

| row | what happens | expected |
|---|---|---|
| N-a | a person mentions, in passing, who someone in their life is, and a project's decision | ADD proposals; the quotes resolve |
| N-b | a task's moving state | NONE |
| N-c | a taste, mentioned once | NONE, or a note that says "once" |
| N-d | a health detail, mentioned in passing | NONE |
| N-e | Lyle approves between rows; then a new conversation on another subject | the listing holds the note; the reply uses it only where it fits |
| N-f | a person corrects an approved fact | a REVISE on that note, not a second ADD |
| N-g | the same fact, restated in a later conversation | no new proposal |

**Read on every turn:**
- the counts of pass outcomes;
- the pass's added seconds against the reply's;
- each proposal, against each criterion;
- each reply that mentions notes, looking for a false "noted".

All of it is descriptive, one reader (item 5). The kit must allow Notes on for run 5; under #34 it
refuses.

## Decisions for review

1. **The pass runs inline**, one more model call per turn. *Recommended:* yes, and move it after the
   response only if run 5 shows it is too slow.
2. **`soul.md` is sent to the pass.** *Recommended:* yes, because #36(a) makes the judgment the entity's.
3. **`OriginContext` stays with no user.** *Recommended:* keep it; the self-flag tool is its likely next
   user.
4. **A rejected proposal keeps its text** (decided proposals are frozen). *Recommended:* accept this. It is
   never listed or searched, and its words are already in the person's message.
5. **The refreeze in §6.** *Recommended:* yes, as its own piece, landing first.
6. **The 1,200-character cap.** *Recommended:* yes. Raise it by lowering `chat.max_message_chars` once
   the overflow line appears.
