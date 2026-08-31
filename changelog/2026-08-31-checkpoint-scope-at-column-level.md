# 2026-08-31 — Checkpoint scope at column level; task 1.1 doc follow-ups

**Tier 0/1 · Sonnet-tier work · not gated.**

Two requested follow-ups to the task 1.1 revision. One was a no-op; the other is
a governance change.

## Summary

Extended `AGENTS.md`'s database-schema checkpoint to bind at the column level on
frozen tables. Found that `BUILD_PLAN.md` needed no edit, and corrected the
document that actually carried the stale list.

## Files changed

- `AGENTS.md` — extended the "Database schema" checkpoint bullet.
- Published Phase 0–3 plan document — task 1.1 file list.
- This changelog.

`BUILD_PLAN.md` — **unchanged**, see below.

---

## 1. `BUILD_PLAN.md` task 1.1 row — no change needed

Checked before editing. The task 1.1 row reads, in full:

```
| SQLite archive (durable) + working (operational) DB schema | **3** | **Opus** |
```

There is no file list, no output list, and no table enumeration in it. A
repo-wide grep confirms `artifacts` and `research_candidates` appear **nowhere**
in `BUILD_PLAN.md` — not in that row, not anywhere else in the document.
`BUILD_PLAN.md` is untouched and its hash is unchanged at `b83e4670`.

The stale list was in the **published Phase 0–3 plan document**, which is what I
flagged as out of date at the end of the previous patch. Its task 1.1 entry
described `working.sql` as containing "`users` with role, `conversations`,
`messages`, `chunks` and the FTS5 virtual table, `settings`, `artifacts`,
`research_candidates`, and the `supersedes` link table".

Corrected there instead, since that is the document with the error and the
correction was already flagged and outstanding. It now lists the seven tables
actually built and records both removals with their reasoning, rather than
dropping the names silently — a plan that quietly loses a table it once
specified is harder to review than one that says what was cut and why. Say if
you would rather that document had been left alone.

## 2. `AGENTS.md` — checkpoint binds at column level on frozen tables

Added to the existing "Database schema (initial design and any migration)"
bullet:

> This applies at the column/field level on frozen tables too, not just at the
> whole-task level: a column decision inside an already-approved Tier 3 task
> still goes up before it is coded, not disclosed afterward. On a frozen table
> the cost of a wrong column is permanent and one-directional, so raising it
> after the fact hands the reviewer a question already answered in code — which
> is a weaker review than being asked first.

This codifies the gap identified in
`2026-08-31-task-1-1-schema-revision.md` §2. The `channel` column omission broke
no rule as written — no `NOW.md` decision was deviated from, and the approved
plan specified the archive's tables without enumerating columns — which is
precisely why the rule needed extending rather than merely being enforced. The
old wording let a permanent, one-directional decision be made inside an approved
task and disclosed afterward, and nothing in it said otherwise.

Placed on the existing bullet rather than as a new one, so it reads as a
clarification of scope on a checkpoint that already existed rather than as a
tenth checkpoint category.

## Tests run

- `python -m pytest` — **54 passed**, unchanged. No code touched.
- `ruff check .` — clean.
- Verified `BUILD_PLAN.md`'s content and hash before and after: unmodified.
- Read back the edited `AGENTS.md` section in context to confirm the bullet list
  still parses as a list and the other nine bullets are intact.

## Known limitations

- The new `AGENTS.md` clause names frozen tables specifically. `archive.db` is
  the only frozen table in the build today, so in practice it currently binds to
  one file. That is the intended scope, but worth noting that it will not catch
  a permanent-feeling decision on a *migratable* table.

## Follow-up

- None outstanding from this patch.
- Task 1.1 remains at its checkpoint awaiting sign-off; nothing here changes its
  status, and 1.2 has not been started.

## Project Anam alignment check

Documentation and governance only — no schema, no runtime behaviour, no entity
surface touched. Items 1–10 and 13–16 unaffected.

11. Migration required? **No** — no schema change.
12. Tests? **No new tests**; nothing testable changed. Existing suite re-run to
    confirm no regression.
