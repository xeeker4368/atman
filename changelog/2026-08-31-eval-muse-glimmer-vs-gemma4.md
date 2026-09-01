# 2026-08-31 — Evaluation: muse-glimmer:30b-mlx vs gemma4:26b

**Not a BUILD_PLAN task. Measurement only — nothing was reconfigured.**
`config/defaults.toml` still specifies `gemma4:26b`; changing it needs separate
approval.

## Environment note before the numbers

`ollama list` now shows **three** models. Task 1.2's changelog recorded **eight**
on 2026-08-29. Gone since: `qwen3.5:9b`, `qwen3.5:27b`, `qwen3.5:27b-mlx`,
`qwen3.6:27b`, `mistral-small3.2`, and **`gemma4:26b-mlx`**.

That last one matters: task 1.2 flagged `gemma4:26b-mlx` as the most likely
performance win and left it open for a decision. It is no longer installed, so
that option is closed unless it is re-pulled. Presumably it made room for
muse-glimmer's 19 GB.

Host unchanged: Mac16,10, arm64, 32 GB, Ollama 0.32.13.

---

## 1. `ollama show muse-glimmer:30b-mlx`

```
  Model
    architecture        muse_glimmer    
    parameters          32.3B           
    context length      131072          
    embedding length    6656            
    quantization        nvfp4           
    requires            0.32.7          

  Capabilities
    completion    
    vision        
    tools         
    thinking      

  Parameters
    top_k                64      
    top_p                0.95    
    draft_num_predict    15      
    temperature          1
```

Three things worth pulling out:

- **32.3B parameters, not 30B.** The tag understates it.
- **Requires Ollama 0.32.7.** We run 0.32.13 — satisfied, but with six patch
  versions of margin against gemma4's `0.20.0`. This model would break on a
  downgrade that gemma4 would survive.
- **`draft_num_predict 15`** in its default parameters, which gemma4 does not
  have — a speculative-decoding hint.

For comparison, `gemma4:26b` reports 25.8B params, **262144** context, embedding
length 2816, Q4_K_M, requires 0.20.0, and the same four capabilities.

## 2. Load footprint

`ollama ps` before:

```
NAME                       ID              SIZE      PROCESSOR    CONTEXT    UNTIL              
nomic-embed-text:latest    0a109f422b47    370 MB    100% GPU     2048       4 minutes from now    
gemma4:26b                 5571076f3d70    17 GB     100% GPU     32768      4 minutes from now    
```

After loading muse-glimmer at the same `num_ctx=32768`:

```
NAME                       ID              SIZE      PROCESSOR    CONTEXT    UNTIL              
muse-glimmer:30b-mlx       015fa21845be    19 GB     100% GPU     32768      4 minutes from now    
nomic-embed-text:latest    0a109f422b47    370 MB    100% GPU     2048       4 minutes from now    
```

**gemma4 was evicted.** 17 GB + 19 GB does not fit in 32 GB, so the two cannot
be resident together. Any future setup wanting both — a chat model and a
differently-specialised one — has to accept eviction churn on this hardware.

Memory free 23% → 29%; swap 1708 MB → 3461 MB across the load.

Cold load to first token: **21.3s**.

## 3. Large-prompt exercise — same 81,600 characters as task 1.2

```
prompt chars       : 81600
prompt_eval_count  : 17668
chars/token        : 4.619
response           : 'harbour'   (correct)
elapsed            : 319s
ollama ps after    : 19 GB, 100% GPU, CONTEXT 32768
memory free after  : 30%
swap after         : 3197 MB of 4096 MB
```

gemma4, same prompt, from task 1.2: **17,626 tokens, 4.63 chars/token, 76s.**

**The chars/token ratio is effectively identical** — 4.619 vs 4.63, a 0.2%
difference. Measured rather than assumed, and the assumption would have been
right this time. It means task 1.10's character-based token estimate holds for
either model without recalibration.

**The wall-clock is not identical: 319s vs 76s, 4.2× slower.**

## 4. Tool calling — the actual premise

Four cases, both models, temperature 0, three tools in scope
(`get_weather`, `memory_search`, `send_email`).

| Case | What it tests | gemma4:26b | muse-glimmer:30b-mlx |
|---|---|---|---|
| simple | Emits a call at all | PASS 1.4s | PASS 29.5s (cold) |
| selection | Picks the right tool of three | PASS 1.4s | PASS 5.8s |
| typed-arg | Integer arg arrives as `int`, not `"3"` | PASS 1.5s | PASS 6.1s |
| restraint | Does **not** call a tool for "17 + 25" | PASS 1.0s | PASS 3.5s |
| | | **4/4** | **4/4** |

Both emitted well-formed calls with correctly typed arguments, and both
correctly declined to call anything when nothing was needed.

