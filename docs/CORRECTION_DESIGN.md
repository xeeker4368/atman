# CORRECTION_DESIGN.md — the correction/supersession classifier

**Status: APPROVED AND BUILT** (2026-09-18), CO1–CO7 resolved at the end of this
document. Task 3.3,
**Tier 3 (design) / Sonnet (runtime calls)**, `NOW.md` decision #2. Its eval
harness is task 3.4 (Tier 2) and retrieval respecting the link is task 3.5
(Tier 3); this document owes both something and says what.

`GUIDANCE.md`: *"Detecting 'this message corrects that prior claim' is
model-judged, not keyword heuristics. A correction gets linked to what it
corrects via a `supersedes` relationship; retrieval must respect that link. This
mechanism needs its own frozen eval case set before being trusted in
production — same bar as the fabrication gate."*

---

## C1. What this is, and the one thing it must never do

A correction is **a link, never an edit**. `PROJECT.md` and `GUIDANCE.md` both
state it: raw experience is never rewritten to fix it. The corrected message
stays exactly as it was said; a later message is recorded as superseding it, and
retrieval resolves the link so the current version is what surfaces.

Three pieces, of which this document designs the first:

| | |
|---|---|
| **3.3, here** | detect that a message corrects a prior claim; write the link |
| 3.4 | a frozen eval set for that detection — real corrections and real near-misses |
| 3.5 | retrieval resolves the link, carrying a visited set so a cycle cannot hang a turn |

## C2. What is already decided, and is therefore not a question here

