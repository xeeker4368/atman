# 2026-09-21 — A1b: what image generation costs, and what it does to 32 GB

**Tier 1 · measurement only.** No client code written, per the brief. Answers Q5
(does generation fit inside a turn) and Q6 (does anything need an explicit unload
step). Nothing committed; nothing in `program/` changed.

Rig: SDXL base 1.0 (`sd_xl_base_1.0.safetensors`, 6.5 GB), ComfyUI 0.37.0 on its own
Python 3.13.13 venv with torch 2.14.0, MPS, Mac mini M4 / 32 GB. Sampler
euler/normal, cfg 8.0. Wall-clock from `/prompt` submit to `/history` result — what a
tool call would actually wait.

## Phase 1 — ComfyUI alone

| | seconds |
|---|---|
| cold, includes the 6.5 GB checkpoint load | **117.9** |
| warm | 105.8 · 107.6 · 108.3 → **median 107.6** |

**~5.25 s per step** at 1024×1024. Latency is linear in steps, which is what makes
the curve below the real decision surface.

## Phase 2 — with `gemma4:26b` resident

| | before | after loading the chat model |
|---|---|---|
| `vram_free` (MPS) | 17.1 GiB | **2.6 GiB** |
| swap used / total | 777 MB / 2,048 MB | **6,002 MB / 7,168 MB** |

The chat model answered in 21.2 s and sat at **17 GB, 100% GPU**. Then:

| contended generation | seconds | swap used after |
|---|---|---|
| 1 | **125.1** | 6,812 MB |
| 2 | **133.1** | 9,242 MB (total grew to 10,240 MB) |

**Nothing was evicted.** `ollama ps` showed the chat model resident at 100% GPU
throughout, and a chat round trip immediately after generating took **5.4 s** and
answered correctly. Both models coexist.

**The cost is paid in swap, not in failure.** macOS grew the swap file from 2 GB to
10 GB and used up to 9.2 GB, and generation slowed **16–24%**.

## The curve (uncontended, one sample each)

| setting | seconds | under the 120 s turn budget | under the 30 s per-tool default |
|---|---|---|---|
| 20 steps @ 1024² | 104.8 | yes | no |
| 15 steps @ 1024² | 81.1 | yes | no |
| 10 steps @ 1024² | 55.2 | yes | no |
| 8 steps @ 1024² | 44.7 | yes | no |
| 20 steps @ 768² | 53.5 | yes | no |
| **10 steps @ 768²** | **27.6** | yes | **yes** |

## Q5 — it does not fit, and the reason is the contended number

Every setting is under 120 s **uncontended**. That is the wrong column to read.

`ollama`'s keep-alive means the chat model is resident during a turn — it has just
answered — so **the realistic figure is the contended one: 125–133 s at default
settings, which exceeds `agent.tool_budget_seconds = 120` outright.** The aggregate
budget would cut the call off.

And the budget is for the **whole turn**, so even at 107 s one image consumes 90% of
it, leaving ~12 s for every other tool call; a second call would be `SKIPPED`.

Applying the measured 16–24% contention penalty to the curve (**inferred, not
measured**): 15 steps ≈ 94–101 s; 10 steps ≈ 64–68 s; 8 steps ≈ 52–55 s;
20 @ 768² ≈ 62–66 s; 10 @ 768² ≈ 32–34 s. Only ≤15 steps @ 1024² fits with margin,
and **nothing fits the 30 s per-tool default except 10 steps @ 768², which is
off-distribution for SDXL.**

**No async mechanism is needed to make generation work** — a synchronous call
completes, and the brief said to build one only if measurement showed it necessary.
It did not. What measurement showed instead is that **the settings have to come
down, or the budget has to go up, or the checkpoint has to change.** That is a
decision, not an implementation, and it is Q5's to take:

- **(a) Ship at reduced settings.** 10 steps @ 1024² is ~55 s uncontended, ~64–68 s
  contended, leaving half the turn budget. Quality cost is real: SDXL base at 10
  euler steps is visibly softer than at 20. *Untested and worth trying before
  deciding: `dpmpp_2m` + `karras` usually recovers most of that at low step counts —
  I did not measure it, so it is a suggestion, not a finding.*
- **(b) Raise `agent.tool_budget_seconds`.** This moves a Tier 3 floor: the
  arithmetic is `2000 + 45 + 45 = 2090 s` → floor 35, and +120 s takes it to
  2,210 s ≈ 36.8 min, so `IN_FLIGHT_GRACE_FLOOR_MINUTES` becomes 37 and the tests
  that recompute it from live config change with it. Even then it buys two images.
