#!/usr/bin/env python3
"""Measurement point B (`docs/DESIGN_GATE_ORDINARY_READING.md` Appendix F; decision #32): the gate
with the ordinary reading's rubric and prompt, on the refrozen case set, at temperature 0.

    venv/bin/python -m scripts.point_b --out ~/anam-measurements/point-b \\
        --main-store "/Volumes/Dock Storage/Atman/data/working.db"

What it samples, all at ``integrity.classifier_temperature`` (0):

* ``gate``: every frozen fabrication-gate case (`eval/fabrication_gate/cases.toml`, 69 cases)
  under the NEW texts (the rubric and `gate._PROMPT` as committed on this branch).
* ``run3``: run 3's 124 replies, each judged twice per pass: under the OLD texts (the rubric and
  prompt on ``--old-ref``, default ``main``, checked against pinned sha256 values so a moved ref
  fails loudly rather than measuring the wrong thing) and under the NEW texts. Same rebuilt
  situation and stored trace in both arms; only the two texts differ.

**Passes.** Three passes over every item, each in an order shuffled with a recorded seed, so the
cases and both arms are interleaved and no prompt is sampled twice in a row. An item whose three
outcomes differ is then sampled in 20 more shuffled passes, each mixing the differing items with
10 fillers. At temperature 0 that is **state noise**, reported as a count with an interval, never
as a rate (`AGENTS.md`, "When the classifier runs at temperature 0").

**Outcomes.** ``unavailable`` (no usable verdict) is its own outcome everywhere: never counted as
right or as wrong.

**What the gate is given.** The speaker line ("You are talking with <name>.") and the earlier-tools
list are in the ENTITY's prompt, not in the gate's input: the gate reads the reply, the trace and
the situation block. Run 3's replies were generated before either existed.

**Also, read-only, no model:** from ``--main-store`` (the main checkout's ``data/working.db``,
opened ``mode=ro``), every assistant turn recorded ``unavailable`` to date, with its reply text.

**Never the real store for anything else.** `scratch_env("point-b")` runs before anything under
`program` is imported. The run stores are opened read-only and immutable with `sqlite3`.

**Resumable.** Each sample is appended to ``<out>/raw.jsonl`` as it finishes; a restart skips what
is done. ``--report-only`` renders without sampling. ``--compare <earlier raw.jsonl>`` adds a
section listing every item whose verdict differs from that run's, pass by pass (the re-run after
the A1 fix, 2026-10-09).

**What production built, and what this rebuilds** (as point A): run 3's kit registry (notes on,
Moltbook off, ComfyUI off); the situation block from production's builder with ``now`` at the
question's stored time and the person's previous message before it; the trace as
``loop.call_entries`` of the stored ``tool_trace``.
"""

from __future__ import annotations

import os
import sys

# Run 3's kit (kit-internal/bin/env.sh), set before any program import.
os.environ["ANAM_NOTES_ENABLED"] = "true"
os.environ["ANAM_MOLTBOOK_ENABLED"] = "false"
os.environ["ANAM_COMFYUI_ENABLED"] = "false"

from scripts._scratch import scratch_env  # noqa: E402

SCRATCH = scratch_env("point-b")

import argparse  # noqa: E402
import ast  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import random  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

from program import config  # noqa: E402
from program.engine import loop  # noqa: E402
from program.engine import situation as situation_block  # noqa: E402
from program.integrity import gate, gate_eval  # noqa: E402
from program.tools import registry  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RUNS = Path.home() / "anam-measurements" / "runs"
RUN3_STORE = RUNS / "run3" / "store" / "data" / "working.db"
DEFAULT_SEEDS = (20261008, 20261009, 20261010)
ESCALATION_PASSES = 20
ESCALATION_FILLERS = 10

#: The texts before decision #32, as on main at 8b0f45b. Checked, never trusted.
OLD_ARCHITECTURE_SHA256 = "63ec73230df12935a7ba80d54b1d9dc1884e76495c8ef75487966cb6c8eb9804"
OLD_PROMPT_SHA256 = "b2e8637b4e324d55eb1bd5f77ee6f85c2eb6c5c17ce03e8614b89173f6847995"

#: Each on its own line in the report (the brief's list).
NAMED = ("P14-thinking-since-yesterday", "P15-background-work",
         "P16-gap-experience-with-situation", "T12-retrained-between-turns",
         "T13-weights-update", "T11b-model-improved-by-conversations",
         "G2-concrete-gap-activity")
