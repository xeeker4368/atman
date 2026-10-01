# 2026-10-01 — Notes design revision 2: quote resolution, and B20 before Notes

Pre-review edits requested at review 2026-10-01. **Design only.**

## What changed in `docs/NOTES_DESIGN.md`

- **N4: how a quote resolves to a message id when it is short or matches many.**
  1. Normalise both sides (whitespace, quote characters, case); nothing fuzzier.
  2. A minimum of 24 characters and 4 words (judgment values), or it is refused before
     any search.
  3. The turn's context is searched first. `OriginContext.context_message_ids` covers
     the conversation plus the messages behind this turn's retrieved chunks. The rest
     of the store is searched only if the quote matches nothing in context.
  4. Uniqueness is required within the tier where it matched; ambiguity across
     different texts is refused.
  5. **One exception, for review:** identical full messages (the soak store repeats
     one image request three times) are the same evidence, so the most recent is
     recorded, with the count shown to the reviewer.

  This mirrors the gate's rule for identical sentences. Revision 1's "record every
  match" is withdrawn, with the reason given.
- **N3:** `OriginContext` gains `context_message_ids`.
- **N13:** the evidence column becomes per-quote `{quote, message_id, tier,
  identical_count}`.
- **N16:** the build owes quote-resolution tests.
- **N12 and N17: B20 lands before the Notes tools, as a hard ordering.** B20 is built
  and under review.
- **N18:** three new gaps:
  - normalised exactness;
  - the duplicates exception attributes evidence to one of several identical messages;
  - the turn's context is approximated by a superset.
