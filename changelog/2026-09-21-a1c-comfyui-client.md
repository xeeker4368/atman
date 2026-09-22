# 2026-09-21 — A1c: the ComfyUI client

**Tier 1.** Transport layer only — it submits a workflow, waits, and returns image
**bytes**. No tool is registered (that is A2) and nothing is stored (that is A3).
Nothing committed. **Stops here for review.**

## Files

Created: `program/media/__init__.py`, `program/media/comfyui.py`,
`tests/test_comfyui.py` (24). Modified: `config/defaults.toml` (new `[comfyui]`
section), `program/config.py` (four accessors, two env overrides), `BUILT.md`.

**1,066 tests pass** (was 1,044), 2 skipped, `ruff` clean.

## Shape

`ollama.py`'s, as scoped: named exceptions carrying the host and how to check it, an
explicit timeout on every request, nothing that can hang. Six exceptions, each for a
state the caller can act on differently:

| | means | what the operator does |
|---|---|---|
| `ComfyUIUnreachable` | nothing answered | start it — the message says how |
| `ComfyUITimeout` | deadline passed | **outcome unknown**, the job may still be running |
| `ComfyUIWorkflowRejected` | HTTP 400, graph refused before running | fix the graph — a bug here |
| `ComfyUIModelMissing` | a named checkpoint or LoRA is not installed | download it |
| `ComfyUIGenerationFailed` | accepted, then a node raised | read the node error |
| `ComfyUINotLoopback` | the configured host is not loopback | see below |

`ComfyUIModelMissing` **subclasses** `ComfyUIWorkflowRejected`, so catching the
parent still catches it, but it is separable because the action is a download rather
than a code fix — the distinction `ToolResult` draws between `INVALID_ARGUMENTS` and
`TOOL_ERROR`. It is detected structurally, not by string matching: a 400 whose
`node_errors[*].errors[*].type` is `value_not_in_list`.

`ComfyUITimeout` is its own outcome for `ToolResult.TIMEOUT`'s reason: the job may
still be running over there, so whether an image was produced is **unknown** rather
than known not to have happened.

## Written against what the instance actually returns

Measured against ComfyUI 0.37.0 rather than read from documentation — the discipline
`web_search.py` established. Four shapes captured and now pinned as test fixtures:

* `POST /prompt` → `{"prompt_id", "number", "node_errors"}`.
* A graph with no output node → **400**, `error.type == "prompt_no_outputs"`.
* A graph naming an absent model → **400**,
  `error.type == "prompt_outputs_failed_validation"`, with a `value_not_in_list`
  entry under `node_errors` whose `details` names the input and lists what *is*
  installed.
* `GET /history/<id>` → **`{}` while queued or running**, then
  `{id: {status, outputs}}`. Failure is `status.status_str == "error"`.
  *The empty dict means "not yet", not "finished with nothing" — a test drives that
  distinction.*

## Two decisions worth the reasoning

**`PreviewImage`, not `SaveImage`.** Both produce a fetchable PNG via `/view`.
`SaveImage` writes a **permanent** file into ComfyUI's own `output/`; `PreviewImage`
writes to `temp/`, which ComfyUI clears on startup, and reports `type: "temp"`.
Since the caller stores the bytes itself, `SaveImage` would mean **every generated
image exists twice** — once where the artifact row points, once in a directory that
grows without bound, sits outside the backup story and outside the go-live wipe. One
copy, in the place the row names. A test asserts `SaveImage` appears nowhere.

**A fresh random seed when none is given**, and not only for variety. A fixed default
would silently hit **ComfyUI's execution cache** and return the previous image in
milliseconds — which is exactly what was misread as a "0.2-second cold generation"
during A1b's follow-up. `secrets.randbelow` rather than `random`, because nothing
security-sensitive rides on it but this is the one value that must not repeat, and a
seeded global RNG elsewhere in the process could make it do so. A test asserts five
calls produce five seeds.

## The loopback guard

`ComfyUI has no authentication of any kind` — its HTTP API executes any workflow
graph posted to it. A non-loopback instance is an unauthenticated execution endpoint
on the LAN, so the client **refuses** one rather than trusting config to stay sane.

Checked at the point of use, not in `config.py`: config reads values, it does not
enforce policy, and the reason belongs where the risk is. **Every resolved address is
checked**, not the first — a name with one loopback and one routable record would
otherwise pass on whichever the resolver ordered first, which is `web_fetch`'s layer-2
reasoning. IPv4-mapped IPv6 is unwrapped first, because `::ffff:127.0.0.1` is loopback
in an IPv6 costume and `::ffff:192.168.0.5` is routable in the same one — getting it
backwards either blocks the real host or admits a LAN one.

**Proven to bite:** neutering `_check_loopback` fails 4 tests. A separate test asserts
the guard runs **before anything reaches the wire**.

## Config, derived rather than chosen

New `[comfyui]` section. `timeout_seconds = 90.0`, derived from A1b's measurements and
documented where the value lives: warm 12.8 s, cold 30.5 s, contended 51–58 s, and a
worst plausible cold-and-contended around 70–75 s given the measured +38–45 s
contention penalty. 90 covers that and stays under `agent.tool_budget_seconds` (120) so
the call is reachable rather than clipped on every turn.

`comfyui_generation()` returns the eight generation settings **as one dict rather than
eight accessors**, because they are not independent dials: Lightning requires cfg 1.0
with euler/sgm_uniform, and a caller picking one without the others would be running a
misconfigured sampler. Reading them together makes the coupling visible at the call
site.

**No `enabled` flag was added.** Q4 decided `image_generate` is gated by one, but
nothing reads it yet and an unread flag is R2's *"an unmounted gate is worse than an
absent one."* A2 adds it when it has a consumer.

## Verified live, end to end

Real instance, real weights:

* `available()` → ComfyUI 0.37.0, device `mps`.
* `generate("a copper kettle on a slate worktop, morning light")` → **1,935,372 bytes
  of real PNG in 13.2 s**, metadata carrying prompt, seed, checkpoint, LoRA and every
  sampler setting.
* A second identical call → **different seed, different bytes, 12.7 s** — no cache hit.
* A deliberately absent checkpoint → `ComfyUIModelMissing`, quoting what ComfyUI said
  is installed.

**The two live tests skip rather than fail when ComfyUI is down** — verified by
stopping it and re-running: 22 passed, 2 skipped. A skip is visible in pytest output
where a mock would look like a pass.

## Known limitations

- **The deadline covers queue time as well as generation.** If ComfyUI is busy with
  another job ours waits, and that wait is charged against the same 90 s. Unlikely on a
  single-household instance; recorded because the timeout would otherwise look
  unaccountably short under load.
- **No progress reporting.** ComfyUI exposes a progress websocket; the client polls
  instead, because polling needs no connection state and 0.5 s costs nothing against a
  13–58 s job. A caller that wants a progress bar would need the websocket.
- **Batch size is fixed at 1.** Nothing needs more, and a batch would multiply both the
  latency and the bytes returned.
- **`PreviewImage`'s temp file is not deleted by this client** — ComfyUI clears `temp/`
  on startup, so a long-running instance accumulates them until then. Bounded by
  restart rather than by us.
- **The metadata is not yet anyone's**: `GeneratedImage.to_metadata()` exists for A3 and
  has no consumer.

## Next

A2 — the `image_generate` tool: `enabled` gating, a derived per-tool timeout, **no
`negative_prompt` parameter** (Q15, locked at review: at cfg 1.0 it would be accepted
and silently ignored), and the text-confirmation-plus-path return shape Q13 settled.
