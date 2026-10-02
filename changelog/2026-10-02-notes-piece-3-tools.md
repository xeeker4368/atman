# 2026-10-02 — Notes piece 3: `note_search` and `note_propose`, shipped dark (stopped for review)

`docs/NOTES_BUILD_PLAN.md` piece 3, Tier 3. **Both tools exist in the catalogue and are not offered to
the model** (`notes.enabled = false`, a bootstrap setting). No auto-apply, no `scripts.note`, no
receipt generalisation: piece 4 does not start. The server was not started.

## Built
- `program/tools/note_search.py`, `note_propose.py`, `note_quotes.py`, `note_texts.py` (every sentence
  the model reads, in one file); `program/memory/notes.py` (the queries; kept out of `db.py`).
- `Tool.untrusted_output` (set on `web_search`, `web_fetch` and the four `moltbook_*` tools) and
  `registry.untrusted_tools()`, derived from the flag as `side_effect_tools()` is from
  `takes_attribution`.
- Config: `notes.enabled` (bootstrap, false), `max_results` 5, `max_subject_chars` 60,
  `min_quote_chars` 24, `min_quote_words` 4, and `max_text_chars` **650, derived**.
- `turn.py`: fills `untrusted_context` after the loop (below).

## How `untrusted_context` is filled (the handler cannot see earlier calls)
As you suggested, with two choices stated. After the answer is durable, in `_after_durable`, the turn
updates the **still-pending** proposals whose `call_id` appears in the trace, setting
`untrusted_context` to the untrusted-output tools that ran **earlier in the same trace**. NULL means
*not recorded*; `[]` means *recorded, none*; a failed update leaves NULL, never `[]`. A pending
proposal is not frozen, so the update is allowed (tested), and a decided one is never touched (the
`WHERE status = 'pending'`; tested).
1. **A tool counts only if it returned text** (`outcome == "ok"`): a failed or timed-out call gave the
   entity nothing written outside the household to read.
2. **"Untrusted" is what the registry in use declares**, resolved as the loop does, not the
   catalogue's list: those are the tools that ran. (`registry.untrusted_tools()` gives the catalogue
   answer for a stored trace whose tool was since disabled; piece 4 can use it.) A first version used
   the catalogue and failed its own test, because a test tool is not catalogued.
A turn that proposed no note does none of this work (tested).

## Decisions to look at
1. **Notes are shown by their first 8 characters**, and `note_id` accepts that prefix (ambiguity is
   refused). Ids are `db.new_id()` (hyphenated UUIDs), so they are not 32-hex and the gate's
   `invented_id` rule would not flag them, but no result text names any id (a quoted identifier is
   what B19 is about). Costs a prefix lookup.
2. **The store-tier quote search prefilters with `LIKE` on the quote's longest word, then matches
   exactly in Python**, instead of N4's `chunks_fts` shortlist: that index lacks messages chunking has
   not sealed, and the evidence for a proposal made this turn is exactly such a message.
3. **`max_text_chars` is 650, not N11's estimated 600**: derived from the built renderer (header 161
   chars, framing worst case 114, 5 notes: 3,996 of 4,000 characters; 651 gives 4,001). A test
   recomputes it and fails if `defaults.toml` is not the largest that fits.
4. **`subject_user_id` is never set by the tool** (no parameter, and linking a label to a household
   member by name would be a guess); the reviewer can set it in piece 4.
5. **Timeouts:** `note_propose` 60 s (the identity classifier alone may take 45 s), `note_search` 15 s.
   Both under the 120 s turn budget, so the in-flight-grace floor is unchanged.
6. **The gate runs on the text, flag-only, stored on the row**; a retire has no text and records NULL
   (no verdict), never clean. A refused proposal never reaches the gate (tested).
7. **Receipts:** until piece 5 a `note_propose` call reads `unknown` in a receipt (a logged
   defect-shaped case). It cannot reach a person while the tool is never offered.

## The gate is unaffected by `note_propose` joining the catalogue
`side_effect_tools()` reads the full catalogue, so it now returns
`('creative_write', 'image_generate', 'note_propose')` even while Notes is dark. Proof, both digests
re-run **with the tool in the catalogue**:
- **Gate verdicts:** the digest over every frozen case under four scripted replies (case id, verdict,
  advisory, every classifier prompt) is `c3a01db6…ad488`, **equal to the value pinned from HEAD's gate
  before `check_identity` existed** (`tests/test_gate_identity.py`), re-computed and printed.
- **Piece 2's turn digest** `e5c92a42…806762` (`tests/test_origin.py`): unchanged.
Both run in the suite; `test_note_propose_is_a_side_effect_tool_and_the_gates_verdicts_are_byte_identical`
calls the first directly after asserting the catalogue change.
A pinned documented gap (N7, N18): with `note_propose` in the trace the action rule clears
*"I've saved that note"*; the result text, not the gate, counters it.

## Existing tests changed, and why
- `tests/test_tools.py::test_the_catalogue_is_exactly_the_tools_that_have_been_built`: the exact
  catalogue tuple gains `note_search`, `note_propose`; the default-registry assertion is unchanged
  (both dark).
- `tests/test_gate.py::test_side_effect_tools_are_the_attribution_taking_ones` and
  `tests/test_receipts.py::test_the_side_effect_set_is_one_definition_shared_by_gate_and_receipts`:
  the pinned set gains `note_propose`, which declares `takes_attribution` (the derivation is the
  point of those tests, not a bug).
- `tests/conftest.py`: `ANAM_NOTES_ENABLED=false` for every test, as Moltbook's.
- `config/defaults.toml`: the B20 derivation comment re-derived for 11 tools (below).

## Budget (B20)
Real tokenizer, measured as the change in `prompt_eval_count` over a no-tools baseline of 15, two calls
each, identical both times: **9 tools 1,057; 11 tools 1,337** (`note_search` +68, `note_propose` +212;
combined +280). The estimator prices the 11 tools at 1,495 (it over-counts, the safe direction). The
**derived `chat.max_message_chars` with B20's term and the two tools: 51,236 characters** (it was
52,360 with 9); the configured 50,000 still fits, leaving ~309 tokens of earlier history beside a
maximal message (~590 before). The next tool would take the headroom to about zero.

## Tested
Full suite 1,732 passed, 4 skipped; `ruff` clean. 77 tests in `tests/test_notes_tools.py`.
**51 mutations (PYTHONDONTWRITEBYTECODE=1), each killed**, one proven-to-bite test per guard:
every refusal's check (action, subject kind, subject empty/long, text required/long, retire text,
note_id required/forbidden/active/ambiguous, quotes required, quote not text, both length floors, a
person's words only in both tiers, no match, ambiguity, the same-user and different-user exceptions,
context-tier preference, paraphrase and reordered-words refusal), the gate (never run, run on a
retire, run before validation, verdict not stored), the row (triggering message, attribution, call id,
pending), the result texts, the search (status re-filter, limit, empty query, short id, header and
`max_text_chars` derivation), the untrusted context (flag dropped, failed tool counted, order, NULL vs
`[]`, decided proposals, not failing the turn, no work when nothing proposed), darkness and the
declarations, and the writer's retry. **One mutant first survived** (looser word-subset matching):
my paraphrase tests were all refused by it too; a reordered-words case was added and kills it.
`BUILT.md` is not edited for this piece; its entry is written at approval.
