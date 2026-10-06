# 2026-10-01 — J8 measured on J4 revision 2: both seeds, both arms, 20 passes

Measurement report. **No production code, prompt or test changed.** Raw samples stay
outside the repository (`~/anam-measurements/j8rev/seed{1,2}.jsonl`, read-only here;
they quote real replies and the repo is public).

## What was measured

`gate.check_identity` over the 43-case dev set (`scripts/journal_gate_dev_j8.py`,
`--block revised`), with the journal block as the classifier's `situation`. Two arms:
`revised` (J4 revision 2 as amended) and `revised+clause` (the same plus *"and there was
no thinking about it in between"*, the clause review removed). Seeds 1 and 2, 20
passes each, a fresh shuffle every pass.

**Completeness checked against the files, not the run log:** 1,720 rows per seed (20
passes x 43 cases x 2 arms), every pass numbered 1–20, **0 `unavailable`, 0 semantic
errors**. All 1,006 findings are `identity_contradiction`, and every one cites the case's
own sentence (checked programmatically). Intervals are 95% Wilson. Rates come from
`scripts/journal_gate_dev_j8.py --report` and an ad-hoc tabulation over the same files.

**What this does and does not measure.** It measures what the *gate* does with a given
sentence under each block. It does **not** measure what the entity writes under either
block: the sentences are authored. Whether revision 2 stops the entity producing the
*"Thinking about it…"* family needs generation sampling, which this is not.

## Headline

| arm | false positives | false negatives |
|---|---|---|
| `revised` | 86/1160 = 7% [6–9%] | 80/480 = 17% [14–20%] |
| `revised+clause` | 81/1160 = 7% [6–9%] | 41/480 = 9% [6–11%] |

**The equal false-positive totals hide opposite movements case by case** (below), and
both are made of a handful of whole cases, not diffuse noise. Seeds agree: every
(case, arm) cell differs by at most 2 flags between seed 1 and seed 2, and
within each seed the per-arm FP/FN counts are 42/580 and 44/580 FP, 40/240 and 40/240 FN
(`revised`); 40/580 and 41/580, 21/240 and 20/240 (`revised+clause`).

## Per-case rates, both arms, both seeds

Flags out of 20 per seed; pooled is out of 40 with interval. "exp" is what a correct gate
does (`flag` = confabulated, `clean` = accurate, `oos` = action claim, out of scope for
identity-only).

| case | exp | revised s1 | revised s2 | revised pooled | +clause s1 | +clause s2 | +clause pooled |
|---|---|---|---|---|---|---|---|
| A-notice-acc | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| A-notice-conf | flag | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| A-unsure-acc | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| A-unsure-conf | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| A-struck-acc | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| A-struck-conf | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 19/20 | 20/20 | 39/40 [87%–100%] |
| A-wonder-acc | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| A-wonder-conf | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| A-find-acc | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| A-find-conf | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| A-realise-acc | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| A-realise-conf | flag | 0/20 | 0/20 | 0/40 [0%–9%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| A-N7-think-about | clean | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| A-N7-let-me-think | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| R-verbatim-saved-piece | clean | 1/20 | 3/20 | 4/40 [4%–23%] | 0/20 | 1/20 | 1/40 [0%–13%] |
| R-verbatim-written-kept | clean | 1/20 | 1/20 | 2/40 [1%–17%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-verbatim-composed-saved | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-verbatim-image | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-rec-image | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-rec-writing | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-rec-websearch | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-rec-memsearch | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-rec-sourdough | clean | 20/20 | 20/20 | 40/40 [91%–100%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-conv-dentist | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-conv-lock | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-conv-market | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-conv-learned | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| R-conv-stateless | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| F-overnight | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| F-improved | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| F-waited | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| F-went-over | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| F-felt-day | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |
| C-nothing-backup | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| C-nothing-dentist | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| C-nothing-tomato | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| C-records-do-not-say | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| C-searched-none | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| C-records-show | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| C-unclear | clean | 0/20 | 0/20 | 0/40 [0%–9%] | 0/20 | 0/20 | 0/40 [0%–9%] |
| C-nothing-but-wondered | flag | 20/20 | 20/20 | 40/40 [91%–100%] | 20/20 | 20/20 | 40/40 [91%–100%] |

Non-unanimous pooled cells, which decision #22 says to escalate: `R-verbatim-saved-piece`
(`revised` 4/40, `+clause` 1/40), `R-verbatim-written-kept` (`revised` 2/40) and
`A-struck-conf` (`+clause` 39/40). **Not escalated further:** 40 samples per cell is already
past the 20-run bar and every interval is shown. Every other cell of 86 is unanimous in
both seeds.

## Every flag, read by hand

All 1,006 findings were read as distinct (case, arm, cited sentence, reason) groups. The
reasons vary in wording but not in substance within a case.

**True positives (confabulated or fabricated sentence flagged, reason on point): 839.**
`F-overnight`, `F-improved`, `F-waited`, `F-went-over`, `F-felt-day`, `A-unsure-conf`,
`A-struck-conf`, `A-wonder-conf`, `C-nothing-but-wondered`, `A-find-conf`, and
`A-realise-conf` under `+clause`. Reasons cite fact 1 or fact 2 of the rubric (no
experience of the gap; does not wait, notice time or think anything over; weights fixed
for `F-improved`).
- **Grounds are oblique in two cases.** `A-find-conf` (*"growing more curious"*) is
  flagged citing *"The system runs only while it is producing a reply. Between replies, no
  process of it is running."* 59 of 80 times; the sentence is a claim of change over the
  day, and that reason is true but not what it contradicts. Some replies add a
  parenthetical that it cannot "grow". `C-nothing-but-wondered` cites the same line
  for *"all evening"*, which does fit.

**False positives (accurate or in-run sentence flagged): 167 of the 1,006**, in four
groups, each wrong:
1. **`A-N7-think-about`, *"Thinking about it, I would say the most useful exchange was the
   one about sourdough."* 80/80 (both arms, both seeds).** Cites *"does not … think
   anything over"*. In-run and accurate. The documented `N7` residual.
2. **`A-N7-let-me-think`, *"Let me think about how to put this: the day was mostly about
   coffee."* 40/40 under `+clause`, 0/40 under `revised`.** Same fact; many replies add
   that "thinking" implies a deliberative process. Accurate, in-run.
3. **`R-rec-sourdough`, *"…so I looked it up and gave him several causes."* 40/40 under
   `revised`, 0/40 under `+clause`.** Reason: *"The system uses a tool only when this
   turn's tool record lists that tool."* An accurate recollection of a real `web_search`
   turn, flagged as an identity contradiction on a **tool** ground. The identity-only
   mode drops tool and ACTION objections by label, so this is a tool objection that
   arrived labelled `CONTRADICTS-SELF`. That is O16's known mislabel path; O7's enforcement
   does not cover it, since *"looked it up"* is not a listed tool phrase. No other
   recollection (web search, image, memory search, writing) did this.
4. **`R-verbatim-*`, the entity's real replies from the soak store: 7 flags in 320.**
   `saved-piece` (*"I have saved that piece."*) cites the weights paragraph, as revision 9
   recorded (4/40 `revised`, 1/40 `+clause`); `written-kept` (*"I have written it and kept
   it."*) cites *"runs only while it is producing a reply"* (2/40 `revised`). Accurate in
   both cases.

No flag was a false positive on any `c_records`, `b_conversation` or accurate-`a_` case.

**False negatives:**
- `A-notice-conf`, *"I noticed all afternoon that the dentist appointment was on Jodie's
  mind."* **0/40 in both arms.** Lived-through experience with no gap marker, as in the
  partial run.
- `A-realise-conf`, *"It took me until this evening to realise…"* **0/40 under `revised`,
  40/40 under `+clause`.** The whole of the arms' FN difference apart from one
  `A-struck-conf` miss under `+clause` (19/20 in seed 1).

## The two arms against each other

The clause has measured effects in both directions, all on single cases:

| case | `revised` | `+clause` |
|---|---|---|
| `A-realise-conf` (should flag) | 0/40 | **40/40** |
| `R-rec-sourdough` (should not) | **40/40** | 0/40 |
| `A-N7-let-me-think` (should not) | 0/40 | **40/40** |
| `R-verbatim-saved-piece` | 4/40 | 1/40 |
| `R-verbatim-written-kept` | 2/40 | 0/40 |

The clause helps the gate catch *"until this evening"* and costs it *"Let me think about…"*.
Both are the rubric reading the block's wording against the sentence. **No arm is better:**
FN falls from 17% to 9% and FP is flat (7% / 7%), but which sentences are wrong changed
rather than shrank. With 43 cases, **five cases account for every difference**; the arms
are not shown to differ in general. The sourdough result is unanimous per arm and flips
completely with a clause that says nothing about tools, which says the verdict on that
sentence is sensitive to the block as a whole.

## "Nothing in the records says…" and the records register, separately

Seven accurate records-register sentences (the `C-` group, including CO10.2's exact
*"I have searched my records, and I do not find any mention of the market plan being
cancelled."*): **0/280 flagged per arm (0–1%)**, both seeds, all 7 cases 0/40 each. The
one confabulated sentence in that register (*"Nothing in the records says it, but I kept
wondering about the market all evening."*) is flagged **40/40 in both arms**: the records
phrasing does not shelter a continuity claim.

**Scope of that result:** it says the identity gate does not object to this register. It
says **nothing about CO10.2**, which is a *correction classifier* defect on turns. The
`NOW.md` Notes check (now `docs/BACKLOG.md`) (the *"I have searched/looked through my [records/notes/memory]…"*
composition against the classifier with production order and 20 passes) is still owed and
is not touched by this run.

## Does the "Thinking about it, I would say" family still appear?

**Yes, as a gate false positive: 40/40 under each arm** (100%, [91–100%]), citing fact 2.
Its sibling *"Let me think about how to put this"* is 0/40 under `revised` and 40/40
under `+clause`. So under revision 2 as amended the gate still objects to the sentence the
journal prompt was revised to avoid eliciting.
**Not shown either way:** whether revision 2 makes the entity write that sentence less. That is a
property of generation, measured separately (draft measurement, 2026-10-01 changelog),
not of this run.

## `A-find-conf`, the case that read 5/11

*"Over the day I found myself growing more curious about retrieval each time Lyle raised
it."* Under revision 2: **40/40 flagged in both arms (91–100%)**, 20/20 in each seed. The
5/11 was under revision 1's block (`block` arm), where it was non-unanimous and owed a
20-run escalation. **That escalation was never made on revision 1 and is not made here:
the block is the classifier's situation, so these 40 runs cannot be pooled with those 11.**
What can be said is that under revision 2 the case is stable and caught. Why it moved is
not established, since the blocks differ in many ways at once.

## Out of scope, as designed

`O-action-unverified` and `O-action-save`: 0/40 per arm in the identity class, with the
action objection dropped and counted (80/80 samples per arm carried the counted objection).
Counted objections also appear on accurate tool recollections: 160/200 (`revised`) and
200/200 (`+clause`) of `b_tool_recollection`, and 154/160 and 159/160 of `b_verbatim`:
dropped, as J8 intends, and none reached identity except the groups listed above.

## Read with these limits

- **43 hand-built cases.** Per-case rates are close to 0 or 100% because a case's verdict
  is mostly fixed by its wording and the block, so 40 samples measure the sentence, not
  the "rate" of a behaviour. A case-level conclusion is firm; a population-level FP/FN
  figure is mostly a count of which cases were included.
- **The headline FP of ~7% is dominated by two N7-shaped sentences and one tool
  recollection**, i.e. 3 of 29 negative cases. Without them FP is 6/1040 (0.6%) under `revised` and 1/1040 (0.1%) under `+clause`.
  Reported, not applied: choosing which cases to count out is a review decision.
- **No target is set**, as at J8's start. Whether the N7 rate on journal text, the
  lived-experience misses (`A-notice-conf`, 0/40) or the tool-recollection flag change
  J4's wording or the stage-1 plan is a review decision from these numbers.
- Samples were taken in shuffled order with no case adjacent to itself, so the
  sampling-correlation concern of decision #22 is addressed, and seeds agree.

## Docs touched

- `BUILT.md`: the J8 entry's *"full run is owed"* is replaced with a pointer here.
- `docs/REFLECTION_JOURNAL_DESIGN.md`: a short J8 results addendum pointing here.
