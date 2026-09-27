# B8: the "warm model call stalled past 300 s" was a runaway generation, not a stall

Date: 2026-09-27 · merged-queue item 17 (diagnostic report #15) · plan B8 · Tier 2 (agent
loop, no schema).
- The approved plan was "investigate first, then decide".
- The investigation refuted the hypothesis the plan was written to test.
- The fix was then proposed and approved, on one condition: the live shape of a
  truncated reply had to be reported before the handler was finalised. That report is
  under "Step 3" below.

## The recorded finding

- One soak turn out of 178 failed with `OllamaTimeout` after 300.06 s, at t=91 min on
  2026-09-21 (23:48:09 → 23:53:09 EDT).
- Report #15 read `~/.ollama/logs/server.log` and made three claims:
  - Ollama "logged NOTHING" during the stall.
  - Ollama then placed `nomic-embed-text` with `free_swap="0 B"`.
  - Therefore the cause was a model reload while swap was exhausted.
- B8's plan followed from that: log swap at each call, reproduce a reload under
  exhausted swap, then choose between a separate embedding timeout and a keep-alive change.

## What the primary source says

All of the following is from `~/.ollama/logs/server-1.log`, which was rotated from
`server.log` on 2026-09-22, and from `data/working.db` opened read-only.

- **Ollama was not silent. It was decoding the whole time.** The request arrived at
  23:48:09 as llama-server task 3978, with a 7,098-token prompt. That task wrote a
  `slot print_timing` line about every 3 s for five minutes, at a steady ~27.5 tok/s.
  Its last line reads `n_decoded = 7564`. Then:
  `[GIN] 23:53:09 | 500 | 5m0s | POST "/api/chat"` and `srv stop: cancel task,
  id_task = 3978`.
  - Those lines have no `time=`/`level=` prefix, which is how a timestamp-based read of
    the log could see nothing between 23:47 and 23:53:09.
- **`free_swap="0 B"` carries no information on this machine.** It appears in **355 of
  355** `system memory` lines across every Ollama log going back to 2026-08-14. That
  includes loads with 22.8 GiB of RAM free. Ollama never reports anything else here.
  - macOS swap is dynamically sized: `sysctl vm.swapusage` right now reports
    total 4096M, used 2677M. So "free swap" isn't the exhaustion signal it would be on
    a fixed swap partition.
  - The boot volume had 75 GiB free when checked, so the dynamic pager had room.
- **The embedder load was incidental and fast.** `nomic-embed-text` started in 0.25 s
  and the `/api/embed` call took 292 ms. That was the *next* turn's retrieval embedding,
  arriving as the cancelled chat call released its slot.
- **What the turn asked for:** *"Compose something brief about the sound of a kettle.
  Save it."* This is a `creative_write` turn. The failed turn left the user message with
  no reply, which is the accurate record. The next turn was also a creative-writing
  request, and it completed in 28.7 s.
- **How anomalous 7,564 tokens is.** Every Ollama log has an `eval time … / N tokens`
  line per completed generation, so they can all be counted.
  - **11,860 completed generations. The longest is 797 tokens and none exceeds 1,024**.
    Median 4, because classifier calls dominate the count; p99 200; p99.9 602.
  - The cancelled generation was 9.5× the longest one that ever finished, and still
    going.
  - At 27.5 tok/s with 25,670 tokens of `num_ctx` left, it would have run roughly
    another 15 minutes before the context filled.

## The actual defect

- **Nothing bounds the length of a chat reply except the 300 s timeout.**
  - `loop.py` sends `config.model_options()` (`num_ctx`, `temperature`, `think`) and no
    `num_predict`.
  - The only `num_predict` in `program/` is the classifier's (120).
- **`history.output_reserve_tokens = 2048` is reserved but never enforced.**
  - The window budget assumes the reply fits in 2048 tokens.
  - The idle-close floor's measured worst case uses the same figure
    (`19.1 + 132.8 + 2048/22.2`).
  - Nothing tells the model to stop there.
- The comment above that value in `config/defaults.toml` says *"Under-reserving here
  truncates the reply"*. That's wrong: nothing truncates the reply. Under-reserving would
  overflow the context instead.
- **The timeout did its job.** It is the reason the runaway ended at five minutes and
  not twenty. What failed is that the timeout was the only thing bounding the call.

## Refuted, and not being built

- **A separate embedding timeout.** The embed call took 292 ms and was never the call
  that timed out.
- **A keep-alive change.** Eviction and reload were not involved. The report's own
  pre-registered prediction had already failed on this: the post-gap turn with
  `resident=[]` completed in 22.28 s.
- **Swap instrumentation and a reproduction under exhausted swap.** The signal it would
  log is constant on this machine, and the mechanism it would reproduce didn't happen.

## Not determined

- **Why the model looped.** Ollama doesn't log generated text, so the content is gone.
  - Whether it was repetition inside `creative_write`'s tool-call arguments, prose, or
    reasoning tokens despite `think = false` can't be told apart from the log.
  - All of them count toward `n_decoded`.
  - A cap bounds every one of them, so the fix doesn't depend on knowing.
