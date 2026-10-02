# 2026-10-02 — Notes piece 5: receipts generalised to records of a kind (stopped for review)

`docs/NOTES_BUILD_PLAN.md` piece 5, Tier 3, gate-adjacent (N8, option A). Nothing is offered to the
model (`notes.enabled` is still off), the gate is untouched, and piece 6 has not begun.

## Shape (the least disruptive)
- **Trace key:** every entry carries `records: [{"kind", "id"}]`, **replacing** `artifact_ids`. Two
  keys for one fact would be a drift risk, so there is one.
- **`registry.Record(kind, id)`** (a frozen record, refused at construction for an unknown kind or an
  empty id) and `registry.RECORD_KINDS = ("artifact", "note_proposal")`.
- **`ToolOutput(text, artifact_ids=(), records=())`:** `artifact_ids` is kept, so **the three existing
  handlers (`creative_write`, `image_generate`, and `ArtifactWriteError`) are not edited at all**;
  `all_records` merges both. `ToolResult` stores `records` and keeps an `artifact_ids` property.
  The "only a tool that declares `takes_attribution` may report a record" guard covers every kind.
- **`receipts.READERS`:** `{kind: one-query reader of that kind's own table}`, tested equal to
  `RECORD_KINDS`. `receipts.records_of(entry)` is the one place that reads old and new traces:
  `records` wins; `artifact_ids` alone reads as artifact records; neither key predates O23 (unknown).
  Each kind's lookup fails alone: a failed proposal read leaves the artifact receipts intact.
- **`db.get_note_proposals_by_ids`:** a read, no lock, retry or timeout change.
- **`note_propose` returns `ToolOutput(text, records=(Record("note_proposal", id),))`.** The model sees
  the same text and no id (B19).
- **`Receipt`** gains `kind`, `record_id`, `status`, `text`. **An artifact receipt serialises exactly as
  before** (the same five keys); a proposal receipt serialises `tool, kind, outcome, record_id, status,
  text, created_at`.
- **The journal's system-record lines** (`journal._outcome_words`) indexed the receipt outcome and would
  have raised `KeyError` on the first proposal; each new outcome has words there, none says "saved".

## Rulings applied
A proposal's receipt is its row's **current** status. A missing row or a failed lookup is `unknown`,
never inferred from the trace. No pending receipt says or implies a note exists. The count of receipts
equals the count of side-effect calls (a test mixes kinds and a failed call).

