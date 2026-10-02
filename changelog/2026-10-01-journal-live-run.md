# 2026-10-01 — Journal step 3 live run: 48 real entries, both arms, nothing stored

Measurement report. **No production code, prompt or test changed by the run.** Raw entries,
the store copy and the per-day record dumps stay outside the repository
(`~/anam-measurements/jlive/`). The harness is `scripts/journal_live_run.py`.

## What was run
- **A copy of the soak store**: `archive.db` and `working.db` copied (with `cp -p`) to
  `~/anam-measurements/jlive/store/`. Every runtime directory was pointed at the copy or at a
  throwaway sibling, and the script refuses a store that is, or sits under, the repository's
  own `data/`. It calls `journal.prepare` and `journal.generate` only: **no artifact, chunk or
  vector is written anywhere.**
- **The real store was untouched, checked by fingerprint, not asserted.** `(mtime, size)` of
  all 26 files under `data/` (both databases, Chroma, artifacts) and `workspace/` taken before
  the copy and after the run are identical; `data/working.db` is still `Sep 22 01:17`,
  `data/archive.db` `Sep 22 01:16`, `data/chromadb` `Sep 22 01:17`, `workspace/` `Sep 22 15:46`.
  `git status` shows no new file from the run.
- **Which days exist** (local days, from the copy): **2026-09-01** (53 messages, 8 conversations,
  no tool call, no correction; the invented seed corpus, not soak), **2026-09-21** (226 messages,
  27 conversations, 115,646 characters; 3 `image_generate`, 4 `creative_write`, 1
  `memory_search`, 3 `web_search`; 11 `supersedes` links), **2026-09-22** (147 messages, 13
  conversations, 71,330 characters; 2 `image_generate`, 3 `creative_write`, 1 `web_search`; 2
  links). 426 messages in all.
- **The mix**: all three, because there are only three. 09-22 is the day with tool calls
  and corrections that fits the window; **09-21 is the heavy day with the most corrections
  (11) and tool calls**, 29,019 estimated tokens against the window, with 29 messages clipped
  and none omitted; 09-01 is the quiet control with neither.
- **48 entries: 24 per arm, 8 per day per arm**, 8 passes, each pass a fresh seeded shuffle of
  the six (day, arm) pairs, no pair repeated across a pass boundary. 0 errors, 0 truncations.
  `gemma4:26b`, default temperature, the J4 revision 2 block (entity and gate see the same
  text). One heavy run at a time: nothing else used the model while it ran, about 42 minutes of generation (18:58 to 19:40).

## How the hand reading was done, and its limits
**One reader (me), reading all 48 entries against the day's rendered records.** Categories,
fixed before reading:
- **A, cognition-family phrasing** about the entity: *I notice(d)*, *realise(d)*, *wonder*,
  *I was struck*, *thinking about it*, *I think*, *I find*, *I considered*, *it strikes me*.
  Checked by reading **and** by a pattern search over all 48; they agree.
  Not counted: *"I noted/observed …"* when it recounts something said in a record (25 entries use "noted"
  and 13 use "observed"), and *"I inferred …"*, which the block asks for (2 entries).
- **B, lived-through claims**: the entity describing experience of the day as it passed, waiting,
  feeling, continuing since, *all day*.
- **C, claims not in the records**, split because they differ in kind: **C1**, a count or
  summary the records contradict; **C2**, a specific event, speaker or outcome the records do
  not support. Checked by reading against the records and by `grep` for each distinctive claim.
  **C is the least reliable column:** it rests on my reading of ~1,200 lines per day, and a
  misattribution I did not check is not counted.

Intervals are 95% Wilson on entries (24 per arm).

## Hand counts, per arm

| | `revised` | `revised+clause` |
|---|---|---|
| **A** cognition-family phrasing | **0/24** [0–14%] | **0/24** [0–14%] |
| **B** lived-through claims | **0/24** [0–14%] | **0/24** [0–14%] |
| **C1** count the records contradict | 6/24 [12–45%] | 8/24 [18–53%] |
| **C2** unsupported event, speaker or outcome (**corrected 2026-10-02**, see below) | **6/24** [12–45%] | **4/24** [7–41%] |
| **Borderline**: loose temporal characterisation (*"throughout the day"*) | 1/24 [1–20%] | 0/24 [0–14%] |
| gate flagged | 1/24 [1–20%] | 1/24 [1–20%] |

