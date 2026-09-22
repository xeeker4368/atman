# 2026-09-21 — Q7 reopened and answered; ComfyUI moved out of the repo; venv cleaned

**Tier 1 · measurement and housekeeping.** Three authorized actions plus one
deviation, recorded below. **No client code written** — Q5's answer was the
precondition and it is now in hand.

## Files

Created: `ops/comfyui/README.md`, `ops/comfyui/requirements.txt` (84 pinned
packages), this changelog. Modified: `BUILT.md`.
**Nothing in `program/` changed.**

---

## 1. Q7 answered: a fast variant fixes it, with real margin

**Deviation from the brief, stated first.** The brief said *"download and measure an
SDXL Lightning or Turbo checkpoint"*. I measured the **SDXL Lightning 4-step and
8-step LoRAs applied to the existing SDXL base** instead of a separate checkpoint.
Three reasons:

* **376 MB each rather than ~6.9 GB.**
* **It keeps Q7's licence reasoning intact.** SDXL base 1.0 is CreativeML
  OpenRAIL++; `stabilityai/sdxl-turbo` is under Stability's Non-Commercial
  Research licence. Q7 chose SDXL base partly for "no licence friction", and a LoRA
  does not spend that.
* **It changes one variable, not two.** Same checkpoint, resolution, prompt and
  measurement as A1b, so the comparison to 107.6 s is clean rather than confounded
  by a different model.

A full Turbo checkpoint remains available if quality at 4 steps disappoints.

Rig as A1b: ComfyUI 0.37.0, own venv on Python 3.13.13, torch 2.14.0, MPS, M4 /
32 GB, 1024×1024, wall-clock from `/prompt` to `/history`. Lightning requires
**euler / sgm_uniform / cfg 1.0** — anything else measures a misconfigured sampler.

| | 4 steps | 8 steps | A1b baseline (20 steps, cfg 8.0) |
|---|---|---|---|
| **cold** (6.5 GB checkpoint + LoRA) | **30.5 s** | — | 117.9 s |
| **warm** | **12.8 s** (12.9 · 12.7 · 12.8) | **23.1 s** (41.1\* · 22.8 · 23.1) | 107.6 s |
| **contended**, `gemma4:26b` resident | **54.6 s** (57.9 · 51.3) | **66.8 s** (68.5 · 65.1) | 125–133 s |
| worst contended, as % of the 120 s turn budget | **48%** | 57% | **over 100%** |

\* the first 8-step sample includes swapping the LoRA; 22.8/23.1 are the warm pair.

**Q5's answer: 4-step Lightning fits, with ~62 s of turn budget left over.**
No change to `agent.tool_budget_seconds`, so `IN_FLIGHT_GRACE_FLOOR_MINUTES` stays
**35**. No step-count compromise on the base model. No async mechanism. Option (b)
is not needed and option (a)'s quality cost is avoided, which is what the
conditional reopening was for.

### Three things the headline hides

**The honest speedup is 2.3×, not 8.4×.** Warm-to-warm it is 8.4× (107.6 → 12.8).
But the operative comparison is contended-to-contended — the chat model has just
answered and is resident — and that is **125–133 s → 51–58 s, i.e. 2.3×.** Still
decisive; not the number the warm rows suggest.

**Because the contention penalty is additive, not proportional, and it punishes
short jobs.** At 20 steps it was +18–25 s (+17–23%). At 4 steps it is **+38–45 s,
which is +300%.** With `gemma4:26b` resident `vram_free` falls to 2.1 GiB and SDXL
has to be paged back in per generation; that fixed cost was amortised across 107 s
and now dominates a 13 s job. **A faster sampler cannot shrink it** — it is memory,
not compute.

**Part of the speedup is cfg 1.0, not the step count.** Per-step cost drops from
5.25 s to ~3.2 s, because at cfg 1.0 the sampler skips the unconditional branch —
there is no second pass for the negative prompt. So the gain is fewer steps *and*
half the work per step.

**Which has a direct consequence for A2:** at cfg 1.0 **the negative prompt is
inert.** If `image_generate` exposed a negative-prompt parameter it would accept it
and silently do nothing — the "gate mounted on nothing" shape, one layer down. The
tool should not expose one while Lightning is the configuration.

### What is still unmeasured, and it is the gating question

