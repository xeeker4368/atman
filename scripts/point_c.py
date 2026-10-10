#!/usr/bin/env python3
"""Measurement point C (`docs/DESIGN_GATE_ORDINARY_READING.md` Appendix E/F; decisions #30, #32 D4):
does the entity still copy the system's tool record into its own replies once that record moves
out of its earlier messages and into one list in the system message?

    venv/bin/python -m scripts.point_c --out ~/anam-measurements/point-c

**The turns.** Run 3's three forged-line turns, C4 t3, C7 t3 and C11 t5: in each, the reply began
with a copy of batch 1's ``[system record of the tools this turn used …]`` line though no tool ran.

**The arms**, about 10 replays each per turn (``--reps``), all interleaved:

* ``old``: batch 1's behaviour. History goes through the pre-#32 ``earlier_tools.with_tool_records``
  (read from ``--old-ref``, default ``main``, checked against a pinned sha256), which puts the line
  inside each earlier tool-calling reply; no list in the system message.
* ``new``: this branch. History as stored; the earlier-tools list in the system message, after the
  speaker line.

Everything else is identical between the arms and goes through production's own
``turn.handle_user_message``: the speaker line, the registry (run 3's kit: notes on, Moltbook off,
ComfyUI off), the model and its options, and the fabrication gate afterwards.

**Rebuilt faithfully:** the conversation up to the turn, every message and every stored trace,
written into a fresh scratch store for each replay (so nothing one replay's tools wrote is seen by
another). **Rebuilt approximately, and identically in both arms:** the situation block (production's
builder at the question's stored time, from the person's previous message) and retrieval, which is
**off** in both arms (no turn's assembled prompt was stored, and the retrieved records cannot be
rebuilt as they were). The correction hook, which runs only after the reply exists, is off too.

**Measures, per reply** (kept apart from the gate numbers):

* ``copies_record``: the reply reproduces a tool-record line: the old line's opening, the new
  list's header or entry form, or a ``<tool> "<query>" <outcome word>`` piece;
* ``unrun_tool``: the gate's deterministic ``unrun_tool`` rule, read on its own, against the
  reply's own trace (a claim to have used a tool this turn did not call);
* the reply's trace (did it call a tool), and the gate's verdict, reported separately.

**One model process at a time; never the real store.** `scratch_env("point-c")` runs before
anything under `program` is imported, and each replay's store is a subdirectory of it. Run 3's
store is opened read-only and immutable. **Resumable**: each replay is appended to
``<out>/raw.jsonl``; a restart skips what is done. ``--report-only`` renders without sampling.
"""

from __future__ import annotations

import os
import sys

# Run 3's kit (kit-internal/bin/env.sh), set before any program import.
os.environ["ANAM_NOTES_ENABLED"] = "true"
os.environ["ANAM_MOLTBOOK_ENABLED"] = "false"
os.environ["ANAM_COMFYUI_ENABLED"] = "false"

from scripts._scratch import path_variables, scratch_env  # noqa: E402

SCRATCH = scratch_env("point-c")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from collections import defaultdict  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

from program import config  # noqa: E402
from program.engine import earlier_tools, loop, turn  # noqa: E402
from program.engine import situation as situation_block  # noqa: E402
from program.integrity import gate  # noqa: E402
from program.memory import db, vectors  # noqa: E402
from program.settings.permissions import Actor, Role  # noqa: E402
from program.tools import registry  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RUN3 = Path.home() / "anam-measurements" / "runs" / "run3"
RUN3_STORE = RUN3 / "store" / "data" / "working.db"
TURNS = (("C4", 3), ("C7", 3), ("C11", 5))
DEFAULT_SEED = 20261011
DEFAULT_REPS = 10

#: earlier_tools.py before decision #32, as on main at 8b0f45b. Checked, never trusted.
OLD_EARLIER_TOOLS_SHA256 = "e264921724f85776ed77c06a4a80b867f9cce5717effadf9a9d17e3ea6af2da8"

OUTCOME = "|".join(re.escape(w) for w in earlier_tools.OUTCOME_WORDS.values())


