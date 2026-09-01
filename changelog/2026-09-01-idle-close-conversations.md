# 2026-09-01 — Idle-close for conversations

**Tier 2 · Sonnet · spec approved before implementation.**

## Summary

A conversation with no new message for its idle window closes automatically,
which runs final chunking and sets `chunked`. Two windows, chosen by whether the
last message is from the assistant or the user. Lazy, no daemon.

Load-bearing rather than housekeeping: task 1.3's chunking never indexes the open
trailing group, so a conversation that never closes leaves its last turns
permanently unretrievable from anywhere except itself.

## Files changed

Created: `anam/memory/idle.py`, `scripts/close_idle_conversations.py`,
`tests/test_idle.py` (18).
Modified: `anam/memory/db.py` (activity query), `anam/config.py`,
`config/defaults.toml`, `BUILD_PLAN.md`, `BUILT.md`.

## Cross-task dependencies, recorded in BUILD_PLAN.md

Both added to **task 2.2's entry**, not just to the spec document:

1. **Re-derive the timing.** `in_flight_grace_minutes` (30) and
   `FLOOR_MINUTES` (20) come from an assumed 5-iteration loop that does not
   exist. The floor is `L × (prompt eval + generation) + tool execution`, so
   2.2's real iteration limit and real tool timeouts must recompute both. The
   measured inputs are recorded there so nobody has to re-measure: 19.1s cold
   load, 132.8s prompt eval at 30,167 tokens, 22.2 tok/s generation at full
   context. Also noted: image generation carried a 300s timeout in the reference
   build, and one such call would exceed a per-iteration budget by itself.
2. **Persist the user's message before generation begins.** Idle-close
   distinguishes in-flight from completed by whether the last message is from
   the user or the assistant. Buffering both and writing them together at the
   end would make an in-flight turn look finished, and the short window would
   then apply to a conversation mid-generation. Recorded as a correctness
   dependency, not a crash-safety nicety.

## The numbers

| Setting | Value | Basis |
|---|---|---|
| `idle_close_minutes` | 15 | Last message from the assistant — turn complete, nothing in flight. No correctness floor; closing early only fragments a conversation someone paused in. |
| `in_flight_grace_minutes` | 30 | Last message from the user — a turn may be running. ~2× the measured 15–16 minute worst case. |
| `IN_FLIGHT_GRACE_FLOOR_MINUTES` | 20 | Enforced in code. **Raises rather than clamping** — a clamped value hides that the operator asked for something unsafe. |

Measured 2026-09-01, `gemma4:26b` at `num_ctx=32768`: cold load 19.1s (19.75s
cold vs 0.65s warm, identical call); prompt eval 132.8s for 30,167 tokens
(227.2 tok/s); generation 22.2 tok/s at full context. One worst-case turn
≈ 197s; at five agent-loop iterations ≈ 15–16 minutes.

Task 1.2's 76s figure was not the worst case — that prompt was 17,626 tokens
against a 32,768 ceiling. Measuring the near-full case moved the answer
materially, which is why the floor is 20 rather than something derived from 76s.

## Implementation notes

**Idle is measured from `MAX(messages.timestamp)`**, never request activity.
Confirmed there is no request-time field in the schema to read by accident. A
conversation with no messages falls back to `started_at`, or it could never
close.

**The two-window split needs no schema change.** `last_role` comes from the same
`MAX()` aggregate as the timestamp — SQLite's bare-column rule makes other
columns in a `MAX()` aggregate come from the matching row. I verified that
behaviour directly rather than trusting the documentation: with messages at
10:00 user / 11:00 assistant / 12:00 user, the query returns `12:00` and `user`.

An explicit `conversations.in_flight_since` column would be more precise and was
rejected: it is a migration, which `AGENTS.md` puts on the stop-and-verify list,
escalating this Tier 2 task to Tier 3 for a refinement the role check already
approximates.

**Ordering: `ended_at` first, then chunking.** A chunking failure then leaves the
conversation closed, `chunked` at 0, and present in
`db.get_unchunked_ended_conversations()` — the recovery queue that already
existed. The reverse order would produce a chunked-but-open conversation, a
state nothing else expects.

