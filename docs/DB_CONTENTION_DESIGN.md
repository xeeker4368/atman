# `db.py` write contention — fix design for approval

**Tier 3 · Sonnet · design only, no code written.**

Decisions numbered `C1`–`C8`.

---

## C1 — What actually fails, measured

Built a reproduction harness rather than reasoning from the symptom. Results:

| Scenario | Failure rate |
|---|---|
| 8 concurrent writers, 40 writes each, no held lock | **0 / 320** |
| 4 writers, snapshot lock held **2s** (< `busy_timeout`) | **0 / 48** |
| 4 writers, snapshot lock held **11s** (> `busy_timeout`) | **4 / 48 (8%)** |

**The trigger is not writer-vs-writer contention.** `sqlite3.connect(timeout=10)`
absorbs that completely. The trigger is **a lock held longer than the busy
timeout**, which is exactly the shape `ops/backup.py` creates: it holds a read
transaction across both databases for the duration of the snapshot.

### Where it raises — this rules out one whole class of fix

Deepest frames, from the 11s run:

```
3 x  program/memory/db.py:275   conn.execute(...)      # inside save_message's body
1 x  program/memory/db.py:122   conn.execute("COMMIT") # inside transaction()
```

**Not** in `_configure()` or `ATTACH` — i.e. not during connection setup. So
"retry just the connection handshake" is not available: by the time it fails,
the caller's transaction body has already begun.

### The integrity claim, confirmed rather than repeated

After 4 failures: `archive=44 working=44`. Cross-store consistency held through
every failure. The existing `BEGIN`/`ROLLBACK` does its job, and the "not a
data-integrity risk" framing is now measured, not just argued.

---

## C2 — Recommended fix: **bounded retry with backoff**, on named write functions

Rejecting the other two on grounds specific to this project, not on general
preference.

### Why not raise `busy_timeout` (C2a)

It only moves the threshold, and the data shows exactly where the threshold is:
0% below it, 8% above it. Backup's hold time **scales with database size**, so
any fixed timeout is beatable by a large enough store — the failure returns
later, on a bigger database, which is the worst time to rediscover it.

It also trades a rare error for a **long stall**: `busy_timeout` is how long a
blocked writer waits before giving up, so raising it to 60s means a chat turn
that hangs for a minute instead of erroring. That is not obviously better for a
user, and it is invisible in tests.

Kept as a *component* of the recommendation — see C4 — but not as the fix.

### Why not serialize writes through a single writer (C2b)

**Decisive: it does not fix the observed failure.**

The failure is writer-vs-*backup*, not writer-vs-writer. An in-process write
mutex serializes writers among themselves — which the data shows was never the
problem — while every one of them still blocks on the SQLite lock backup holds.

And it **cannot** coordinate with backup, because `scripts/backup.py` runs as
**its own process**. A `threading.Lock` is process-local; a second process
taking a SQLite lock does not participate in it. Making it work would mean an
inter-process lock (a lockfile, an advisory lock) with its own lifecycle — what
starts it, what stops it, what happens to a caller when it is not running, what
happens when a holder dies mid-write — for a chokepoint that would not have
prevented the failure that was actually observed.

### Why retry (C2c)

The failure is transient by construction: a lock held by a finite operation that
will finish. Retrying is the response that matches the failure's nature, and it
bounds total tolerance without lengthening any individual wait.

---

## C3 — Where retry lives, and why it cannot live in `transaction()`

**`transaction()` is a `@contextmanager`. A context manager cannot replay its
caller's body** — by the time `__exit__` sees the exception, the body has
already run and cannot be re-entered. Combined with C1's finding that failures
occur *inside* the body and at `COMMIT`, retry has to wrap a **whole unit of
work**.

**Proposal:** a `@retry_on_locked` decorator applied to the write functions that
already are that unit:

```python
# program/memory/db.py
@retry_on_locked
def create_user(...)            @retry_on_locked
def start_conversation(...)     def save_message(...)
def end_conversation(...)       def insert_chunk(...)
def mark_conversation_chunked(...)

# program/settings/store.py
def set(...)   def clear(...)
```

Each is already a self-contained `with transaction()` block, so the diff is one
decorator line per function and no body changes.

### What is deliberately **not** decorated — a finding from checking

**`migrations.run_working_migrations()` must not be retried.** Its transaction
body calls `migration.apply(conn)` — arbitrary migration code — and mutates
Python state inside the `with` block (`applied.append(...)`, `known.add(...)`).
A retry would re-run migration code and append duplicate names to a list the SQL
rollback cannot undo.

It also does not need retry: it runs once, single-threaded, at startup, and
`MIGRATIONS` is currently empty.

