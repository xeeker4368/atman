#!/usr/bin/env python3
"""Measurement point A (`docs/DESIGN_GATE_ORDINARY_READING.md`, Appendix F): the classifier at
temperature 0 (piece 3.1), with the CURRENT rubric and prompt.

    venv/bin/python -m scripts.point_a --out ~/anam-measurements/point-a

What it samples, all at ``integrity.classifier_temperature`` (0):

* ``gate``: every frozen fabrication-gate case (`eval/fabrication_gate/cases.toml`).
* ``corr``: every frozen correction case (`eval/corrections/cases.toml`); 3.1 changes the
  classifier both consumers share (`program/integrity/classifier.py`, F13).
* ``run3``: run 3's 124 replies, re-judged by `gate.check` as production calls it, against the
  verdicts stored at the time (at the chat temperature, 0.35).
* ``run1``: reply ``7cf73d40`` (run 1, C3 t4), the re-check owed since batch 1.

**Passes.** Three passes, each over every item, in an order shuffled with a recorded seed, so the
two sets and the replies are interleaved and no prompt is ever sampled twice in a row. An item
whose three outcomes differ is then sampled in 20 more shuffled passes. Each escalation pass mixes
the differing items with 10 other items drawn with the same seed, so they are never back to back.
At temperature 0 a repeat mostly repeats; what differs is **state noise** (what ran before it),
and the report labels it so, as a count with an interval, never as a rate (AGENTS.md "When the
classifier runs at temperature 0", ruling of 2026-10-04).

**Resumable.** Every sample is appended to ``<out>/raw.jsonl`` as it finishes. A restart skips
what is done, then re-renders the report from the raw file. ``--report-only`` renders without
sampling.

**Never the real store.** `scratch_env("point-a")` runs before anything under `program` is
imported, so the settings table read for every call is the empty scratch one (the config seeds
apply). The two run stores are opened read-only and immutable with `sqlite3`, never through
`program.memory.db`, so nothing is written to them, not even a journal-mode change.

**What production built, and what this rebuilds** (AGENTS.md "A harness must build what production
builds"):

* The tool registry is run 3's: notes on, Moltbook off, ComfyUI off. These are the run kit's
  `env.sh` values, set here before import, so `gate.check`'s known-tool list is the one production
  had.
* The situation block is production's `situation.build_situation`, with `now` set to the
  question's stored timestamp and `previous_message_at` the person's last message **before
  that question**. This is one difference from production's query:
  `db.get_previous_user_message_time` takes the latest message, and in the end-of-run store
  that would include messages written after the turn. Production built the block a few
  milliseconds after the question was saved, and the block shows minutes, so the clock is
  exact except across a minute boundary.
* The trace is `loop.call_entries` of the stored `tool_trace`, exactly as `turn.py` hands it to
  the gate.
"""

from __future__ import annotations

import os
import sys

# Run 3's kit (kit-internal/bin/env.sh), set before any program import.
os.environ["ANAM_NOTES_ENABLED"] = "true"
os.environ["ANAM_MOLTBOOK_ENABLED"] = "false"
os.environ["ANAM_COMFYUI_ENABLED"] = "false"

from scripts._scratch import scratch_env  # noqa: E402

SCRATCH = scratch_env("point-a")

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import random  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from collections import defaultdict  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

from program import config  # noqa: E402
from program.engine import loop  # noqa: E402
from program.engine import situation as situation_block  # noqa: E402
from program.integrity import correction_eval, gate, gate_eval  # noqa: E402
from program.tools import registry  # noqa: E402

RUNS = Path.home() / "anam-measurements" / "runs"
RUN3_STORE = RUNS / "run3" / "store" / "data" / "working.db"
RUN1_STORE = RUNS / "run1" / "store" / "data" / "working.db"
RUN1_REPLY_PREFIX = "7cf73d40"
DEFAULT_SEEDS = (20261006, 20261007, 20261008)
ESCALATION_PASSES = 20
ESCALATION_FILLERS = 10
MUST_CATCH = ("P14-thinking-since-yesterday", "P15-background-work",
              "P16-gap-experience-with-situation", "T12-retrained-between-turns",
              "T13-weights-update")


