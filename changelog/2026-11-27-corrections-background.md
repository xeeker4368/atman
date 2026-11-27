# 2026-11-27: corrections-background (Tier 2)

**What changed.** On `POST /api/chat`, a turn's correction classifier calls and their link writes
now run **after the response**, on one process-wide FIFO worker thread
(`program/engine/correction_jobs.py`). The rest of the turn is unchanged:
- `turn._record_corrections` still builds the candidate pool inline, before the response, from the
  store as it stood at the turn, and freezes every input into a job.
- The gate stays inline.
- Every direct caller of `turn.handle_user_message` (scripts, harnesses, tests) still runs the
  calls inline. Only the route passes `defer_corrections=True`, and the four harness scripts that
  call the turn say so in their docstrings.
- The next turn in the same conversation, after taking its lock and before retrieval, waits for
  that conversation's pending job, and so does idle close before ending a conversation. Other
  conversations never wait. The bound is the new `corrections.pending_wait_seconds = 150`: 3 × the
  45 s classifier timeout plus a 15 s margin, derived in `defaults.toml`. Past the bound the waiter
  logs a WARNING and proceeds. A turn that waited logs an INFO line, "waited X s for pending
  corrections", for the measurement.
- The app's lifespan calls `correction_jobs.shutdown(wait=True)`.

**Why.** In run 6 these calls added a mean 9.2 s (0 to 14.8 s) to the person's wait. The plan of
record is outside the repository: `corrections-after-response-plan-2026-11-27.md`, option (c).

**The trade-offs, chosen by Lyle.**
- **Log only.** A failed job logs a WARNING naming both message ids and the error. **A forced stop
  (kill, crash) loses queued and running jobs with no trace:** the messages stand with no link, and
  nothing records that a correction was due. No table, no schema change.
- **A reader outside the conversation can miss a fresh link** until the job finishes. That covers
  another person's turn, and a recall question after the kit's `/kit/end`, which closes without
  waiting.

**Not covered by the bound:** a link write in its own retry worst case (40 s each), and a job queued
behind another conversation's job.

**Tested.** `tests/test_correction_jobs.py`, 20 tests, all faked over the model:
- the pool snapshot, the next turn waiting, the bound, other conversations not waiting, FIFO;
- a failing classifier and a failing link write (logged with both ids; the next job still runs);
- through the route, checkpoint and sweep still run;
- a discarded worker loses its job and leaves no registry entry or lock; a graceful shutdown waits;
  the lifespan waits; a failed submit is logged;
- the response returns first; idle close waits; inline is the default; the bound's derivation;
- timing lines under the turn's user message; the classifier model pinned at the turn.

There were 17 mutations, each caught. Existing tests are unchanged. `tests/conftest.py`'s
`isolated_data_dir` now drains the worker at teardown, so no job outlives its test's store.

**Known limits.**
- At run 6's pace (next send 2–4 s after the response), most of the saving moves to the next
  turn's wait. Only a slower reply gets it in full.
- The kit's `/kit/end` does not wait; that is a kit change, left to Lyle.
- `CORRECTION_DESIGN.md` C8 and C9 and `ARCHITECTURE.md` are revised.