Everything else was checked too. Every other `transaction()` body is **pure
SQL** plus `now_iso()`, verified by walking the AST rather than by reading:

```
db.py:174,226,242,274,363,383   -> pure SQL (+ now_iso)
store.py:421,447                -> pure SQL (+ now_iso)
migrations.py:82                -> apply(), _record(), add(), append()   <-- excluded
```

And `chunking.py` contains **no** `db.transaction()` block at all — it calls
`db.insert_chunk()` and the vector store as separate steps, so no retry can ever
re-run an embedding call. That was the specific worry worth checking, and it is
structurally impossible.

---

## C4 — What is retried, and what must never be

**Retry only `sqlite3.OperationalError` whose message is a lock message**
(`database is locked`, `database table is locked`). Everything else is either
permanent (`no such table`) or semantically meaningful.

**`sqlite3.IntegrityError` must never be retried.** `insert_chunk`'s own
docstring says a duplicate `(conversation_id, chunk_index)` raise "is how two
concurrent writers are resolved rather than by locking alone" — chunking depends
on that error as an *arbiter*. Retrying it would spin on a genuine conflict and
break a mechanism that is working as designed.

### The `COMMIT`-failed ambiguity, addressed

If `COMMIT` raises `SQLITE_BUSY`, did it commit? In rollback-journal mode, no:
the commit failed to acquire the exclusive lock, the transaction stays open, and
`transaction()`'s `except` rolls it back. So a retry cannot duplicate a write
that already landed.

The harness supports this — 4 failures, 44 succeeded, `archive == working == 44`,
with one of those failures at the `COMMIT` line. A test will assert it directly
rather than leaving it inferred (C6).

### Proposed policy — judgment values, flagged

| Setting | Proposed | Basis |
|---|---|---|
| `db.busy_timeout_seconds` | 10 (unchanged) | current value, now made configurable so tests can lower it |
| `db.write_retry_attempts` | 4 | **JUDGMENT** |
| `db.write_retry_base_delay` | 0.05s, doubling, ±50% jitter, capped 1.0s | **JUDGMENT** |

Total tolerance ≈ 4 × 10s ≈ **40s of lock hold** before a caller sees an error.
`busy_timeout` returns as soon as the lock frees, so this costs nothing when
there is no contention — the sleeps only accrue when a lock is genuinely held.

**Stated plainly:** worst case is a caller blocked ~40s before raising. That is a
long stall for a chat turn. The alternative shape — a total wall-clock deadline
rather than an attempt count — is more predictable and is open question 2.

---

## C5 — Interaction with the atomicity guarantee (the thing that actually matters)

`AGENTS.md`: *"a fix verified only against the lock-timeout symptom can weaken
the guarantee without failing anything."* So, explicitly:

**No interaction. The concerns are orthogonal, and here is why that is a
conclusion rather than an assumption:**

1. **Retry lives entirely outside `transaction()`.** It observes the exception
   after the context manager has already committed or rolled back and after
   `connection()`'s `finally` has closed the connection. It never sees a
   half-open transaction, and it has no way to resume one.
2. **Each attempt is a fresh connection.** `transaction()` calls `connection()`,
   which opens a new `sqlite3.Connection`, re-`ATTACH`es the archive, and
   re-applies both journal-mode pragmas. Attempt *n+1* shares no state with
   attempt *n*.
3. **Nothing about the `ATTACH`ed transaction semantics changes.** No new
   statement, no changed isolation level, no nesting. A retried write is
   byte-identical to a first attempt.
4. **`DELETE` journaling is untouched.** The reason for it — SQLite only
   guarantees atomic commit across attached databases when no participant is in
   WAL — is unaffected by how many times a transaction is attempted.
5. **The guarantee is a property of one transaction.** Retry changes *how many
   transactions are attempted*, never what a transaction does. The invariant
   "either both rows land or neither does" is enforced per attempt and holds for
   each attempt independently.

The failure mode that *would* weaken it — a partial commit that a retry then
duplicates — is closed by C4's `COMMIT`-BUSY analysis, and will be asserted by a
test rather than argued.

---

## C6 — Reproduction test that would have caught the original bug

The existing race test is not a reproduction: it fails ~1 run in 6 and proves
nothing when it passes.

**Proposed: a deterministic contention test.**

To make it fast and reliable, `busy_timeout` becomes configurable (it is
currently a hardcoded `timeout=10`). The test sets it to ~0.2s, so a lock held
for ~1s reliably exceeds it — the same condition as an 11s hold against a 10s
timeout, in a fiftieth of the wall time.

```
hold a snapshot-shaped lock (BEGIN + read both DBs) for ~1s
  with busy_timeout ~0.2s
  4 writer threads calling save_message in a loop
assert: zero OperationalErrors
assert: archive count == working count
assert: every write that returned is actually present
```

