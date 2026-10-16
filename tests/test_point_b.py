"""``scripts/point_b.py`` keeps the classifier's raw reply and label for every sample.

Point B2 could not show A1's classifier output: only the verdict was kept (decision (c),
2026-10-09). The script calls ``scratch_env`` at import, which refuses once ``program`` is
imported, so each check runs in its own process with ``scratch_env`` pointed at ``tmp_path``.
No model: ``classifier.classify`` is replaced by a scripted reply.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sample(tmp_path: Path, behaviour: str) -> dict:
    """Run ``point_b.sample`` on a frozen gate case and on a run-3-shaped reply, in a fresh
    process, with the classifier scripted by ``behaviour``."""
    code = f"""
import json, sys
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
import scripts._scratch as scratch
scratch.DEFAULT_ROOT = Path({str(tmp_path)!r})
from scripts import point_b
from program.integrity import gate, gate_eval

def scripted(prompt):
    behaviour = {behaviour!r}
    if behaviour == "fails":
        raise TimeoutError("the classifier did not answer")
    return {{"consistent": "CONSISTENT",
             "contradicts": "CONTRADICTS-SELF\\n- I was retrained overnight",
             "garbage": "maybe"}}[behaviour]

gate.classifier.classify = scripted
texts = {{"new": point_b.new_texts()}}
case = next(c for c in gate_eval.load_cases() if c.answer.strip())
results = {{
    "gate": point_b.sample({{"kind": "gate", "arm": "new", "case": case}}, texts),
    "run3": point_b.sample({{"kind": "run3", "arm": "new", "answer": "I was retrained overnight.",
                             "trace": [], "situation": ""}}, texts),
    "restored": gate.classifier.classify is scripted,
    "scratch": str(point_b.SCRATCH),
}}
print(json.dumps(results))
"""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_every_sample_keeps_the_raw_reply_and_its_label(tmp_path):
    got = _sample(tmp_path, "contradicts")
    for kind in ("gate", "run3"):
        calls = got[kind]["classifier"]
        assert calls == [{"raw": "CONTRADICTS-SELF\n- I was retrained overnight",
                          "label": "CONTRADICTS-SELF"}], kind
    assert got["run3"]["status"] == "flagged"
    assert got["restored"], "the wrapper must hand the classifier back"


def test_a_clean_verdict_keeps_its_reply_too(tmp_path):
    got = _sample(tmp_path, "consistent")
    assert got["run3"]["classifier"] == [{"raw": "CONSISTENT", "label": "CONSISTENT"}]
    assert got["run3"]["status"] == "clean"


def test_a_failed_call_or_an_unusable_reply_is_kept_with_its_error(tmp_path):
    failed = _sample(tmp_path, "fails")
    for kind in ("gate", "run3"):
        [call] = failed[kind]["classifier"]
        assert call["raw"] is None and call["label"] is None
        assert "TimeoutError" in call["error"]
    assert failed["run3"]["scored"] is False  # no structural finding: the verdict is unavailable

    unusable = _sample(tmp_path, "garbage")
    [call] = unusable["run3"]["classifier"]
    assert call["raw"] == "maybe" and call["label"] is None and "no usable verdict" in call["error"]
    assert unusable["run3"]["status"] == "unavailable"


def test_the_script_ran_against_the_tests_scratch_root(tmp_path):
    assert Path(_sample(tmp_path, "consistent")["scratch"]) == tmp_path / "point-b"
