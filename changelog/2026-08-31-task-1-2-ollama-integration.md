# 2026-08-31 — Task 1.2: Ollama integration (chat + embeddings, num_ctx pinned)

**Tier 1 · Sonnet · not gated.**

## Summary

Chat and embedding clients against the local Ollama instance, with `num_ctx`
pinned to a measured value and every failure mode surfacing as a distinct,
named exception. Tests run against the real instance.

**Placement:** `anam/engine/ollama.py`. `engine/` is where model plumbing
belongs — `memory/` is stores and retrieval, `tools/` is things the entity
calls, `integrity/` is the gates. The client decides nothing; it moves requests
and fails loudly.

## Files changed

- `anam/engine/ollama.py` — new. Chat (streaming and not), embeddings, error
  taxonomy, `is_available()`, `loaded_models()`.
- `anam/config.py` — added `[models]`, `[model_options]`, `[embedding]` to the
  fallback; six new `ANAM_*` env mappings; accessors.
- `config/defaults.toml` — same sections, with the reasoning inline.
- `tests/test_ollama.py` — new, 17 tests.
- `BUILT.md`, this changelog.

---

## num_ctx: what was actually run

### `ollama ps` — before doing anything

```
NAME    ID    SIZE    PROCESSOR    CONTEXT    UNTIL 

```

**Empty.** No model was loaded. Worth stating plainly rather than glossing:
the task says to check `ollama ps` against "whatever model is actually
configured for chat use right now", and the honest answer at the start was that
nothing was loaded *and* nothing was configured — `config/defaults.toml` had an
`[ollama]` host and timeout but no `[models]` section at all. Task 1.2 is where
a chat model gets chosen for this build for the first time.

### `ollama list` — what is actually on the machine

```
NAME                       ID              SIZE      MODIFIED     
qwen3.5:9b                 6488c96fa5fa    6.6 GB    2 weeks ago     
gemma4:26b-mlx             c8656f50f0a6    17 GB     8 weeks ago     
qwen3.5:27b-mlx            41eb9db48b39    19 GB     3 months ago    
qwen3.6:27b                a50eda8ed977    17 GB     3 months ago    
qwen3.5:27b                7653528ba5cb    17 GB     3 months ago    
nomic-embed-text:latest    0a109f422b47    274 MB    3 months ago    
gemma4:26b                 5571076f3d70    17 GB     3 months ago    
mistral-small3.2:latest    5a408ab55df5    15 GB     3 months ago    
```

### `ollama show gemma4:26b`

```
  Model
    architecture        gemma4    
    parameters          25.8B     
    context length      262144    
    embedding length    2816      
    quantization        Q4_K_M    
    requires            0.20.0    

  Capabilities
    completion    
    vision        
    tools         
    thinking      

  Parameters
    temperature    1       
    top_k          64      
    top_p          0.95    

  License
    Apache License               
    Version 2.0, January 2004    
    ...                          
```

### `ollama show nomic-embed-text`

```
  Model
    architecture        nomic-bert    
    parameters          137M          
    context length      2048          
    embedding length    768           
    quantization        F16           

  Capabilities
    embedding    

  Parameters
    num_ctx    8192    

  License
    Apache License               
    Version 2.0, January 2004    
    ...                          
```

### `ollama ps` — after loading at the pinned value

```
NAME                       ID              SIZE      PROCESSOR    CONTEXT    UNTIL              
gemma4:26b                 5571076f3d70    17 GB     100% GPU     32768      4 minutes from now    
nomic-embed-text:latest    0a109f422b47    370 MB    100% GPU     2048       4 minutes from now    
```

### Environment

```
Mac16,10 · arm64 · 32 GB RAM · ollama version is 0.32.13
```

---

## Choosing 32768

**The model's ceiling is 262144.** That is its capability, not a sensible
allocation. Working out what the machine can actually carry:

**`ollama ps` SIZE cannot answer this.** It reports 17 GB at *every* context
size — I loaded the model at 32768, 65536, 131072 and 262144 and the column
never moved, because KV cache is allocated lazily rather than at load. So the
obvious measurement is not a measurement.

**I could not compute KV cache size honestly either.** The architecture metadata
reports `block_count 30`, `head_count 16`, `key_length 512`, `value_length 512`,
`sliding_window 1024`, with separate `key_length_swa`/`value_length_swa` of 256
— but `attention.head_count_kv` comes back **`None`** and
`sliding_window_pattern` is unspecified, so the number of KV heads and the
local/global layer split are both unknown. Any figure I derived would have been
a guess dressed as arithmetic. Not presenting one. (The sliding-window design is
almost certainly why 262144 loads at all — most layers cap their KV at a
1024-token window.)

