# 2026-11-25: speed-timing-keepalive (Tier 2)

**What changed.**
- **Timing per model call (A).** Every model call inside a chat turn now logs one line,
  `ollama call timing {json}`, from the `program.engine.call_timing` logger at INFO. The line holds:
  - the turn's user message id and its conversation;
  - the step (`query_embedding`, `main`, `gate`, `corrections`), with a count within the step and
    one within the turn;
  - the endpoint and the model;
  - Ollama's own fields, durations in ns. For chat: `load_duration`, `prompt_eval_count`,
    `prompt_eval_duration`, `eval_count`, `eval_duration`, `total_duration`. For embed:
    `load_duration`, `total_duration`, `prompt_eval_count`.

  A field Ollama leaves out is logged as `null` and named in `missing`. Outside a turn, nothing is
  logged.
- **`keep_alive` (B).** New key `[ollama] chat_keep_alive`, unset by default. When set (a string such
  as `"30m"`, or seconds as an integer), it is sent on every `/api/chat` call whose model is the chat
  model. When it is absent or empty, nothing is sent, as before.

**Why.** Part A of the speed investigation had to rebuild per-call costs from Ollama's server log,
because nothing in the program kept them. Run 6 had three cold loads, each after more than 5 minutes
idle, which is Ollama's default keep-alive.

**Design.** I chose a log line over a table because it needs no schema change, and it lands in the
server log the play kit already archives with each run. Hooks:
- the client records each call right after it parses the response;
- `turn.handle_user_message` opens the turn context;
- `_answer` labels each step.

What the entity sees and says is unchanged. So are the return values and the error handling.

**Note.** `prompt_eval_count` counts the whole prompt even when the cache held most of it. Measured
2026-11-25: an identical repeat of a 6,565-token prompt reported 6,565 tokens in 0.06 s.
`prompt_eval_duration` is the measure of work done.

**Tested.** `tests/test_call_timing.py`, 16 tests:
- capture covers fields, steps, counts and the message id across a whole turn;
- nothing is logged outside a turn;
- a missing field is logged as `null`;
- a failed write is logged, and both the call and the turn still complete;
- keep_alive: absent, empty, set, only on the chat model, on the classifier, on a streamed chat,
  and a wrong type is refused.

Five mutations each failed the tests.

**Known limits.** `chat_stream` is not timed; no turn uses it. The note pass, the journal and
checkpoint embeddings are not timed, because they run outside a turn. Lyle chooses the keep_alive
value.
