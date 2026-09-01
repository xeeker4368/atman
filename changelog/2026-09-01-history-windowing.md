# 2026-09-01 — History windowing (token-budget cutoff)

**Task 1.10 · Tier 1 · Sonnet.** Decision #6.

## Summary

What gets resent to the model each turn is now decided by a **token budget**,
not a message count. Reserve space for the system prompt, the retrieved chunks
and the model's own output; the remainder goes to the most recent raw history,
newest-first, stopping at the first turn that does not fit.

Nothing is deleted and nothing is summarised. A turn outside the window stops
being resent and stays retrievable through `memory_search` exactly as before.
`anam/engine/history.py` never writes anything.

## Files changed

Created: `anam/engine/history.py`, `tests/test_history.py` (20).
Modified: `anam/config.py` (four accessors + three env vars),
`config/defaults.toml` (`[history]`), `BUILT.md`.

## It takes its reserves as inputs, and that is deliberate

The two things it reserves against — the system prompt (task 1.9) and the
retrieved chunks (task 1.5) — are both **Tier 3 and both unbuilt**.

So `plan_budget()` takes `system_prompt_chars` and `retrieved_chars` as
caller-supplied character counts rather than building or calling either. That
keeps this task complete and testable now, keeps it out of two Tier 3 files
entirely, and means neither of those tasks has to come back and rework this one
when it lands — they pass a number in.

## The estimator, and which way it is wrong

`chars / history.chars_per_token`, rounded **up**. No tokenizer dependency:
that would pin the cut-point arithmetic to one model's vocabulary.

The two errors are not symmetric, which is the whole design constraint:

| Error | Effect | Severity |
|---|---|---|
| Estimate **more** tokens than occur | window under-fills; omitted history still retrievable | non-event |
| Estimate **fewer** tokens than occur | `num_ctx` overflows, model server drops oldest content silently | the failure being avoided |

So the divisor must sit **at or below** the text's true ratio. Used the measured
figure rather than re-deriving it, as instructed: task 1.2 saw **4.63
chars/token** over an 81,600-char real prose prompt, and the 2026-08-31
muse-glimmer eval independently saw 4.619 on the same prompt — a 0.2% spread.
At 4.63 actual against a 4.0 divisor the estimate over-counts by ~14%. Measured,
and the safe direction.

A test pins this against the real numbers: 81,600 chars must estimate to more
than the 17,626 tokens Ollama actually reported.

## Both chars-per-token margins, documented together

BUILD_PLAN's task 1.10 entry asks that this margin and the embedding-input
margin be documented in one place "rather than letting them diverge by
accident." They **do** diverge, by a lot, and the reason is now written down in
`config/defaults.toml` under `[history]`:

| | Implied chars/token | Direction |
|---|---|---|
| `history.chars_per_token` | 4.0 | ~14% conservative vs measured prose |
| `embedding.max_input_chars` (5000 chars / 2048-token model context) | 2.44 | ~47% conservative vs measured prose |

They differ because the consequences differ. Over-running the embedder's context
is a hard HTTP rejection of one call, so its margin is bought cheaply and set
wide. Over-running `num_ctx` is a silent drop, so its margin is set as wide as
it can be without spending most of a 32K window on slack.

## ⚠ Flagged, not decided — four values

Three of the four `[history]` values have **nothing measured behind them**,
flagged here the way `chunking.max_turns` was:

1. **`chars_per_token = 4.0` — measured for prose, a known gap for dense text.**
   Symbol-dense content (code, JSON, tool traces) runs nearer 3 chars/token. At
   3.0 actual against a 4.0 divisor the estimate **under**-counts by ~33% — the
   overflow direction. Nothing here has measured a code-heavy conversation,
   because there are no conversations yet. Given what this project's own
   conversations will contain, I think this is the likeliest of the four to be
   wrong in practice. Dropping it to 3.0 costs window space rather than
   correctness. `test_dense_content_would_under_count_which_is_the_known_gap`
   pins the arithmetic so it cannot be quietly forgotten.
2. **`message_overhead_tokens = 4`** — judgment. Reading it off the model's
   actual chat template would make it real; that was not done.
3. **`output_reserve_tokens = 2048`** — judgment. It is ~92s of generation at
   the measured 22.2 tok/s, which is a plausible ceiling but not a measurement
   of what this system's answers actually run to. There is no chat endpoint to
   measure yet.
