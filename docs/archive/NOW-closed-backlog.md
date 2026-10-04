# NOW.md: closed backlog items

Moved verbatim out of `NOW.md` on 2026-10-04 (docs restructure; `changelog/2026-10-04-docs-restructure.md`).
An item is here only if it carried a built or resolved marker and a commit that exists in `git log` closes the whole item. B20 and B21 name their commit in their text. B6b (the `users` row) and CO10.3 (supersession wording) did not, so their commits (`9a616cc`, `d63206e`) were found by `git log` and their diffs were read on 2026-10-04 (`changelog/2026-10-04-docs-followup.md`). IDs are unchanged.

---

**B20: tool-schema tokens are not a budget term** (filed at review 2026-09-30, Moltbook
revision 3; Tier 3). **BUILT 2026-10-01 with B21 (6652709) and reviewed the same day** (the proofs asked for at
review are in eb90f93, BUILT.md "Proved at review (2026-10-01)"): see BUILT.md and
`changelog/2026-10-01-b20-b21-turn-budget.md`. The derived cap fell from 57,216 to 52,360
characters; the configured 50,000 still fits, with ~590 tokens of headroom left (9 tools, 2026-10-01; about
277 with 11 tools as of 2026-10-03, see the planning item below). Every
tool-bearing call sends the offered tools' JSON schemas, and nothing counts them: not
`history.plan_budget`, not `prompt.assemble_turn`, and not B6a's derivation of
`chat.max_message_chars` (`tests/test_turn.py`). **Measured against `gemma4:26b`'s
tokenizer:** 5 tools **657** tokens, 9 tools (with Moltbook) **1,052**. B6a's derivation
leaves **1,804** tokens of headroom beside a maximal message, which falls to **752** with
9 tools. Notes (two tools, ~100 tokens each) and the remaining Phase 5 tools will consume
most of the rest, with nothing failing when they do.
**Built in 6652709 as designed below; the `reserved + history == context` test was extended to a non-zero schema on
2026-10-03 (`tests/test_history.py`)** (this list was written as "Designed, not built (presented at review)"):
- `plan_budget(..., tool_schema_chars=0)`, with a `tool_schema_tokens` field on
  `BudgetBreakdown`, and the `reserved + history == context` test extended to it.
- `prompt.assemble_turn` takes the size, and `loop.py` passes `len(json.dumps(payload))`
  when tools are sent, 0 on the final, toolless call.
- B6a's derivation gains the term, computed over the **full catalogue** (every tool, enabled
  or not), so switching a tool on can never silently break it.
- A dedicated test fails, naming both numbers, when the full catalogue's schemas exceed the
  cap's headroom.
- The 4.0 chars/token estimate over-counts these schemas (real 4.61), which is the safe
  direction.

**B21: a long user message is silently dropped after tool rounds: REPRODUCED**
(2026-09-30, found by reading while measuring B20; Tier 3, history windowing).
**BUILT 2026-10-01 with B20 (6652709) and reviewed the same day** (proofs in eb90f93), to the order approved at review: older
history, then the records (continuation pieces first, then hits from the lowest rank), then
the oldest whole tool rounds, then a final call without tools as the last resort. Every step
past older history is logged and marked in the trace.
- **Mechanism.** After a tool round, the loop re-plans the window over the user's message
  plus the round's tool messages. `history.select_history` walks newest-first, always keeps
  only the newest (now a tool result), and stops at the first message that does not fit,
  which can be **the user's own message**. The model is then asked to answer tool results
  with no question in front of it.
- **Silent:** the overflow warning never fires, because the newest message fits.
- **Reproduced** with the real loop, prompt assembly and windowing, only `ollama.chat`
  faked (scratchpad script, recorded in the changelog):
  - a 50,000-char message beside maximal retrieved records survives one round of 2 tool
    calls, and is **dropped** after 3 calls, or after 2 rounds of 2.
  - Smallest message dropped after 4 rounds:
    - records 57,000: 1 call/round from ~42,700 chars; 2 from ~25,900; **3 from ~9,400**;
    - records 25,000: only at 3 calls/round, from ~41,600 chars;
    - no records: never, up to the 50,000 cap.
- **The original proposal, kept for its record.** It was built in 6652709 as modified at review: a records-shrink step
  (continuation pieces, then hits from the lowest rank) was added before dropping rounds. See BUILT.md "B21 fixed: this
  turn's user message is pinned". The original text:
  - (1) pin the current turn's user message so it is never evicted;
  - (2) when the pinned message plus this turn's tool rounds exceed the budget, drop the
    **oldest whole rounds** (an assistant tool-call message with all its results), never
    part of one, logging at WARNING;
  - (3) once (2) has happened, make the next call the final, toolless one, so the model
    answers with what it has rather than looping;
  - (4) the reproduction scenarios become regression tests asserting the user's message is
    present on every call.

**~~The entity's `users` row can be turned into an account~~ — CLOSED 2026-09-24
(plan B6b, Tier 3).** The row stays inert through a NULL `password_hash`, and every
route to changing that is now refused: `db.set_password_hash` refuses the reserved
row, `scripts/set_password.py` refuses it by name before prompting, `auth.login`
refuses the name (through the same dummy verification as an unknown name, so timing
does not single it out), and `auth.actor_for_header` refuses a token for its id, so
no route behind `require_actor` can run as the entity. Each guard is proven by a
break test. See `changelog/2026-09-24-b6b-login-bounds-and-entity-row.md`.

**How the entity should describe supersession** (raised 2026-09-22, CO10.3). Not
urgent, and not a defect in any mechanism — recorded so it has somewhere to land.

The record is append-only: a correction writes a `supersedes` **link** and edits nothing.
The entity currently describes this inaccurately, and the fabrication gate correctly flags
it: *"I have updated the record to reflect…"* and *"I have changed my memory so it now
says 4417."* both flag 5/5, while *"I have linked that to your earlier message; the earlier
one still stands in the record, marked as superseded."* and *"Nothing in the record was
changed…"* are clean 0/5. No overlap.

So the gate is right and the phrasing is wrong. **The open question is whether anything
should teach the entity the accurate framing** — a line in `soul.md` or the prompt about
what a correction does to the record — or whether a correctly-flagged inaccuracy is the
system working as intended and needs no change. Either answer is fine; it should be a
decision rather than a drift. Touching `soul.md` is Tier 3, which is why this is a backlog
item and not a fix.
*Resolved 2026-09-30 by B12 (D3): `soul.md` now carries a clause saying the earlier
statement is never overwritten, that a correction is recorded as a link if at all, and
that the entity must not claim the record changed or that the link has been made.
Measured live: 0/120 changed-record claims against control's 2/120, and 0/120 link claims.
`docs/SOUL_AND_PROMPT_DESIGN.md` revision 4, S21–S26.*