def _copy_patterns() -> list[re.Pattern[str]]:
    tools = "|".join(re.escape(name) for name in sorted(registry.default_registry().names))
    return [
        re.compile(r"\[system record of the tools", re.IGNORECASE),
        re.compile(r"record of tools used earlier", re.IGNORECASE),
        re.compile(r"Your reply to \""),
        re.compile(rf"\b(?:{tools})\b(?: \"[^\"\n]*\")? (?:{OUTCOME})"),
    ]


# --- the old behaviour ------------------------------------------------------------------


def load_old_earlier_tools(ref: str):
    done = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{ref}:program/engine/earlier_tools.py"],
        capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"cannot read earlier_tools.py at {ref}: {done.stderr.strip()}")
    if hashlib.sha256(done.stdout.encode("utf-8")).hexdigest() != OLD_EARLIER_TOOLS_SHA256:
        raise SystemExit(f"{ref} no longer holds batch 1's earlier_tools.py (sha256 mismatch). "
                         f"Pass --old-ref with a commit that does, e.g. 8b0f45b.")
    path = SCRATCH / "old_earlier_tools.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(done.stdout, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("old_earlier_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the turns --------------------------------------------------------------------------


def _open_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"run store not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_turns() -> dict[str, dict]:
    """Each target turn: the rows before it, the question, the speaker and the rebuilt block."""
    conn = _open_ro(RUN3_STORE)
    turns = {}
    for label, k in TURNS:
        cid = json.loads((RUN3 / "state" / f"{label}.json").read_text())["conversation_id"]
        rows = conn.execute("SELECT * FROM messages WHERE conversation_id = ? "
                            "ORDER BY timestamp, id", (cid,)).fetchall()
        replies = [i for i, r in enumerate(rows) if r["role"] == "assistant"]
        reply_at = replies[k - 1]
        question_at = max(i for i in range(reply_at) if rows[i]["role"] == "user")
        question = rows[question_at]
        user = conn.execute("SELECT name FROM users WHERE id = ?",
                            (question["user_id"],)).fetchone()["name"]
        previous = conn.execute(
            "SELECT MAX(timestamp) AS last FROM messages WHERE user_id = ? AND role = 'user' "
            "AND id != ? AND timestamp < ?",
            (question["user_id"], question["id"], question["timestamp"])).fetchone()["last"]
        block = situation_block.build_situation(
            now=datetime.fromisoformat(question["timestamp"]),
            previous_message_at=datetime.fromisoformat(previous) if previous else None,
            speaker=user)
        turns[f"{label} t{k}"] = {
            "label": f"{label} t{k}", "speaker": user, "question": question["content"],
            "situation": block, "run3_reply": rows[reply_at]["content"],
            "earlier": [{"role": r["role"], "content": r["content"], "tool_trace": r["tool_trace"]}
                        for r in rows[:question_at]],
        }
    conn.close()
    return turns


def _fresh_store(n: int) -> None:
    """Every [paths] directory under its own subdirectory for this replay."""
    base = SCRATCH / "replays" / f"{n:04d}"
    if base.exists():
        # A replay that did not finish (its record is not in raw.jsonl) starts again from empty.
        if not base.resolve().is_relative_to((SCRATCH / "replays").resolve()):
            raise SystemExit(f"refusing to clear {base}: not under the scratch root")
        shutil.rmtree(base)
    for var, key in path_variables().items():
        os.environ[var] = str(base / key.removesuffix("_dir"))
    config.reload()
    vectors.reset_vector_store()
    for attr in ("data_dir", "workspace_dir", "artifact_dir", "backup_dir"):
        value = getattr(config, attr)()
        if not str(Path(value).resolve()).startswith(str(SCRATCH.resolve())):
            raise SystemExit(f"config.{attr}() resolves to {value}, outside the scratch root")
    db.init_databases()