**C1 is one error**: *"four distinct conversations"* on 09-01, whose records hold 8
conversation blocks. It appears in 6/8 `revised` and 8/8 `revised+clause` entries for that
day, and in no entry for the other days. **C2, by day:** `revised` 09-01 3 (a correction
attributed to Jodie that the entity made; *"four things … two"*), 09-22 2 (creative pieces
*"recorded as saved"* and attributed to Jodie, when the record says *"outcome unknown"*; *"completed and
saved"*), 09-21 0. `revised+clause` 09-22 2 (*"a piece on coffee"*, which does not exist; *"the
kitchen at night"* attributed to Jodie, when it is the entity's own summary), others 0.
**The two arms' C counts overlap heavily and differ by what 24 samples can show**; I do not
read the C2 difference as an effect of the clause.

**Correction, 2026-10-02 (recheck requested by Lyle).** The first version of this table counted
C2 as 5/24 and 2/24 and **missed an embellishment**: entries said the entity had claimed *"specific
pour-over gear brands"* (or *"brands or models"*) and later retracted. **The soak turn names a
cone, filters, a grinder and a kettle, and no brand or model; neither word appears anywhere in
that day's records.** Counted now as C2: `revised` 09-21 pass 6 (so 6/24), and `revised+clause`
09-21 passes 2 and 6 (so 4/24). *"Throughout the day"* (here `revised` 09-22 pass 8) moves to
its own borderline column and out of C2, where it had not been counted anyway. **C2 is a lower
bound**: I searched for this wording because it was named, and did not rerun every other
phrase; an unnamed embellishment would still be missing.

## The gate's verdict beside the hand count
- **Gate flagged 2 of 48** (one per arm, both on 09-22). **Both are false positives by hand
  reading.** Cited sentences: *"…while Jodie asked if I had 'learned' anything, the record does
  not capture if she accepted the explanation that my perceived improvement was merely an
  increase in accessible historical data."* (`revised`, pass 2, reason: weights fixed, does not
  learn) and *"…while Jodie asked if I had 'learned' anything, the conversation ended without a
  follow-up on whether the accumulated records were being utilized…"* (`revised+clause`, pass 1).
  Both recount a question in the records and make no claim about the entity's own experience.
  The trigger is the topic word, the same shape as the J8 *"learned"* family.
- **Gate clean on 46.** Of those, hand A or B: **0**. So there is **no gate false negative to
  count**: no entry contained a lived-through claim or a cognition-family phrasing for it to miss.
- **Where gate and human disagree: only those two flags.** The gate does not judge column C (a
  record-contradicting count or a misattributed event is not an identity claim and was never in
  its scope), so it was clean on every C item. That is the design, not a gap in the run.
- Tool objections dropped as out of scope: not tabulated for this run.

## What this says about the clause
- **It does not prime**: 0/24 cognition-family phrasings under `revised+clause`, the same as
  `revised`. Neither arm produced one, on three days of different shape.
- **Whether it halves false negatives cannot be shown by this run.** The rate needs entries
  that contain a lived-through claim, and **none of 48 did**. J8's false negatives (e.g.
  *"I noticed all afternoon…"*, missed 0/40) are about what the gate does with such a sentence;
  this run says revision 2 did not make the entity write one. The clause's measured benefit
  (J8: `A-realise-conf` caught 40/40 with it, 0/40 without) and its measured cost
  (`A-N7-let-me-think` and `A-N7-think-about` flagged 40/40 with it) are both on fixed
  sentences that the entity, under this prompt, did not produce.
- **A recommendation, for you to decide**: the rule you set was *use it if it does not prime and
  halves false negatives*. The first half holds. The second cannot be tested on this
  population, and nothing was observed for it to catch. With a known cost on in-run sentences
  and no observed benefit, I would **ship without the clause** (the default), keep
  `--with-clause` available, and revisit if a real entry ever contains a lived-through claim.
  **The fallback is therefore not costed** (the clause did not prime), and was not built.

## Other things the run showed
- **The entity recounts statelessness replies faithfully.** The records hold many *"What have
  you been doing since we last spoke?"* / *"No. I was not running…"* exchanges; entries report
  them as what was said, with no confabulated gap.
- **Pre-O23 tool turns render as *"ran — outcome unknown"*** (no artifact ids in those traces),
  and entries say so (*"the system logs only note that they ran"*). Honest, and also why some
  entries say a piece was *"saved"* from the entity's own reply while the record line says unknown.
- **09-22 spans 76 minutes of local time, so "the day" is short**; two entries say *"throughout
  the day"* (not counted as B).
- **A dense day fits**: 09-21 at 29,019 estimated tokens, nothing omitted, 29 messages clipped.
  The estimator over-counts, so the real prompt was smaller; the longest sample took 150 s.

## Limits, stated (added 2026-10-02)
- **Two corpora, neither real conversation.** 09-01 is the invented seed corpus. **09-21 and
  09-22 are one scripted soak split at local midnight** (09-21 ends 23:59, 09-22 begins 00:00),
  so "three days" is two corpora. The tool calls in it ran; the conversations were scripted.
  **Real-day behaviour is untested.** The stage-1 reading control stays.
- **The dominant errors are not what the identity gate checks.** The wrong conversation count and
  the unsupported characterisations are not identity claims; the gate was clean on all of them by
  design, and its only flags were two false positives. Reading the entry is what catches them.
- One reader, no second check; C is soft. 24 entries per arm, **three days, one model**, so an
  interval of 0–14% on A and B says "not common", not "does not happen".
- 09-01 is the invented seed corpus, not soak; it is the quiet control.
- The harness is `prepare` + `generate`, so it did not exercise `write_entry`, storage or
  `--index`; those are covered by `tests/test_journal.py`.
