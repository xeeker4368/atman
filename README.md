# Project Anam

A persistent, local AI entity running on a Mac mini for a two-person household: chat, image generation and a
creative-writing space, built on a deliberately plain substrate (retrieval, storage, model plumbing) so that
accumulated, provenanced memory is the one interesting variable (`PROJECT.md`). It is a hobby project with no
finish line. The entity has no name; "Anam" is the substrate.

## Run it

- Python 3.14 virtualenv at `venv/`; dependencies in `requirements.txt` (unpinned).
- Backend: `./start.sh` (loopback only; `--lan` binds all interfaces) or `python run_server.py [--debug] [--port N]`.
  It refuses to start without `auth.session_secret` (at least 32 characters): set `ANAM_AUTH_SESSION_SECRET` or
  `config/local.toml` (see `config/local.example.toml`). Startup creates and migrates the databases under `data/`.
- Needs Ollama (chat and embedding models), and optionally a local SearXNG and ComfyUI (`ops/`).
- There is no frontend yet.

## Test and lint

- `venv/bin/python -m pytest` (a few tests call live Ollama when it is reachable and skip when it is not).
- `ruff check .`

## Scripts (`scripts/`)

- **Operator tools, run against the real store:** `backup.py`, `close_idle_conversations.py`, `note.py`,
  `reconcile_vectors.py`, `set_password.py`, `write_journal.py`.
- **Setup:** `seed_dataset.py` (a small corpus for a scratch store).
- **Eval harnesses** (frozen cases against the live model): `fabrication_eval.py`, `correction_eval.py`;
  `measure_classifier_budget.py` sizes the classifier's output.
- **Diagnoses and measurements:** the other `gate_*`, `correction_*`, `notes_*`, `journal_*`, `b12_*`, `soul_*` and
  `history_*` scripts are dated one-off studies; each docstring names its task.
- **`_scratch.py`:** the helper for running anything against a scratch store.

## Run something against a scratch store

The real store is never touched by a measurement. Before any `program` import:

```python
from scripts._scratch import scratch_env
scratch_env("my-run")          # FIRST: every runtime directory now points under ~/anam-measurements/my-run
from program import config
```

`scratch_env` refuses to run if `program` is already imported and checks that every directory accessor resolves
under the scratch root. `tests/test_scratch_helper.py` lists the scripts that do not call it yet.

## Documents

- `AGENTS.md` how Claude Code works here; `CLAUDE.md` its entry point.
- `PROJECT.md` what and why; `GUIDANCE.md` behavioural principles.
- `NOW.md` current state, the decision log and open items; `BUILD_PLAN.md` the phases and their status.
- `ARCHITECTURE.md` the current invariants, each cited to a test or code; `BUILT.md` frozen history.
- `docs/` design docs; `changelog/` dated task records; `docs/archive/` closed items; `docs/measurements/` measurement detail.

The repository is public: no keys, account names or real conversation text belong in it.