# --- items ------------------------------------------------------------------------


def _open_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"run store not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _replies(path: Path, label: str, only_prefix: str | None = None) -> list[dict]:
    """Each assistant reply with what production handed the gate, rebuilt from the store."""
    conn = _open_ro(path)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
    rows = conn.execute("SELECT * FROM messages WHERE role = 'assistant' "
                        "ORDER BY timestamp, id").fetchall()
    items = []
    for row in rows:
        if only_prefix and not row["id"].startswith(only_prefix):
            continue
        question = conn.execute(
            "SELECT id, timestamp FROM messages WHERE user_id = ? AND role = 'user' "
            "AND timestamp <= ? ORDER BY timestamp DESC, id DESC LIMIT 1",
            (row["user_id"], row["timestamp"])).fetchone()
        previous = None
        if question is not None:
            previous = conn.execute(
                "SELECT MAX(timestamp) AS last FROM messages WHERE user_id = ? AND role = 'user' "
                "AND id != ? AND timestamp < ?",
                (row["user_id"], question["id"], question["timestamp"])).fetchone()["last"]
        now = datetime.fromisoformat(question["timestamp"] if question else row["timestamp"])
        block = situation_block.build_situation(
            now=now,
            previous_message_at=datetime.fromisoformat(previous) if previous else None,
            speaker=names.get(row["user_id"]),
        )
        trace = json.loads(row["tool_trace"]) if row["tool_trace"] else []
        keys = row.keys()
        items.append({
            "kind": label, "id": row["id"], "answer": row["content"],
            "trace": loop.call_entries(trace), "situation": block,
            "conversation_id": row["conversation_id"], "timestamp": row["timestamp"],
            "stored": json.loads(row["integrity_check"])
            if "integrity_check" in keys and row["integrity_check"] else None,
        })
    conn.close()
    if only_prefix and len(items) != 1:
        raise SystemExit(f"{label}: expected one reply starting {only_prefix}, found {len(items)}")
    return items


def _turn_labels(path: Path) -> dict[str, str]:
    """reply id -> "C<n> t<k>", from the run's state files and timestamp order."""
    state = path.parents[2] / "state"
    labels: dict[str, str] = {}
    if not state.is_dir():
        return labels
    conn = _open_ro(path)
    for f in sorted(state.glob("C*.json")):
        try:
            cid = json.loads(f.read_text()).get("conversation_id")
        except (OSError, ValueError):
            continue
        rows = conn.execute("SELECT id FROM messages WHERE conversation_id = ? AND "
                            "role = 'assistant' ORDER BY timestamp, id", (cid,)).fetchall()
        for k, r in enumerate(rows, start=1):
            labels[r["id"]] = f"{f.stem} t{k}"
    conn.close()
    return labels


def build_items() -> tuple[dict[str, dict], dict]:
    gate_cases = gate_eval.load_cases()
    corr_cases = correction_eval.load_cases()
    items: dict[str, dict] = {}
    for case in gate_cases:
        items[f"gate:{case.id}"] = {"kind": "gate", "case": case}
    for case in corr_cases:
        items[f"corr:{case.id}"] = {"kind": "corr", "case": case}
    for reply in _replies(RUN3_STORE, "run3"):
        items[f"run3:{reply['id']}"] = reply
    for reply in _replies(RUN1_STORE, "run1", only_prefix=RUN1_REPLY_PREFIX):
        items[f"run1:{reply['id']}"] = reply
    meta = {
        "gate_cases": len(gate_cases), "gate_fingerprint": gate_eval.fingerprint(gate_cases),
        "corr_cases": len(corr_cases), "corr_fingerprint": correction_eval.fingerprint(corr_cases),
        "run3_replies": sum(1 for k in items if k.startswith("run3:")),
    }
    return items, meta


# --- sampling -----------------------------------------------------------------------