**Quality was not assessed at any setting.** These are latency numbers only. Whether
4-step Lightning looks good enough is a judgment that has to be made by looking, and
**26 sample images are in `/Volumes/Dock Storage/ComfyUI/output/`** —
`lightning4_*`, `lightning8_*` and `cold_*` from this pass, `a1b_*` and `curve_*`
from the 20-step baseline. Same prompt throughout, so they are directly comparable.
That look is now A2's precondition the way Q5 was this task's.

Also unmeasured: sustained load, and any seed-to-seed quality variance.

### A measurement error caught and redone

The first run of this measurement was **invalid and discarded**. `ollama ps` showed
`gemma4:26b` and `nomic-embed-text` already resident when "uncontended" phase 1
began — the full test suite had just run and its live-Ollama tests had loaded them.
The phase split was meaningless.

Rerun after stopping both, and the script now **asserts** `ollama ps` is empty before
phase 1 rather than trusting it. A second artifact was caught in the same pass: the
first "cold" figure came back **0.2 s**, which is a ComfyUI **execution-cache hit** —
the graph was byte-identical to one the aborted run had already executed. The 30.5 s
cold figure above was taken after restarting ComfyUI with a never-used seed.

---

## 2. ComfyUI moved out of the repository

**`Atman/ComfyUI` → `/Volumes/Dock Storage/ComfyUI`.**

**Verified clean before moving**, as instructed: `git ls-files ComfyUI` returned 0 and
`git diff --cached --name-only -- ComfyUI` returned 0 — `git status` had only ever
seen it as `?? ComfyUI/`. Nothing staged, no history to rewrite.

**One deviation: the destination is `/Volumes/Dock Storage/ComfyUI`, not `~/ComfyUI`.**
The brief said "sibling directory, e.g. `~/ComfyUI`". This is a literal sibling of
`Atman/`, it is where SearXNG already lives (`/Volumes/Dock Storage/searxng/`), and
being same-volume it was an instant rename rather than an 8.5 GB cross-volume copy.
Both targets had room (459 GiB / 76 GiB free).

All three costs are resolved at the root:

| | before | after |
|---|---|---|
| `git status` | `?? ComfyUI/` — 8.5 GB one `git add .` from the index | gone from the tree |
| `ruff check .` | **1,024 files, 914 of them ComfyUI's own source** | **110 files, 0** |
| generated output | landing inside the repo | outside |

**`ops/comfyui/` holds only the recreate record**: a README and an 84-package
`requirements.txt` frozen from the live venv. Pinned commit
**`b0f4b7b294ce482a2e071d9d762c133d38c7aa07`** (`v0.37.0-5-gb0f4b7b2`).

**A relocation hazard found and fixed.** A venv's `bin/python` survives a move —
`sys.prefix` resolves from `pyvenv.cfg` at runtime, and `torch.backends.mps.is_available()`
was confirmed True afterwards — but **entry-point scripts carry the absolute
interpreter path in their shebang**. `pip`, `pip3`, `pip3.13`, `typer`, `dotenv` and
`activate.fish` all pointed at the old location, so `pip` was silently broken.
Rewritten in place and verified (`pip 26.1`). Recorded in the README, because the next
person to move it will hit the same thing.

ComfyUI is **stopped** and 8188 free. Restart:
`cd /Volumes/Dock\ Storage/ComfyUI && ./venv/bin/python main.py` — and **do not pass
`--listen`**, which broadens the bind to `0.0.0.0`.

---

## 3. The 16 orphan packages removed

Uninstalled via the exact recorded command. `Atman/venv` went from **130 to 113
packages**; `pip list` now matches nothing on `comfy|transformers|safetensors|spandrel`.

**Verified rather than assumed**, as instructed — "nothing imports them" was the
hypothesis, not the evidence:

* **Full suite: 1,044 passed**, same count as before removal.
* Every package imported explicitly afterwards (`api.app`, `memory.db`,
  `memory.retrieval`, `memory.vectors`, `artifacts.ingest`, `artifacts.kinds`,
  `integrity.gate`, `integrity.corrections`) — all clean.
* `ruff check .` clean.

---

## Next

**A2 is unblocked on latency but gated on the quality look.** 4-step Lightning at
~58 s worst contended is the configuration the numbers support; whether it looks
acceptable is yours to decide from the images above. The tool design should also
**not expose a negative prompt** while cfg 1.0 is the configuration.
