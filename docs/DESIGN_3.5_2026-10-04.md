# Piece 3.5 — lifecycle and concurrency: design, 2026-10-04

## Approved rulings and amendments

Approved by Lyle on 2026-10-04, before the build. The design below was written before them; where
the two differ, these win.

**Rulings**

1. Per-conversation in-process lock, never blocking a send with no `conversation_id`; 409 body as
   designed.
2. A refused reply goes **alone** into a new conversation; the user's message is not copied.
3. No new `ChatResponse` field.
4. The recovery-queue drain runs in the post-response task, bounded to one conversation, plus
   `--drain`; **not** at startup.
5. Truncate the embedding query to the **first** `config.embedding_max_input_chars()` characters
   (prefix); record the limitation.
6. `flock` on `data_dir()/server.lock`, pid text for the message only, no `--force` flag.
7. A second server on one store is refused.
8. B23's third option (route all vector writes through the server) is not taken here; Phase 6 gets
   the journal route design note only.

**Amendments**

a. **Step 2:** the sweep's last-message pick must be deterministic (`ORDER BY timestamp DESC` with a
   tie-break such as `rowid`), with a test where two messages share a timestamp. Add a sentence to
   `save_message`'s docstring that the single-statement atomicity relies on `DELETE` journal mode.
b. **Step 5, before writing anything else for it:** prove, in two real processes, that `flock`
   excludes on the actual data-dir volume (state the filesystem) and on a scratch directory, and
   that a killed holder releases it; if either fails, stop and report. Also check test **fixtures**
   (not only test functions) for a second `TestClient` on one store. `seed_dataset` must create the
   data directory before taking the lock.
c. **Step 3:** record the before and after wall time of `tests/test_chunking.py`; stop if the repair
   fires on an ordinary run.
d. The kit's `/kit/end` route must take the turn lock: do **not** edit the kit, and include the
   exact patch in the final report.

---

Design only. No repo edit, no branch, no commit, no model call. HEAD `e9bb8bc` (the squashed piece 4a),
`git status` clean at the start and at the end of this pass.

Everything below was checked against the tree at that HEAD. Reproductions ran in this session's
scratchpad with `ANAM_DATA_DIR` pointed there and a fake embedder (`ollama.embed` replaced by a
deterministic 768-float function). Nothing under `~/anam-measurements/play/` or `store-evidence/`
was read or touched; the real `data/` was never opened.

Source: `docs/FIX_PLAN_2026-10-04.md` §3.5 plus the approved rulings and amendments at the top of
that file (409 for a second turn; B23 recovery **plus** refusal; a reply that cannot be saved because
its conversation closed goes into a new conversation with `new_conversation` true; a script must
detect a live server and clear a stale lock; query-side truncation with a test). B25 is not in 3.5.

---

## 0. What I verified first, and the three plan premises that need correcting

### 0.1 The three Part A reproductions all reproduce (they become the first tests)

| # | reproduction | measured today |
|---|---|---|
| 1 | a reply saved after `finalise_conversation` is never indexed | `save_message` **succeeds** on a closed conversation; the following `checkpoint_conversation` writes 0 and skips 0; the text is in **no** chunk; the conversation is **not** in `get_unchunked_ended_conversations()` (because `chunked = 1`), so nothing will ever pick it up |
| 2 | a failed vector upsert leaves a chunk every later run skips | first `finalise` raised at the upsert with 1 chunk row written and 0 vectors; the rerun reports `written 0, skipped 1` and the vector is **still** missing; only `reconcile_vectors` finds it (it reports 1 missing) |
| 3 | the idle sweep closes a conversation that got a message after its snapshot | candidate found, then a message arrives, then the sweep closes it anyway: `closed 1, chunked 1`, `ended_at` set, with the new message in the conversation |

### 0.2 Premise correction 1 — reopening a closed conversation is not merely undesirable, it **raises**

I tested the "reopen instead of starting a new conversation" alternative (clear `ended_at`, set
`chunked = 0`, finalise again). It fails with

```
ChunkIntegrityError: chunk <cid>/0 is stored with a different text than the packer just produced.
```

because adding a message to the trailing group changes the text of a chunk index that is already
stored, and `_write_group` compares sha256 and refuses. So reopening needs a chunk-rewrite path,
which this project does not have and should not grow here. This is now an evidence-based rejection
rather than a preference.

### 0.3 Premise correction 2 — B23 has a **second, silent** shape the plan does not describe

The plan (and `NOW.md` B23) describe one failure: a Chroma `InternalError` from a query. Measured
in this session, chromadb 1.5.9, two real processes, fake vectors:

| long-lived process's first query | another process then upserts | long-lived process's next query |
|---|---|---|
| on a collection with **no on-disk segment** (empty) | 1 vector | **raises** `chromadb.errors.InternalError: Error executing plan: Internal error: Error creating hnsw segment reader: Nothing found on disk` |
| on a collection that **already held** a vector | 1 vector, then another | **no error, and the new vectors are never returned**: `query` kept answering with the pre-existing vector only, while `count()` rose 1 → 2 → 3 and `has("new-id")` returned **True** |

Both shapes are cured by the same recovery. The consequence for the design is sharp: **catching an
exception cannot detect the second shape.** A long-lived server would serve a quietly stale vector
view with nothing in the log and nothing in the trace. That is the strongest argument for the
refusal half of step 5, and it means the recovery is a safety net for the loud shape, not the fix.

I also measured which half of the recovery is load-bearing:

| attempt | loud shape | quiet shape |
|---|---|---|
| `vectors.reset_vector_store()` alone | still raises | still stale |
| `SharedSystemClient.clear_system_cache()` alone (keeping our cached store object) | still raises | still stale |
| `clear_system_cache()` **then** rebuild this store's own client and collection in place | works | works (`['b','s']` returned, count 2) |

`chromadb.api.shared_system_client.SharedSystemClient.clear_system_cache` is a `@staticmethod` that
empties two class-level dicts (`_identifier_to_system`, `_identifier_to_refcount`). Both ingredients
are needed; each gets its own mutation test.

### 0.4 Premise correction 3 — the drain does not belong at startup

