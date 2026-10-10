# 2026-10-07: the speaker is named on every turn

**Tier 2, carried on the Tier 3 branch `cc/gate-ordinary-reading`** (no second lane may open beside
an unmerged Tier 3 branch). Not gate work; reviewed on its own terms.

**Why.** The only place a turn named its speaker was the situation block, which does so on a first
message or after a gap of 15 minutes or more. Under that the block is the time alone and is not
resent with history, so on most turns after a session's opener no name was anywhere in the prompt,
while `soul.md` says "each turn tells you who is speaking". On 2026-10-07 two turns that asked who
was speaking (gaps of 24 s and 72 s) carried no name.

**What changed.** `prompt.speaker_line()` renders "You are talking with <name>." from the
authenticated actor's stored name. It is its own system part: soul, operational, situation,
**speaker**, retrieved records (`AssembledPrompt.speaker_chars`; the parts still sum to the system
string and the budget counts it). `turn.py` passes `actor.name` through `loop.run_turn(speaker=)`.
No person (the journal, callers passing nothing) states nothing. `situation.py` is unchanged and
the line is never in the situation string, so the gate's input does not move: no gate
re-measurement (`AGENTS.md` "Until go-live" item 4). The line passes the authored-text checks.

**Tests.** `tests/test_speaker_line.py`: present on a first message, under 15 minutes (block = time
only) and after 16 days (block also names); overlapping sessions in separate conversations each
name their own speaker; the gate's situation and the classifier calls are byte-identical with and
without the line on the same turn. Mutation-checked (speaker dropped, line folded into the
situation, order swapped, budget omitting it). `BEFORE_PIECE_2` re-taken: with the line patched out
the old digest holds.

**Follow-up.** B5's tools list (design Appendix E) goes after this line, before retrieved records.