4. **`safety_margin_tokens = 512`** — judgment. Covers roughly a 2% under-count
   of a 32K window. It does **not** cover the ~33% dense-content case in (1);
   it is slack, not a fix for that.

All four are configurable, with `ConfigError` on nonsense values rather than
silent defaulting.

## Implementation notes

**Stops at the first message that does not fit**, rather than continuing
backwards looking for a smaller one. Skipping a large turn to include an older
small one resends a history with a hole in it, which reads to the model as
though the turn never happened. `test_it_stops_at_the_first_message_that_does_
not_fit` constructs exactly that temptation — a small oldest message reachable
past an over-large one — and asserts it is not taken.

**The newest message is sent even when it alone exceeds the budget**, with
`overflowed=True` and a logged warning. Dropping the turn the model is being
asked to answer would be a worse failure than overflowing, and the overflow is
at least visible rather than silent.

**`BudgetBreakdown` is a record, not a number.** "Why was history only N tokens"
is answerable from the returned object without re-deriving the arithmetic. A
test asserts `reserved + history == context`, so the breakdown cannot silently
stop adding up.

**Defect found and fixed during the task, in this task's own code:**
`estimate_message_tokens()` used `message.get(...)`, which works for a dict and
raises `AttributeError` for a `sqlite3.Row`. Since callers pass
`db.get_conversation_messages()` straight in, that was the common path, not an
edge case. Caught by
`test_it_accepts_sqlite_rows_from_the_message_store` before it went anywhere.
Nothing already-shipped was affected.

## Tests: 20 new, 171 total

`ruff check .` clean.

They assert behaviour rather than the presence of a function:

- `test_it_is_a_token_budget_not_a_message_count` — the same budget admits
  many short turns and few long ones. This is the property that distinguishes
  decision #6 from the approach it rejected.
- `test_estimate_over_counts_at_the_measured_prose_ratio` — against task 1.2's
  real 81,600 chars → 17,626 tokens.
- `test_it_keeps_the_most_recent_turns_and_drops_the_oldest` — and that what
  survives is the contiguous tail, in order.
- `test_nothing_is_summarised_or_truncated_only_omitted` — every surviving
  message comes through byte-for-byte.
- `test_the_newest_message_is_sent_even_when_it_alone_overflows` — asserts the
  warning is logged, not just the flag set.
- `test_reserving_more_leaves_less_for_history` — the reservation actually
  competes, rather than being cosmetic.

## Known limitations

- **The four values above.** The dense-content gap is the headline one.
- **No live end-to-end verification.** There is no chat endpoint, so nothing
  has yet sent a windowed history to Ollama and confirmed the real token count
  came in under `num_ctx`. That verification belongs to task 2.2, and it is the
  only thing that will actually prove the margin rather than reason about it.
- **Tool-call traces are not priced.** `_normalise()` drops everything but role
  and content. Once the agent loop puts tool results into the message list
  (task 2.2), those carry real tokens this estimator does not currently see —
  and they are exactly the dense content the divisor is weakest on.
- **Per-message overhead is a flat charge**, not the model's real template.

## Follow-up

- Task 1.5 / 1.9: pass real `retrieved_chars` and `system_prompt_chars` in.
- Task 2.2: measure a real windowed turn against `num_ctx` and re-derive
  `output_reserve_tokens`; price tool traces into the estimate.
- Re-measure `chars_per_token` against real conversation content once it exists.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes** — this module has no write path at all.
   Omitted turns are untouched in both stores.
5. Traceable derived artifacts? **Yes** — `BudgetBreakdown` accounts for every
   token of the window.
6. Tool calls recorded? **N/A** — noted as a limitation for task 2.2.
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Yes** — this is the task that makes it
   so: included/omitted counts, estimated tokens, and the full reservation
   breakdown come back with every window.
9. Autonomy more cumulative? **Neutral.**
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No** — no schema contact.
12. Tests? **Yes**, 20.
13. Core substrate changed unnecessarily? **No** — nothing existing was
    modified except additive config.
14. External dependencies added? **None** — deliberately no tokenizer.
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** The reference build's windowing was
    not consulted; this task does not point at it.
