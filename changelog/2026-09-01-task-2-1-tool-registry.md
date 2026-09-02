# 2026-09-01 — Task 2.1: tool registry + dispatch framework

**Tier 1 · Sonnet · not gated.**

## Summary

`program/tools/registry.py` — the registry a tool is declared in, and the
dispatch call that invokes one by name. **Not** the agent loop that drives them;
that is task 2.2 and calls into this.

## Files changed

Created: `program/tools/registry.py`, `tests/test_tools.py` (31).
Modified: `BUILT.md`.

No schema change, no config change, no dependency added.

## No tools exist, and none was invented

`TOOLS` is an **empty tuple**. `memory_search`, `web_search`, `web_fetch` and
file ingestion are each their own later task in this phase, and each appends its
`Tool` here when built.

A placeholder tool would be the same category of problem as a permission gate
mounted on no route: something that reads as built and is not. Two tests hold
that line — `test_the_default_registry_is_empty_because_no_tools_exist_yet`
asserts `TOOLS == ()`, and
`test_no_scaffolding_tool_is_reachable_from_the_default_registry` dispatches
each of the test file's scaffolding names against the *default* registry and
requires `UNKNOWN_TOOL` back.

The test file's four `Tool`s are labelled **TEST-ONLY scaffolding** in their own
descriptions, live only in `tests/test_tools.py`, and are only ever put into a
locally constructed `ToolRegistry`. Nothing outside that file can reach them.

## Data, not scattered conditionals

A `Tool` is a frozen dataclass — `name`, `description`, `parameters` (a JSON
Schema object schema), `handler` — in a module-level tuple, looked up through
one function that raises on an unknown name. Same shape as
`permissions.CAPABILITIES` and `store.SETTINGS`.

`parameters` is carried **as data rather than derived from the handler's
signature**. Deriving it from Python annotations would make the schema the model
sees an accident of the implementation; declaring it means the contract is the
thing that was written down. Invalid definitions raise at construction — bad
name shape, empty description, a non-object schema, a non-callable handler.

## Central registration, not self-registration (question 4)

Tools are listed explicitly in `TOOLS` rather than registering themselves via an
import-time decorator.

**The tradeoff:** self-registration keeps a tool's declaration beside its
implementation and means adding one touches a single file. But it makes the live
tool set depend on *which modules happened to be imported* — a tool silently
missing because nothing imported its module is a failure with no error message —
and the full set stops being greppable from one place.

**Chosen central**, on this repo's own stated precedent: `config.py`'s
`_ENV_MAP` says it is "named explicitly rather than derived, so the full set of
overrides is greppable from one place." For a registry whose contents determine
what the entity can *do*, and which the fabrication gate later reasons over,
"what is registered" must be answerable by reading one tuple rather than by
tracing imports. The cost is that adding a tool touches two files; that is the
intended cost.

## The dispatch contract (question 3)

**`dispatch()` always returns a `ToolResult` and never raises** for the three
model-facing failure modes, because all three are things task 2.2 must report
back to the model rather than crash on:

| `ToolOutcome` | Meaning | `ran` |
|---|---|---|
| `OK` | tool ran and returned | `True` |
| `UNKNOWN_TOOL` | model named a tool that does not exist | `False` |
| `INVALID_ARGUMENTS` | call was malformed; handler never entered | `False` |
| `TOOL_ERROR` | call was well-formed, execution raised | `True` |

**The distinction the loop actually needs** is `INVALID_ARGUMENTS` versus
`TOOL_ERROR`: the first says the *call* was wrong and a retry with different
arguments may work; the second says the call was fine and the *execution*
failed, where the same retry will likely fail the same way. Collapsing them into
"it didn't work" would leave 2.2 unable to tell a hallucinated argument from an
unreachable network. `test_tool_error_and_invalid_arguments_are_distinguishable`
pins it, and `ran` makes "did anything actually execute" answerable without
interpreting an error string.

**Programmer errors still raise.** A duplicate registration or an invalid tool
definition is a bug in this repo, not a model mistake, and fails loudly at
construction. `registry.get()` also raises for an unknown name — `dispatch()`
deliberately does not go through it, because a model naming a missing tool is an
expected runtime event.

**`except Exception`, not `BaseException`.** A raising tool becomes
`TOOL_ERROR`; `KeyboardInterrupt`, `SystemExit`, and the suite's
`StoreIsolationViolation` — which derives from `BaseException` precisely so an
`except Exception` cannot hide it — propagate untouched. A test asserts that.

## Argument validation is deliberately small

