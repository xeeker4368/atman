# 2026-10-01 — Notes design revision 3: same-user duplicates, and six review amendments

Review 2026-10-01. **Design only.**

**Disclosed:** the review asked to confirm that revision 2 covered six earlier
amendments. **It did not.** That earlier message was never received in this session,
so revision 2 contained only the quote-resolution and B20-ordering changes. Of the six,
two were partly present (the `applied_without_review` decision value, and FTS triggers
indexing active rows) and four were absent. All six are added here, from the one-line
descriptions in the review.

## What changed in `docs/NOTES_DESIGN.md`

- **N4:** the identical-messages exception applies **only when every match belongs to
  the same user**. Identical text from different users is refused as ambiguous.
- **N6: an empty search is observable.** `result_count` goes on each `note_search`
  trace entry, and `scripts.note misses` reports empty searches. This is also the
  measurement N1's vector-leg trigger needs.
- **N7: untrusted-tool flags.** `Tool.untrusted_output` is declared on `web_search`,
  `web_fetch` and the four `moltbook_*` tools. A proposal made in a turn that read
  external text is flagged to the reviewer, stored as `untrusted_context`. A flag, not
  a block. Evidence still resolves only to messages.
- **N9 ship gates:**
  - **CO15 on the entity's real replies:** at least 10 live turns with an empty
    `note_search`, with each actual reply used verbatim as the claim;
  - **the pending-claim measurement:** 20 decorrelated live turns that end in
    `note_propose`, every reply read and classed, with an interval. **Any
    pending-claimed-as-done reply blocks shipping** until fixed and re-measured.
- **N10: auto-apply logging.** With approval off, `applied_without_review` is written
  in the same transaction as the note change and the proposal's status flip. If the
  log row fails, nothing changes. A forced-failure test is owed.
- **N13:**
  - **FTS5 consistency:** triggers on every path that changes the indexed set;
    `scripts.note check` (set comparison plus FTS5 `integrity-check`) and `reindex`;
    and `note_search` re-filtering by status at read time;
  - **the proposal status table:** the allowed edges, decisions final, every other
    edge refused and tested, and a stale target refused at approval;
  - the new `untrusted_context` column.
- **N16, N17 (four new decisions) and N18 (two new gaps)** updated.