def sample(item: dict, ground_truth: str) -> dict:
    kind = item["kind"]
    if kind == "gate":
        outcome = gate_eval.sample_once(item["case"], ground_truth)
        return {"flagged": outcome.flagged, "scored": outcome.scored, **outcome.to_dict()}
    if kind == "corr":
        outcome = correction_eval.sample_once(item["case"])
        return {"outcome": outcome.outcome, "scored": outcome.scored,
                "linked_target": outcome.linked_target, "linked_state": outcome.linked_state,
                "unusable": outcome.unusable}
    verdict = gate.check(item["answer"], item["trace"], item["situation"],
                         ground_truth=ground_truth)
    return {"flagged": verdict.status is gate.GateStatus.FLAGGED,
            "scored": verdict.status is not gate.GateStatus.UNAVAILABLE, **verdict.to_dict()}


def key_of(kind: str, result: dict) -> str:
    """What has to be equal across passes for an item to count as not differing."""
    if kind == "corr":
        return json.dumps([result.get("outcome"), result.get("linked_target"),
                           result.get("linked_state")])
    if not result.get("scored"):
        return "unavailable"
    return json.dumps([result.get("status"), sorted(
        [f.get("rule"), f.get("claim_class")] for f in result.get("findings", [])
    ) if "findings" in result else sorted(zip(result.get("rules", []),
                                               result.get("finding_classes", [])))])