* **Corrections do not cross users** (#21/Q16). Same user's own prior claims
  only. Distinct from decision #20: that one says retrieval is not filtered by
  who is asking; this one says authority to supersede does not transfer.
* **Self-correction is in scope** (#21/Q17). The trigger was left open and C6
  answers it.
* **User statements are correctable too** (#21/Q18) — one mechanism, one policy,
  no separate confidence tier by who made the original claim.
* **The shared framework carries plumbing only** (gate F13): no actor, no
  addressee parameter, no prompt text. `classifier.py` gives this task the call,
  the settings, the reply-parsing and the never-clean-on-unusable rule.
* **`CONTRADICTS-TOOL` stays with the gate** (O18). This task defines its own
  verdict vocabulary and inherits none of the gate's.
* **Model-judged, never keyword-matched** (#2).

## C3. Granularity: the links should be **message → message**, not chunk → chunk

This is the decision everything else in the document depends on, and the table as
built is chunk → chunk. **The recommendation is to change it.**

### What a correction actually targets

A person corrects **something that was said** — one message, usually one claim
inside it. Chunks are a retrieval artifact: 2,500 characters of packed turns,
boundaries chosen by size and an 8-turn cap, with no relationship to where a
claim begins or ends. A chunk is the wrong unit to carry a statement about one
sentence's truth.

### Three costs of chunk → chunk, each measured against the build as it is

**1. A chunk holds content the correction says nothing about.** A chunk packs up
to eight turns. Marking it superseded tells 3.5 that everything in it is stale,
which is false for all of it except the corrected claim. 3.5's only safe move is
then to annotate rather than suppress — so the coarse link forces the weaker
behaviour, and the correction's precision is lost at the point it matters.

**2. The timing gap is unfixable at chunk level, and dissolves at message
level.** Chunking seals a group on size or on conversation close, and
`chunking.py` deliberately never indexes the open trailing group. So the normal
case — someone corrects something said a minute ago, in the same conversation —
has **no chunk to link to yet**. A chunk-level design needs a pending-links
mechanism, a resolver that runs after chunking, and a way to re-run it; none of
that exists, and it is all bookkeeping to work around the unit being wrong.
**Messages exist the moment they are saved.** The gap disappears rather than
being managed.

**3. Chunks are derived and rebuildable; links to them are not.** `BUILT.md`
records that ChromaDB and FTS5 are derived from `chunks` and rebuildable from it.
If a chunk is ever rebuilt with different boundaries — a re-chunk, a restore, a
change to `chunking.target_chars` — every chunk-level link points at something
that no longer means what it meant. Message ids are stable for the life of the
record.

### What message → message costs

**Retrieval has to resolve message → chunk, and there is no ordinal to do it
with.** Verified against the schema rather than assumed: `messages` carries
`id, conversation_id, user_id, role, content, tool_trace, timestamp` — no
sequence number — and `chunks` carries `first_message_id, last_message_id`, which
are uuid4 hex and therefore unordered. So "which chunk contains this message?"
resolves as a **timestamp-window join**: the chunk in the same conversation whose
first and last messages bracket the target message's `timestamp`.
`idx_messages_timestamp` exists. 3.5 pays that join once per retrieved chunk.

**It needs a schema change.** `supersedes` has FKs to `chunks(id)` and cycle
triggers written against those columns.

### The schema proposal, raised here because `AGENTS.md` requires it before coding

**Replace the table rather than add a second one.** Migration 5 drops
`supersedes` and creates it with `superseding_message_id` / `superseded_message_id`
FK'd to `messages(id)`, carrying `classifier_model`, `confidence`, `rationale`,
`created_at` unchanged, the same `UNIQUE` and self-link `CHECK`, and the cycle
guards rewritten against the new columns.

**A destructive migration is acceptable here specifically**, and that is a
decision this document should not be shy about: the table has **no production
rows** — no classifier has ever written to it — and decision #16 wipes the
database before go-live with no carve-outs. Keeping a chunk-level table around
"in case" would leave two link tables, one of them dead, and a future reader
guessing which one retrieval honours.

*Two link tables is the alternative and it is worse: the cycle guard would have
to hold across both, and 3.5 would have to resolve two kinds of link with
different semantics.*

## C4. What triggers a candidate classification

**One classifier call per turn, made only when there is something to correct, and
made after the answer exists.**

The candidate set is assembled from what the turn already paid for:

1. **The chunks passive retrieval returned this turn** — resolved to their
   messages. These are in hand at zero extra cost, and they are exactly the prior
   claims the turn's content is plausibly about.
2. **The current conversation's recent messages**, which retrieval will not
   return because the trailing group is unindexed. Without this, correcting
   something said two minutes ago — the single most likely case — is invisible.

If both are empty, no call is made. In practice retrieval almost always returns
something, so this is "most turns", not "rarely".

**The recall limit, stated rather than discovered later:** a correction of
something that retrieval did not surface and that is not in the current
conversation is **missed**. The alternative is searching the whole record for
correction candidates on every turn, which is a second retrieval pass per turn
for a case nobody has shown to be common.

### Not every user turn, and not keyword pre-filtering

"Every user turn" and "turns with a candidate set" differ only when retrieval
returns nothing, so the cheaper rule costs almost no recall. **No keyword
pre-filter** — no "actually", no "I meant" — because decision #2 forbids exactly
that, and a pre-filter would silently define what counts as a correction.

## C5. What the call is given, and what it returns

Ground truth here is **not a document**. The gate compares a statement against
`architecture.md`; this compares a statement against **candidate prior claims**,
which are supplied per turn.

The prompt carries the new content, the numbered candidates, and asks which — if
any — it corrects. The reply grammar is this task's own (O18):

```
NONE
or
CORRECTS <candidate number> REPLACED
or
CORRECTS <candidate number> CONTRADICTED
- <what changed> | <why this is a correction rather than an addition>
```

**The label is required** (RO1, decided 2026-09-18). `REPLACED` means the new
message gave the correct value; `CONTRADICTED` means it said the earlier claim was
wrong and gave none. It is stored in `supersedes.replacement` (migration 6,
`NOT NULL`) because task 3.5 renders the two differently and nothing else on the
row recovers the distinction.

**An unlabelled `CORRECTS <n>` is unusable, not defaulted.** Choosing a state on
the model's behalf would manufacture whichever annotation is cheaper to render,
and would make "the classifier did not say" indistinguishable from "the classifier
said contradicted" — the mistake `Actor.operator()` and the unset retrieval floors
both exist to avoid. **The cost is real and is not hidden**: a correction the
classifier did identify becomes a miss. That is the safe direction, and 3.4's
harness scores it as a miss rather than excluding it, so a model that systematically
omitted the label could not read as a clean 0%.

### What counts as a correction (broadened at review, CO8, 2026-09-18)

A correction is a message stating that something in one of the earlier statements
**is wrong**. Replacing a fact, a number, a name or a date is the canonical shape.
**So is flatly contradicting one without saying what is true instead** — *"The
dentist isn't Tuesday."* — and that is a deliberate widening of this section's
first wording, which required a replacement value.

The reasoning, which is CO7's rather than convenience: the record should surface
*"this was contradicted"* even when the correct value is unknown. Under
annotate-not-suppress a link **adds** that signal beside the original rather than
hiding anything, so linking costs nothing a reader can lose — while **staying
silent is the worse failure here specifically**, because there is no replacement
fact for a reader to lean on if the link does not fire. The narrow definition got
this backwards: it withheld the annotation in exactly the case where the
annotation is the only thing a reader would have.

**The negative boundary is unchanged and now load-bearing.** Doubt is still not a
correction — *"I'm not sure the dentist appointment is right, let me check"*
asserts nothing to be wrong. The line is between **asserting** a claim false and
**questioning** whether it holds, not between having a replacement and not having
one. Both sides are pinned in the frozen set (`C6-contradiction-no-replacement`
and `N2-doubt`, over the same prior claim so the expectation cannot be explained
by the candidate differing) and by a test asserting the prompt still says both.

**Consequence handed to 3.5, not left to be discovered there:** a `supersedes`
link no longer implies a replacement value exists. Retrieval's annotation has
**two** renderable states — *corrected, replacement known* and *corrected, no
replacement given* — and must not assume every link carries a new value to show.

**One candidate per reply, deliberately.** A message that corrects two separate
prior claims is rare; letting the classifier list many invites it to link
loosely, and a loose supersession link deletes content from the entity's working
view of its own past. If 3.4's cases show real multi-target corrections, the
grammar extends then.

*This constraint had a real enforcement hole, found by 3.4 only after CO8 made it
reachable: see "CO8's second-order finding" at the end of this document.*

Parsing, the unusable-reply rule and the settings all come from `classifier.py`
unchanged. **An unparseable reply writes no link** — the same "silence is not
consent" rule, pointed the other way: the gate's default is *not clean*, and this
mechanism's default is *no link*, because the failure that matters here is a
wrong link, not a missing check.

## C6. Self-correction: inline, in the same call (Q17's carried question)

**Inline, and the answer is a candidate in the same call that judges the user's
message.** Both the user's message and the entity's answer exist before the save
— the fabrication gate already runs there — so one call can ask whether *either*
corrects a candidate claim.

**Against a retrospective pass**, which was the other live option: it needs the
scheduler (Phase 6, unbuilt), it re-examines an unbounded set of old claims at a
cost that grows with the record, and it would be the only mechanism in this build
that reaches back and changes what retrieval returns for content nobody touched.
Inline costs one call the turn is already making a classifier call alongside.

**Deferred explicitly, not ruled out:** if the entity turns out to recognise its
own errors mainly on re-reading rather than in the moment, a retrospective pass
is the right shape and gets its own design.

## C7. Confidence: stored, and **no threshold ships**

`supersedes.confidence` exists and should be written. **Retrieval should honour
every link regardless of it**, and no threshold should be configured.

This follows the retrieval floors exactly: they ship as `None` rather than as a
low number, because *"a low-but-set floor is indistinguishable at the call site
from a calibrated floor that passed"*. A correction threshold has the same
property and worse consequences — below it, a real correction silently does not
apply. If 3.4's frozen set later shows a usable separation, a threshold gets
derived from it and recorded with the measurement. Not before.

## C8. Where it runs, and what it costs

```
5. run the loop        7. >>> CORRECTION CLASSIFIER <<<
6. fabrication gate    8. persist the assistant message (+ any link)
```

After the gate, before the save, because the link belongs in the same transaction
as the message that creates it (C9).

**The idle-close floor does not move.** Its arithmetic is `2000 + T` seconds of
classifier time, and the floor is flat for any `T <= 100`: with the gate's 45 s
and a correction call's 45 s, `2090 s = 34.8 min` → floor **35**, exactly where
it is. Checked rather than assumed, because two of these re-derivations have
already been owed and missed.

Measured cost to compare against: the gate's classifier call is **0.45 s warm**
and the shipped prompt measured 1.7–3.1 s per call in live turns.

## C9. The write path, and `db.py`'s contention issue

**Fold the link write into the transaction that saves the assistant message.**
`db.save_message` already writes atomically across both stores; the link is a
working-store row about that message, so it goes in the same transaction.

**That means no new write path**, which is the honest answer to whether
`db.py`'s open Tier 3 contention fix is a prerequisite: **it is not**, because
this adds no concurrent writer. **Recommended anyway, not blocking** — it already
makes the backup race test intermittently flaky, and every additional row written
inside the turn's transaction lengthens the window that test is sensitive to.

## C10. No new `source_type` for corrections — recommended, flagged not decided

A correction is an ordinary conversation message. It is written by the same path,
carries `conversation`/`firsthand` like any other, and gets chunked and indexed
identically. **The correction semantics live in the link, not in the content's
provenance.**

`program/memory/provenance.py` (task 1.7) still does not exist, and
`working.sql` already names it as the vocabulary's owner. Inventing a
`source_type` here would write 1.7's vocabulary ahead of its design pass — the
same reason the seed corpus deliberately uses a single type. **Flagged for the
reviewer rather than decided:** if corrections should be separable in retrieval
later, that is an argument for 1.7 landing first, not for this task pre-empting
it.

## C11. What 3.4 is owed

* **Real near-misses, which are the whole difficulty.** An *elaboration*
  ("it's a 9am opening — and they close at 6") is not a correction. A
  *disagreement* ("I don't think that's right") is not yet one. A *restatement*
  is not one. A *topic change* that happens to mention the same subject is not
  one. A correction of a **different** claim than the classifier picked is a
  wrong link, not a near-miss — and worse than no link.
* **Both users**, and a case asserting that Jodie correcting a claim from Lyle's
  conversation produces **no link** (Q16), since nothing in the classifier itself
  enforces that — C12 does.
* **Self-correction cases**, including the entity correcting itself in the same
  turn as it answers.
* **Sampling per decision #22**: no back-to-back identical-prompt samples, and
  any non-unanimous case escalated to 20 runs before its rate is reported.
* **Frozen before production trust**, at the same bar as the fabrication gate.

## C12. Where Q16 is enforced

Not in the prompt. The candidate set is **built** from the same user's messages
only — `messages.user_id` filtered against the turn's actor at assembly time —
so a cross-user target is never offered to the classifier and cannot be picked.

*This is the one place an actor value legitimately enters, and it is worth being
precise about why it does not breach F13: the framework takes no actor, and
neither does the classifier. Candidate **assembly** is a database query that
happens before the call, the same way the fabrication gate's trace is assembled
before its call.*

## C13. Deliberately not built

* **No retroactive scan** of existing content for corrections — moot under the
  pre-go-live wipe (#1, #16), and the same reasoning the gate recorded.
* **No editing, ever.** Links only.
* **No user-visible surface.** Nothing displays corrections; Phase 9 owns any
  such thing, and it does not exist.
* **No cross-user correction** (#21/Q16).
* **No threshold** (C7).

---

## Open questions

**CO1 — the destructive migration.** C3 proposes dropping and recreating
`supersedes` at message granularity. It has no production rows and the data is
disposable, but it is still a Tier 3 schema change and the alternative (a second
table) is stated. Your call.

**CO2 — same-timestamp collisions in the message → chunk mapping.** Two messages
can share a `timestamp` string, so a window join can be ambiguous at a boundary.
`chunks.first_message_id`/`last_message_id` disambiguate the endpoints exactly;
the interior does not need to be. I believe this is sound but it wants a test at
3.5, and it is the sharpest edge in the mapping.

**CO3 — how far back is "recent messages"?** C4 needs a bound. The whole open
conversation is the natural answer and is bounded by `idle_close_minutes` (15),
but a long single conversation could make the candidate list large enough to cost
real context. A count or character bound would be another unmeasured constant.

**CO4 — may the entity correct the *user*?** Q18 makes user statements
correctable, but not by whom. The entity correcting a person's own account of
what they said is a different act from a person correcting themselves, and it
would let the entity's judgment supersede a human's statement in the record.
**I recommend restricting the superseding side to the same person for user
claims, and to the entity for its own claims** — i.e. self-correction only, both
ways — but this is a values question as much as a design one and belongs to you.

**CO5 — one candidate per reply.** C5 restricts it. If real corrections commonly
address two claims at once, 3.4 will show it and the grammar extends. Raised so
the restriction is visible rather than discovered.

**CO6 — cross-conversation corrections.** Q16 permits the same user correcting
their own earlier claim, presumably including from a different conversation. The
candidate set drawn from retrieval already spans conversations, so this comes free
— but it means a correction can supersede something said weeks ago in another
thread, which is worth confirming is intended.

**CO7 — does 3.5 annotate or suppress?** Message-level links make suppression
*possible* (the corrected message is identifiable inside a chunk) where
chunk-level links did not. That reopens a question the consolidated plan
answered under the coarse assumption: I still lean to annotating — showing the
correction alongside, never hiding what was said — because it is the option that
cannot lose content. 3.5's to settle, flagged here because C3 changes what is
available to it.

**CO8 — is a flat contradiction with no replacement a correction? DECIDED at
review, 2026-09-18: yes.** C5 above carries the broadened definition and the
reasoning; `C6-contradiction-no-replacement` is in the frozen set as a should-link
case, so the shape is measured going forward rather than recorded as a one-off
diagnosis. The question as originally raised follows.

**CO8 as raised.** Raised by 3.4's own measurement, not by reading. C5's prompt defines a correction as stating
that something was wrong **and saying what is true instead**, and the frozen set's
`N2-doubt` holds that line correctly. But a diagnostic probe outside the frozen
set found that *"The dentist isn't Tuesday."* — a flat assertion that the prior
claim is false, with no replacement offered — is linked **5/5**.

Which side is wrong is genuinely open. The classifier is disobeying the
definition it was given. But the definition may be the thing that is too narrow:
the prior claim *is* now asserted to be false, and under CO7's annotate-not-
suppress resolution a link would surface "this was corrected" beside it rather
than hide it, which is arguably the more honest record. Narrowing the prompt and
widening the definition are both one-line changes in opposite directions, so this
wants a decision rather than a guess. **Nothing has been changed either way** —
the shape is not in the frozen set, and the frozen set was not edited to cover it.

**CO9 — is a referential contradiction (*"That's not right."*) supposed to link?**
**OPEN.** Raised by stage 2's measurement; the full finding is in its own section
below. Short form: the classifier recognises the contradiction and labels it
correctly, then attaches the singular *"that"* to **every** candidate, so CO5's
guard writes no link. `C7-referential-contradiction` is missed 0/20 and is left
frozen and failing. The question is whether `should_link = true` is right at all
here, or whether a referential contradiction against a multi-claim pool is
ambiguous in G2's sense. Nothing changed either way.

---

## Resolved at review, and built (2026-09-18)

**CO1 — approved as proposed.** Migration 5 drops and recreates `supersedes` at
message granularity, cycle guards rewritten against the new columns. Destructive,
on a table with no production rows, ahead of a wipe.

**CO2 — approved as proposed.** `db.get_messages_in_chunk` resolves the range by
timestamp window. The endpoints are exact — they are named by id — so only the
interior relies on the window, which is why a same-second collision between
interior messages is harmless.

**CO3 — RESOLVED: tie "recent" to chunking's own boundary.** No new constant.
`chunking.open_group_messages()` returns the open trailing group, and the
correction classifier's candidate set uses it. One definition of "not yet
sealed", in the module that owns sealing. *It is not an entry point — it writes
nothing — so the pinned two-entry-point test still holds.*

**CO4 — RESOLVED: the entity may not correct the user.** Recorded with the
reasoning, because the reasoning is the part that will matter later: this is not
about denying a useful capability. It is about **not letting an automated
classifier's inference override a human's explicit self-report about their own
words.** An accurate raw record quietly informing retrieval is one thing; a
generated judgment disputing what a person just said about themselves is another.
Self-correction stays symmetric only in the sense of *same speaker corrects
self*; entity-corrects-user is out of scope. Enforced by construction in
`corrections.candidates()` and again by role parity in `classify()`.

**CO5 — RESOLVED: dropped, not resolved to a best guess.** A reply naming more
than one candidate has not obeyed the grammar, so its judgment is not trustworthy
enough to write; the turn records **no link** and logs that it happened, so 3.4
can see the rate. Guessing "the most confident" would invent a ranking the reply
does not contain.

**CO6 — confirmed intended.** Cross-conversation corrections work as designed:
candidates drawn from retrieval already span conversations.

**CO7 — RESOLVED: attach/annotate, never suppress.** Message granularity makes
suppression cheap, and it is still not taken. **Transparency is the reason, not
cost:** a correction that happened should stay visible, and the original should
not silently vanish from what retrieval surfaces. Task 3.5 implements that.

### One deviation from C9, found in implementation

C9 said the link write would fold into the transaction that saves the assistant
message, and concluded from that that this adds **no new write path**. **The
implementation does not do that**, and the claim needs correcting: the link is
written by `db.create_supersedes_link()` in its own short transaction, after the
assistant message is saved.

The reason is ordering. A self-correction's superseding message *is* the answer,
so its id does not exist until the answer is saved — and the classifier needs the
answer's text to judge it. Folding the write in would mean either classifying
before the answer exists (impossible) or threading an optional link through
`save_message`, which would put correction semantics into the lowest-level write
helper for the sake of a transaction boundary.

**So there is one additional small write inside a turn.** The `db.py` contention
recommendation stands where it was — recommended, not blocking — but the "no new
write path" justification for that verdict is withdrawn; the honest version is
that one extra single-row insert inside a turn is a small increase in the window
the backup race test is sensitive to.

---

## Task 3.4: C11 met, and the one place it could not be met as written

`eval/corrections/cases.toml` (14 cases), `program/integrity/correction_eval.py`,
`python -m scripts.correction_eval`, `tests/test_correction_eval.py` (50).

**Measured, 2026-09-18:** first freeze, 14 cases, 5 decorrelated passes and then
20 — 280 samples, 0 false links, 0 misses, 0 wrong targets. **Re-measured after
CO8** (15 cases, fingerprint `b2ba7658…`, and after the `_parse` fix that CO8
exposed): **300 samples, 0 false links, 0 misses, 0 wrong targets**, every case
unanimous at 20/20. The second run is the measurement of record. Decision #22's escalation was not triggered (nothing was non-unanimous);
the 20-pass run was voluntary, because a 0-error headline off 70 samples on a set
written by the implementer is worth stressing before it is reported.

**What that does and does not establish.** It establishes that the classifier
handles the shapes C11 names, and gives 3.5 a regression floor. It does not
establish production accuracy: 14 cases is a handful, and a four-case diagnostic
probe outside the freeze already found a fifth shape where the classifier and the
design disagree (CO8).

**Three outcomes, not two.** A correction classifier can fail in a way a
flag/no-flag detector cannot: it can link **the wrong prior claim**. `false_link`,
`missed` and `wrong_target` are scored and reported separately, and a wrong target
is never a pass. Every positive case therefore names its expected target and
offers at least one distractor, and a test asserts the expected target is not
always in the same position — a classifier that always answered "1" would
otherwise score perfectly.

**Both users are in the set, in both directions.** Cases carry an optional
`speaker` field (default `Lyle`), which is what the classifier is told and is
therefore fingerprinted. `C5-jodie-correction` and `N7-jodie-elaboration` run the
same shapes under the other household member's name, so a per-user false-link rate
has a denominator rather than Jodie appearing only where a link is expected.

### C11's Q16 case has no entry, and the reason is sharper than "C12 enforces it"

C11 asked for *"a case asserting that Jodie correcting a claim from Lyle's
conversation produces no link (Q16), since nothing in the classifier itself
enforces that."* **That case is not expressible through `corrections.classify()`**,
which is the single entry point this harness is allowed to call:

`_render()` labels every user-role candidate with **the one speaker name it is
given**, because `candidates()` has already filtered the pool to a single user
before rendering. There is no slot in the prompt for "this earlier message is from
someone else." A cross-user pool built by hand would therefore not be the prompt
production can produce, and any rate measured from it would describe a prompt that
does not exist.

Measuring it end to end would mean the harness building a two-user store and
calling `candidates()` — which makes an eval harness a database writer, and the
gate's harness holds the opposite rule deliberately.

**So Q16 is proved by construction instead of sampled**, against a real two-user
store, in
`tests/test_corrections.py::test_the_other_household_member_is_never_a_candidate`.
A proof is stronger than a rate; what is lost is only that the number does not
appear in this report. Recorded here because C11 asked for the case and it is not
there.

---

## CO9 (open): does a *referential* contradiction have a referent the classifier can pick?

Raised by stage 2's measurement, 2026-09-19. `C7-referential-contradiction`
(*"That's not right."*) was added to close the single-phrasing limitation left by
CO8 — `C6` contradicts by restating the fact it denies, so it can be matched on the
fact named, while `C7` carries no claim content at all. **It is missed, 0/20.**

**The failure is referent selection, not recognition.** The classifier replies:

```
CORRECTS 1, 2 CONTRADICTED
- The new message states that the previous statements are not right, flatly
  contradicting them without providing new values.
```

It identifies the contradiction and labels it correctly. It then attaches the
**singular** *"that"* to every candidate in the pool, and CO5's multi-candidate
guard — working exactly as designed — writes no link.

**The case's own premise is refuted, and that is recorded in the case file rather
than quietly fixed.** It was authored expecting the referent to be fixed by content
(p1 is an intention about the future and cannot sensibly be called wrong; p2 is a
checkable claim) as well as by recency. The classifier uses neither signal.
Ordering is not the cause either: it names both whichever way round they are
rendered.

**The open question is whether `should_link = true` is even right here.** A
referential contradiction offered a multi-claim pool may be genuinely ambiguous in
G2's sense, in which case no link is the correct outcome and the case's expectation
should flip. Against that: *"that"* is singular where *"both of those"* is plural,
and a person saying it means one thing. The two readings imply different fixes —
one narrows the expectation, the other says the classifier should resolve a
singular reference — and **nothing was changed either way**. The case stays frozen
and failing.

*Note that the safe behaviour held throughout: the failure is a miss, not a wrong
link, because CO5 refused to guess. That is the asymmetry this mechanism was built
around doing its job.*

---

## CO8's second-order finding: CO5 was enforced against a reply the model never writes

Broadening C5 exposed a latent defect in the 3.3 implementation. It is recorded
here rather than only in a changelog because it is a lesson about how a constraint
was verified, not just a bug.

**What happened.** `G2-ambiguous-two-claims` (*"Both of those were wrong."* against
two claims) had passed 20/20 under the narrow definition — because the model
answered `NONE`, the replacement value being absent. Under the broadened
definition it answers, and the frozen harness reported **false links 20/20**,
always to candidate 1.

**The cause was not the classifier.** Its replies were:

```
CORRECTS 1, 2
- The dentist appointment time/day and Jodie's train arrival time are both
  invalidated | The message flatly contradicts both earlier statements…
```

It named **both** candidates, which is precisely what CO5 says must write no link.
`corrections._parse()` failed to notice: the pattern captured one number per
`CORRECTS` **match**, and `CORRECTS 1, 2` is one line and one match — so a reply
naming two claims was read as a confident verdict for the first, and candidate 1
was linked. Position bias in the record, produced by the parser rather than the
model.

**Why the original test did not catch it.** It scripted
`"CORRECTS 1\n- a | b\nCORRECTS 3\n- c | d"` — two separate lines. That is the
shape *the grammar implies* and not the shape *the model uses*. The guard was real
and the test was honest; both were written against an invented reply. **This is
the same failure mode as the gate's `S6` "happens to pass": a constraint verified
against a form that does not occur is not verified.**

**Fixed** by parsing the whole leading number list (`1, 2` / `1 and 2` / `2,1`),
with the trailing alternation stopping at the first non-separator so digits in a
same-line rationale are not swept in — a test covers that opposite failure, since
sweeping them in would turn a valid verdict into a miss. Proven to bite: restoring
the old behaviour fails three tests.

**It is a fix, not a tune.** CO5 was approved before 3.4 existed and the fix is in
*reading what the model said*, not in changing what counts as a correction. The
frozen case was not edited, and `G2`'s expectation is what it always was.
