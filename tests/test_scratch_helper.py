"""Measurement scripts reach ``program`` only through ``scripts._scratch.scratch_env`` (2026-10-03).

A static scan, no imports of the scripts: for each ``scripts/*.py`` it finds the first line that
imports ``program`` (anywhere in the file, including inside functions) and the first call to
``scratch_env``. A script is compliant if it never imports ``program``, or calls ``scratch_env``
on an earlier line. Everything else must be listed below with a reason, in one of two groups:

- ``OPERATOR``: tools meant to act on the real store; exempt by design.
- ``NOT_YET_MIGRATED``: measurement scripts written before the helper. Listed, not fixed. The test
  also fails if one of them becomes compliant or disappears, so the list can only shrink.

A new script that imports ``program`` without the helper fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

#: Run against the real store on purpose.
OPERATOR = {
    "backup.py": "backs up the real databases",
    "close_idle_conversations.py": "the operator's manual idle-close path",
    "note.py": "the operator's notes command, the only human control on notes",
    "reconcile_vectors.py": "repairs the real vector store",
    "set_password.py": "sets a real user's password",
    "write_journal.py": "the operator's journal command",
}

#: Measurement scripts that import `program` without the helper. Several set the ANAM_* dirs some
#: other way (notes_live.setup(), or os.environ before the import); the scan cannot see that, so
#: they are listed here rather than judged.
NOT_YET_MIGRATED = {
    "b12_variants.py", "correction_diagnosis_acknowledgement.py", "correction_diagnosis_co10_2.py",
    "correction_diagnosis_pn9.py", "correction_diagnosis_scope.py",
    "correction_diagnosis_stage3.py",
    "correction_eval.py", "correction_validate_disclaim.py", "fabrication_eval.py",
    "gate_design_eval_3_6b.py", "gate_diagnosis_3_6a.py", "gate_diagnosis_n7.py",
    "gate_diagnosis_n7n8.py", "gate_diagnosis_o16.py", "gate_diagnosis_o7_cost.py",
    "gate_diagnosis_pronoun.py", "gate_diagnosis_tool_recall.py", "history_window_diagnosis_b21.py",
    "journal_gate_dev_j8.py", "journal_live_run.py", "measure_classifier_budget.py",
    "notes_co15.py", "notes_co15_real.py", "notes_co15_reverse.py", "notes_co17_e2e.py",
    "notes_gap_after.py", "notes_identity.py", "notes_live.py", "notes_pending.py",
    "notes_pending_text.py", "soul_diagnosis_b12.py",
    # Needs a store holding the seed corpus, not the real one; read-only, but Chroma can
    # write on open.
    "checkpoint_queries.py",
    # Needs a target store, not the real one; calls db.init_databases(), so it migrates
    # whatever store it opens.
    "seed_dataset.py",
}


def _first_program_import(tree: ast.AST) -> int | None:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
                a.name.split(".")[0] == "program" for a in node.names):
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                and node.module.split(".")[0] == "program":
            lines.append(node.lineno)
    return min(lines) if lines else None


def _first_scratch_call(tree: ast.AST) -> int | None:
    lines = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == "scratch_env")
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "scratch_env"))]
    return min(lines) if lines else None


def test_scripts_import_program_only_after_scratch_env():
    noncompliant = set()
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name in ("__init__.py", "_scratch.py"):
            continue
        tree = ast.parse(path.read_text())
        imp, call = _first_program_import(tree), _first_scratch_call(tree)
        if imp is not None and (call is None or call > imp):
            noncompliant.add(path.name)

    unexpected = noncompliant - set(OPERATOR) - NOT_YET_MIGRATED
    assert not unexpected, (
        f"these scripts import program without calling scripts._scratch.scratch_env first: "
        f"{sorted(unexpected)}. Call scratch_env(name) before any program import, or (for a tool "
        f"meant to act on the real store) add it to OPERATOR with a reason.")
    stale = (NOT_YET_MIGRATED | set(OPERATOR)) - noncompliant
    assert not stale, (
        f"listed but no longer non-compliant (migrated, or removed): {sorted(stale)}. "
        f"Take them off the list.")


# --- the helper itself, in subprocesses: "already imported" is a per-process property -----------

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, env_drop: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in env_drop}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_scratch_env_puts_every_discovered_directory_under_scratch_and_creates_nothing(tmp_path):
    root = tmp_path / "scratch-root"
    out = _run(f"""
import json, sys
sys.path.insert(0, {str(ROOT)!r})
from pathlib import Path
from scripts._scratch import scratch_env, path_variables
base = scratch_env("t1", root=Path({str(root)!r}))
from program import config
print(json.dumps({{"base": str(base), "vars": sorted(path_variables()),
                  "dirs": {{k: str(getattr(config, k)()) for k in path_variables().values()}}}}))
""")
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert set(got["vars"]) >= {"ANAM_DATA_DIR", "ANAM_WORKSPACE_DIR", "ANAM_BACKUP_DIR",
                                "ANAM_ARTIFACT_DIR"}
    for accessor, path in got["dirs"].items():
        assert Path(path).resolve().is_relative_to((root / "t1").resolve()), (accessor, path)
    assert not root.exists() or not any(root.rglob("*")), "scratch_env must create nothing"


def test_scratch_env_refuses_when_program_is_already_imported(tmp_path):
    out = _run(f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
import program.config
from pathlib import Path
from scripts._scratch import scratch_env
scratch_env("t2", root=Path({str(tmp_path)!r}))
""")
    assert out.returncode != 0
    assert "scratch_env must run before anything under program is imported" in out.stderr


def test_scratch_env_raises_naming_a_directory_that_resolves_outside_scratch(tmp_path):
    """Drop one discovered variable, as a directory added outside [paths] would be: the helper's
    post-import check must catch it and name the accessor. The variable is also removed from the
    inherited environment, so the accessor falls back to its default (resolved, never opened)."""
    out = _run(f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from pathlib import Path
import scripts._scratch as s
real = s.path_variables
s.path_variables = lambda: {{k: v for k, v in real().items() if k != "ANAM_WORKSPACE_DIR"}}
s.scratch_env("t3", root=Path({str(tmp_path)!r}))
""", env_drop=("ANAM_WORKSPACE_DIR",))
    assert out.returncode != 0
    assert "config.workspace_dir() resolves to" in out.stderr
    assert "outside the scratch root" in out.stderr