def replay(turn_spec: dict, arm: str, n: int, old_module) -> dict:
    _fresh_store(n)
    name = turn_spec["speaker"]
    role = Role.ADMIN if name == "Lyle" else Role.USER
    user_id = db.create_user(name, role=role.value)
    actor = Actor(user_id=user_id, name=name, role=role)
    conversation_id = db.start_conversation(user_id)
    for row in turn_spec["earlier"]:
        db.save_message(conversation_id, user_id, row["role"], row["content"],
                        tool_trace=row["tool_trace"])

    real_run_turn = loop.run_turn
    seen: dict = {}

    def run_turn(messages, *args, **kwargs):
        if arm == "old":
            messages = old_module.with_tool_records(messages)
            kwargs["earlier_tools"] = ""
        seen["earlier_tools"] = kwargs.get("earlier_tools", "")
        return real_run_turn(messages, *args, **kwargs)

    saved = (turn.loop.run_turn, turn._retrieve, turn._record_corrections)
    turn.loop.run_turn = run_turn
    turn._retrieve = lambda query: None
    turn._record_corrections = lambda *a, **k: None
    try:
        outcome = turn.handle_user_message(actor, turn_spec["question"], conversation_id,
                                           situation=turn_spec["situation"])
    finally:
        turn.loop.run_turn, turn._retrieve, turn._record_corrections = saved

    calls = loop.call_entries(outcome.trace)
    copies = [p.pattern for p in _copy_patterns() if p.search(outcome.content)]
    unrun = [f.evidence for f in gate.structural_findings(outcome.content, calls)
             if f.rule == "unrun_tool"]
    return {
        "text": outcome.content, "copies_record": bool(copies), "copy_patterns": copies,
        "unrun_tool": unrun, "tools_called": [c.get("tool") for c in calls],
        "gate": outcome.integrity.to_dict() if outcome.integrity else None,
        "earlier_tools_chars": len(seen.get("earlier_tools") or ""),
        "iterations": outcome.iterations, "stop_reason": outcome.stop_reason,
    }


def plan(turns: dict, reps: int, seed: int) -> list[tuple[str, str, int]]:
    """Every (turn, arm, rep), shuffled, with no (turn, arm) prompt twice in a row."""
    jobs = [(label, arm, rep) for label in turns for arm in ("old", "new")
            for rep in range(1, reps + 1)]
    rng = random.Random(seed)
    for _ in range(1000):
        rng.shuffle(jobs)
        for i in range(1, len(jobs)):
            if jobs[i][:2] == jobs[i - 1][:2]:
                for j in range(i + 1, len(jobs)):
                    if jobs[j][:2] != jobs[i - 1][:2] and (
                            j + 1 >= len(jobs) or jobs[i][:2] != jobs[j + 1][:2]) and (
                            jobs[i][:2] != jobs[j - 1][:2] or j - 1 == i):
                        jobs[i], jobs[j] = jobs[j], jobs[i]
                        break
        if all(jobs[i][:2] != jobs[i - 1][:2] for i in range(1, len(jobs))):
            return jobs
    raise SystemExit("could not interleave the replays")