The plan says "add a drain for `get_unchunked_ended_conversations` (at startup and by script)".
Final chunking **embeds**, so a startup drain would make the first model calls of the process during
`lifespan`, before the port is served: a slow or absent Ollama would delay or noisily fail boot, and
today's startup deliberately touches no model (`config.auth_session_secret()`, `capability.probe()`,
`db.init_databases()`, `vectors.get_vector_store()`). Recommended instead: drain in the existing
**post-response background task**, bounded to one conversation per request, plus a script flag. The
startup option is kept as a decision for Lyle with its cost stated.

### 0.5 Smaller facts that shape the design

- `program/api/routes/chat.py::chat` is a **sync** `def`, so it runs in Starlette's threadpool: concurrent turns
  are threads of one process (one uvicorn worker, `run_server.py` passes no `workers`), a
  `threading.Lock` is the right instrument, and a client disconnect does **not** cancel the handler —
  the function runs to completion and a `finally` releases.
- `working.messages` has `FOREIGN KEY (conversation_id) REFERENCES conversations(id)` with
  `PRAGMA foreign_keys = ON`; `archive.messages` has no such key (the archive has no conversations
  table). So a message for a missing conversation already fails on the *second* insert and rolls back
  both. The new guard must preserve that, not convert it into "closed".
- A lone assistant message **is** a complete turn to `_to_turns`/`_is_complete`, so an orphan reply
  does chunk. `_format_line` renders it `assistant: …`.
- The lexical leg survives a 50,000-character query: 4,297 distinct terms, a 69,488-character FTS5
  `OR` expression, `MATCH` executed in 0.01 s with no error (measured against an in-memory FTS5
  table). So step 4's truncation is a **vector-leg-only** concern.
- `ollama.embed` deliberately does not truncate ("sizing the input is the application's job"), and
  chunking relies on that refusal. The truncation therefore goes in the retrieval caller, never in
  `embed`.
- `config.embedding_max_input_chars()` is **5,000**, derived in `config/defaults.toml` from
  `nomic-embed-text`'s real 2,048-token context (an implied 2.44 chars/token, conservative against
  the measured 4.63). That is the limit to use, by name, not a new constant.

---

## 1. Step 1 — per-conversation turn guard (Tier 2, no stop)

### Exact behaviour

A second turn in a conversation that already has one running is refused immediately with
**HTTP 409** and the body

```json
{"detail": "a turn is already running in this conversation; it started 42 seconds ago."}
```

Nothing is written for the refused request: the guard is taken **after** the conversation is resolved
and **before** the user's message is saved, so a 409 leaves no message, no conversation and no model
call. A first send with **no** `conversation_id` is never refused: the id does not exist until
`db.start_conversation` returns, so two concurrent first sends simply produce two conversations.

### Files and functions

- **New** `program/engine/turn_locks.py` — the registry and nothing else, so `turn.py` and `idle.py`
  can both use it with no import cycle:
  - `class TurnAlreadyRunning(RuntimeError)` carrying `conversation_id` and `running_for_seconds`;
  - `held(conversation_id) -> bool` (what the sweep asks, step 2);
  - `@contextmanager turn(conversation_id)` — non-blocking `acquire(blocking=False)`; on failure
    raises `TurnAlreadyRunning` with the age of the running turn; on success records a monotonic
    start time, and releases and forgets the start time in a `finally`.
- `program/engine/turn.py` — `handle_user_message` wraps everything from after
  `_resolve_conversation` to the `return` in `with turn_locks.turn(conversation_id):`; re-exports
  `TurnAlreadyRunning` beside its other error classes so callers need one import.
- `program/api/routes/chat.py` — one more `except turn.TurnAlreadyRunning` → `HTTPException(409)`.

### Failure modes and how each is closed

| failure mode | closed by |
|---|---|
| an exception inside the turn leaves the lock held | the context manager's `finally`; test asserts a turn that raises `OllamaError` still allows the next turn |
| the client disconnects mid-turn | nothing cancels a sync endpoint, and the `finally` runs regardless; stated in the module docstring |
| a turn hangs for its whole bound | the lock can be held for the derived worst case, ~2,045 s / 35 min; every send **in that conversation** gets 409, and the escape hatch is a send with no `conversation_id`, which is never blocked. Documented in the 409 text ("it started N seconds ago") so the operator can see which case they are in |
| the guard protects nothing across processes | stated: one uvicorn worker, a `threading.Lock`, **not** a database lock. The cross-process backstop is step 2's conditional save |
| the registry grows one entry per conversation seen | accepted and named; `chunking._locks` already does exactly this at the same magnitude. Not fixed here |

### Tests, each with the one mutation that makes it bite

| test | mutation that must fail it |
|---|---|
| `test_two_turns_in_one_conversation_do_not_overlap` (a fake model that blocks on an event while a second thread posts the same conversation) | make the guard blocking (`acquire()` without `blocking=False`) → the second turn succeeds instead of 409 |
| `test_the_409_body_names_the_running_turn` | drop `running_for_seconds` from the message |
| `test_a_refused_second_turn_writes_nothing` (message count unchanged, no new conversation) | move the guard to after `db.save_message` → a message is written |
| `test_a_failed_turn_releases_the_conversation` | remove the `finally` |
| `test_two_first_sends_with_no_conversation_id_both_succeed` | key the lock by `actor.user_id` instead of the conversation id |
| `test_a_turn_in_another_conversation_is_not_blocked` | use one global lock |

### `/kit/end`-style callers

The kit's launcher-only `POST /kit/end` (`~/anam-measurements/kit-internal/bin/serve.py`) ends and
finalises a conversation inside the server process, with no knowledge of a running turn — exactly
reproduction 1's shape. `turn_locks` is therefore **public**: the kit's route should take
`turn_locks.turn(conversation_id)` non-blocking and return 409 when a turn is running. The kit is
outside the repository, so this piece only provides the handle and names the change; the repo-side
backstop is step 2 (a close conditional on the snapshot, and a save that refuses a closed
conversation), which protects the kit route even unchanged.

---

## 2. Step 2 — conversation lifecycle: atomic close, atomic save (Tier 3, **stop for review**)

### The state machine