- **(c) Revisit Q7's checkpoint.** SDXL base is simply slow on an M4. A Lightning or
  Turbo variant at 4–8 steps would land ~20–45 s and fit the per-tool default.
  **Q7 was decided before any measurement existed**, so this is evidence rather than
  relitigating — but it is a reopened decision and therefore yours.

## Q6 — no unload step is needed

**Nothing is forced out.** Neither model evicts the other, the chat model stays
resident at 100% GPU through a generation, and it answers in 5.4 s afterwards. Per
the brief, **do not build a memory-unload step** — the measurement does not call for
one.

What it does call for is recording the swap behaviour: **2 GB → 10 GB of swap file,
up to 9.2 GB used, and a 16–24% generation slowdown.** That is sustained SSD
pressure rather than a correctness problem, and it is the number to re-measure if a
third resident model ever appears.

## Known limitations

- **Two contended generations, one sample per curve point.** Enough to separate
  107 s from 130 s; not enough for a confidence interval, and decision #22's
  discipline does not apply here because nothing is model-*judged* — this is
  wall-clock, and the spread across phase 1's three warm runs was 2.5 s.
- **Sustained load is unmeasured.** Swap at 9 GB over hours, rather than over four
  generations, is not something these numbers speak to.
- **Quality was not assessed at any setting.** The curve is latency only; whether 10
  steps looks acceptable is a judgment nobody has made yet, and it should be made by
  looking at images rather than at a table.
- The cold figure (117.9 s) includes a 6.5 GB checkpoint load and will recur after
  any ComfyUI restart or model switch.

## Two findings outside the measurement

**`ComfyUI/` is not gitignored.** *(CORRECTED 2026-09-21, later the same day: this was right about `ComfyUI/` and I then wrongly extended the same claim to `workspace/`, which has been gitignored since Phase 0. See `changelog/2026-09-21-b0-workspace-protection.md`.)* It sits at `/Volumes/Dock Storage/Atman/ComfyUI`
— inside the repository — and `git status` collapses it to a single untracked entry,
so its 6.5 GB checkpoint is one `git add .` away from the index. The project's
explicit-`git add` rule makes that unlikely from a terminal; a GUI client is another
matter. **Not fixed** — where ComfyUI should live is a real question (SearXNG
deliberately runs from outside the repo, with only its compose file checked in), and
that is Q8's territory rather than something to patch silently. 13 generated images
(18 MB) are also sitting in `ComfyUI/output/`.

**And it has captured the lint gate.** `ruff check .` now scans **1,024 files, of
which 914 — 89% — are ComfyUI's own source**, because `pyproject.toml` declares no
`exclude`. It passes today, with one warning about an invalid `# noqa` directive in
`ComfyUI/comfy/ldm/sam3/detector.py`. That is luck: a future ComfyUI update can break
this repo's lint gate for reasons that have nothing to do with this repo. Whatever is
decided about where ComfyUI lives, `exclude` needs setting or the directory needs
moving.

**The repo venv still has 16 ComfyUI-ecosystem packages.** `torch`, `torchvision`
and `torchaudio` were removed from `Atman/venv` as reported, but
`comfy-aimdo`, `comfy-angle`, `comfy-kitchen`, ten `comfyui-workflow-templates-*`,
`comfyui_frontend_package`, `comfyui-embedded-docs`, `spandrel`, `transformers 5.17.0`
and `safetensors 0.8.0` remain. **All orphans** — `pip show` reports no `Required-by`
outside the cluster, and nothing in `program/`, `scripts/` or `tests/` imports any of
them. `requirements.txt` declares five dependencies; these are not among them.

**The passing suite is not evidence they are gone** — nothing imports them, so 1,044
tests pass either way. Recorded rather than uninstalled: removing 16 packages from
the environment the whole suite depends on, mid-phase and unasked, is not a
measurement task's business. The command is
`./venv/bin/pip uninstall -y comfy-aimdo comfy-angle comfy-kitchen comfyui-embedded-docs comfyui_frontend_package comfyui_workflow_templates comfyui-workflow-templates-core comfyui-workflow-templates-json comfyui-workflow-templates-media-api comfyui-workflow-templates-media-assets-01 comfyui-workflow-templates-media-assets-02 comfyui-workflow-templates-media-image comfyui-workflow-templates-media-other comfyui-workflow-templates-media-video spandrel transformers safetensors`
and the suite should be re-run after it.

## State left behind

ComfyUI **stopped** and port 8188 free — the machine is as I found it. *An earlier
line in this session's transcript said "stopped" when it was not: `pkill -f
"ComfyUI/venv/bin/python main.py"` matched nothing, because the process's command
line carries the resolved Homebrew interpreter path rather than the venv symlink. It
was verified by PID afterwards and killed properly.* Restart with
`cd ComfyUI && ./venv/bin/python main.py`.
