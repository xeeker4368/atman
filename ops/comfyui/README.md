# ComfyUI — how it runs, and how to recreate it

The **live installation is deliberately NOT in this repository.** It runs from:

```
/Volumes/Dock Storage/ComfyUI
```

a sibling of `Atman/`, exactly as SearXNG runs from `/Volumes/Dock Storage/searxng/`.
This directory holds only the record needed to rebuild it.

**Why it is outside the repo.** It was briefly installed *inside* at
`Atman/ComfyUI`, and that cost three things at once (all measured, none
hypothetical, 2026-09-21):

* **Git.** `git status` collapses an untracked directory to one entry, so an 8.5 GB
  tree — including a 6.5 GB checkpoint — sat one `git add .` away from the index.
  This project's explicit-`git add <filename>` rule makes that unlikely from a
  terminal and not from a GUI client.
* **The lint gate.** `ruff check .` went from 110 files to **1,024, of which 914 were
  ComfyUI's own source**, because `pyproject.toml` declares no `exclude`. It passed
  by luck; a ComfyUI update could have broken this repo's gate for reasons that have
  nothing to do with this repo.
* **Generated output.** Images written to `ComfyUI/output/` landed inside the tree.

Moving it resolves all three at the root rather than adding three separate
mitigations. Nothing under it had ever been staged — verified with
`git ls-files ComfyUI` (0) and `git diff --cached --name-only -- ComfyUI` (0) before
the move — so there is no history to clean up.

**Known cost, the same one SearXNG carries:** the two locations can drift. This file
is the record, not the installation, and it is only as current as the last time
somebody updated it.

## What is running

| | |
|---|---|
| ComfyUI version | **0.37.0** |
| Pinned commit | **`b0f4b7b294ce482a2e071d9d762c133d38c7aa07`** (`v0.37.0-5-gb0f4b7b2`) |
| Python | **3.13.13**, in ComfyUI's own venv at `ComfyUI/venv` |
| torch | **2.14.0**, MPS backend (`torch.backends.mps.is_available()` is True) |
| Bind | **`127.0.0.1:8188` only** — verified: the LAN address refuses the connection |
| Checkpoint | `models/checkpoints/sd_xl_base_1.0.safetensors` (SDXL base 1.0, 6.5 GB) |
| LoRAs | `models/loras/sdxl_lightning_{4,8}step_lora.safetensors` (376 MB each) |

`requirements.txt` beside this file is a `pip freeze` of that venv.

## Recreating it

```sh
cd /Volumes/Dock\ Storage
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
git checkout b0f4b7b294ce482a2e071d9d762c133d38c7aa07
python3.13 -m venv venv
./venv/bin/pip install -r /Volumes/Dock\ Storage/Atman/ops/comfyui/requirements.txt
```

Then fetch the weights by hand — they are not in the freeze and not in this repo:

* `models/checkpoints/sd_xl_base_1.0.safetensors` — Stability AI, CreativeML
  OpenRAIL++, ungated.
* `models/loras/sdxl_lightning_4step_lora.safetensors` and `..._8step_lora...` —
  `https://huggingface.co/ByteDance/SDXL-Lightning/resolve/main/<filename>`.

Start it with:

```sh
cd /Volumes/Dock\ Storage/ComfyUI && ./venv/bin/python main.py
```

**Do not pass `--listen`.** With no argument it broadens the bind to `0.0.0.0`;
the default is already loopback. Whichever way it ends up, check the LAN address
refuses the connection rather than trusting the flag — the same verification
`BUILT.md` records for SearXNG.

## Two things that will bite

**Python version.** ComfyUI needs a Python that PyTorch publishes wheels for. This
repo's venv is **3.14**, which is why ComfyUI has its own on 3.13 — do not install
ComfyUI into `Atman/venv`. It was done accidentally once and left 16 orphan packages
behind (`comfy-*`, `spandrel`, `transformers`, `safetensors`), removed 2026-09-21.

**Moving the venv breaks console scripts.** A venv's `bin/python` survives a move —
`sys.prefix` is resolved from `pyvenv.cfg` at runtime — but entry-point scripts
(`pip`, `pip3`, `typer`, `dotenv`, `activate.fish`) carry the **absolute** interpreter
path in their shebang and silently point at the old location. After the 2026-09-21
move they were rewritten in place. If ComfyUI is ever relocated again, either rewrite
them or use `./venv/bin/python -m pip` instead of `./venv/bin/pip`.

## Not covered here

Backup and the go-live wipe. The installation, the 6.5 GB checkpoint and the LoRAs
are all reproducible from this file plus the two download URLs, so `backup.py` has no
reason to copy them — unlike `data/artifacts/`, whose contents exist nowhere else.
Generated images are a different question and belong to A3, which decides where a
generated-image artifact is stored (`workspace/`, per Q1) rather than leaving them in
ComfyUI's own `output/`.