**So I measured behaviour instead.** A real 81,600-character prompt at
`num_ctx=32768`:

```
prompt chars       : 81600
prompt_eval_count  : 17626
response           : 'Harbour'   (correct)
elapsed            : 76s
ollama ps after    : 17 GB, 100% GPU, CONTEXT 32768
memory free after  : 27%
swap               : 2733 MB used of 4096 MB
```

A 17,626-token prompt completes comfortably, stays entirely on GPU, and leaves
the machine in better shape than it was in at idle-with-model-loaded (8% free,
2925 MB swap — measured before this run).

**Why 32768 and not more:**

1. **The application cannot fill it.** The retrieved-context budget is ~14,000
   chars and the prompt-budget warning threshold ~30,000 chars — roughly 6,500
   tokens at the ratio measured below — plus system prompt and windowed history.
   32K is about twice anything this build currently assembles.
2. **Headroom that is used is headroom that costs.** The machine already swaps
   ~2.9 GB with the model merely loaded. Context the application never fills is
   free; context it *does* fill is KV cache on a 32 GB shared-memory box.
3. **It is verified end to end**, not just accepted at load: a test asserts
   Ollama reports `context_length: 32768` for the loaded model *after* a real
   chat call, so the pin demonstrably takes effect rather than merely existing
   in a config dict.

Raising it later is a one-line config change and now a measured decision rather
than a guess.

### A measurement that lands on task 1.10

81,600 chars → 17,626 tokens is **4.63 chars/token** on ordinary prose. Task
1.10's ruling was a character-based estimate at ~4 chars/token biased to err
high. Estimating 4 where reality is 4.63 means the estimator predicts *more*
tokens than actually occur — it under-fills the window, which is the safe
direction that ruling asked for. First real datapoint confirming it; dense
content will run lower and should be re-measured when there is some.

---

## Model selection — flagged, not assumed

Nothing in `PROJECT.md` or `NOW.md` names a chat model for this build, so this
was an open choice. I configured **`gemma4:26b`**, matching the reference build's
chat model, on the grounds that it is the well-trodden quantisation (Q4_K_M),
carries `tools` capability that Phase 2 needs, and has `vision` for whenever the
camera arrives.

**`gemma4:26b-mlx` is worth your consideration and I did not switch to it.** Same
26B model, nvfp4 quantisation, MLX builds are usually faster on Apple Silicon,
and it needs Ollama ≥ 0.31.0 (we have 0.32.13). It lacks `vision`, which is
deferred anyway. This is a one-line config change; say the word. Flagging rather
than deciding, since it is a real performance choice about hardware I cannot
benchmark meaningfully in one sitting.

`think` is pinned **off**: gemma4 advertises a thinking capability, it costs
latency and tokens on every turn, and nothing in this build consumes a reasoning
trace.

---

## The embedding model's context trap, confirmed empirically

`ollama show nomic-embed-text` reports **context length 2048**, while its own
Parameters block defaults **`num_ctx` to 8192**. Those disagree, and `/api/ps`
sides with the smaller: it reports `2048` for the loaded model.

This is the reference build's hard-won over-length embedding lesson, visible
directly in the model metadata. `embed()` therefore **does not truncate** —
silently shortening input would hide that a chunk was too big. Sizing input is
the caller's job, `embedding.max_input_chars` (5000) is the budget, and the
splitter that enforces it is task 1.3.

`embedding length 768` confirmed from both `ollama show` and a real embed call,
and asserted on every call.

---

## Error handling

Five named exceptions under one `OllamaError` base, because "something went
wrong talking to the model" is not useful when the real situation is "you have
not pulled this model":

| exception | condition | message includes |
|---|---|---|
| `OllamaUnreachable` | nothing listening | the host, and `ollama ps` as the check |
| `OllamaTimeout` | accepted but silent | the timeout, and that cold loads can exceed it |
| `OllamaModelNotFound` | 404 with a model body | the model name and `ollama pull <model>` |
| `OllamaResponseError` | HTTP error, non-JSON, missing fields | status and body excerpt |
| `EmbeddingDimensionError` | wrong vector width | got, expected, and why it matters |

Every request carries an explicit timeout. Nothing can hang indefinitely.