- **Rate.** One occurrence in 178 soak turns, and nothing longer than 797 tokens in the
  11,860 generations that completed. No interval is claimed.

Every figure above comes from a query against the log or the store. The counting
scripts were run inline and not kept.

## Step 3: what a truncated reply actually looks like

- **Setup.** `gemma4:26b` on Ollama 0.34.2, raw HTTP, the real
  `default_registry().ollama_schema()` with all five tools offered, cases interleaved.
  Two rounds, and the shapes were identical in both.
- **A truncated tool call is not malformed.** It comes back as HTTP 200 with
  `done_reason: "length"` and a complete, parsed `tool_calls` entry whose argument is
  simply cut off:
  - `num_predict=25` gave
    `{"name": "creative_write", "arguments": {"text": "It begins as a secret, a
    low-frequency hum that vibrates against the"}}`.
  - An `image_generate` prompt at 40 tokens ended in `"…The kettle features a"`.
  - The control at 2048 finished normally: `done_reason: "stop"`, 350–356 tokens.
- **The boundary.**
  - `num_predict` 6–8: empty content and no `tool_calls`.
  - 10: `{"text": ""}`. The required key is present but empty, so the registry's
    required-key validation passes.
  - 12, 15: growing prefixes.
  - Prose: cut mid-sentence.
- `program.engine.ollama.chat` passes the same dict through unchanged.
- **So `done_reason` is the only signal.** There is no malformed-JSON path to handle,
  and the check has to run before `tool_calls` or content is read.

## The fix

- **`num_predict` on every agent chat call** (`loop.py`), set from
  `history.output_reserve_tokens` through a new `_output_cap()`.
  - Using one source means the cap can't drift from the reservation.
  - The reserve may be 0 for budgeting, but as a cap it must be positive, so 0 raises
    `ConfigError` before any model call.
  - A caller's explicit `num_predict` wins. Nothing in production passes one.
  - Classifier calls keep their own 120.
- **`ollama.OllamaOutputTruncated(OllamaError)`.**
  - The loop raises it when `done_reason == "length"`, before content or tool calls are
    read, on every iteration including the final tool-free one.
  - The route's existing `OllamaError` → 503 mapping applies unchanged.
  - Nothing is dispatched and nothing is saved. The user's message stays unanswered,
    the same record a timeout leaves.
  - Only `"length"` raises. `"stop"`, or a missing `done_reason`, proceeds as before.
  - The check lives in the loop, not in `ollama.chat`, because a classifier call hits
    its own small cap on purpose.
- **Rejected at review: saving the partial text with a marker.** That would put
  degenerate output into the append-only archive and into retrieval.
- **The `defaults.toml` comment is corrected.** It now says the value is also the hard
  cap and records the 797-token measurement.
- **Unchanged:**
  - `ollama.timeout_seconds` stays 300. It still covers a cold load plus a near-full
    prompt eval.
  - The in-flight-grace floor stays 35. It prices each call at the timeout ceiling, and
    a cap only shortens calls.

## Tested

- **10 new tests: 9 in `tests/test_loop.py`, 1 in `tests/test_chat_route.py`.**
  - `num_predict` equals the reserve on every iteration.
  - Four truncated shapes raise, each scripted from the observed responses: a valid call
    with a cut-off argument; a call with an empty required argument; an empty reply
    with no calls; prose. The tool shapes also assert the handler was never reached.
  - Truncation on the final tool-free call raises.
  - A `"stop"` reply is unaffected.
  - A zero reserve refuses before calling the model.
  - The route returns 503 with "output cap" in the detail, only the user's message is
    stored, and `list_artifacts()` is empty, with the **real** `creative_write` tool
    registered.
- **Live:** `test_live_a_truncated_tool_call_has_the_shape_the_handler_assumes` asserts
  `done_reason == "length"` with a dict-argument `tool_calls` entry at
  `num_predict=25`. It ran and passed, and it skips without Ollama. If a future Ollama
  version changes the shape, this test fails instead of the handler silently matching
  nothing.
- **Proven to bite:**
  - Disabling the `done_reason` check fails 6 tests. With the route test's artifact
    assertion moved first, it shows the truncated call **storing real `creative_write`
    artifacts** (five, because the fake repeats the call each iteration), so the route
    test isn't vacuous.
  - Removing the cap fails 6.
  - Both files were restored and verified by `diff`.
- **Full suite:** 1244 passed, 2 skipped. `ruff check .` clean.

## Known limitations

- **A runaway still costs ~75–92 s before the cap stops it,** plus prompt eval. It's
  bounded, not prevented. Why the model looped is still unknown.
- **A legitimate reply longer than 2048 tokens now fails as a 503** rather than
  completing. None of 11,860 observed generations came within 1,200 tokens of that.
  Raising the reserve trades history for headroom.
- **The person sees a 503 whose detail names the cap,** not a friendlier message. That
  matches every other model failure today.
