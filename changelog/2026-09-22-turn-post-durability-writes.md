# A turn that succeeded can no longer surface as a 500

2026-09-22 · finding #7 of the diagnostic pass · narrow fix, authorised individually

## What was wrong

Three writes run after `db.save_message()` has put the assistant's answer in both
stores — the integrity verdict (`turn.py:326`), the advisory note (`:329`) and the
correction links (`:337`). **None of them was guarded, and there is no `try` anywhere
in `handle_user_message`'s body.**

`routes/chat.py` catches exactly three exceptions: `ConversationAccessError` → 404,
`EmptyMessageError` → 400, `ollama.OllamaError` → 503. Anything else is an unhandled
500.

So a `sqlite3.OperationalError: database is locked` — which `db.create_supersedes_link`
raises once `@retry_on_locked` passes its deadline, and which
`corrections.record()` does **not** catch, since it catches only the expected
`sqlite3.IntegrityError` schema refusal — reached FastAPI as a server error **on a turn
whose answer was already durable**.

The person saw a 500 and never saw the reply. The reply was in both stores, so the
*next* turn's history contained an answer they were never shown, and the entity
continued as though it had said something the person never read.

**Reachable, not theoretical.** Measured this session: with a writer racing a backup
snapshot, `database is locked` fired **2 of 5 times**, the retry machinery behaving
exactly as documented before giving up —
`gave up after 32.4s of lock contention (2 retries)`. The concurrent writers that
produce this in production are the ones `BUILT.md` already names: the post-response
background idle sweep, and `backup.py` holding its cross-store read lock.

## What changed

`program/engine/turn.py` only.

- **`_after_durable(step, conversation_id)`**, a context manager, wraps each of the
  three writes. It catches `Exception`, logs a WARNING naming *which* step was lost,
  and returns the answer unchanged.
- **`except Exception`, never `BaseException`**, deliberately: `KeyboardInterrupt`,
  `SystemExit` and the suite's `StoreIsolationViolation` must still propagate. Same
  line `registry.dispatch` draws.
- The module docstring gains a section stating the invariant, and
  `_record_corrections`' docstring is corrected: it promised *"never raises"* while
  its `try` covered only assembling and classifying, with the write loop outside it.
  The guarantee now lives in one place instead of being claimed in a docstring and
  enforced nowhere.

**Deliberately not done**, per the review's scope instruction: no general resilience
audit of `turn.py`, and no change to the ordering, to `db.py`'s locking, or to
`corrections.record()`'s own exception handling.

## Why swallowing these three is safe, per step

Stated in the helper's docstring rather than left to be worked out:

- **integrity verdict** — `messages.integrity_check` stays `NULL`, which `gate.py`
  defines as *no verdict recorded*, never as clean. The record stays honest; the turn
  is simply unchecked. **Asserted by a test**, not assumed.
- **advisory note** — a non-authoritative signal is missing. It could never change a
  verdict; that is what migration 4 split the column off to guarantee.
- **correction link** — a correction is missed, leaving the record accurate and merely
  uncorrected. The same direction of failure `corrections.py` already chose with
  *never link by default*.

Each is better than the alternative it replaces: a 500 for a turn that succeeded.

## What was tested

Seven new tests (`tests/test_turn.py`, `tests/test_chat_route.py`):

- each of the three writes failing leaves the turn standing, with the answer returned
  and on record;
- all three failing at once still returns the answer;
- a failed verdict write records **NULL** rather than something false;
- the failure is **logged with which step was lost** — a swallowed write must not be a
  silent one;
- a `BaseException` still propagates;
- the route returns **200, not 500**, when the correction-link write raises.

**Proven to bite.** With the guard neutered, **6 of the 7 fail**. The seventh is the
`BaseException` control, which passes either way — that is what shows the guard is not
over-broad.

Full suite: **1147 passed, 2 skipped** (the two ComfyUI live tests, which skip when
ComfyUI is down), `ruff` clean.

## Known limitations

- **The underlying contention is untouched.** This makes a lost write survivable; it
  does not make the write succeed. `db.py`'s write-serialisation question remains the
  open Tier 3 item it already was.
- **A lost correction link is not retried and not queued.** There is no equivalent of
  `db.get_unchunked_ended_conversations()` for supersedes links, so the correction is
  simply missed. That is the accepted direction of failure, but it is now reachable
  silently-but-logged rather than loudly-and-fatally.
- The three log lines are the only signal. Nothing counts them, so a store under
  sustained contention would drop verdicts and links at a rate visible only in the log.