The 404 disambiguation was written against the real body, verified live:
`{"error":"model 'definitely-not-a-real-model:1b' not found"}`.

---

## Tests: 17, live where possible, no mocks on the failure paths

```
17 passed, 0 skipped
```

Confirmed by `-v` grep: 17 PASSED, 0 SKIPPED — the live tests genuinely ran
rather than silently skipping, which is the exact way this kind of integration
gets to look tested without being tested.

**Live against the real instance:** chat returns text; `chat_text` helper;
streaming yields multiple chunks and terminates with `done`; embed returns 768
dimensions; embed is deterministic for identical input and differs for different
input; unknown model raises with the model name and the fix; `is_available()`.

**`test_live_num_ctx_actually_reaches_the_server`** is the one that matters most
for this task: it makes a real chat call, then reads `/api/ps` and asserts the
loaded context is 32768. Configuring `num_ctx` and it *taking effect* are
different claims and only the second one is worth anything.

**`test_live_embedding_dimension_guard_fires`** makes a real embedding call with
the expectation deliberately set to 999 and asserts the guard raises naming both
numbers — proving the guard fires rather than assuming it would.

**Failure paths use real injection, not mocks.** Unreachable is a genuinely
closed port (127.0.0.1:1). The timeout test stands up a real socket that accepts
a connection and then never writes, which is the failure mode that actually
matters in a live turn — unreachable is fast and obvious, a stalled socket
blocks forever without an explicit timeout.

**Where mocking was used: nowhere.** No mocked transport anywhere in this file.

**Live tests skip rather than fail when Ollama is unreachable**, so the suite
runs on a machine without it. That skip is the single portability trade in the
file, and a skipped test is visible in pytest output where a mocked one looks
like a pass.

Full suite: **71 passed**, `ruff check .` clean.

---

## Known limitations

- **KV cache growth is uncharacterised.** I measured to ~17.6K tokens, not to
  the 32K ceiling. Behaviour with a genuinely full context window is untested,
  and the architecture metadata does not let me predict it. If long-context
  turns ever feel slow or the machine starts swapping hard, this is the first
  thing to measure properly.
- **The 4.63 chars/token figure is one sample of English prose.** Code and
  symbol-dense text run lower. One datapoint, not a calibration.
- **`is_available()` is for health reporting, not control flow.** Checking then
  calling leaves a window where the answer changes and doubles the round trips;
  callers needing a model call should make it and handle the exception. Said in
  the docstring, not enforceable.
- **No retry or backoff anywhere.** A transient failure surfaces to the caller.
  Deliberate for now — retries hide instability, and there is no caller yet with
  a considered opinion about what to do on failure.
- **Streaming raises on an undecodable line rather than skipping it.** A dropped
  chunk is output the caller never knows was missing. Correct, but it means one
  malformed line fails a whole turn.
- **`ollama ps` output in this entry shows `4 minutes from now`** for both
  models — Ollama's default keep-alive. Nothing in this build sets it yet.

## Follow-up

- Task 1.3 owns the input splitter working to `embedding.max_input_chars`.
- Task 1.10 calibrates its token estimate against `prompt_eval_count`, which
  `chat()` returns.
- Task 2.2's agent loop consumes `chat_stream`.
- Model choice (`gemma4:26b` vs `gemma4:26b-mlx`) is open, above.
- Ollama keep-alive is unset; if model load latency becomes annoying, that is
  the knob.

## Project Anam alignment check

1. Assign the entity a name? **No.**
2. Call the entity Anam or Tír? **No.**
3. Assign personality? **No** — `temperature` is a sampling parameter, and no
   persona text exists anywhere in this task.
4. Preserve raw experience? **N/A** — nothing is stored here.
5. Traceable derived artifacts? **N/A.**
6. Tool calls recorded? **N/A** — `tools` is plumbed through to the payload but
   no tool exists until Phase 2.
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **Partly** — `chat()` returns Ollama's full
   response including counters rather than just the text.
9. Autonomy more cumulative? **N/A.**
10. Anam/entity distinction preserved? **Yes.**
11. Migration required? **No** — config only, no schema touched.
12. Tests? **Yes**, 17, live against the real instance.
13. Core substrate changed unnecessarily? **No.**
14. External dependencies added? **None new** — `requests` was already in
    `requirements.txt` from Phase 0.
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes** — fresh implementation; the
    reference build was not consulted for this task, which does not point at it.