**Continue-on-error, as approved.** A deliberate deviation from task 1.3's
abort-immediately policy: one unreachable model must not stop every other idle
conversation from closing. Failures are collected and raised together as
`IdleCloseError` at the end of the sweep — visible, never swallowed, never fatal
mid-sweep.

## What actually runs this today

**Nothing automatic.** There is no chat endpoint, so the per-request sweep does
not exist; it arrives with task 2.2, passing the active conversation as
`exclude_conversation_id` so a sweep can never close the turn that triggered it.

Today the callers are `scripts/close_idle_conversations.py` and the tests.
`BUILT.md` says this rather than listing idle-close as live.

## Tests: 18 new, 151 total

`ruff check .` clean. Full suite **151 passed**.

They prove outcomes, not the existence of a check function:

- `test_idle_conversation_is_closed_and_marked_chunked` — `ended_at` set **and**
  `chunked = 1`.
- `test_closing_makes_the_trailing_turns_retrievable` — the point of the task:
  chunks exist afterwards where none did before, the final reply is among them,
  and FTS matches.
- `test_completed_turn_uses_the_shorter_window` — same age, different
  last-message role, different outcome.
- `test_idle_is_measured_from_the_last_message_not_started_at` — a conversation
  open three days with a message two minutes ago is not idle.
- `test_excluded_conversation_is_never_closed`.
- `test_chunking_failure_leaves_it_closed_unchunked_and_recoverable` — and
  present in the recovery queue.
- `test_one_failure_does_not_stop_the_sweep` — the second conversation still
  chunks after the first fails, and the error still raises.
- `test_grace_below_the_floor_raises` / `test_short_window_has_no_floor`.

**Live run against a real store with real embeddings**, outside the suite:

```
seeded: 1 idle (40m), 1 fresh (0m)

--- dry run ---
idle window (turn complete) : 15m
in-flight grace             : 30m (floor 20m)
  e8237cbe  idle: quiet 40.0m of 15m
examined : 1   closed : 0 (dry run)   chunked : 0

--- real run ---
  e8237cbe  idle: quiet 40.0m of 15m
examined : 1   closed : 1   chunked : 1

--- state after ---
  e8237cbe closed=True  chunked=1
  5e371a4c closed=False chunked=0
  chunk rows: 2
```

## Known limitations

- **The 30/20 values are placeholders** derived from an agent loop that does not
  exist. This is the headline limitation and it is recorded against task 2.2.
- **Tool execution time is not in the floor arithmetic** — there are no tools
  yet. Image generation in particular could dominate a per-iteration budget.
- **An abandoned turn and a running turn are indistinguishable.** Both show a
  user message with no reply and both wait the full grace period. Accepted:
  waiting 30 minutes to close a crashed turn costs nothing.
- **No automatic trigger**, as above.
- **A conversation closed while a human is still typing gets fragmented.** Their
  next message starts a new conversation. That is what the 15 minutes is
  balancing, and it is a judgment with nothing measured behind it.
- **The sweep is O(open conversations)** per call. Trivial at this scale.

## Follow-up

- Task 2.2: both dependencies above, plus wiring the per-request sweep.
- A recovery pass for `get_unchunked_ended_conversations()` — the queue exists
  and is populated, but nothing drains it yet.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes** — closing only sets `ended_at` and chunks;
   no message is altered or removed.
5. Traceable derived artifacts? **Yes** — chunks trace to messages as before.
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Partly** — `IdleCloseResult` separates
   examined / closed / chunked / skipped / failures, so a partial sweep is
   legible rather than a single count.
9. Autonomy more cumulative? **Yes, indirectly** — closing is what makes a
   conversation's final turns retrievable to later ones.
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No** — the two-window split reuses existing columns
    specifically to avoid one.
12. Tests? **Yes**, 18, plus a live run.
13. Core substrate changed unnecessarily? **No** — chunking is untouched.
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** The reference build's idle-close was
    not consulted — this task does not point at it — though its 15-minute window
    and 2-minute floor are visible in `PROJECT.md`-era notes and the values here
    were derived independently from measurement.