CLASSES = (("tool_output", "TOOL"), ("action", "ACTION"), ("identity", "identity"))


# --- the two sets of texts ------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def old_texts(ref: str) -> tuple[str, str]:
    """(rubric, prompt) before decision #32, read from ``ref`` and verified."""
    def show(path: str) -> str:
        done = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{path}"],
                              capture_output=True, text=True)
        if done.returncode:
            raise SystemExit(f"cannot read {path} at {ref}: {done.stderr.strip()}")
        return done.stdout

    rubric = show("program/integrity/architecture.md").strip()
    tree = ast.parse(show("program/integrity/gate.py"))
    prompt = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                  and getattr(node.targets[0], "id", "") == "_PROMPT")
    if _sha(rubric) != OLD_ARCHITECTURE_SHA256 or _sha(prompt) != OLD_PROMPT_SHA256:
        raise SystemExit(f"{ref} no longer holds the pre-#32 rubric and prompt (sha256 "
                         f"mismatch). Pass --old-ref with a commit that does, e.g. 8b0f45b.")
    return rubric, prompt


def new_texts() -> tuple[str, str]:
    return gate.load_architecture(), gate._PROMPT


# --- items ------------------------------------------------------------------------------


def _open_ro(path: Path, immutable: bool = True) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"store not found: {path}")
    flags = "mode=ro&immutable=1" if immutable else "mode=ro"
    conn = sqlite3.connect(f"file:{path}?{flags}", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _run3_replies() -> list[dict]:
    """Each assistant reply with what production handed the gate, rebuilt (as point A)."""
    conn = _open_ro(RUN3_STORE)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
    items = []
    for row in conn.execute("SELECT * FROM messages WHERE role = 'assistant' "
                            "ORDER BY timestamp, id").fetchall():
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
            now=now, previous_message_at=datetime.fromisoformat(previous) if previous else None,
            speaker=names.get(row["user_id"]))
        trace = json.loads(row["tool_trace"]) if row["tool_trace"] else []
        items.append({
            "id": row["id"], "answer": row["content"], "trace": loop.call_entries(trace),
            "situation": block, "conversation_id": row["conversation_id"],
            "stored": json.loads(row["integrity_check"]) if row["integrity_check"] else None,
        })
    conn.close()
    return items


def _turn_labels() -> dict[str, str]:
    """reply id -> "C<n> t<k>", from the run's state files and timestamp order."""
    state = RUN3_STORE.parents[2] / "state"
    labels: dict[str, str] = {}
    if not state.is_dir():
        return labels
    conn = _open_ro(RUN3_STORE)
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
    cases = gate_eval.load_cases()
    items: dict[str, dict] = {}
    for case in cases:
        items[f"gate:{case.id}"] = {"kind": "gate", "arm": "new", "case": case}
    replies = _run3_replies()
    for reply in replies:
        for arm in ("old", "new"):
            items[f"run3:{reply['id']}:{arm}"] = {"kind": "run3", "arm": arm, **reply}
    meta = {"gate_cases": len(cases), "gate_fingerprint": gate_eval.fingerprint(cases),
            "run3_replies": len(replies)}
    return items, meta


# --- sampling ---------------------------------------------------------------------------


def sample(item: dict, texts: dict[str, tuple[str, str]]) -> dict:
    rubric, prompt = texts[item["arm"]]
    saved = gate._PROMPT
    gate._PROMPT = prompt
    try:
        if item["kind"] == "gate":
            outcome = gate_eval.sample_once(item["case"], rubric)
            return {"flagged": outcome.flagged, "scored": outcome.scored, **outcome.to_dict()}
        verdict = gate.check(item["answer"], item["trace"], item["situation"], ground_truth=rubric)
        return {"flagged": verdict.status is gate.GateStatus.FLAGGED,
                "scored": verdict.status is not gate.GateStatus.UNAVAILABLE, **verdict.to_dict()}
    finally:
        gate._PROMPT = saved


def _findings(result: dict) -> list[tuple[str, str]]:
    if "findings" in result:
        return sorted((f.get("rule"), f.get("claim_class")) for f in result.get("findings", []))
    return sorted(zip(result.get("rules", []), result.get("finding_classes", [])))


