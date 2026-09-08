# 2026-09-08 — Idle-close timing re-derivation (task 2.2's held-back part)

**Follow-on to the agent loop · approved judgment values, now landed.**

## Summary

`conversations.in_flight_grace_minutes` 30 → **40** and
`config.IN_FLIGHT_GRACE_FLOOR_MINUTES` 20 → **34**, plus three tests that
recompute the derivation from live config rather than asserting the constants.

## What was actually found

The re-derivation was reported and approved but **had not landed anywhere**.
Checked directly rather than assumed, as asked:

* `config/defaults.toml` still read `in_flight_grace_minutes = 30`, under the
  original PLACEHOLDER comment.
* `program/config.py` still read `IN_FLIGHT_GRACE_FLOOR_MINUTES = 20`, and both
  of its 30-valued fallbacks were untouched.
* `config.in_flight_grace_minutes()` resolved to **30** at runtime, floor **20**.

So this is the worse of the two cases: `defaults.toml` is read before
`config.py`'s hard-coded fallback, and it was stale too. **The system was
running on a floor its own arithmetic had already shown to be unsafe on
measured timings** — an L=5 turn measures 21.4 minutes worst case against a
20-minute floor — not merely on the pessimistic enforced-ceiling numbers.

## The arithmetic, now written where the value is

Built on **enforced ceilings**, because the floor is the point below which
idle-close closes a conversation mid-generation, and a slow-but-not-timed-out
turn still has to fit under it:

| Component | Ceiling | Enforced by |
|---|---|---|
| persist the user message | 40 s | `write_retry_deadline_seconds` + `busy_timeout_seconds` |
| retrieval embedding | 300 s | `ollama.timeout_seconds` |
| 5 model calls | 1500 s | `agent.max_iterations` x `ollama.timeout_seconds` |
| tool execution, aggregate | 120 s | `agent.tool_budget_seconds` |
| persist the assistant reply | 40 s | as above |
| idle sweep | 0 s | runs after the response, in the background |
| **total** | **2000 s = 33.3 min → floor 34** | |

40 is the floor plus ~18% for un-modelled overhead (HTTP, JSON, thread
scheduling). Less headroom than the old 1.5x ratio, because the floor is now
built from ceilings rather than from a measurement and is already pessimistic.
**That last step is the only judgment in the chain**; the 34 underneath it is
arithmetic.

## Files changed

Modified: `config/defaults.toml` (the value, and its PLACEHOLDER comment
replaced by the derivation), `program/config.py` (the constant, both stale 30
fallbacks, and the `ConfigError` message, which now names the settings the bound
comes from), `tests/test_idle.py` (+3 tests, 1 amended), `BUILT.md`.

No schema change, no new dependency. 480 tests pass; `ruff check` clean.

## The tests pin the derivation, not the number

Following `test_without_retry_contention_actually_fails`' precedent — assert the
thing the value was derived from, not that the value loads.

* `test_the_floor_is_recomputed_from_the_loops_own_limits` reads every term from
  live config and asserts the sum is 2000 s and its ceiling in minutes is 34. So
  raising `agent.max_iterations` or `ollama.timeout_seconds` without raising the
  floor fails here rather than silently invalidating it.
* `test_the_shipped_grace_is_above_the_floor_in_every_layer` checks
  `defaults.toml` **and** `config.py`'s `_FALLBACK` separately. They are read in
  different situations — a checkout with no config directory takes the second —
  so one being stale is invisible until the other is missing. This is the exact
  drift that just happened, and it now has a test.
* `test_the_floor_also_clears_the_measured_worst_case_not_only_the_ceilings`
  computes the measured-timing worst case (21.4 min) and asserts it sits under
  the new floor **and above the old 20**, recording why the old value was wrong
  rather than merely superseded.

**Each was proven to bite by breaking it**, not by passing once:

| Break | Result |
|---|---|
| `ANAM_AGENT_MAX_ITERATIONS=6` | `assert 2300.0 == 2000` |
| floor back to 20 | `assert 20 == 34`, and `assert 21.44 < 20` |
| `defaults.toml` back to 30 | `assert 30 == 40` |

## One existing test changed, deliberately

`test_unanswered_message_closes_at_the_grace_window` used a hard-coded
`minutes_ago=35`, chosen when the window was 30. It now derives its offset from
`config.in_flight_grace_minutes() + 5`, so it tracks the setting instead of
failing every time the derivation legitimately moves. No other idle test was
window-sensitive — the 20- and 30-minute literals elsewhere bracket the
15-minute short window, which is unchanged.

## Unchanged

`idle_close_minutes` stays 15. No correctness floor applies to a conversation
whose last turn completed — closing early only fragments a conversation someone
paused in the middle of, which is a continuity judgment, not a safety one.