**What it does with the fix reverted** — this is the evidence it is a real test:
with the decorator removed (or `write_retry_attempts = 1`), the same test
produces a **non-zero** failure count. That is asserted directly as its own
test — `test_without_retry_contention_actually_fails` sets attempts to 1 and
requires `OperationalError`, so the suite proves both that the bug exists and
that the fix removes it. A test that only passes after the fix cannot
distinguish "fixed" from "never reproduced".

Measured baseline to calibrate against: **8% failure rate** at 11s hold / 10s
timeout, 0% below the threshold.

---

## C7 — The existing race test needs no change to what it asserts

`test_a_write_during_the_snapshot_cannot_land_in_one_store_only` asserts that a
writer racing a backup lands in **both** stores or **neither** — measured as
`archive count == working count` in the backup.

**Nothing that test asserts changes**, because the fix changes no SQL semantics:
no statement, no isolation level, no journal mode, no lock ordering. "Consistent"
means exactly what it meant. Only the reliability of *reaching* the assertion
changes — the writer thread stops raising, so the test stops flaking.

Its 5ms writer pacing, added when it was written, can stay. It was there to
avoid starving the snapshot's own connection, and the retry makes that pacing a
belt rather than the only thing holding the test up. **Flagged:** I would keep
it, since removing it changes what the test exercises, and that is a separate
question from this fix.

---

## C8 — What this does not fix

- **The root cause is backup's lock-hold duration**, which scales with database
  size. Retry raises tolerance to ~40s; it does not make the hold shorter.
  Reducing it is a backup-side change and out of scope here.
- **Cross-process contention is unchanged in kind.** Two processes still
  contend through SQLite; retry just makes losing survivable.
- **A genuinely stuck lock still fails**, correctly — after the attempt budget,
  a caller gets the same clean `OperationalError` it gets today.

---

## Open questions — all four resolved

1. **Decorator vs. a `write(fn)` helper.** The decorator keeps existing function
   bodies untouched (one line each) but spreads the policy across two modules. A
   `db.write(callable)` helper centralises it but rewrites all eight write
   functions. I lean decorator on diff size and because each function already
   *is* the unit of work — but the helper is more greppable, which this project
   generally prefers.

   **RESOLVED: decorator.** `@retry_on_locked` is applied to the eight write
   functions that already are the unit of work (six in `db.py`, two in
   `settings/store.py`), so no function body changed. The greppability argument
   for a `db.write(callable)` helper was real and was traded away deliberately:
   the policy is spread across two modules, and the compensating measure is that
   `retry_on_locked` names its own exclusions in one docstring —
   `migrations.run_working_migrations()`, `IntegrityError`, non-lock
   `OperationalError` — with a test asserting migrations carry no `__wrapped__`.
   C3 carries why retry cannot live in `transaction()` at all.

2. **Attempt count vs. wall-clock deadline (C4).** `write_retry_attempts = 4`
   gives ~40s tolerance but a fuzzy worst-case stall. A
   `write_deadline_seconds = 30` is more predictable and directly expressible.
   Slightly more code. I lean deadline on honesty, count on simplicity — genuinely
   unsure.

   **RESOLVED: deadline.** Shipped as
   `database.write_retry_deadline_seconds = 30.0`, with
   `database.busy_timeout_seconds = 10` and
   `database.write_retry_base_delay_seconds = 0.05` (doubling, ±50% jitter,
   capped at 1s). There is no attempt cap — the loop runs until the deadline
   gates a new attempt from *starting*, which is why **the worst case is
   `deadline + busy_timeout` ≈ 40s, not 30s**: an attempt beginning just under
   the deadline can still wait a full busy timeout.

3. **Making `busy_timeout` configurable** is required by C6's fast test. It also
   becomes a live tuning knob. Confirm that is wanted, or should it stay
   hardcoded with the test paying the 11-second cost?

   **RESOLVED: yes.** `database.busy_timeout_seconds` is read at connect time
   and is a live tuning knob as well as a test lever. C6's fast test sets it to
   **0** rather than the ~0.2s proposed here, so a ~1s hold reproduces
   deterministically what an 11s hold does against the shipped 10s timeout.

4. **Should `retry_on_locked` log?** A retry that silently succeeds hides that
   contention is happening at all. I would log at WARNING on each retry, so the
   frequency is observable before it becomes a failure — but that is a new log
   line in a hot path.

   **RESOLVED: yes, WARNING on both paths.** A line per retry (function,
   attempt, elapsed, next delay) and a line when the deadline is reached and the
   error is re-raised, so contention is observable in the ordinary case as well
   as the failing one. It costs nothing when there is no contention: neither
   line is reached unless a lock error was actually caught.