**The differentiator did not differentiate.** Tool reliability is muse-glimmer's
stated selling point, and on this set the two are indistinguishable on
correctness while gemma4 is ~4× faster per call. I want to be careful about how
much that proves: four cases, all fairly easy, no adversarial or multi-step
sequences, no forced-failure recovery. A harder suite could separate them. What
this shows is that the *easy* cases are not where the difference lives, so the
premise is unsupported until someone measures the hard ones.

One notable asymmetry: encoding the same three-tool schema cost gemma4 **84**
prompt tokens and muse-glimmer **417** — 5×. On a trivial no-tool prompt it was
18 vs 61. That overhead is paid on every turn, and it eats context budget.

## 5. Throughput

From Ollama's own duration counters, identical prompt, warm:

| | gemma4:26b | muse-glimmer:30b-mlx |
|---|---|---|
| Prompt eval | 210 tok/s | 90.3 tok/s |
| Generation | 33.8–34.3 tok/s | 14.4 tok/s |

gemma4 is ~2.3× faster on prompt processing and ~2.4× on generation. The 4.2×
gap on the 81,600-character prompt is steeper than either, so the disadvantage
appears to widen with context length rather than staying proportional.

Also observed, one sample each, asked for "exactly three sentences about a
harbour at dawn": gemma4 produced 52 tokens, muse-glimmer 208. That is a
verbosity difference, not a quality judgment — one sample, and I did not read
the full outputs closely enough to score them.

---

## Side-by-side

| Axis | gemma4:26b | muse-glimmer:30b-mlx | Ahead |
|---|---|---|---|
| Context ceiling | **262144** | 131072 | **gemma4**, 2× |
| Load footprint | **17 GB** | 19 GB | **gemma4** |
| Both resident with the other? | — | — | *Neither — they evict each other* |
| Measured chars/token | 4.63 | 4.619 | **Tie** (0.2%) |
| Generation throughput | **33.8–34.3 tok/s** | 14.4 tok/s | **gemma4**, 2.4× |
| Prompt eval throughput | **210 tok/s** | 90.3 tok/s | **gemma4**, 2.3× |
| 81,600-char prompt | **76s** | 319s | **gemma4**, 4.2× |
| Tool-call correctness | 4/4 | 4/4 | **Tie** |
| Tool-call latency (warm) | **1.0–1.5s** | 3.5–6.1s | **gemma4** |
| Tool-schema token cost | **84 tok** | 417 tok | **gemma4**, 5× |
| Capabilities | completion, vision, tools, thinking | same | **Tie** |
| Ollama version margin | **0.20.0** | 0.32.7 | **gemma4** |
| Parameters | 25.8B | **32.3B** | muse-glimmer |
| Embedding length | 2816 | **6656** | muse-glimmer, *unclear if it matters* |
| Cold load | not re-measured | 21.3s | — |

### Reading it by the axes that matter for different work

- **Agent loop reliability (task 2.2):** tie on correctness, gemma4 clearly ahead
  on latency and schema cost. Nothing measured supports switching.
- **Context budget (task 1.10):** gemma4, on two counts — twice the ceiling, and
  a fifth of the per-turn tool-schema overhead. The token estimate needs no
  change either way.
- **`soul.md` tone and creative writing:** **not measured.** This is the one axis
  where muse-glimmer's larger parameter count and different training could
  plausibly win, and nothing here speaks to it. Two 150-character samples is not
  an evaluation of prose quality. If that axis matters, it needs its own
  exercise with real prompts and a human reading the output.

### The plain summary

**gemma4:26b wins or ties every axis measured here**, and the two axes where
muse-glimmer leads — raw parameter count and embedding width — are inputs rather
than outcomes, with no measured behaviour attached.

The specific claim that motivated the comparison, tool reliability, came back a
tie at 4/4 while costing 4× the latency and 5× the schema tokens.

**No recommendation to change anything, and no change made.** The one honest gap
is prose quality, which I did not measure and would not want inferred from
throughput numbers.

## Known limitations of this evaluation

- **Four tool cases, all easy.** No multi-step sequences, no malformed-schema
  recovery, no parallel calls, no long-conversation tool use. The premise is
  unsupported, not disproven.
- **Single-sample timings.** Each measurement is one run. The gaps are large
  enough (2–4×) that noise is unlikely to explain them, but they are not averaged.
- **Prose quality untested**, as above.
- **`num_ctx=32768` for both**, matching the configured pin. muse-glimmer's
  behaviour nearer its own 131072 ceiling is unmeasured, as gemma4's is nearer
  262144.
- **Thinking disabled on both** (`think: false`), matching the configured
  default. Neither model's reasoning mode was exercised.
- **Cold-load timings are contaminated by eviction** — each cold load also
  evicted the other model.