## Receipt texts, verbatim (a scratch store, real dispatch and real decisions)
```
--- artifact outcomes ---
saved (row exists)                             {"tool": "creative_write", "outcome": "saved", "artifact_id": "1da66802a55b48b4b7c84eaf15789157", "artifact_type": "creative_writing", "created_at": "2026-10-02T19:06:05.953558+00:00"}
not_saved (tool_error, no row)                 {"tool": "creative_write", "outcome": "not_saved", "artifact_id": null, "artifact_type": null, "created_at": null}
not_saved (never ran: skipped)                 {"tool": "image_generate", "outcome": "not_saved", "artifact_id": null, "artifact_type": null, "created_at": null}
unknown (timeout)                              {"tool": "creative_write", "outcome": "unknown", "artifact_id": null, "artifact_type": null, "created_at": null}
unknown (id with no row)                       {"tool": "creative_write", "outcome": "unknown", "artifact_id": "ffffffffffffffffffffffffffffffff", "artifact_type": null, "created_at": null}
unknown (trace predates O23)                   {"tool": "creative_write", "outcome": "unknown", "artifact_id": null, "artifact_type": null, "created_at": null}
old trace: artifact_ids only (same as saved)   {"tool": "creative_write", "outcome": "saved", "artifact_id": "1da66802a55b48b4b7c84eaf15789157", "artifact_type": "creative_writing", "created_at": "2026-10-02T19:06:05.953558+00:00"}
--- proposal states ---
pending (live)                                 {"tool": "note_propose", "kind": "note_proposal", "outcome": "proposed", "record_id": "659665db-0f0f-4f1a-981c-b1ad6c7b93b7", "status": "pending", "text": "Proposed, awaiting a person's review.", "created_at": "2026-10-02T19:06:06.247373+00:00"}
approved                                       {"tool": "note_propose", "kind": "note_proposal", "outcome": "accepted", "record_id": "659665db-0f0f-4f1a-981c-b1ad6c7b93b7", "status": "approved", "text": "Reviewed and accepted.", "created_at": "2026-10-02T19:06:06.247373+00:00"}
rejected                                       {"tool": "note_propose", "kind": "note_proposal", "outcome": "declined", "record_id": "19205cb5-31fa-40c6-b75a-c2e20b4be034", "status": "rejected", "text": "Reviewed and not accepted.", "created_at": "2026-10-02T19:06:06.252053+00:00"}
applied_without_review                         {"tool": "note_propose", "kind": "note_proposal", "outcome": "applied_without_review", "record_id": "164ef915-6f36-4edd-a09d-1cee69cf65c4", "status": "applied_without_review", "text": "Applied without review.", "created_at": "2026-10-02T19:06:06.253873+00:00"}
call failed (quote not found): not_proposed    {"tool": "note_propose", "kind": "note_proposal", "outcome": "not_proposed", "record_id": null, "status": null, "text": "Not proposed. Nothing was recorded.", "created_at": null}
call timed out: unknown                        {"tool": "note_propose", "kind": "note_proposal", "outcome": "unknown", "record_id": null, "status": null, "text": "Unknown. The proposal's record could not be confirmed.", "created_at": null}
row missing: unknown                           {"tool": "note_propose", "kind": "note_proposal", "outcome": "unknown", "record_id": "4300a804-9cf7-4d93-b074-a7d0064ea7a4", "status": null, "text": "Unknown. The proposal's record could not be confirmed.", "created_at": null}
lookup failed: unknown                         {"tool": "note_propose", "kind": "note_proposal", "outcome": "unknown", "record_id": "428a7c45-c973-435e-bc4a-46fce1114fbb", "status": null, "text": "Unknown. The proposal's record could not be confirmed.", "created_at": null}
```
The outcome words differ from N8's draft (`proposed / approved / rejected / active`) on purpose, per
the rulings: `accepted` covers both `approved` and `edited` (the `status` field keeps the row's word),
`declined` is `rejected`. `active` (the `approval_required` off path) does not exist yet: piece 6
will read it from the note row.

## Tests that changed, and why
- `tests/test_receipts.py` (5) and `tests/test_tools.py` (1): they asserted the trace key
  `artifact_ids`; they now assert `records`. The artifact **receipt** assertions are untouched.
- `tests/test_origin.py`: the piece-2 digest's scrub normalised `artifact_ids`; it now reads
  `records` back into that shape. **The pin `e5c92a42…806762` is unchanged and passes**, so
  everything else a turn observes is unchanged, and the scrub fails on any other new key.
- `tests/test_receipts.py` also still holds ~10 traces written in the **old** shape (`artifact_ids`
  only), which now double as the compatibility proof; `tests/test_journal.py` likewise.
- New: `tests/test_receipts_records.py`, 40 tests.

## Proofs
- **Gate verdicts byte-identical** over every frozen case, four scripted replies, with artifact
  records, proposal records, and neither (verdict, advisory and every classifier prompt equal):
  `tests/test_receipts_records.py`. The pin `c3a01db6…ad488` (`tests/test_gate_identity.py`) passes.
  `gate.py` is unchanged and a test asserts it never mentions `records`.
- **Soak store copy** (`~/anam-measurements/jlive/store`, copied to a scratch directory; the real
  `data/` fingerprint is unchanged): HEAD's `receipts.py`, loaded from git, against the new module
  over every stored trace: **17 traces, 36 receipts, 51 variants** (as stored; with `artifact_ids`
  naming real rows and a ghost id; with empty `artifact_ids`): **51 identical, 0 different.** These stored traces
  predate O23, so as stored they are all `unknown`; the variants are what exercise `saved`.
- **Mutations, `PYTHONDONTWRITEBYTECODE=1`, 31, each killed**, one per guard: reader table, legacy
  read, records-vs-artifact_ids precedence, no-key reading, each status mapping, each text, missing
  row, failed lookup (and per-kind isolation, and order), never-ran and timeout and error for a
  proposal, silent defect, artifact serialisation, unrecognised status, unknown kind, record
  validation, the declaration guard, the duplicate key, `all_records`, a lost record after a failed
  write, `note_propose` reporting nothing or the wrong id, and the journal's words. **Two first
  survived** (the no-key reading differed from the real one only for `tool_error`/`timeout`; the
  per-kind isolation test listed the failing kind first) and each now has a test.

## Known limits
- The error log line for a call that named no record now says "no records" where it said "no artifact
  ids"; log text only.
- Nothing renders a receipt yet (Phase 9), as for artifacts.
