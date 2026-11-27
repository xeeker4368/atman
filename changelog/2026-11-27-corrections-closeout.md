# 2026-11-27: corrections-closeout (Tier 2)

**What changed.**
- **Decision #40** is added to `docs/DECISIONS.md`: a turn's correction calls run after the chat
  response, using the text proposed in the corrections-background report. `CLAUDE.md`'s decision
  count goes from 39 to 40.
- **The unmerged `cc/note-pass-v2-4` is untouched.** Its own #40 proposal stays there as evidence,
  at `e6c5768`.
- **`correction_jobs.drain()` is bounded.** It used to wait with no timeout, so a job stuck in a
  test would hang the whole run.
  - Its `timeout` now defaults to `corrections.pending_wait_seconds`. That is the bound a turn
    already waits on a pending job: one job's own worst case, three classifier timeouts plus the
    stated margin.
  - Past it, `drain()` logs a WARNING and raises `DrainTimeout`. The stuck test then fails with a
    message.
  - Its docstring now says it is called only from tests (`tests/conftest.py` and
    `tests/test_correction_jobs.py`).
  - It reads that configured bound only if the queue has not drained within 1 s
    (`_DRAIN_FAST_SECONDS`). The first commit read it on every teardown, which turned
    `test_config.py::test_bad_integer_env_raises_rather_than_silently_defaulting` into a teardown
    error: that test leaves `ANAM_API_PORT` deliberately invalid. The second commit fixes it.

**Why.** These are the close-out items of the corrections-background build (`f6fc9e0`).

**Tested.** `test_drain_gives_up_on_a_stuck_job_with_a_warning_and_a_raise` holds a job, then
checks that `drain(timeout=0.2)` raises within 5 s and logs a WARNING. After the job is released,
`drain()` finishes and the link is written. No existing assertion changed. Removing the bound fails
the test: the call then returns only once the held call releases itself, after 10 s.

**Known limits.** `NOW.md`'s decision index still lists decisions 1 to 39. This piece was limited to
the files above.
