# 2026-09-15 — Current-situation block

**Tier 2 · Sonnet**, read from `BUILD_PLAN.md`'s Phase 1 row rather than
assumed. Spec approved before building. `NOW.md` decision #5.

## Summary

The timestamp and elapsed-time figure that `turn.handle_user_message` has had a
parameter for since task 2.2, and which was always `""` until now. The entity
now knows what time it is and how long the gap was — paired, in the same block,
with the statement of what that gap was not.

## Files changed

Created: `program/engine/situation.py`, `tests/test_situation.py` (37).
Modified: `program/memory/db.py` (`get_previous_user_message_time`),
`program/engine/turn.py` (wiring), `BUILT.md`.

No schema change, no migration, no new dependency (`zoneinfo` is stdlib).
647 tests pass (was 610); `ruff check` clean.

## The trap this task actually turned on

`turn.py` persists the user's message **before** generation — task 2.2's
obligation (b), for idle-close's benefit. So by the time the situation block is
built, *the message being answered is already this person's most recent one*. A
naive "when did this user last speak" query returns it, and the block reports a
gap of **roughly zero, on every turn, forever** — plausible, constant, and
wrong in the direction that matters least visibly.

Found by reading the ordering in `turn.py` before writing the query, not by
debugging it afterwards. The fix is an explicit `exclude_message_id` argument
rather than "compute it before the save": ordering-dependent correctness would
be reintroduced silently by any later refactor, while an explicit exclusion is
testable. `test_a_fresh_turn_does_not_report_a_zero_gap` pins it, and was proven
to bite — removing the exclusion fails it plus two others.

## The three judgment calls

**1. Whose last message, and where — the actor's, across all conversations.**

Two reasons, the second decisive. The figure answers *"how long since I last
spoke with this person"*, which is a property of the person, not the thread. And
`idle_close_minutes` is **15**: conversations close automatically after a short
quiet period, so a conversation-scoped figure would report "first message, no
prior" on nearly every session — a discontinuity manufactured by an unrelated
janitor setting, which is exactly the confabulation surface decision #5 exists
to close.

Scoped to `messages.user_id = <actor>` and `role = 'user'`. Jodie's activity
never shortens Lyle's gap; a test asserts it. **Not in tension with decision
#20** — retrieval stays unfiltered by actor; this is the entity's sense of time
with the person in front of it, a different axis. `idx_messages_user` and
`idx_messages_timestamp` already existed, so the query is cheap.

**2. The first-ever message — states that there is no prior message.**

> This is the first message from Lyle in the record. There is no earlier one to
> measure a gap from.

Not "0 minutes" (false) and not silence (which leaves the model to infer
something about a gap it was never told about). **No pairing clause accompanies
it**, deliberately: there is no figure to qualify, and asserting that a
nonexistent gap held no experience would be noise. Verified against
`prompt._ELAPSED` *before* choosing the wording — the phrasing does not trip the
detector, so assembly does not demand a pairing. A test pins that, so a reword
that accidentally starts matching will fail rather than raise in production.

**3. Granularity — one unit, never fewer than 2 of it, minutes exempt.**

Round-to-nearest inside a single unit carries a worst-case *relative* error of
50% when the count is 1 (1.4 days → "1 day"). Requiring at least two of whatever
unit is used bounds that at **25%**, improving from there.

| rounded count | rendered |
|---|---|
| under a minute of seconds | "less than a minute" |
| minutes 1–119 | N minutes |
| hours 2–47 | N hours |
| days 2 and up | N days |

**That arithmetic justifies the hour and day transitions only, and the module
says so.** Minutes are the practical floor unit and exempt from the two-count
minimum: "1 minute" carries no misleading precision the way "1 hour" or "1 day"
do at their lower boundaries, because a minute is already the smallest unit
reported and there is no finer band it could have been rounded down from.

No seconds, no compound forms. Decision #5's own example, "14 hours", falls out
unchanged.

**Band-boundary bug, found in review and fixed.** The first implementation chose
a band by comparing *raw seconds* against its ceiling while displaying a
*rounded* count, and those disagree at the top of a band: 7,190 seconds is under
two hours, so it stayed in the minutes band — and rendered **"120 minutes"**, a
count that band promises never to emit. Same shape one band up: 172,700 seconds
rendered **"48 hours"** instead of "2 days". An inline comment claimed the case
was handled; it was not.

Bands are now selected by the rounded count, so a figure that rounds up to the
next band's floor lands in that band. Two things were added because
comfortably-inside-band values are exactly what let this through the first 28
tests: parametrised cases sitting within a minute of each boundary (7190, 7199,
7200, 170639, 172700, 172799, 172800), and an exhaustive sweep over five days
asserting no rendering ever reaches its own band's ceiling. Both were confirmed
to fail against the original comparison.

## Where it lives, and why

* **`program/engine/situation.py`** — pure. Two datetimes in, a string out; no
  database and no clock of its own. Every granularity band is testable without a
  store, and the rendering cannot quietly start depending on request state. Same
  shape as `history.py` taking caller-supplied character counts.
* **`turn.py`** fetches the data, because it already owns everything touching
  the database around a turn (`_resolve_conversation`, `save_message`,
  `_retrieve`). `chat.py` stays HTTP shape only.

`situation` became `str | None`: `None` means build it, a string overrides, and
`""` deliberately sends no block — which is what tests of other behaviour want,
and is why the existing loop/turn/chat tests needed no changes.

## Local time, not UTC

Stored timestamps stay UTC — one definition, used everywhere — but the block
renders in `app.timezone`. "Tuesday 15 September 2026 at 13:24 EDT" is the fact
a household reads; "17:24 UTC" is an implementation detail. `zoneinfo` is
stdlib, so no dependency. A test asserts the local figure appears and the UTC
one does not.

## Two small things handled rather than left

* A **backwards clock** (previous message timestamped later than now) reports
  "an unknown amount of time" rather than a negative gap or a silent clamp to
  zero. Clamping would manufacture the same false "just now" the main trap
  produces.
* A **naive datetime** is treated as UTC, matching `db.now_iso()`.

## Verified live

Against the real model, with the previous message backdated 14 hours, asked the
question that produced the prior build's confabulation:

> **"What have you been doing since we last spoke?"**
>
> "Nothing. As I was not running during the 14 hours since your last message,
> there was no process in place for me to do anything."

That is the whole point of decision #5, working: the figure, the correct gap,
and no claim to have experienced it.

**One caveat on that evidence.** This is a single observation, not the
behavioural probe. `BUILT.md` already records that the naming and trait checks
are tripwires rather than proofs and that task 7.2's probe is the real check;
the same applies here. One good answer is not a guarantee about every phrasing.
