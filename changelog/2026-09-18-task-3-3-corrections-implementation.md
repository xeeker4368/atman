# 2026-09-18 — Task 3.3 implemented: the correction/supersession classifier

**Tier 3 · Sonnet.** Implements `docs/CORRECTION_DESIGN.md` with CO1–CO7
resolved. Nothing committed. **Stops here for review.** Task 3.4's frozen eval
set does not exist, so nothing here establishes that the detection is accurate
enough to trust.

## Files

Created: `program/integrity/corrections.py`, `tests/test_corrections.py` (33).
Modified: `program/memory/migrations.py` (version 5), `program/memory/db.py`,
`program/memory/chunking.py`, `program/engine/turn.py`, `tests/test_db.py`
(supersession block ported), `docs/CORRECTION_DESIGN.md`, `BUILT.md`.

**905 tests pass** (was 872); `ruff` clean. One new classifier call per turn, no
new setting, **no change to the idle-close floor** (`2000 + 45 + 45 = 2090 s` →
35, flat for any total ≤ 100 s).

## What landed

**Migration 5** moves `supersedes` from chunk → message granularity, destructively,
with the cycle guards rewritten. The three reasons are in the migration's own
docstring so they are readable where the schema is.

**`corrections.py`** assembles candidates, makes one call per speaker who spoke
this turn, and writes at most one link each. Its grammar is `NONE` /
`CORRECTS <n>` — local, not inherited from the gate (O18), parsed in this module
because the shared layer carries the call and the principle, not each consumer's
vocabulary.

**Never linked by default.** No candidates, a `NONE` verdict, an unparseable
reply, a number outside the list, or a reply naming several candidates all produce
no link. The gate's rule is *never clean by default*; this one's is the mirror,
because the failures are not symmetric — **a missed correction leaves the record
accurate and merely uncorrected; a wrong link makes retrieval present the wrong
claim as current.**

**Who may correct whom is enforced by construction**, twice: `candidates()` never
offers a message from the other household member or from the other speaker's
role, and `classify()` re-checks role parity rather than trusting assembly alone.

**`chunking.open_group_messages()`** gives "recent" one definition — chunking's
own sealing boundary (CO3). Read-only, writes nothing, and not an entry point, so
the pinned two-entry-point test still holds.

**A classifier failure never takes the turn down.** The answer is already
generated and saved; a missed link is the cost.

## Live verification

Real model, three prior claims, six new messages — **a smoke test, not a rate**
(decision #22 governs anything reported as a rate, and 3.4 owns accuracy):

| case | link | target |
|---|---|---|
| "Actually the dentist is Wednesday, not Tuesday." | yes | the dentist claim |
| "Sorry — her train is 19:40, I misread it." | yes | the train claim |
| "We also need bread and butter." | no | — |
| "I'm not sure the dentist is right, let me check." | no | — |
| "So: dentist Tuesday at 3." | no | — |
| "What's a good grind setting for a flat white?" | no | — |

Both corrections picked the **right** candidate out of three, not merely some
candidate. 1.3–2.5 s per call.

## Twelve existing tests were ported, not deleted

`tests/test_db.py`'s supersession block wrote chunk-level links. Every property it
pinned still holds and is still pinned — real referents, no self-link, no
duplicate, no cycle at two or four hops, update-cycle coverage, forward
resolution terminating, and the original never altered — now over messages.

**One transcription error of mine, caught by the suite:** I rewrote the
update-cycle test to repoint a link to `A→C`, which does not close a loop, so the
test stopped testing anything and failed honestly. Restored to the original's
`A→B` against `B→A`, with the original's explanation of why that closes the loop.

## A deviation from the approved design, disclosed

**C9 claimed this adds no new write path**, because the link would be written in
the same transaction as the assistant message. **It is not**, and the claim is
withdrawn in the design doc.

Ordering makes it impossible as specified: a self-correction's superseding message
*is* the answer, so its id does not exist until the answer is saved, and the
classifier needs the answer's text to judge it. The alternative — threading an
optional link through `save_message` — would push correction semantics into the
lowest-level write helper to win a transaction boundary.

So there is **one extra single-row insert inside a turn**. `db.py`'s contention
fix stays *recommended, not blocking*, but the reasoning behind that verdict is
now weaker than the design claimed, and the honest statement is that this widens
the window the backup race test is already sensitive to.

## What is owed

**Task 3.4, the frozen eval set** — real corrections and real near-misses,
including the four shapes C11 names, both users, self-correction, and Q16's
no-link case. Nothing about accuracy is established until it runs. **Task 3.5**
then resolves the link at read time, annotating rather than suppressing (CO7),
carrying a visited set so a cycle cannot hang a turn.
