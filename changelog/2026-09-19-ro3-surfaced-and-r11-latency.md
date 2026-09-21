# 2026-09-19 — RO3 reversed, and R11's latency measured: Phase 3's last piece

**Tier 3, closing task 3.5.** RO3 was reversed at review and is implemented as
decided; R11's measurement was owed before 3.5 could close. Nothing committed.

## Files

Modified: `program/engine/prompt.py` (the unresolved note),
`tests/test_supersession.py` (+5, now 30),
`docs/RETRIEVAL_SUPERSESSION_DESIGN.md`, `BUILT.md`.

**1,010 tests pass**, `ruff` clean. One standing failure, unrelated: `session_secret`.

## RO3, implemented as reversed — and I have no counter-argument

My lean was to record a failed correction lookup without telling the model, on the
grounds that it would invite hedging on every record in a turn where one lookup
failed. **The reviewer's argument is better and it is `memory_search`'s own:**
*nothing found with the vector leg down is a different claim from nothing found.*
Here, the absence of annotations carries **no information** when the check did not
run, and saying nothing lets that absence be read as "nothing was corrected" — which
is precisely the unstated uncertainty this project has refused everywhere else.

What lands:

```
[The check for later corrections to these records did not complete, so no
corrections are shown below whether or not any exist.]
```

Three deliberate details:

* **It is about the check, not about the records' truth.** That is what keeps my
  hedging worry from being realised: the note says the *mechanism* did not run, so
  there is nothing in it to generalise into doubt about the memory.
* **It is stated before the records, not after** — the ordering rule this module
  already enforces for the elapsed-time figure. A caveat arriving after the thing it
  qualifies has been read is the wrong way round.
* **It is authored text, so the naming and trait tripwires cover it**, exactly as
  they cover `_RETRIEVED_HEADER`. A test asserts it survives
  `build_system_prompt()`.

A test also asserts **no results means no note** — there is nothing to qualify, and
a caveat about an empty section would be noise, the same reason the first-message
situation block carries no pairing clause.

## R11 — what resolution actually costs

Measured against `search()`'s recorded baseline (0.66 s first call, 0.04 s warm —
task 2.3, 2026-09-08). Real store, real embeddings, 12 conversations, 10 returned
chunks, 21 samples each.

| | resolve_for_chunks (the added work) | search() end to end |
|---|---|---|
| no links at all | **0.55 ms** median | 32.1 ms |
| 10 links, depth 1 (one per returned chunk) | **0.96 ms** median | 32.0 ms |
| plus one depth-9 chain (19 links followed) | **2.44 ms** median | 35.6 ms |
| **every chunk chained to the depth bound** — 100 links, 10 extra queries | **2.34 ms** median (max 2.89) | 36.7 ms |

`db.get_supersedes_for_chunks` alone is **0.44 ms** median. The last row is the
worst case the code permits: `MAX_DEPTH` is 10 and each level is one query, so ten
levels is the ceiling on queries however corrupt the data.

**So resolution is ~1.7% of a warm search in the ordinary case and ~6% at the
absolute bound.**

**One framing correction, because the comparison is easy to get wrong.** The 0.66 s
baseline is dominated by the **cold `nomic-embed-text` load** — measured separately
here at **617 ms** for a cold chunk-and-embed, after which `search()` was 45 ms
cold-ish and 34 ms warm. **Supersession resolution makes no model call at all**, so
it is pure SQLite and invariant to model warmth. There is no cold-versus-warm figure
for it to have, and claiming one would be describing a dependency it does not have.

**The in-flight-grace floor does not move**, checked rather than assumed: the
arithmetic is `2000 + T` seconds and this adds under 3 ms, so the floor stays **35**
and `in_flight_grace_minutes` stays 41.

## Known limitations

- **The corpus is 12 conversations.** Query cost on `supersedes` is an indexed
  lookup on `superseded_message_id` so it should scale with matching links rather
  than table size, but that is reasoning, not a measurement — nothing here was run
  against a large store, because none exists.
- **Lock contention is not modelled.** These numbers are an uncontended store; the
  readers wait `busy_timeout_seconds` like any other read, which is the recorded
  `db.py` issue rather than a new one.
- **Rendering cost is not separated** from resolution. It is string work bounded by
  RO4 at ~4,600 characters, so it is not expected to matter, and it was not measured.