| state | row | set where | from → to |
|---|---|---|---|
| **open** | `ended_at IS NULL`, `chunked = 0` | `db.start_conversation` (`turn._resolve_conversation`) | — → open |
| **ended** | `ended_at` set, `chunked = 0` | `db.end_conversation` (idle sweep; the kit's `/kit/end`) | open → ended |
| **chunked** | `ended_at` set, `chunked = 1` | `db.mark_conversation_chunked`, only inside `chunking.finalise_conversation` | ended → chunked |

Invariants after this step, each with a test:

1. `ended_at` is **always** set before final chunking, so an interrupted close leaves *ended, not
   chunked* — which is the recovery queue, and is drained (step 3). The reverse order would leave
   chunked-but-open, which nothing expects.
2. **No message is ever added to a conversation whose `ended_at` is set.** This is what makes
   "the trailing group is indexed exactly once, by the close" true rather than hoped for.
3. There is no transition back to open. Reopening raises `ChunkIntegrityError` (§0.2), so it is
   refused by construction rather than by policy.

### 2a. `save_message` refuses an ended conversation

```sql
INSERT INTO archive.messages (id, conversation_id, user_id, role, content, tool_trace, timestamp)
SELECT ?, ?, ?, ?, ?, ?, ?
WHERE NOT EXISTS (
    SELECT 1 FROM main.conversations WHERE id = ? AND ended_at IS NOT NULL
)
```

`cursor.rowcount == 0` → raise `db.ConversationClosed` **inside** the transaction, so the context
manager rolls back and neither store is touched. The working-store insert and the `message_count`
bump are unchanged.

Why this exact shape:

- The guard is phrased as `NOT EXISTS (… ended_at IS NOT NULL)`, not `EXISTS (… ended_at IS NULL)`,
  so a **missing** conversation still behaves exactly as today: the archive insert proceeds, the
  working insert fails on its foreign key, the transaction rolls back, `IntegrityError` reaches the
  caller. Tests and callers that rely on that are unaffected.
- The check and the write are one statement, so the conversation cannot be closed between them: a
  close committed earlier is visible to the subquery, and a close attempted later cannot commit while
  we hold the write lock. **No `BEGIN` mode, `busy_timeout`, retry or lock-ordering change** — which
  is what keeps this inside the existing cross-database atomicity guarantee rather than altering it.
  `save_message` keeps `@retry_on_locked`, so `test_every_write_in_db_carries_the_retry` still passes.
- `db.ConversationClosed(RuntimeError)` lives in `db.py` beside `StoreWouldMigrateError` and
  `ArtifactAlreadyIndexed`.

### 2b. the sweep's close is conditional on its own snapshot

```sql
UPDATE conversations SET ended_at = ?
WHERE id = ? AND ended_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM messages WHERE conversation_id = ? AND timestamp > ?)
```

- `db.end_conversation(conversation_id, *, not_after: str | None = None) -> bool` — returns whether
  it closed. `not_after=None` keeps today's unconditional close for callers that mean it (the kit
  route, scripts, tests).
- `idle.find_idle_conversations` returns a frozen `IdleCandidate(conversation_id, reason,
  last_message_at)` instead of a `(id, reason)` tuple, because the close needs the snapshot time it
  judged. `scripts/close_idle_conversations.py`'s print loop and the `find_idle_conversations`
  assertions in `tests/test_idle.py` are updated with it.
- `idle.close_idle_conversations` passes `not_after=candidate.last_message_at`, and **skips chunking
  when the close returns False**, counting it in a new `IdleCloseResult.skipped_recently_active` and
  logging at INFO. Finalising a conversation that just received a message is the harm being avoided:
  it would seal the open tail and mark it chunked while the conversation is still open.
- The in-process sweep additionally **skips a conversation whose turn lock is held**
  (`turn_locks.held(cid)`), via a `is_busy` predicate passed in by the caller so `idle.py` keeps its
  current import list. This makes a mid-turn close rare rather than routine; the conditional close is
  the cross-process backstop.

### 2c. what happens to an already-generated reply when its save refuses

Ruling: it goes into a new conversation with `new_conversation` true. Concretely, in
`turn.handle_user_message`:

- the **user message** save is wrapped too: on `ConversationClosed` (the narrow window between
  `_resolve_conversation` and the first save), start a new conversation, set `is_new = True`, and save
  there. One retry only; a second refusal is a defect and raises.
- the **assistant message** save, on `ConversationClosed`: log at WARNING naming both ids
  (`conversation %s closed while a turn was running; the reply is saved in %s`), call
  `db.start_conversation(actor.user_id)`, save the reply there, and return `TurnOutcome` with
  `conversation_id` = the new id and `new_conversation = True`. Everything after the save (the
  integrity verdict, the advisory, untrusted context, correction links) runs against the new ids
  unchanged, still inside `_after_durable`.

What each conversation then contains, and what is indexed:

- **The old conversation**: the user's message, as the trailing incomplete turn. The sweep set
  `ended_at` and then finalised, and finalise includes the open tail — so that message **is** indexed,
  once.
- **The new conversation**: the reply alone. It is a complete turn to `_to_turns`, so it chunks
  normally; `last_role` is `assistant`, so the 15-minute window applies and it closes and chunks
  within the hour if the person does not continue in it.
- **The person** gets their answer, with `conversation_id` = the new id and `new_conversation: true` —
  the same contract the client already handles for "you posted into a closed conversation"
  (`turn._resolve_conversation`). No new response field.

**Why not copy the user's message into the new conversation** (the obvious alternative): the person
said it once. A second archive row for one utterance writes a record of something that did not
happen into an append-only store, and produces two near-identical chunks competing for the same
retrieval slots. The cost of not copying is named instead: the new conversation's first chunk reads
`assistant: …` with no question in it, and nothing links the two conversations (no column could,
without a migration). Both halves are recoverable by a reader, because the two conversations are
adjacent in time and the time-window filter spans them.

