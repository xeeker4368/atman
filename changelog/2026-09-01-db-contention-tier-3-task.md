# 2026-09-01 — `db.py` write contention promoted to a Tier 3 task

**Tier 0/1 · governance change · not gated.**

## Summary

The `database is locked` finding from task 1.14's backup work is now a planned
**Tier 3** task in Phase 2 rather than only a `[unverified]` note in `BUILT.md`.

## Files changed

- `BUILD_PLAN.md` — new Phase 2 row.
- `AGENTS.md` — stop-and-verify list, synced in the same change (required).
- This changelog.

No code changed. Test suite unchanged at 233 passing.

## Placement

Inserted **immediately before the Agent loop row**, which is deliberate:

- It sits *after* "Tool registry + dispatch framework", which does not write
  concurrently and so does not need the fix.
- It sits *before* the Agent loop, which is exactly the task that makes the
  risk live — a chat turn writing messages while idle-close's sweep or a
  background pass runs against the same database.

That ordering means the substrate fix lands before the thing that depends on it,
rather than the plan recording a hazard that goes live one row earlier than its
remedy.

## AGENTS.md sync, because the rule requires it

`AGENTS.md` says: *"This list is kept in sync with BUILD_PLAN.md's Tier 3 tasks.
If a task is Tier 3 there, it belongs here — if you add a Tier 3 task without
adding it here, fix this list in the same change, not later."*

So a new stop-and-verify bullet went in alongside the row. I wrote it to say why
the category is gated rather than just naming it, because this one is easy to
mistake for tuning:

> Database concurrency and locking semantics in `anam/memory/db.py` —
> `busy_timeout` tuning, write retry, or write serialisation. These read as
> operational tuning and are not: the cross-database atomicity guarantee depends
> on the locking behaviour they change, so a fix verified only against the
> lock-timeout symptom can weaken the guarantee without failing anything.

That mirrors the task row's own instruction to verify against the atomicity
constraint explicitly, not just against the symptom. It is the reason the task
is Tier 3 at all: raising a timeout looks like a one-line config change, and the
thing it can quietly break has no test that would fail.

## Why Tier 3 and not Tier 2

The three candidate fixes are not interchangeable:

- **Raise `busy_timeout`** — cheapest, changes no structure, but only moves the
  failure threshold rather than removing it.
- **Retry with backoff** — needs a decision about where the retry lives, since a
  retry wrapped around `transaction()` re-runs a caller's whole write.
- **Serialise writes through a single writer** — removes the contention but
  introduces a chokepoint and a lifecycle to own.

Each touches the locking behaviour that `db.py`'s docstring builds its
cross-database atomicity guarantee on, and `db.py` is task 1.1 territory
(Tier 3, Opus). The hard gate is the right tier.

## What this does not change

- **No code was touched.** The contention is still present and still unfixed.
- **Still dormant.** Nothing in the codebase writes concurrently yet, so there
  is no live risk today — the `BUILT.md` bullet already says this and still
  does.
- **Not a data-integrity risk**, unchanged: every write goes through
  `transaction()`'s explicit `BEGIN`/`COMMIT`/`ROLLBACK`, verified across all
  six write paths in `db.py`, so a caller that loses the race raises with
  nothing written.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4–10. **N/A** — no runtime behaviour changed.
11. Migration required? **No.**
12. Tests? **N/A** — documentation and plan only; suite unchanged at 233.
13. Core substrate changed unnecessarily? **No** — nothing was changed, which is
    the point of raising a task instead of patching it.
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.**
