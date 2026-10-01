# 2026-10-01 — AGENTS.md mutation-check rule; Notes design revision 4

Review 2026-10-01. **Docs only.**

## AGENTS.md

New *Verification discipline* subsection: **mutation checks run with
`PYTHONDONTWRITEBYTECODE=1`** (or `__pycache__` cleared after each restore). Python
reuses a `.pyc` while the source's size and mtime-to-the-second match, so a same-size
mutation restored within one second keeps the mutated bytecode in force. That can fake
a failure, which happened on 2026-10-01: two tests failed against code byte-identical
to HEAD. It can equally hide a working mutation. This was previously only in CC's
private memory, which other sessions cannot see.

## docs/NOTES_DESIGN.md, revision 4

- **N6: empty-search reporting is derived from the existing `tool_trace`.**
  `scripts.note misses` is a read-only query for `note_search` entries whose `value`
  is the fixed empty-result sentence. Revision 3's `result_count` trace key is
  **removed**. Nothing had been built, and no table write was ever proposed. The
  coupling this leaves (report and sentence) is pinned to one shared constant by a
  test.
- **N9.2: the pending-claim measurement.** 20+ shuffled passes, at least two seeds,
  an interval, every flag read by hand, and **the result brought to Lyle to decide,
  with no numeric target.** This replaces revision 3's "any such reply blocks
  shipping".
- **N13: how `approved`, `edited` and `applied` relate.**
  - All three are terminal states in which the proposal took effect. They differ in
    who decided (reviewer, reviewer, nobody) and which text took effect (proposed,
    reviewer's, proposed).
  - **The one-way rule holds in code by construction:** `pending` is the only state
    with outgoing edges, each decision is one transaction with its log row, and refused
    edges are tested.
  - **It does not hold in the schema.** A direct `UPDATE` could move a status, so a
    `BEFORE UPDATE OF status` trigger is proposed for migration 8.
  - Renaming `applied` to `applied_without_review` is offered.
  - Both are new N17 decisions, #21 and #22.

## Checked

- **N9 and N10 are in every committed revision** of the doc (`cb6dafd`, `581162d`,
  `ee71756`), read from the commits themselves.
- **GitHub's `main` is `ee71756`** (`git ls-remote`, read-only), so revision 3, N9 and
  N10 included, was on the remote. The local `origin/main` ref showed `1cf9e17` only
  because nothing had fetched.
