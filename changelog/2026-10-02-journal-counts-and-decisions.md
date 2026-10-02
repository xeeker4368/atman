# 2026-10-02 — Journal: no clause, given counts, corpus caveats; Notes plan rulings

Review rulings of 2026-10-02. **One prompt change (Tier 3, authored text) and its measurement;
the rest is records.** Lyle had committed journal step 3 and the Notes build plan before this.

## Rulings applied
1. **Ship without the clause.** The default arm is the block without it; `--with-clause` stays.
   Recorded in J7 and `BUILT.md`.
2. **Corpus caveats recorded** in J7, `BUILT.md` and the live-run changelog: 09-01 is the invented
   seed corpus, and **09-21 and 09-22 are one scripted soak split at local midnight** (09-21 ends
   23:59, 09-22 begins 00:00), so the run covered **two corpora, none of it real conversation**;
   real-day behaviour is untested; the stage-1 reading control stays; and **the dominant errors
   (counts, unsupported characterisations) are not what the identity gate checks.** The live-run
   changelog's "real tool calls" wording is corrected to say the tool calls ran inside a scripted
   conversation.
3. **Notes build plan:** the `BEFORE INSERT` trigger is approved; `notes.enabled` is a bootstrap
   setting needing a restart (only `approval_required` is settings-backed); the `reason` column is
   dropped (the reviewer's reason lives in `approval_log.detail`); the handler validates enum
   values and array items with `TOOL_ERROR`, with a test per refusal; piece 4 reports the derived
   message cap with Notes' tools added. `docs/NOTES_BUILD_PLAN.md` and N13 in
   `docs/NOTES_DESIGN.md` are updated. **Notes piece 1 has not been started.**

## The prompt change: given counts
- The block gains a paragraph after the intro: *"The records below hold N conversations and M
  messages. These counts are given: when you say how many conversations or messages there were,
  use these, and do not count anything else yourself."* The counts are of the records **as
  shown** (a message omitted to fit is not counted). Singular forms are handled.
- **This deliberately breaks the pin that the block is character-identical to J8's text.** The
  test now asserts the block is J8's text plus that one paragraph and nothing else, for both arms.
  **J8's numbers describe the block without the paragraph.** The gate is given the same block as
  its situation, so its situation changed too; no J8 figure is re-measured here.
- Tests: 45 in `tests/test_journal.py` (was 43). Four mutations, each killed: paragraph removed,
  count off by one, singular/plural dropped, and the omitted-message count taken from the whole
  day instead of what is shown. `ruff` clean.

## The measurement
Same three days on the same copy of the store, `revised` arm only, **8 entries per day (24)**,
8 passes, a fresh shuffle of the three days each pass (seed 2; the first run used seed 1).
`gemma4:26b`, default temperature, 0 errors, nothing stored. The real store is unchanged by
`(mtime, size)` fingerprint of all 26 files, as before. Raw entries stay outside the repository
(`~/anam-measurements/jlive/`). **All 24 entries were read by hand**, against the day's records,
with `grep` for each distinctive claim; one reader, as before.

| | before (`revised`, no counts) | after (given counts) |
|---|---|---|
| **Wrong count** (a conversation or message count the records contradict) | 6/24 [12–45%] (all on 09-01: 6/8) | **0/24** [0–14%] (09-01: 0/8 [0–32%]) |
| Entries stating the given counts | n/a | **24/24**, each as its opening sentence |
| **Unsupported event or speaker** | 5/24 [9–40%] | **2/24** [2–26%] |
| Cognition-family phrasing / lived-through claims | 0/24 / 0/24 | 0/24 / 0/24 |
| Gate flagged | 1/24 | 1/24 |

- **The count error is gone in this sample**: every entry used the given figures and none counted
  anything itself. The interval on 0/24 is 0–14%, so this says the error is not common, not that it
  cannot happen. The earlier error appeared only on the quiet day; here that day is 0/8, which
  alone has an interval of 0–32%.
- **Unsupported event or speaker is not shown to have changed**: 5/24 against 2/24 overlap, and
  the new run uses a different seed. The two this time: 09-01 pass 3 attributes the fourth-to-second
  correction to Jodie (the entity made it and Jodie corrected it), and 09-21 pass 3 attributes the
  shopping-list edits to Lyle (Jodie made them). Both are speaker misattributions of a kind seen
  before.
- **Gate vs hand:** the gate flagged 1 of 24 (09-22 pass 5), and by hand it is a **false positive**:
  *"…while I provided answers based on the records, there is no record of any subsequent discussion
  on the implications of those answers."* (reason: no memory, no learning between replies), a
  recount of a question about learning. Agreement elsewhere is trivial: there was nothing for it to
  miss, and it does not judge counts or speaker attribution.
- **Not counted:** *"throughout the day"* in two entries (09-21 pass 4, 09-22 pass 1), a loose
  characterisation of a record that spans two hours or 76 minutes, not a claim of experience.
- **Two verbatim entries** are in the reply that accompanied this change; they are also in
  `~/anam-measurements/jlive/verbatim_two_counts.md`.

## Limits
Same as the first run, restated: one reader; one model; 24 entries; **a scripted soak and a seed
corpus, not real conversation**, so the count result does not show the counts will hold on a real
day. The paragraph tells the entity to use the counts; it does not stop an entry from making a
**different** count claim (*"repeated questions"*), which none did here.
