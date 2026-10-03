#!/usr/bin/env python3
"""Shared setup for the live Notes measurements (piece 8): a scratch store and real turns.

**Scratch only.** ``setup()`` points every runtime directory at a temporary location under
``~/anam-measurements/p8/`` and sets ``ANAM_NOTES_ENABLED=true`` **in this process only**, then
creates the databases there. It must run before anything imports a ``program`` module that reads
config (the scripts call it first). The real ``data/`` is never opened; the callers record its
fingerprint before and after.

Turns go through ``turn.handle_user_message`` with the **default registry**, exactly as the chat
route does: the real loop, the real tools, the real gate, the real model.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path.home() / "anam-measurements" / "p8"


def setup(name: str, *, fresh: bool = True, approval_required: bool = True):
    base = ROOT / name
    if fresh and base.exists():
        shutil.rmtree(base)
    for key, sub in (("ANAM_DATA_DIR", "data"), ("ANAM_BACKUP_DIR", "backups"),
                     ("ANAM_ARTIFACT_DIR", "artifacts"), ("ANAM_WORKSPACE_DIR", "workspace")):
        os.environ[key] = str(base / sub)
    os.environ["ANAM_NOTES_ENABLED"] = "true"
    os.environ["ANAM_MOLTBOOK_ENABLED"] = "false"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from program import config
    config.reload()
    from program.memory import db, note_admin
    from program.settings import store
    from program.tools import registry

    assert str(db.working_path()).startswith(str(base)), "the store must be the scratch one"
    db.init_databases()
    store.reset_cache()
    registry.reset_default_registry()
    # idempotent, so a resumed run (fresh=False) finds the same two people
    existing = {u["name"]: u["id"] for u in db.list_users()}
    lyle = existing.get("Lyle") or db.create_user("Lyle", role="admin")
    jodie = existing.get("Jodie") or db.create_user("Jodie")
    if not approval_required:
        note_admin.set_approval_required(False)
    assert "note_propose" in registry.default_registry().names
    return {"base": base, "lyle": lyle, "jodie": jodie}


def actor(user_id: str, name: str, admin: bool = False):
    from program.settings.permissions import Actor, Role
    return Actor(user_id=user_id, name=name, role=Role.ADMIN if admin else Role.USER)


def run_turn(act, text: str, conversation_id: str | None = None):
    from program.engine import turn
    return turn.handle_user_message(act, text, conversation_id)


def trace_summary(trace: list[dict]) -> list[dict]:
    return [{"tool": e.get("tool"), "outcome": e.get("outcome"),
             "arguments": e.get("arguments"), "error": e.get("error"),
             "value": (e.get("value") or "")[:300] if isinstance(e.get("value"), str) else None}
            for e in trace if e.get("tool")]


def append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
