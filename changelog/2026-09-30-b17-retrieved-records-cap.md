# B17: the retrieved-records block has a cap

Date: 2026-09-30 · queue item B17 · Tier 3 (it changes what reaches the prompt). Plan
approved at review: 51,000 from B6a's figures, continuation pieces dropped before ranked
hits.

## What was wrong

Ranked hits are bounded individually (10, each at most `embedding.max_input_chars`, 5,000
characters). But `retrieval._attach_siblings` adds up to `max_siblings_per_hit` (3) more
pieces of the same long message per hit, and nothing bounded the rendered block. At the
extreme, 10 x 4 x 5,000 is about 200,000 characters, past the whole 32,768-token window on
retrieval alone. The history window logged `overflowed` and nothing prevented it.

B6a's derivation of `chat.max_message_chars` assumed retrieval takes at most 51,000
characters and said so: *"NOT covered: split-sibling attachment…"*.

## What changed (`program/engine/prompt.py`)

- **`retrieved_records_max_chars()`** = `top_k x embedding.max_input_chars +
  RECORD_HEADER_ALLOWANCE_CHARS` (1,000), from live config: **51,000**. It is B6a's
  figure, so the derivation is now true instead of assumed. The header allowance is
  B6a's own estimate, carried over and labelled.
- **`render_retrieved()`** renders every ranked hit, then gives what room is left to
  continuation pieces:
  - in their parent's rank order, whole pieces only;
  - within one hit, stopping at the first piece that does not fit, so a later piece
    never appears without the one before it;
  - whatever is left out is **counted** in a closing line: *"[N further continuation
    pieces of long records were left out to keep these records within their size
    limit.]"*
  - Ranked hits are never dropped for size, and all `top_k` fit by construction.
  - The layout (siblings under their parent) is unchanged. Correction annotations keep
    their own RO4 bound.
- The same renderer serves passive retrieval and `memory_search`, so a chunk still reads
  the same whichever way it was retrieved.
- **`tests/test_turn.py`:** B6a's derivation test uses `prompt.retrieved_records_max_chars()`
  instead of its own copy of the arithmetic. **`config/defaults.toml`:** the "NOT
  covered" note is replaced by one saying B17 covers it.

## Tests (`tests/test_retrieved_cap.py`, 8)

- The cap equals B6a's figure from live config.
- A constructed case that would overflow (uncapped over 190,000) is bounded to the cap.
- Every ranked hit survives, and continuations go first.
- What is left out is counted exactly.
- Continuations are taken in rank order.
- A later, smaller piece never appears without the one before it.
- **An ordinary result WITH siblings attached renders byte-identically to before.** As
  asked at review, this is not only a no-siblings case. The expected string was produced
  by `render_retrieved` at `3884c08` (pre-B17), loaded from git into its own module, on
  the same input, and checked equal. So the test is genuinely at risk of catching a
  layout regression.
- No result is still no block.

## Measured

On the pathological case, the pre-B17 renderer produced **201,922 characters**. With the
cap it is **50,617**.

## Proven to bite

| mutation | tests that fail |
|---|---|
| cap disabled | 4 |
| sibling marker changed (layout regression) | 2, including the byte-identical test |
| a later, smaller piece allowed to skip ahead | 1 |

The third mutation **passed the first version of the suite**: the "never skip a piece"
rule had no test. The test for it was added after that, and it now fails on the mutation.

**Full suite:** 1,318 passed and 2 skipped (1,310 before); `ruff` is clean.

## Known limitations

- The cap bounds records, not the whole block. The fixed header, the "check did not
  complete" note and the closing lines sit outside it, a few hundred characters. The
  annotations keep their own separate bound, about 4,600 characters.
- The 1,000-character header allowance is an estimate (B6a's). Ranked headers use about
  60–80 characters each, so ten fit easily, but it has not been measured against a real
  pathological store.
- A retrieved *document* chunk has no siblings (its message-id columns are NULL), so
  this changes nothing for uploads.
