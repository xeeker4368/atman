# B1: mid-conversation chunking now runs

Date: 2026-09-24 · merged-queue item 5 · plan B1 · Tier 3 (chunking pipeline), plan
approved before implementation

## What was wrong

`chunking.checkpoint_conversation()` had no production caller. Its docstring says "Called
after a completed assistant turn", but `turn.py` never imports chunking. The only
production chunking call was `finalise_conversation()`, which runs at idle-close (plus
the seed script).

- **Measured on the live store during the soak:** 0 chunks belonged to any open
  conversation. At one sample, 76 of 227 messages sat in no chunk at all.
- **Consequence:** with the token-budgeted history window, a long session's early turns
  were neither resent to the model nor retrievable until the conversation closed.

## What changed

`program/api/routes/chat.py`:

- A new `_checkpoint()` is scheduled with `background.add_task` for the active
  conversation, before the existing `_sweep`.
- **Why after the response:** a sealed group costs an embedding call. Inside the turn,
  that call would enter the in-flight-grace floor's arithmetic. This is the same reason
  the idle sweep runs there.
- **The floor does not move (still 35).** Nothing was added to the turn itself.
- **Failures are logged, never raised.** `ChunkIntegrityError` logs at ERROR, because it
  means the boundary rule disagrees with the store. Anything else logs at WARNING. A
  failed checkpoint loses nothing: the next checkpoint, or final chunking at idle-close,
  writes the groups.
- **Overlap with other writers:** a checkpoint running at the same time as a sweep or a
  close of the same conversation is the case chunking's per-conversation lock and the
  `(conversation_id, chunk_index)` unique index already arbitrate between the two entry
  points. The existing `test_concurrent_write_of_the_same_index_is_resolved_not_duplicated`
  covers it.

No change to `chunking.py`, `turn.py` or the schema. The pinned two-entry-point surface
is unchanged.

## Tested

Three new tests in `tests/test_chat_route.py`, using real chunking and deterministic
embeddings:

- **A sealed group is chunked while the conversation is open.** Nine turns are run. The
  eighth-turn cap seals the first group, so exactly one chunk exists, containing turn 0
  and not turn 8. The conversation is still open.
- **Nothing is chunked while only the open group exists.** Three turns are run with the
  embedder set to raise if called.
- **A failed checkpoint doesn't fail the turn.** The ninth turn runs with the embedder
  unreachable and returns 200 with 0 chunks. The tenth turn, with the embedder back,
  writes the group.

**Proof it bites:** deleting the `add_task(_checkpoint, …)` line fails the first and third
tests. The second is the control and passes either way.

Full suite **1189 passed, 2 skipped** (was 1186/2). `ruff check .` clean.

## Known limitations

- **Background tasks run one after another.** A slow checkpoint embedding (worst case the
  300 s Ollama timeout) delays that request's idle sweep. It does not delay the person,
  who already has their answer.
- **The current conversation's sealed chunks can now reach the retrieval side of
  `corrections.candidates()`.** That is intended and is what B2 is ordered after, but no
  test yet covers the interaction.
- **Not verified live** against a real server and a real embedder in this change. The
  tests use TestClient, which runs background tasks before returning. A live
  multi-turn check is owed.
