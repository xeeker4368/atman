# 2026-09-18 — Task 3.3: correction/supersession classifier (design)

**Tier 3 · Opus (design).** Design only. **No code**, no schema change, no
`cases.toml` change. Design of record: `docs/CORRECTION_DESIGN.md` (C1–C13,
CO1–CO7). Nothing committed. Stops here for review.

## The decision everything else depends on: link at **message** granularity

The table as built is chunk → chunk. **The design recommends replacing it**, and
the case is three measured costs rather than a preference:

1. **A chunk holds content the correction says nothing about.** Up to eight turns
   packed to 2,500 characters, boundaries chosen by size. Marking it superseded
   asserts staleness for all of it, so 3.5's only safe move becomes annotation —
   the coarse link *forces* the weaker behaviour and throws away the correction's
   precision.
2. **The timing gap is unfixable at chunk level and dissolves at message level.**
   Chunking seals on size or conversation close and never indexes the open
   trailing group, so the *normal* case — correcting something said a minute ago
   — has no chunk to link to. Chunk-level needs pending links, a resolver, and a
   re-run path; none exists. Messages exist the moment they are saved.
3. **Chunks are derived and rebuildable; links to them are not.** A re-chunk, a
   restore, or a change to `chunking.target_chars` leaves every chunk-level link
   pointing at something that no longer means what it meant.

**What it costs, verified rather than assumed:** `messages` has no ordinal — only
`timestamp` — and `chunks.first_message_id`/`last_message_id` are uuid4 hex and
unordered. So 3.5 resolves message → chunk with a **timestamp-window join**
(`idx_messages_timestamp` exists). That is the real price of the finer grain, and
`CO2` flags same-timestamp boundaries as its sharpest edge.

**The schema proposal is destructive and says so** (`CO1`): migration 5 drops and
recreates `supersedes` against `messages(id)`, cycle guards rewritten. Acceptable
*here specifically* because the table has **no production rows** — no classifier
has ever written to it — and decision #16 wipes the database before go-live. Two
link tables, one dead, would leave a future reader guessing which one retrieval
honours. Per `AGENTS.md` the column decision goes up before it is coded.

## Q17's carried question, answered: self-correction is inline

One classifier call per turn, after the answer exists, judging **both** the
user's message and the entity's answer against the candidate claims. A
retrospective pass was the other live option and is declined with reasons: it
needs the unbuilt scheduler, it re-examines an unbounded set at a cost that grows
with the record, and it would be the only mechanism here that reaches back and
changes what retrieval returns for content nobody touched. Deferred, not ruled
out — if the entity turns out to notice its own errors on re-reading rather than
in the moment, that is a different design.

## The other open items

* **Trigger:** one call per turn, made only when a candidate set exists — the
  turn's retrieved chunks (already paid for) plus the open conversation's recent
  messages (which retrieval cannot return). **Recall limit stated:** a correction
  of something neither retrieved nor in the current conversation is missed. No
  keyword pre-filter, because decision #2 forbids exactly that.
* **Confidence:** stored, **no threshold ships** — the retrieval floors'
  precedent, where a low-but-set value is indistinguishable from a calibrated one
  that passed. A threshold would need 3.4's frozen set to derive it.
* **Timing gap:** dissolved by C3 rather than managed.
* **`source_type`:** none. The semantics live in the link; inventing a type here
  would write task 1.7's vocabulary ahead of its design pass. **Flagged, not
  decided** — if corrections should be separable in retrieval, that argues for 1.7
  landing first.
* **`db.py` contention:** **not a prerequisite**, because the link write folds
  into the transaction that saves the assistant message — no new concurrent
  writer. Recommended anyway, not blocking.
* **Floor:** unchanged. `2000 + 45 + 45 = 2090 s = 34.8 min` → floor **35**, flat
  for any total classifier time ≤ 100 s. Checked, because two of these
  re-derivations have already been owed and missed.

## Where Q16 is enforced, and why it does not breach F13

Not in the prompt: the candidate set is **built** from the acting user's messages
only, so a cross-user target is never offered and cannot be picked. That is a
database query before the call — the same shape as the gate assembling its trace
— so the framework and the classifier still take no actor.

## Seven open questions

**CO1** the destructive migration · **CO2** same-timestamp collisions in the
mapping · **CO3** how far back "recent messages" reaches (another unmeasured
constant if bounded by count) · **CO4** **may the entity correct the user?** Q18
makes user statements correctable but not by whom; I recommend self-correction
only on both sides, and flag it as a values question · **CO5** one candidate per
reply · **CO6** cross-conversation corrections come free and should be confirmed
as intended · **CO7** 3.5 annotating versus suppressing, reopened because
message-level links make suppression possible where chunk-level did not.

## Not built

No retroactive scan, no editing, no user-visible surface, no cross-user
correction, no threshold.