Required-key presence, unexpected keys, and top-level primitive types. **Not a
JSON Schema implementation** — no `$ref`, no nested validation, no `enum`, no
bounds — and it does not pretend to be.

That is enough to make the `INVALID_ARGUMENTS` / `TOOL_ERROR` distinction real,
which is the contract 2.2 depends on. Going further means a large hand-rolled
validator or an external dependency, and neither is justified before one real
tool exists to show which constructs are actually used. A tool needing stricter
checks validates in its own handler and raises, surfacing as `TOOL_ERROR`.

One subtlety handled: `bool` is a subclass of `int` in Python, but the JSON
Schema types are distinct, so `{"a": True}` against `{"type": "integer"}` is
rejected.

## Feeding task 3.1's fabrication gate

BUILD_PLAN requires the turn's tool-call trace to be *"a first-class return
value the fabrication gate reasons over structurally — not debug/log output."*
`ToolResult.to_trace_entry()` is that value's per-call unit: a dict, not a
string.

Every dispatch gets a `call_id`, which is what turns 3.1's structural check —
*"invalid IDs, no matching tool_result in trace"* — into a lookup rather than a
guess. Failed and unknown calls appear in the trace too, since a claim about a
tool that failed is only checkable if the failure is recorded.

## A bug the tests caught

`dispatch()` coerced `arguments` to a `dict` **before** shape-checking it, so
`dispatch("t", ["a", "b", "c"])` raised `ValueError` from inside dispatch —
breaking the never-raises contract for exactly the case the contract exists to
cover, a model emitting the wrong JSON shape. Reordered so the shape check comes
first.

## Deliberately not included

- **Per-tool timeouts.** BUILD_PLAN's agent-loop entry needs real tool timeouts
  to re-derive idle-close's floor, so a `timeout_seconds` field is tempting. It
  is omitted: the registry cannot *enforce* one without threads or signals, and
  an unenforced field declaring a timeout would read as protection that exists.
  Task 2.2 owns the loop's time budget and should add the field alongside
  enforcement.
- **The agent loop** — task 2.2, and this does not build ahead of it.
- **Any permission gating on tool invocation.** `permissions.CAPABILITIES`
  registers nothing for tools, and this task registers no capability. When a
  real tool needs gating, it registers its own.

## Tests: 31 new, 380 total

`ruff check .` clean.

## ⚠ Unrelated observation: the known `db.py` contention flake actually fired

During one full-suite run, `test_a_write_during_the_snapshot_cannot_land_in_one_store_only`
failed with `database is locked`. It passes in isolation and passed on the next
four consecutive runs of `tests/test_backup.py` and on a full-suite rerun (380
passed).

This is **the already-recorded Tier 3 item** — `db.connection()` raising under
sustained write contention — not a regression from this task, which touches no
database code. But it is worth recording that it has now been *observed* rather
than only reasoned about: the backup race test spawns a real concurrent writer,
so the suite itself is a concurrent-writer workload. `BUILT.md`'s bullet
describing the issue as "currently dormant — nothing in the codebase writes
concurrently yet" was true of production code paths and not of the test suite;
that bullet is corrected in this commit.

No fix attempted — fixing it means editing `program/memory/db.py`, which is the
Tier 3 task already queued in BUILD_PLAN.

## Known limitations

- **The registry is empty**, so dispatch has been exercised only against
  test-only scaffolding. No real tool has round-tripped through it.
- **Never exercised against a live model.** `Tool.to_ollama_schema()` produces
  the documented shape and `ollama.chat()` already accepts a `tools=` list, but
  no model has been handed a schema from here. That is task 2.2's proof.
- **Argument validation is a subset**, as described.
- **Nothing calls `dispatch()`** outside tests. That is correct today.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes** — no write path of any kind.
5. Traceable derived artifacts? **Yes** — `call_id` plus a structured trace
   entry per call is precisely what makes a later claim traceable.
6. Tool calls recorded? **Yes** — this is the task that makes them recordable,
   as a return value rather than a log line.
7. Created artifacts remembered? **N/A** — no tool creates anything yet.
8. Context construction inspectable? **N/A.**
9. Autonomy more cumulative? **Neutral** — groundwork.
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No.**
12. Tests? **Yes**, 31.
13. Core substrate changed unnecessarily? **No** — nothing existing modified.
14. External dependencies added? **None** — the validator is hand-rolled and
    deliberately small rather than pulling in `jsonschema`.
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** The reference build's tool
    dispatch was not consulted; this task does not point at it.
