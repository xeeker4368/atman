# O23 part 2: wording iteration abandoned, receipt direction recorded

Date: 2026-09-30 · documentation only, decided at review. No code, prompt, harness or frozen
change. Follows `2026-09-30-o23-action-doc-corrections.md` (committed as `4048ed4`).

## What was decided

- **No further wording work on the ACTION trigger.** `_PROMPT` stays as it is. The trigger
  remains a flag-only, best-effort secondary signal, and `A7` remains a documented miss.
- **The person-facing safety property becomes a system-written receipt**, shown whenever a
  side-effect tool (`image_generate`, `creative_write`) actually ran. Design pending; nothing
  built.

## Why

Recorded in full in `docs/FABRICATION_GATE_DESIGN.md` F50. In short:

- A proposition-judged rewording raised catches on 13 new fabrication phrasings from 2/13
  to 9/13, with 0/200 control false positives for both.
- It newly missed `A2` and a save claim written after inline text, 0/20 each, where the
  current wording catches both 20/20.
- **The decision was made on the kind of failure, not on the rate.** Silent misses on the
  plainest form of the claim are worse than an over-literal trigger. Continued tuning
  against the same hand-built set would measure the phrasings, not the judgment.

## Tested

- These are measurements, not code: live `gemma4:26b`, the empty trace, both prompts
  interleaved in one run, 20 decorrelated passes. Also a 5-pass screen of the rewording over
  the frozen 41. The rewording was swapped in-process only.
- The scripts and raw per-call results are in the session scratchpad, not the repo.
- Not measurements of record, and not a production rate: every phrasing was invented or
  taken from the soak store.

## Follow-up

- The receipt design pass, including a possible conflict with decision #10's
  "private by default", which has to be settled before building.

## Addendum: decision #10 clarified (same day, at review)

- Receipts cover `creative_write` as well as `image_generate`, existence only.
- Recorded in F50 and as a dated note under `NOW.md` decision #10 (now `docs/DECISIONS.md`): the decision governs the
  entity's discretion over the **content** of its work, not the mechanical fact that a write
  occurred. This clarifies #10; it does not narrow it.
- `creative_write`'s *"Nothing shows it to anyone"* result text is to be replaced with an
  accurate statement when the receipt lands, not merely deleted.

## Addendum: B19 filed (same day, at review)

- `invented_id` flags a genuine artifact id, which shares the 32-hex call-id shape. The
  defect is latent: 0 of 12 real side-effect turns quote an id.
- Filed as **B19** (Tier 3, gate-rule change) in `NOW.md`'s backlog (now `docs/BACKLOG.md`), with a known-defect
  line in `BUILT.md`.
- It depends on the receipt task's `artifact_ids` trace key, and is deliberately not
  bundled into it.