def key_of(result: dict) -> str:
    if not result.get("scored"):
        return "unavailable"
    return json.dumps([bool(result.get("flagged")), _findings(result)])


def load_raw(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def run_pass(name, seed, order, items, texts, raw_path, done, log) -> None:
    for position, key in enumerate(order, start=1):
        if (name, key) in done:
            continue
        started = time.monotonic()
        try:
            result = sample(items[key], texts)
        except Exception as exc:  # noqa: BLE001 - recorded, never silent
            result = {"scored": False, "error": f"{type(exc).__name__}: {exc}"}
        record = {"pass": name, "seed": seed, "position": position, "key": key,
                  "kind": items[key]["kind"], "arm": items[key]["arm"], "result": result,
                  "seconds": round(time.monotonic() - started, 3),
                  "at": datetime.now(timezone.utc).isoformat()}
        with raw_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        done.add((name, key))
        log(f"{name} {position}/{len(order)} {key[:70]} "
            f"{'unavailable' if not result.get('scored') else 'ok'} {record['seconds']}s")


def _no_back_to_back(order: list[str]) -> list[str]:
    """A reply's two arms are different prompts, but keep them apart too: the same answer back to
    back is the closest thing to a repeat this set has."""
    out = order[:]
    for i in range(1, len(out)):
        if out[i].rsplit(":", 1)[0] == out[i - 1].rsplit(":", 1)[0] and out[i].startswith("run3:"):
            for j in range(i + 1, len(out)):
                if out[j].rsplit(":", 1)[0] != out[i - 1].rsplit(":", 1)[0]:
                    out[i], out[j] = out[j], out[i]
                    break
    return out


# --- reporting --------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _mark(result: dict | None) -> str:
    if result is None:
        return "(not run)"
    if not result.get("scored"):
        return "unavailable"
    return "FLAG" if result.get("flagged") else "clean"


def _classes(result: dict | None) -> str:
    return ", ".join(f"{r}/{c}" for r, c in _findings(result or {})) or "-"


def _outcome(case, result: dict | None) -> str:
    if result is None:
        return "not run"
    if not result.get("scored"):
        return "unavailable"
    return "right" if bool(result.get("flagged")) == case.should_flag else "wrong"


def main_store_unavailable(path: Path | None) -> list[str]:
    if path is None:
        return ["(no --main-store given; not read)", ""]
    conn = _open_ro(path, immutable=False)
    rows = conn.execute(
        "SELECT a.id, a.timestamp, a.content, a.integrity_check, "
        "(SELECT u.content FROM messages u WHERE u.conversation_id = a.conversation_id AND "
        " u.role = 'user' AND u.timestamp <= a.timestamp ORDER BY u.timestamp DESC LIMIT 1) AS q "
        "FROM messages a WHERE a.role = 'assistant' AND a.integrity_check IS NOT NULL "
        "ORDER BY a.timestamp").fetchall()
    total = conn.execute("SELECT count(*) FROM messages WHERE role = 'assistant'").fetchone()[0]
    conn.close()
    unavailable = []
    for row in rows:
        try:
            verdict = json.loads(row["integrity_check"])
        except ValueError:
            continue
        if verdict.get("status") == "unavailable":
            unavailable.append((row, verdict))
    lines = [f"{len(unavailable)} of {total} assistant turns are recorded `unavailable` "
             f"(store `{path}`, read-only).", ""]
    for row, verdict in unavailable:
        lines += [f"### `{row['id'][:8]}` at {row['timestamp']}", "",
                  f"- semantic_error: {verdict.get('semantic_error')!r}", "",
                  "The person's message before it:", "", "```", str(row["q"]), "```", "",
                  "The reply:", "", "```", row["content"], "```", ""]
    return lines


def compare_section(items, raw, previous: list[dict], previous_path: Path) -> list[str]:
    """Every item whose verdict in any of the three passes differs from an earlier run's.

    Same seeds and the same item set give the same order, so pass ``p1`` here meets the same
    neighbours as ``p1`` there: at temperature 0 a difference is the change between the runs,
    or state noise, which the old arm (identical texts in both runs) measures.
    """
    def table(records):
        by_key: dict[str, dict[str, dict]] = defaultdict(dict)
        for record in records:
            by_key[record["key"]][record["pass"]] = record["result"]
        return by_key

    now, before = table(raw), table(previous)
    base = ("p1", "p2", "p3")
    labels = _turn_labels()
    changed = [key for key in sorted(set(now) | set(before)) if any(
        key_of(now[key].get(p) or {}) != key_of(before[key].get(p) or {}) for p in base)]
    counts = defaultdict(int)
    for key in changed:
        counts["gate" if key.startswith("gate:") else f"run3 {key.rsplit(':', 1)[1]}"] += 1
    lines = [f"## Every verdict that differs from the earlier run (`{previous_path}`)", "",
             f"{len(changed)} item(s) differ in at least one of p1-p3: gate cases "
             f"{counts['gate']}, run 3 old arm {counts['run3 old']}, run 3 new arm "
             f"{counts['run3 new']}. The old arm's texts are the same in both runs, so its count "
             f"is the state noise between them.", ""]
    for key in changed:
        item = items.get(key)
        if key.startswith("gate:"):
            expected = "flag" if item and item["case"].should_flag else "clean"
            title = f"`{key[5:]}` (expected {expected})"
        else:
            rid, arm = key.rsplit(":", 1)
            title = f"{labels.get(rid[5:], '?')} `{rid[5:13]}`, {arm} arm"
        lines += [f"### {title}", "",
                  "- earlier: " + " / ".join(f"{_mark(before[key].get(p))} "
                                             f"({_classes(before[key].get(p))})" for p in base),
                  "- now: " + " / ".join(f"{_mark(now[key].get(p))} "
                                         f"({_classes(now[key].get(p))})" for p in base)]
        for p in base:
            for f in (now[key].get(p) or {}).get("findings", []):
                lines.append(f"  - now {p}: {f.get('rule')}/{f.get('claim_class')} evidence "
                             f"{f.get('evidence')!r}; detail {f.get('detail')!r}")
        answer = item["case"].answer if item and key.startswith("gate:") else (item or {}).get(
            "answer")
        if answer is not None:
            lines += ["", "```", answer, "```"]
        lines.append("")
    return lines


def render(items, meta, header, raw, main_store: Path | None,
           previous: tuple[list[dict], Path] | None = None) -> str:
    by_key: dict[str, dict[str, dict]] = defaultdict(dict)
    for record in raw:
        by_key[record["key"]][record["pass"]] = record["result"]
    base = ("p1", "p2", "p3")
    labels = _turn_labels()

    def results(key):
        return [by_key[key].get(p) for p in base]

    def differs(key) -> bool:
        return len({key_of(r) for r in results(key) if r is not None}) > 1

    def escalation(key) -> str:
        extra = [r for p, r in by_key[key].items() if p.startswith("e")]
        if not extra or not differs(key):
            return ""
        scored = [r for r in extra if r.get("scored")]
        k = sum(bool(r.get("flagged")) for r in scored)
        lo, hi = wilson(k, len(scored))
        return (f"state noise: flagged {k}/{len(scored)}, unavailable "
                f"{len(extra) - len(scored)}, over {len(extra)} escalation passes "
                f"(95% Wilson {lo:.2f}-{hi:.2f}); not a rate")

    lines = ["# Measurement point B (decision #32: the ordinary reading, refrozen set, T=0)", "",
             "Descriptive unless a line says otherwise. Raw samples: `raw.jsonl` beside this file.",
             "",
             "**The speaker line (\"You are talking with <name>.\") and the earlier-tools list are "
             "in the entity's prompt, not in the gate's input.** The gate reads the reply, the "
             "trace and the situation block only. Run 3's replies were generated before either "
             "existed.", "",
             "`unavailable` is its own outcome throughout: never counted as right or as wrong.", "",
             "## Header", "", "```", json.dumps(header, indent=2), "```", ""]

    # The frozen set, by class.
    lines += ["## Frozen gate set (new texts)", ""]
    for cls, title in CLASSES:
        keys = [k for k, v in items.items() if v["kind"] == "gate" and v["case"].claim_class == cls]
        tally: Counter = Counter()
        all_right = differing = 0
        rows = []
        for key in keys:
            case = items[key]["case"]
            outs = [_outcome(case, r) for r in results(key)]
            tally.update(outs)
            all_right += outs.count("right") == 3
            differing += differs(key)
            flag = "" if outs.count("right") == 3 else (
                "DIFFERS" if differs(key)
                else ("UNAVAILABLE" if "unavailable" in outs else "WRONG"))
            rows.append(f"| {case.id} | {'flag' if case.should_flag else 'clean'} | "
                        f"{' / '.join(_mark(r) for r in results(key))} | {flag} | "
                        f"{escalation(key)} |")
        lines += [f"### {title}: {all_right}/{len(keys)} right in all 3 passes; samples right "
                  f"{tally['right']}, wrong {tally['wrong']}, unavailable {tally['unavailable']}; "
                  f"{differing} differ across passes", "",
                  "| case | expected | p1 / p2 / p3 | | escalation |", "|---|---|---|---|---|",
                  *rows, ""]

    lines += ["### Named cases, each on its own line", ""]
    for cid in NAMED:
        key = f"gate:{cid}"
        if key not in items:
            lines.append(f"- **{cid}**: not in the case set")
            continue
        expected = "flag" if items[key]["case"].should_flag else "clean"
        lines.append(f"- **{cid}** (expected {expected})"
                     ": " + " / ".join(f"{_mark(r)} ({_classes(r)})" for r in results(key))
                     + (f"; {escalation(key)}" if escalation(key) else ""))
    lines.append("")
    for prefix, title in (("gate:SR", "Short replies (must get a verdict, and be clean)"),
                          ("gate:R3", "Run 3 lines (must not flag)"),
                          ("gate:G1", "Affection that presumes the gap (must not flag)")):
        keys = [k for k in items if k.startswith(prefix)]
        lines += [f"### {title}", ""]
        for key in keys:
            lines.append(f"- {items[key]['case'].id}: "
                         + " / ".join(f"{_mark(r)} ({_classes(r)})" for r in results(key)))
        lines.append("")

    # Run 3, old texts against new.
    replies = sorted({k.rsplit(":", 1)[0] for k in items if k.startswith("run3:")})
    lines += [f"## Run 3: {len(replies)} replies, old texts against new", ""]
    for arm in ("old", "new"):
        tally: Counter = Counter()
        for rid in replies:
            for r in results(f"{rid}:{arm}"):
                tally[_mark(r)] += 1
                for _, cls in _findings(r or {}):
                    tally[f"finding {cls}"] += 1
        lines.append(f"- **{arm} texts**, samples over 3 passes: "
                     + ", ".join(f"{name} {count}" for name, count in sorted(tally.items())))
    for cls, title in CLASSES:
        counts = {arm: sum(any(c == cls for _, c in _findings(r or {}))
                           for rid in replies for r in results(f"{rid}:{arm}"))
                  for arm in ("old", "new")}
        lines.append(f"- {title} findings (samples): old {counts['old']}, new {counts['new']}")
    stored_flags = sum((items[f"{rid}:new"]["stored"] or {}).get("status") == "flagged"
                       for rid in replies)
    lines += [f"- stored at the time (run 3, chat temperature): flagged {stored_flags}", ""]

    moved = []
    for rid in replies:
        old_keys = [key_of(r) for r in results(f"{rid}:old") if r is not None]
        new_keys = [key_of(r) for r in results(f"{rid}:new") if r is not None]
        old_marks = {_mark(r) for r in results(f"{rid}:old")}
        new_marks = {_mark(r) for r in results(f"{rid}:new")}
        if old_marks != new_marks or differs(f"{rid}:old") or differs(f"{rid}:new") or (
                set(old_keys) != set(new_keys)):
            moved.append(rid)
    lines += [f"### Replies whose verdict moved between the arms, or differed within one "
              f"({len(moved)}), with the reply text", ""]
    for rid in moved:
        item = items[f"{rid}:new"]
        stored = item["stored"] or {}
        lines += [f"#### {labels.get(item['id'], '?')} `{item['id'][:8]}`", "",
                  f"- stored: {stored.get('status', 'none')} ({_classes(stored)})"]
        for arm in ("old", "new"):
            lines.append(f"- {arm}: " + " / ".join(f"{_mark(r)} ({_classes(r)})"
                                                     for r in results(f"{rid}:{arm}")))
            if escalation(f"{rid}:{arm}"):
                lines.append(f"  - {arm} {escalation(f'{rid}:{arm}')}")
        for arm in ("old", "new"):
            for p, r in zip(base, results(f"{rid}:{arm}")):
                for f in (r or {}).get("findings", []):
                    lines.append(f"  - {arm} {p}: {f.get('rule')}/{f.get('claim_class')} evidence "
                                 f"{f.get('evidence')!r}; detail {f.get('detail')!r}")
        lines += ["", "```", item["answer"], "```", ""]

    if previous is not None:
        lines += compare_section(items, raw, *previous)

    lines += ["## The main checkout's store: turns recorded unavailable to date (no model)", ""]
    lines += main_store_unavailable(main_store)
    return "\n".join(lines)


# --- main -------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True,
                        help="directory for raw.jsonl, report.md and run.log (outside the repo)")
    parser.add_argument("--main-store", type=Path, default=None,
                        help="the main checkout's data/working.db, read-only, for the count of "
                             "turns recorded unavailable")
    parser.add_argument("--old-ref", default="main",
                        help="git ref holding the pre-#32 rubric and prompt (verified by sha256)")
    parser.add_argument("--seeds", type=int, nargs=3, default=list(DEFAULT_SEEDS))
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--compare", type=Path, default=None,
                        help="an earlier run's raw.jsonl: the report lists every item whose "
                             "verdict differs from it, pass by pass")
    args = parser.parse_args(argv)

    out = args.out.expanduser().resolve()
    if str(out).startswith(str(REPO)):
        parser.error("--out must be outside the repository (it will hold raw replies)")
    out.mkdir(parents=True, exist_ok=True)
    raw_path, log_path = out / "raw.jsonl", out / "run.log"
    main_store = args.main_store.expanduser().resolve() if args.main_store else None

    def log(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    if config.classifier_temperature() != 0:
        raise SystemExit("point B is defined at classifier temperature 0")
    items, meta = build_items()
    texts = {"old": old_texts(args.old_ref), "new": new_texts()}
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    header = {
        **meta, "commit": commit, "scratch_root": str(SCRATCH), "old_ref": args.old_ref,
        "old_rubric_sha256": _sha(texts["old"][0]), "old_prompt_sha256": _sha(texts["old"][1]),
        "new_rubric_sha256": _sha(texts["new"][0]), "new_prompt_sha256": _sha(texts["new"][1]),
        "new_rubric_chars": len(texts["new"][0]),
        "classifier_model": config.classifier_model(),
        "classifier_temperature": config.classifier_temperature(),
        "num_predict": config.classifier_num_predict(),
        "tools_registered": list(registry.default_registry().names),
        "seeds": args.seeds, "escalation_passes": ESCALATION_PASSES,
        "escalation_fillers": ESCALATION_FILLERS,
        "speaker_line_and_tools_list": "in the entity's prompt, not in the gate's input",
    }

    previous = None
    if args.compare is not None:
        compare = args.compare.expanduser().resolve()
        if compare == raw_path:
            parser.error("--compare must be an earlier run's raw.jsonl, not this run's")
        if not compare.exists():
            parser.error(f"--compare: {compare} does not exist")
        previous = (load_raw(compare), compare)
        header["compared_with"] = str(compare)

    raw = load_raw(raw_path)
    if not args.report_only:
        done = {(r["pass"], r["key"]) for r in raw}
        log(f"start: {len(items)} items, {len(done)} samples already done, header {header}")
        keys = sorted(items)
        for i, seed in enumerate(args.seeds, start=1):
            order = keys[:]
            random.Random(seed).shuffle(order)
            run_pass(f"p{i}", seed, _no_back_to_back(order), items, texts, raw_path, done, log)
        raw = load_raw(raw_path)
        by_key = defaultdict(dict)
        for record in raw:
            by_key[record["key"]][record["pass"]] = record["result"]
        differing = sorted(k for k in keys if len({
            key_of(by_key[k][p]) for p in ("p1", "p2", "p3") if p in by_key[k]}) > 1)
        log(f"{len(differing)} item(s) differ across the 3 passes: {differing}")
        others = [k for k in keys if k not in set(differing)]
        for j in range(1, ESCALATION_PASSES + 1):
            if not differing:
                break
            seed = args.seeds[0] * 100 + j
            rng = random.Random(seed)
            order = differing + rng.sample(others, min(ESCALATION_FILLERS, len(others)))
            rng.shuffle(order)
            run_pass(f"e{j}", seed, _no_back_to_back(order), items, texts, raw_path, done, log)
        raw = load_raw(raw_path)
        log("sampling finished")

    (out / "report.md").write_text(render(items, meta, header, raw, main_store, previous),
                                    encoding="utf-8")
    log(f"report written: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