**Alternative 1, reopen the old conversation** — rejected on evidence: it raises
`ChunkIntegrityError` on the next finalise (§0.2).
**Alternative 2, refuse the turn and discard the reply** (B8's precedent for a truncated reply) —
rejected: the reply is sound, the person has waited minutes, and discarding it is a real loss where
B8's partial text was itself the hazard.

### 2d. the gap where a reply saved after finalise is never indexed

Closed by 2a: that save no longer happens. The reply is written where it will be chunked, and 2b plus
the lock-skip make the case rare in the first place. Step 3 adds the belt: a checkpoint on an ended
conversation includes the open tail, and the recovery queue is drained.

### Files and functions

`program/memory/db.py` (`save_message`, `end_conversation`, new `ConversationClosed`),
`program/memory/idle.py` (`IdleCandidate`, `find_idle_conversations`, `close_idle_conversations`,
`IdleCloseResult`), `program/engine/turn.py` (`handle_user_message`), `program/api/routes/chat.py`
(`_sweep` passes `is_busy=turn_locks.held`), `scripts/close_idle_conversations.py` (the print loop and
a line for the skipped count).

### Migration

**None.** No column is added, no table is created, no constraint changes. Both changes are new `WHERE`
clauses on existing statements. (If anything here ever seemed to need a column, it would have to
stop: migration 9 is reserved for piece 7's `turn_retrieval` and 10 for piece 5.)

### Failure modes and how each is closed

| failure mode | closed by |
|---|---|
| a close commits between the guard's read and the insert | impossible: one statement, write lock held to COMMIT; test drives a close inside a patched `transaction()` seam |
| a missing conversation is reported as "closed" | the `NOT EXISTS (… IS NOT NULL)` phrasing; test asserts `IntegrityError`, not `ConversationClosed` |
| the sweep counts a close it did not perform | `end_conversation` returns bool; `closed` is incremented only on True |
| the sweep finalises a conversation that just got a message | chunking is skipped when the close returns False |
| the reply is lost when its conversation closed mid-turn | 2c, with the new id in the response |
| `ConversationClosed` escapes to the client as a 500 | it is handled in `turn.py` and never reaches the route; a route test asserts 200 with a different `conversation_id` |
| a conversation is closed while a turn runs, in another process | accepted and bounded: the reply lands in a new conversation. The in-process case is prevented by the lock-skip |

### Tests, each with its mutation

| test | mutation |
|---|---|
| `test_a_reply_saved_after_the_conversation_closed_is_indexed_or_refused` (reproduction 1, written to fail on HEAD first) | restore the unconditional `INSERT` → the reply is saved into the closed conversation and indexed nowhere |
| `test_save_message_refuses_an_ended_conversation` | as above |
| `test_save_message_on_a_missing_conversation_still_raises_integrityerror` | change the guard to `EXISTS (… ended_at IS NULL)` |
| `test_a_turn_whose_conversation_closes_mid_turn_answers_in_a_new_conversation` (asserts the reply's `conversation_id` differs, `new_conversation` is True, the old conversation holds only the question, the reply is in exactly one conversation, and it is chunkable) | remove the `ConversationClosed` handling around the assistant save → the turn raises |
| `test_the_users_message_moves_to_a_new_conversation_if_the_close_beats_it` | remove the user-side retry |
| `test_a_sweep_does_not_close_a_conversation_that_got_a_message_after_its_snapshot` (reproduction 3, fails on HEAD) | drop the `NOT EXISTS` clause from the UPDATE |
| `test_a_sweep_does_not_chunk_a_conversation_it_did_not_close` | increment `closed`/chunk regardless of the return value |
| `test_a_sweep_skips_a_conversation_with_a_running_turn` | pass `is_busy=None` from the route |
| `test_every_write_in_db_carries_the_retry` (existing) | remove `@retry_on_locked` from `save_message` |

---

## 3. Step 3 — chunk row and vector (Tier 3, **stop for review**)

### The three candidate mechanisms, compared

| option | what it does | verdict |
|---|---|---|
| **(i) upsert-if-missing in `_write_group`** | on the skip path, when the store indexes vectors and `store.has(chunk_id)` is false, embed the unit's text and upsert, counting it | **recommended.** Repairs inside the path that found the problem, no new state, no migration, idempotent |
| (ii) write the vector before the row | removes this inconsistency by creating the opposite one | **rejected.** A vector with no row is invisible to SQLite and needs a purge tool; `reconcile.py`'s docstring records that the current order was chosen for exactly this reason |
| (iii) a per-chunk state column | `chunks.vector_state` written after the upsert | **rejected, and it would force a stop:** it needs a migration, and 9 and 10 are reserved (piece 7, piece 5). It also adds a second source of truth that can disagree with the store it describes |
| (iv) compare chunk ids with the store in a pass | this is `reconcile.reconcile_vectors`, which already exists and already finds the case (measured: 1 missing) | **keep as the operator tool**, not per turn: it walks every chunk row and `has()`es each |

### Exact behaviour

1. `chunking._write_group`, skip path: today `result.chunks_skipped += 1; continue`. Now, before
   that, if `store.indexes_vectors and not store.has(existing["id"])`: `vector = ollama.embed(text)`,
   `store.upsert(existing["id"], vector, {…the same metadata…})`, `result.vectors_repaired += 1`, and
   log at WARNING (`chunk %s had a row and no vector; re-embedded and upserted`). `chunks_skipped` is
   still incremented, so every existing count assertion holds. The `indexes_vectors` guard matters:
   `NullVectorStore.has()` is always False, so without it every test on the null store would embed on
   every skip.
2. `chunking.checkpoint_conversation` includes the open tail when the conversation's `ended_at` is
   set (it still never calls `mark_conversation_chunked`; only finalise does). Safe and idempotent:
   once ended, the message set cannot change (step 2a), so a later finalise re-packs the same groups,
   finds the same sha, and skips.
3. **A drain for the recovery queue.** New `idle.drain_recovery_queue(limit: int = 1) -> DrainResult`:
   for each row from `db.get_unchunked_ended_conversations(limit=limit)`, call
   `chunking.finalise_conversation`, collecting failures the way the sweep does. Called from
   `program/api/routes/chat.py::_sweep` after the idle sweep (so it is after the response, never in the person's
   wait, and never inside the in-flight-grace arithmetic), and from
   `scripts/close_idle_conversations.py --drain [--limit N]`. Failures are logged, never raised into
   the background task. **Not at startup** — see §0.4; it is a decision for Lyle.

### Files and functions

`program/memory/chunking.py` (`_write_group`, `_run`, `checkpoint_conversation`, `ChunkingResult`),
`program/memory/idle.py` (`drain_recovery_queue`, `DrainResult`), `program/api/routes/chat.py`
(`_sweep`), `scripts/close_idle_conversations.py` (`--drain`, `--limit`).

### Migration

**None.** `ChunkingResult` is a dataclass, not a table.

### Failure modes and how each is closed

| failure mode | closed by |
|---|---|
| the repair makes a previously-free checkpoint fail when the embedder is down | accepted and stated: the embed error propagates exactly as it already does in `_write_group`, and `_checkpoint` logs it at WARNING; nothing is left half-written because the upsert follows the embed |
| `has()` per already-written chunk on every checkpoint costs time | measured obligation: time `test_chunking.py` before and after and record it. A long conversation does one `get(ids=[id])` per existing chunk per checkpoint. If it shows, the fix is a batched `present(ids)` on the protocol, which is a protocol change and so a separate task |
| the drain runs forever on a conversation that always fails | `limit=1` per request; failures are counted and logged, and the row stays in the queue |
| the drain and a sweep finalise the same conversation | `chunking._conversation_lock` already arbitrates, and the second run skips on sha |
| a checkpoint that includes the tail, followed by a message landing anyway (direct SQL, or a bug) | would raise `ChunkIntegrityError` on the next finalise. Named as a residual; step 2a is what makes it unreachable through the application |

### Tests, each with its mutation

| test | mutation |
|---|---|
| `test_a_rerun_after_a_failed_upsert_writes_the_missing_vector` (reproduction 2, fails on HEAD) | restore the bare `chunks_skipped += 1` |
| `test_the_repair_does_not_run_against_a_store_that_holds_no_vectors` (null store: no embed call) | drop the `indexes_vectors` guard |
| `test_a_repaired_chunk_keeps_its_row_and_its_text` (the sha check still runs first) | move the repair above the integrity comparison |
| `test_a_checkpoint_on_an_ended_conversation_indexes_the_tail` | keep `include_open_tail=False` when `ended_at` is set |
| `test_a_checkpoint_never_marks_a_conversation_chunked` | call `mark_conversation_chunked` from the checkpoint |
| `test_the_recovery_queue_is_drained` (an ended, unchunked conversation becomes chunked) | remove the drain call from `_sweep` |
| `test_the_drain_is_bounded_per_request` (two queued, one drained) | pass no limit |
| `test_a_failing_drain_does_not_fail_the_turn` | let the drain raise out of `_sweep` |

---

## 4. Step 4 — retrieval degradation and query-side truncation (Tier 3, **stop for review**)

### 4a. B24: a vector-store failure must not take the lexical leg with it

Today `_vector_leg` guards `ollama.embed` and not the line after it, `store.query`, so a Chroma error
propagates out of `retrieval.search()`; `turn._retrieve` then logs *"retrieval failed for this turn"*
and the turn runs with **no** records although the lexical leg had results. Observed in every failing
turn of B23.

- `program/memory/retrieval.py::_vector_leg`: wrap `store.query(...)` in `try/except Exception`, set
  `report.ran = False` and `report.skip_reason = f"the vector store could not be queried: {exc}"`, and
  log at WARNING with the **existing phrase** `"vector leg unavailable: %s"`. Using the same phrase as
  the embed failure is deliberate: the kit's tripwire already watches for it, so a degraded turn keeps
  marking itself, and the two failures of one leg read the same way in the log.
- **What the model sees: nothing new is proposed.** A Chroma failure now takes the identical path as an
  embedder failure: the passive records block renders the lexical results and says nothing about the
  leg, and `memory_search` appends its existing sentence *"[This search was incomplete: the vector leg
  did not run, so these results come from the other leg alone.]"*. That the **passive** block never
  tells the model a leg was down is a pre-existing asymmetry; it is recorded here and not changed,
  because adding prompt text is 3.1/3.6's territory and would move pinned render digests.

### 4b. query-side truncation of a long message

A chat message may be 50,000 characters; `retrieval.search` passes the raw message to `ollama.embed`,
which does not truncate, and the embedding model's real context is 2,048 tokens. So a long paste
probably loses the vector leg on every such turn (unexercised — no model call was permitted).

- `retrieval._vector_leg` truncates **only the text it embeds**, to
  `config.embedding_max_input_chars()` (5,000), using `splitting.split_text(raw, budget)[0]` so the
  boundary is the paragraph/line/sentence/word rule this project already uses rather than a new one,
  and multi-byte characters survive.
- Logged at **INFO**, not WARNING: `embedding query truncated from %d to %d characters for the vector
  leg`. A long paste is routine and nothing is degraded in the record; a WARNING would trip the kit's
  degraded-state tripwire on every long message.
- The limit's source is named in the code comment: `embedding.max_input_chars`, derived in
  `config/defaults.toml` from the embedding model's 2,048-token context. No new constant.
- **The stored message is never touched**, and neither is the lexical leg: the full text still goes to
  `build_fts_query` (measured safe at 50,000 characters, §0.5), and `chat.max_message_chars` stays
  50,000.
- Prefix, not suffix: it matches the first chunk the message will be stored as, and "truncate the
  front off a question" is the odder failure. Listed as a decision for Lyle, since the operative
  sentence of a long paste is sometimes at the end.

### Files and functions

`program/memory/retrieval.py` (`_vector_leg`, and the module docstring's degradation paragraph),
`tests/test_retrieval.py`, `tests/test_memory_search.py` (one assertion about the incomplete-search
sentence under a store failure).

### Migration

**None.**

### Failure modes and how each is closed

| failure mode | closed by |
|---|---|
| the guard hides a real defect in our own query code | the exception text goes into `skip_reason` and the WARNING, so the failure is named in both the log and the leg report; `report.ran = False` keeps "did not run" distinguishable from "ran and found nothing" |
| a bare `except Exception` swallows the suite's isolation violation | `StoreIsolationViolation` derives from `BaseException`; the same line `registry.dispatch` draws. A test asserts it propagates |
| truncation silently shortens what the person said | the stored message, the lexical leg and the prompt are untouched; only the embedded string changes, and the INFO line names both lengths |
| the truncation masks the real limit if the embedding model changes | the limit is read from config at call time, and `expected_dimension`/`max_input_chars` are already the documented pair to change together |

### Tests, each with its mutation

| test | mutation |
|---|---|
| `test_a_vector_store_failure_keeps_the_lexical_results` (a store whose `query` raises; results non-empty, `report.ran` False, `skip_reason` set) | remove the `try` around `store.query` → `search()` raises |
| `test_a_vector_store_failure_is_logged_as_the_vector_leg_being_unavailable` (caplog, exact phrase) | change the phrase |
| `test_a_store_failure_does_not_swallow_the_isolation_violation` | catch `BaseException` |
| `test_memory_search_says_the_search_was_incomplete_when_the_store_fails` | as the first mutation |
| `test_a_long_message_is_truncated_for_the_embedding_only` (a fake embedder that raises above 5,000 characters, as the model does; assert the stored message and the FTS terms are full-length) | remove the truncation → the fake embedder raises and the vector leg is skipped |
| `test_the_truncation_limit_is_the_embedding_budget` (reads `config.embedding_max_input_chars()`, not a literal) | hard-code 5,000 |
| `test_the_truncation_cuts_on_a_word_boundary` | replace `splitting.split_text(...)[0]` with `raw[:budget]` |

---

## 5. Step 5 — cross-process vector writes, B23 (Tier 3, **stop for review**)

### 5a. in-process recovery (the loud shape only)

- `vectors.ChromaVectorStore` gains `_open()` (the client and collection construction, factored out of
  `__init__`) and `_recover_stale_reader()`: call
  `chromadb.api.shared_system_client.SharedSystemClient.clear_system_cache()`, then `self._open()`.
  The exact internal API, verified in this session against chromadb 1.5.9: a `@staticmethod` on
  `SharedSystemClient` that empties `_identifier_to_system` and `_identifier_to_refcount`.
- `ChromaVectorStore.query` catches `chromadb.errors.InternalError`, logs at WARNING (*"the vector
  store's reader was stale (another process wrote vectors); rebuilding the client and retrying once"*),
  recovers, and retries **once**. A second failure propagates — and step 4a now turns that into a
  degraded leg rather than a failed turn.
- Measured: this recovers both shapes (§0.3). `reset_vector_store()` is not needed for the retry
  because the client is rebuilt in place, which is what lets the retry happen inside the call that
  failed. The module-level `reset_vector_store()` is left exactly as it is.
- **A test must fail loudly if the installed Chroma no longer has it**:
  `test_the_chroma_stale_reader_recovery_api_exists` asserts the import resolves, the attribute is
  callable, and (named in the failure message) that `requirements.lock` pins the version this was
  measured against. `requirements.txt`/`.lock` are not edited here; 4a already pinned chromadb 1.5.9.

### 5b. refusal (what actually protects the quiet shape)

- **New** `program/ops/store_lock.py`:
  - the file is `config.data_dir() / "server.lock"` — **no new config key and no new directory**, so
    the `AGENTS.md` runtime-directory rule (backup coverage, `REAL_DIRS`, `test_directories.py`) does
    not trigger; the isolation fixture already repoints `data_dir()`, and the end-of-run fingerprint
    already covers it.
  - `hold_for_server()` — context manager used by the app's `lifespan`: open the file, `fcntl.flock(fd,
    LOCK_EX | LOCK_NB)`, write `pid`, ISO start time, port and resolved data dir (truncating first),
    keep the fd open for the process's life, release and remove the text on shutdown.
  - `refuse_if_held(action: str)` — what a vector-writing script calls first: try the same
    non-blocking exclusive lock. If it fails, read the file's text and exit with
    *"refusing to <action>: a server is using this store (pid 12345, started 2026-10-04T18:02:11,
    port 8000, data dir …). Stop the server first; vectors written by another process break the
    running server's vector search until it restarts."* If it succeeds, **hold it for the script's
    run**, so two writing scripts cannot race and a server cannot start underneath one.
  - **Stale state cannot exist**: `flock` is released by the kernel when the holder dies, so a crashed
    server leaves a file with no authority. The pid text is used only for the message, never to decide.
    `os.kill(pid, 0)` liveness is **not** used, because pid reuse makes it wrong exactly when it
    matters. Named caveat: `flock` is advisory, per open-file-description, and not reliable over NFS;
    this store is local.
- The guard lives at **script entry points**, not in `vectors.upsert`: the server's own writes (the
  post-response checkpoint, the sweep, the drain, the kit's `/kit/end`) must not be refused, and they
  are the only writes that are safe while a server runs.
- Scripts covered, each gaining one `store_lock.refuse_if_held(...)` line at the top of `main()`:
  `scripts/close_idle_conversations.py` (chunking upserts), `scripts/reconcile_vectors.py`,
  `scripts/seed_dataset.py`, `scripts/write_journal.py` (`--index` only; a plain write or
  `--show-records` writes no vector and must stay usable while the server runs).
  **Not covered, deliberately:** `scripts/backup.py` (writes no vector, and backing up a running
  store is a supported case, B14), `scripts/note.py` (notes have no vectors), `set_password.py`, and
  the read-only eval harnesses — a *reader* in another process does not disturb the server.
- A new `tests/test_store_lock.py` enumerates `scripts/*.py`, and any script importing `chunking`,
  `indexing` or `reconcile` must call the guard or be listed as explicitly exempt with a reason — the
  `tests/test_scratch_helper.py` and `tests/test_directories.py` pattern, so a future script cannot
  omit it silently.

### 5c. Phase 6, stated plainly

**Phase 6's launchd journal indexing will be refused by this guard.** `write_journal --index` writes
vectors, and the server normally runs, so a nightly launchd job would either be refused (with the
message above) or, without the guard, silently break the running server's vector search until
restart. The smallest design note for Phase 6, to be written there and **not built here**:

> Journal indexing runs **in the server's process**. Add a loopback-gated admin route
> (`POST /api/journal/index` with the artifact id, behind `require_actor` plus an admin capability,
> refusing a non-loopback client the way the admin panel will), calling
> `indexing.index_existing(artifact_id)`. The launchd job authenticates and calls it; `scripts/
> write_journal --index` stays as the operator path for a stopped server, keeping the refusal guard.
> J7's control is unchanged: an entry is indexed only after a person has read it, so the route takes
> an explicit id and never scans for unindexed entries.

### Files and functions

**New** `program/ops/store_lock.py`; `program/memory/vectors.py` (`ChromaVectorStore._open`,
`_recover_stale_reader`, `query`); `program/api/app.py` (`lifespan` holds the lock);
`scripts/close_idle_conversations.py`, `scripts/reconcile_vectors.py`, `scripts/seed_dataset.py`,
`scripts/write_journal.py`; **new** `tests/test_store_lock.py`; `tests/test_vectors.py`.

### Migration

**None.**

### Failure modes and how each is closed

| failure mode | closed by |
|---|---|
| the quiet stale view (no exception) | the refusal: no other process writes vectors while the server holds the store. The recovery cannot see this shape, and that is stated in the code |
| a crashed server leaves a lock nobody can clear | the kernel releases `flock` on process death; the file's text has no authority |
| the lock blocks a legitimate concurrent backup or read | only vector-writing scripts take it; `backup.py` and the harnesses do not |
| a second server starts on the same store | also refused by the same exclusive lock. This is a **behaviour change** and the one thing to check first in the build: no current test creates two `TestClient`s in one function (checked), but if one does, this becomes a decision rather than a default |
| the private Chroma API disappears in a later version | `test_the_chroma_stale_reader_recovery_api_exists` fails with a message naming the pin |
| the recovery loops | one retry, then the exception propagates into step 4a's guard |
| the old Chroma system is left unstopped by `clear_system_cache` | named as an unverified cost: the dicts are emptied without `stop()`, so a recovery may leak the old reader until collection. Unmeasured, and the recovery is rare by design |

### Tests, each with its mutation

| test | mutation |
|---|---|
| `test_a_second_process_writing_vectors_does_not_break_the_first` (the measured loud shape: query an empty collection, a real subprocess upserts, query again) | remove `clear_system_cache()` from the recovery → still raises (measured: reset alone does not recover) |
| `test_a_second_process_writing_vectors_does_not_leave_a_stale_view` (the quiet shape: a seeded collection, query, a subprocess upserts, query again — asserts the new id comes back) | remove the `self._open()` rebuild → the stale view persists (measured) |
| `test_the_recovery_retries_only_once` (a store whose query always raises) | retry in a loop |
| `test_the_chroma_stale_reader_recovery_api_exists` | import a different attribute name |
| `test_a_script_refuses_while_a_server_holds_the_store` (hold the lock in-process, call the script's `main`) | remove the guard line → it proceeds |
| `test_a_script_runs_once_the_lock_is_released` (and after a simulated crash: a subprocess that takes the lock and is killed) | use `os.kill(pid, 0)` liveness off the file instead of `flock` → the killed holder's pid text still refuses |
| `test_the_refusal_names_the_pid_and_the_data_dir` | drop the file's text |
| `test_every_vector_writing_script_takes_the_guard` (enumeration) | add a decoy script that writes vectors without it |
| `test_the_server_releases_the_lock_on_shutdown` | leave the fd open past `yield` |

---

## 6. Implementation order, and what each step's tests must show before the next begins

The order follows the plan, and each step is one commit set on `cc/3.5-lifecycle` with its own
`git add` list; steps 2–5 each stop for review before the next starts.

| order | step | what must be shown before moving on |
|---|---|---|
| 0 | **the three reproductions as failing tests**, committed first | each fails on HEAD for the stated reason, with the failure output in the changelog. Nothing else in the commit |
| 1 | turn guard (Tier 2, diff review) | the six step-1 tests pass; the 409 body; a refused turn writes nothing; the full suite unchanged otherwise |
| 2 | atomic close and save (**stop**) | reproductions 1 and 3 now pass; `test_every_write_in_db_carries_the_retry` still passes; the mid-turn-close turn answers in a new conversation; `test_idle.py`'s grace-floor derivation tests pass **unmodified** (this step must not move `IN_FLIGHT_GRACE_FLOOR_MINUTES`) |
| 3 | chunking repair, checkpoint-when-ended, drain (**stop**) | reproduction 2 now passes; the null-store test shows no embed; the recorded before/after time of `test_chunking.py` |
| 4 | B24 and truncation (**stop**) | a store failure keeps lexical results and names the leg; the truncation test with the stored message asserted intact; no pinned render digest moves (`B17` render, the B20/B21 fitting-turn digest, `e5c92a42…806762`) |
| 5 | B23 recovery and refusal (**stop**) | both shapes recovered in two real subprocesses; both mutations fail; the script enumeration test; the full suite and `ruff` |

Each step's commit carries its mutation evidence run with `PYTHONDONTWRITEBYTECODE=1`.

## 7. What, in each step, would show the design is wrong (stop the build and report)

| step | the surprise |
|---|---|
| 1 | a test that needs two turns in one conversation to overlap (the guard would be breaking a real use), or any test where the 409 arrives for a *sequential* second send — that would mean the lock is not released on some path |
| 2 | `cursor.rowcount` not being 0/1 as expected for `INSERT … SELECT … WHERE` on this SQLite, or the conditional insert changing the behaviour of a *missing* conversation; a `database is locked` appearing in the new tests, which would mean the guard changed the locking profile after all (it must not); any need for a column |
| 3 | `store.has()` per chunk showing up as a visible cost in `test_chunking.py`'s time, or the repair firing on an *ordinary* run (which would mean vectors are going missing routinely, a different defect) |
| 4 | the truncated query changing retrieval results for ordinary short messages (it must be a no-op below 5,000 characters), or a pinned digest moving |
| 5 | the quiet shape not reproducing in the test harness (it needs the collection to be non-empty at the first query), the recovery not working inside the server's threadpool, or two `TestClient`s in one process needing the same store |

## 8. Files touched, and the overlap with later pieces

| file | 3.5 touches | also wanted by |
|---|---|---|
| `program/engine/turn_locks.py` (new) | step 1 | — |
| `program/engine/turn.py` | steps 1, 2 | 3.3, 3.4b, 3.6 (via `tests/test_turn.py` digests) |
| `program/api/routes/chat.py` | steps 1, 2, 3 | piece 7 (recording retrieval per turn) |
| `program/memory/db.py` | step 2 (`save_message`, `end_conversation`) | 3.4b (batched read), 3.6, piece 5 (startup trigger check), piece 7 (migration 9 table writes) |
| `program/memory/idle.py` | steps 2, 3 | 3.5 only |
| `program/memory/chunking.py` | step 3 | 3.5 only |
| `program/memory/retrieval.py` | step 4 | 3.1b (links rendering), 3.4a (window), 3.4b, 3.6, piece 6 (floors) |
| `program/memory/vectors.py` | step 5 | — |
| `program/ops/store_lock.py` (new) | step 5 | Phase 6 (journal route), piece 5 (restore must not run against a live server either — worth reusing) |
| `program/api/app.py` | step 5 (lifespan) | piece 5 (startup trigger check) |
| `scripts/close_idle_conversations.py`, `reconcile_vectors.py`, `seed_dataset.py`, `write_journal.py` | steps 3, 5 | Phase 6 |
| `tests/test_idle.py` | steps 2, 3 | nothing — but its grace-floor derivation tests must pass unmodified |
| `BUILT.md` / `NOW.md` / `ARCHITECTURE.md` / changelog | docs set, last | every piece (serialise) |

**Serialisation that matters:** `retrieval.py` is wanted by 3.1b, 3.4a/b and 3.6, so step 4 should land
before 3.1 starts (the agreed order already does this). `db.py` is wanted by piece 5 and piece 7; step
2's change is confined to two statements and adds no column, so it does not constrain them.
`ARCHITECTURE.md` gains invariants for the lifecycle state machine and the leg-degradation rule, each
cited to one of the new tests.

## 9. Decisions for Lyle

1. **Second turn in one conversation: 409 (ruled) — confirm the lock's scope.** Recommended as
   designed: per conversation id, in-process, acquired after resolution so a refusal writes nothing,
   and a send with no `conversation_id` is never refused.
2. **The refused reply's home.** Recommended: **the reply alone** in a new conversation, because
   copying the person's message would write a second archive row for one utterance. Alternative:
   copy the question too, for a coherent chunk at that cost. (Reopening is out — it raises.)
3. **Whether `ChatResponse` should say *why* the conversation changed.** Recommended: no new field;
   `new_conversation: true` plus the WARNING naming both ids is enough.
4. **Where the recovery-queue drain runs.** Recommended: the post-response background task (bounded
   to one) plus `--drain`. Alternative: also at startup, which would make the first model calls of
   the process during `lifespan` and could delay or fail boot when Ollama is down.
5. **Truncation side for the embedding query.** Recommended: the first 5,000 characters (matches the
   message's first stored chunk). Alternative: the last 5,000, if the operative sentence of a long
   paste is usually at the end. Hybrid rejected: it embeds text nobody wrote.
6. **The store lock's mechanism.** Recommended: `flock` plus a descriptive pid file, because a crash
   leaves no stale authority. Alternative: a pid file with `os.kill(pid, 0)` liveness, which is wrong
   under pid reuse. Sub-decision: **no `--force`/`--ignore-lock` flag** (the correct action is to stop
   the server).
7. **Whether a second server on one store should also be refused** (it falls out of the same lock).
   Recommended: yes.
8. **B23's third option is deliberately not taken now:** routing *all* vector writes through the
   server. Recommended: recovery plus refusal now (as ruled), with the server route added for Phase
   6's journal indexing only, where launchd forces it.

## 10. Expected model-free test time

Measured now, without `--run-live`: the nine files this piece touches or neighbours
(`test_idle`, `test_chunking`, `test_turn`, `test_chat_route`, `test_retrieval`, `test_vectors`,
`test_db`, `test_db_contention`, `test_memory_search`) run **213 tests in 16.2 s**. The full suite is
**2,002 passed, 23 skipped in ~49 s** (piece 4a's last recorded run).

Estimated additions: about **32 tests**, of which the slowest are the two B23 subprocess tests (a
measured full repro cycle, two Python starts and two Chroma opens, ran in **0.74 s** wall) and the
threaded overlap test (bounded by an event, not a sleep). Expected cost **+6 to +10 s**, so a full
suite around **55–60 s**. The one unknown is step 3's `has()` per existing chunk, which is why the
before/after time of `test_chunking.py` is part of that step's evidence.

## 11. What I did not verify

- **That a long real message actually fails to embed.** No model call was permitted; the behaviour is
  read off `ollama.embed`'s refusal to truncate plus the model's 2,048-token context. The truncation
  test therefore uses a fake embedder that raises above the budget, which is a model of the failure,
  not the failure.
- **Everything about B23 was measured with fake vectors**, not real embeddings. The failure is in
  Chroma's reader, not in the vectors, so this should not matter — but it is a difference.
- **The quiet stale shape in the real server.** I reproduced it in two plain processes; I did not run
  a server. The kit's own note (vectors written by another process break the running server until
  restart) is consistent with it.
- **Whether `fcntl.flock` behaves on this volume** (`/Volumes/Dock Storage`, APFS, local): not tested
  in this pass. It is the first thing step 5 should prove, before anything else is built on it.
- **Timing of the recovery inside the threadpool** (a rebuilt client under concurrent turns).
- **`last_role` when two messages share a timestamp**: in reproduction 3 the sweep reported the
  in-flight-grace window for a conversation whose last message was the assistant's, because both rows
  carried the same timestamp and `MAX()`'s bare-column pick is arbitrary. Real turns differ by
  seconds, so this is an artifact of my fixture rather than a defect to chase — but it is the kind of
  thing that makes a test pass for the wrong reason, so it is recorded.
- **Whether any fixture (not a test function) creates a second `TestClient` against one store.** I
  checked test functions only.
