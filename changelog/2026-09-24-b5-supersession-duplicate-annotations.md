# B5: one correction of a split message, one annotation per piece

Date: 2026-09-24 · merged-queue item 9 (diagnostic finding #13) · plan B5 · Tier 3
(retrieval respecting the supersedes link). Plan approved before implementation.

## What was wrong

`db.get_supersedes_for_chunks()` returns one row per (chunk, link). When the corrected
message had been split across N sibling chunks, one correction therefore arrived as N
rows. `supersession.resolve_for_chunks()` built one branch per **row**, producing N
identical tips, and then attached every tip to every chunk the message appears in.
That is N² annotations for a single correction.

Measured in the diagnostic pass, with 3 siblings:

- 3 annotations per chunk, 9 in total;
- `links_followed = 3`.

Consequences:

- `MAX_PER_CHUNK = 3` rendered three identical "later corrected" lines per piece.
- 9 of the 12 global annotation slots went on one correction. RO4's "shorten before
  dropping" spent its budget on repetition, while genuine corrections on lower-ranked
  records were pushed into the closing count.
- `test_siblings_are_annotated_too` built exactly this state but asserted only
  `len(by_chunk) == len(chunks)`.

## What changed

In `program/memory/supersession.py` `resolve_for_chunks()`:

- The first-hop loop still records every chunk the corrected message appears in.
- It now builds a branch, and counts a followed link, only once per distinct
  `(corrected message, correcting message)` link.
- Result: one tip per correction, attached to each sibling chunk once, which is N
  annotations for N pieces.
- Two genuine corrections of the same message remain two branches (R3: every tip is
  rendered).
- Chain following, the cycle and depth guards, and rendering are untouched. Ranking is
  unaffected, because resolution runs after fusion.

## Tested

- **`test_siblings_are_annotated_too`, tightened.** Each piece now carries exactly the
  one correcting message, `links_followed == 1`, and `annotations == len(chunks)`.
- **New: `test_two_corrections_of_one_split_message_each_appear_once_per_piece`.** Two
  corrections of one split message appear on every piece, once each, with
  `links_followed == 2` and `annotations == 2 × pieces`. This guards against
  over-deduplicating by corrected message alone.
- **Proof it bites:** the previous `supersession.py` fails both.
- `test_supersession.py`, `test_retrieval.py` and `test_prompt.py` pass (124).
- Full suite **1203 passed, 2 skipped** (was 1202/2). `ruff check .` clean.

## Known limitations

- **A correction of a split message still appears once per sibling piece.** When
  several pieces are rendered, the reader sees it on each. That is the design's intent
  (the test is named for it), and it is now N rather than N². Collapsing it to a single
  piece would be a rendering-design change, not this fix.
- **Not re-measured live.** No real corpus holds corrections yet (already recorded in
  BUILT.md).