def load_raw(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def render(turns: dict, header: dict, raw: list[dict]) -> str:
    by = defaultdict(list)
    for record in raw:
        by[(record["turn"], record["arm"])].append(record["result"])
    lines = ["# Measurement point C (decision #32 D4: the earlier-tools list), run 3's forged-line "
             "turns", "",
             "Descriptive: one model, about 10 replays an arm a turn. Kept apart from the gate "
             "numbers. Raw replies: `raw.jsonl` beside this file.", "",
             "## Header", "", "```", json.dumps(header, indent=2), "```", "",
             "| turn | arm | replays | copies a record line | unrun_tool | called a tool | "
             "gate flagged | errors |", "|---|---|---|---|---|---|---|---|"]
    totals = defaultdict(lambda: defaultdict(int))
    for label in turns:
        for arm in ("old", "new"):
            results = by[(label, arm)]
            ok = [r for r in results if "error" not in r]
            row = {
                "n": len(results),
                "copies": sum(r["copies_record"] for r in ok),
                "unrun": sum(bool(r["unrun_tool"]) for r in ok),
                "tool": sum(bool(r["tools_called"]) for r in ok),
                "flagged": sum((r.get("gate") or {}).get("status") == "flagged" for r in ok),
                "errors": len(results) - len(ok),
            }
            for key, value in row.items():
                totals[arm][key] += value
            lines.append(f"| {label} | {arm} | {row['n']} | {row['copies']} | {row['unrun']} | "
                         f"{row['tool']} | {row['flagged']} | {row['errors']} |")
    for arm in ("old", "new"):
        t = totals[arm]
        lines.append(f"| **all** | **{arm}** | {t['n']} | {t['copies']} | {t['unrun']} | "
                     f"{t['tool']} | {t['flagged']} | {t['errors']} |")
    lines += ["", "## Every replay that copied a record line or claimed an unrun tool", ""]
    for record in raw:
        r = record["result"]
        if "error" in r or not (r["copies_record"] or r["unrun_tool"]):
            continue
        lines += [f"### {record['turn']} {record['arm']} #{record['rep']}", "",
                  f"- patterns: {r['copy_patterns']}; unrun_tool: {r['unrun_tool']}; "
                  f"tools called: {r['tools_called']}; gate: "
                  f"{(r.get('gate') or {}).get('status')}", "", "```", r["text"], "```", ""]
    errors = [rec for rec in raw if "error" in rec["result"]]
    if errors:
        lines += ["## Errors", ""] + [f"- {e['turn']} {e['arm']} #{e['rep']}: "
                                      f"{e['result']['error']}" for e in errors] + [""]
    lines += ["## The turns as replayed", ""]
    for spec in turns.values():
        lines += [f"### {spec['label']} ({spec['speaker']})", "", "Situation:", "", "```",
                  spec["situation"], "```", "", "Question:", "", "```", spec["question"], "```",
                  "", "Run 3's reply:", "", "```", spec["run3_reply"], "```", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True,
                        help="directory for raw.jsonl, report.md and run.log (outside the repo)")
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--old-ref", default="main",
                        help="git ref holding batch 1's earlier_tools.py (verified by sha256)")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)

    out = args.out.expanduser().resolve()
    if str(out).startswith(str(REPO)):
        parser.error("--out must be outside the repository (it will hold raw replies)")
    out.mkdir(parents=True, exist_ok=True)
    raw_path, log_path = out / "raw.jsonl", out / "run.log"

    def log(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    turns = load_turns()
    old_module = load_old_earlier_tools(args.old_ref)
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    header = {
        "commit": commit, "scratch_root": str(SCRATCH), "old_ref": args.old_ref,
        "turns": list(turns), "reps": args.reps, "seed": args.seed,
        "chat_model": config.chat_model(), "chat_options": config.model_options(),
        "tools_registered": list(registry.default_registry().names),
        "retrieval": "off in both arms", "correction hook": "off in both arms",
    }
    raw = load_raw(raw_path)
    if not args.report_only:
        done = {(r["turn"], r["arm"], r["rep"]) for r in raw}
        jobs = plan(turns, args.reps, args.seed)
        log(f"start: {len(jobs)} replays, {len(done)} already done, header {header}")
        for n, (label, arm, rep) in enumerate(jobs, start=1):
            if (label, arm, rep) in done:
                continue
            started = time.monotonic()
            try:
                result = replay(turns[label], arm, n, old_module)
            except Exception as exc:  # noqa: BLE001 - recorded, never silent
                result = {"error": f"{type(exc).__name__}: {exc}"}
            record = {"n": n, "turn": label, "arm": arm, "rep": rep, "result": result,
                      "seconds": round(time.monotonic() - started, 1),
                      "at": datetime.now(timezone.utc).isoformat()}
            with raw_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            log(f"{n}/{len(jobs)} {label} {arm} #{rep}: "
                f"{'error' if 'error' in result else 'copies' if result['copies_record'] else 'ok'}"
                f" {record['seconds']}s")
        raw = load_raw(raw_path)
        log("sampling finished")
    (out / "report.md").write_text(render(turns, header, raw), encoding="utf-8")
    log(f"report written: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