def load_raw(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def run_pass(name: str, seed: int, order: list[str], items, ground_truth, raw_path, done,
             log) -> None:
    for position, key in enumerate(order, start=1):
        if (name, key) in done:
            continue
        started = time.monotonic()
        try:
            result = sample(items[key], ground_truth)
        except Exception as exc:  # noqa: BLE001 - recorded, never silent
            result = {"scored": False, "error": f"{type(exc).__name__}: {exc}"}
        record = {"pass": name, "seed": seed, "position": position, "key": key,
                  "kind": items[key]["kind"], "result": result,
                  "seconds": round(time.monotonic() - started, 3),
                  "at": datetime.now(timezone.utc).isoformat()}
        with raw_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        done.add((name, key))
        log(f"{name} {position}/{len(order)} {key[:60]} "
            f"{'unscored' if not result.get('scored') else 'ok'} {record['seconds']}s")


# --- reporting ----------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _gate_flag(result: dict) -> str:
    if not result.get("scored"):
        return "unavailable"
    return "FLAG" if result.get("flagged") else "clean"


def _classes(result: dict) -> str:
    classes = result.get("finding_classes") or [f.get("claim_class") for f in
                                                result.get("findings", [])]
    rules = result.get("rules") or [f.get("rule") for f in result.get("findings", [])]
    return ", ".join(f"{r}/{c}" for r, c in zip(rules, classes)) or "-"


def render(items, meta, header, raw) -> str:
    by_key: dict[str, dict[str, dict]] = defaultdict(dict)
    for record in raw:
        by_key[record["key"]][record["pass"]] = record["result"]
    base = [f"p{i}" for i in (1, 2, 3)]
    labels = _turn_labels(RUN3_STORE)
    lines = ["# Measurement point A (piece 3.1, temperature 0, current rubric and prompt)", "",
             "Descriptive unless a line says otherwise. Raw samples: `raw.jsonl` beside this file.",
             "", "## Header", "", "```", json.dumps(header, indent=2), "```", ""]

    def differs(key: str) -> bool:
        results = [by_key[key][p] for p in base if p in by_key[key]]
        return len({key_of(items[key]["kind"], r) for r in results}) > 1

    def escalation(key: str) -> str:
        # Only for an item that differed across the base passes; the fillers that kept the
        # escalation passes interleaved are sampled too, and their extra samples are not
        # reported as anything.
        extra = [r for p, r in by_key[key].items() if p.startswith("e")]
        if not extra or not differs(key):
            return ""
        kind = items[key]["kind"]
        if kind == "corr":
            k = sum(r.get("outcome") == correction_eval.OK for r in extra if r.get("scored"))
            what = "ok"
        else:
            k = sum(bool(r.get("flagged")) for r in extra if r.get("scored"))
            what = "flagged"
        n = sum(1 for r in extra if r.get("scored"))
        lo, hi = wilson(k, n)
        return (f"state noise: {what} {k}/{n} over {len(extra)} escalation passes "
                f"(95% Wilson {lo:.2f}-{hi:.2f}); not a rate")

    # Gate set, by class.
    lines += ["## Frozen gate set", ""]
    for cls, title in (("tool_output", "TOOL"), ("action", "ACTION"), ("identity", "identity")):
        keys = [k for k, v in items.items() if v["kind"] == "gate" and v["case"].claim_class == cls]
        correct = differing = 0
        rows = []
        for key in keys:
            case = items[key]["case"]
            results = [by_key[key].get(p) for p in base]
            marks = [_gate_flag(r) if r else "(not run)" for r in results]
            right = [r is not None and r.get("scored")
                     and bool(r.get("flagged")) == case.should_flag for r in results]
            correct += all(right)
            differing += differs(key)
            note = "" if all(right) else ("DIFFERS" if differs(key) else "WRONG")
            rows.append(f"| {case.id} | {'flag' if case.should_flag else 'clean'} | "
                        f"{' / '.join(marks)} | {note} | {escalation(key)} |")
        lines += [f"### {title}: {correct}/{len(keys)} right in all 3 passes; "
                  f"{differing} differ across passes", "",
                  "| case | expected | p1 / p2 / p3 | | escalation |", "|---|---|---|---|---|",
                  *rows, ""]
    lines += ["### The five must-catch cases", ""]
    for cid in MUST_CATCH:
        key = f"gate:{cid}"
        results = [by_key[key].get(p) for p in base]
        lines.append(f"- **{cid}**: " + " / ".join(
            f"{_gate_flag(r)} ({_classes(r)})" if r else "(not run)" for r in results)
            + (f"; {escalation(key)}" if escalation(key) else ""))
    lines.append("")

    # Correction set.
    keys = [k for k, v in items.items() if v["kind"] == "corr"]
    rows = []
    correct = 0
    for key in keys:
        case = items[key]["case"]
        outs = [by_key[key].get(p, {}).get("outcome", "(not run)") for p in base]
        ok = all(o == correction_eval.OK for o in outs)
        correct += ok
        rows.append(f"| {case.id} | {case.kind} | {' / '.join(outs)} | "
                    f"{'' if ok else ('DIFFERS' if differs(key) else 'WRONG')} | "
                    f"{escalation(key)} |")
    lines += [f"## Frozen correction set: {correct}/{len(keys)} ok in all 3 passes", "",
              "| case | kind | p1 / p2 / p3 | | escalation |", "|---|---|---|---|---|", *rows, ""]

    # Run 3 re-judge against stored verdicts.
    keys = [k for k, v in items.items() if v["kind"] == "run3"]
    moved = []
    tallies = defaultdict(int)
    for key in keys:
        item = items[key]
        stored = item["stored"] or {}
        stored_flag = stored.get("status") == "flagged"
        results = [by_key[key].get(p) for p in base]
        new_flags = [bool(r and r.get("flagged")) for r in results]
        tallies["stored flagged"] += stored_flag
        tallies["flagged at T=0, pass 1"] += new_flags[0]
        for cls in ("identity", "tool_output", "action"):
            tallies[f"stored {cls}"] += any(f.get("claim_class") == cls
                                            for f in stored.get("findings", []))
            tallies[f"T=0 p1 {cls}"] += bool(results[0]) and any(
                f.get("claim_class") == cls for f in results[0].get("findings", []))
        if any(f != stored_flag for f in new_flags) or differs(key):
            moved.append((key, item, stored, results))
    lines += [f"## Run 3: {len(keys)} replies re-judged at T=0 against their stored verdicts", "",
              *[f"- {name}: {count}" for name, count in tallies.items()], "",
              f"### Verdicts that moved ({len(moved)}), with the reply text", ""]
    for key, item, stored, results in moved:
        label = labels.get(item["id"], "?")
        lines += [f"#### {label} `{item['id'][:8]}`", "",
                  f"- stored: {stored.get('status', 'none')} ({_classes(stored)})",
                  *[f"- {p}: {_gate_flag(r) if r else '(not run)'} ({_classes(r) if r else ''})"
                    for p, r in zip(base, results)],
                  *([f"- {escalation(key)}"] if escalation(key) else []),
                  "", "```", item["answer"], "```", ""]
        for p, r in zip(base, results):
            for f in (r or {}).get("findings", []):
                lines.append(f"  - {p} finding: {f.get('rule')}/{f.get('claim_class')}: "
                             f"evidence {f.get('evidence')!r}; detail {f.get('detail')!r}")
        lines.append("")

    # Run 1's reply.
    for key in [k for k, v in items.items() if v["kind"] == "run1"]:
        item = items[key]
        results = [by_key[key].get(p) for p in base]
        lines += [f"## Run 1, C3 t4 `{item['id'][:8]}` (the re-check owed since batch 1)", "",
                  f"- stored at the time: {(item['stored'] or {}).get('status', 'none')}",
                  *[f"- {p}: {_gate_flag(r) if r else '(not run)'}; "
                    + "; ".join(f"{f.get('rule')}/{f.get('claim_class')} evidence "
                                f"{f.get('evidence')!r} detail {f.get('detail')!r}"
                                for f in (r or {}).get("findings", []))
                    for p, r in zip(base, results)],
                  *([f"- {escalation(key)}"] if escalation(key) else []),
                  "", "Situation block as rebuilt:", "", "```", item["situation"], "```", "",
                  "Reply:", "", "```", item["answer"], "```", ""]
    return "\n".join(lines)


# --- main ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True,
                        help="directory for raw.jsonl, report.md and run.log (outside the repo)")
    parser.add_argument("--seeds", type=int, nargs=3, default=list(DEFAULT_SEEDS))
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)

    out = args.out.expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if str(out).startswith(str(repo)):
        parser.error("--out must be outside the repository (it will hold raw replies)")
    out.mkdir(parents=True, exist_ok=True)
    raw_path, log_path = out / "raw.jsonl", out / "run.log"

    def log(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    items, meta = build_items()
    ground_truth = gate.load_architecture()
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    header = {
        **meta, "commit": commit, "scratch_root": str(SCRATCH),
        "classifier_model": config.classifier_model(),
        "classifier_temperature": config.classifier_temperature(),
        "chat_temperature": config.model_options().get("temperature"),
        "num_ctx": config.model_options().get("num_ctx"),
        "num_predict": config.classifier_num_predict(),
        "tools_registered": list(registry.default_registry().names),
        "seeds": args.seeds, "escalation_passes": ESCALATION_PASSES,
        "escalation_fillers": ESCALATION_FILLERS,
    }
    if config.classifier_temperature() != 0:
        raise SystemExit("point A is defined at classifier temperature 0")

    raw = load_raw(raw_path)
    if not args.report_only:
        done = {(r["pass"], r["key"]) for r in raw}
        log(f"start: {len(items)} items, {len(done)} samples already done, header {header}")
        keys = sorted(items)
        for i, seed in enumerate(args.seeds, start=1):
            order = keys[:]
            random.Random(seed).shuffle(order)
            run_pass(f"p{i}", seed, order, items, ground_truth, raw_path, done, log)
        raw = load_raw(raw_path)
        by_key = defaultdict(dict)
        for record in raw:
            by_key[record["key"]][record["pass"]] = record["result"]
        differing = sorted(k for k in keys if len({
            key_of(items[k]["kind"], by_key[k][p]) for p in ("p1", "p2", "p3")
            if p in by_key[k]}) > 1)
        log(f"{len(differing)} item(s) differ across the 3 passes: {differing}")
        others = [k for k in keys if k not in set(differing)]
        for j in range(1, ESCALATION_PASSES + 1):
            if not differing:
                break
            seed = args.seeds[0] * 100 + j
            rng = random.Random(seed)
            fillers = rng.sample(others, min(ESCALATION_FILLERS, len(others)))
            order = differing + fillers
            rng.shuffle(order)
            run_pass(f"e{j}", seed, order, items, ground_truth, raw_path, done, log)
        raw = load_raw(raw_path)
        log("sampling finished")

    (out / "report.md").write_text(render(items, meta, header, raw), encoding="utf-8")
    log(f"report written: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
