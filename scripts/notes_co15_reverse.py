#!/usr/bin/env python3
"""CO17, the other direction: a NEW message that is itself a no-result report.

    python -m scripts.notes_co15_reverse run [--passes 20] [--seeds 1,2]
    python -m scripts.notes_co15_reverse report

The exclusion drops an earlier entity message that reported an empty search from the pool. This
measures the mirror before any rule is added for it: the **new** message says nothing was found
(*"There is no note about X."*, *"I looked in the notes and found nothing about X."*, plus the 12
real turn-1 replies of piece 8), and the **older candidates are genuine entity claims** (N9's ten
background messages and a claim about X that is not about notes). Nothing in the new message
contradicts any of them, so **any link is a false link**; if one occurs, the symmetric skip (a
message that reports only empty searches is not classified) is warranted, and if none does it is
recorded as measured clean.

The frozen harness's own pool shape and production order (`Case.pool()`), `sample_once`, 2 seeds x
20 passes, a fresh shuffle every pass. Controls that MUST link prove the harness is alive.
Raw samples go to ``~/anam-measurements/p8/co15_reverse.jsonl`` as each pass finishes (resumable).
"""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import collections
import json
import os
import random
import sys
import time
from pathlib import Path

_SCRATCH = Path.home() / "anam-measurements" / "p8" / "scratch-reverse"
for _k, _s in (("ANAM_DATA_DIR", "data"), ("ANAM_BACKUP_DIR", "backups"),
               ("ANAM_ARTIFACT_DIR", "artifacts"), ("ANAM_WORKSPACE_DIR", "workspace")):
    os.environ.setdefault(_k, str(_SCRATCH / _s))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from program.integrity import correction_eval  # noqa: E402
from scripts.notes_co15 import WORDINGS, fmt  # noqa: E402

OUT = Path.home() / "anam-measurements" / "p8"
RAW = OUT / "co15_reverse.jsonl"
TOPICS = {
    "descaling": ("descaling", "The kettle should be descaled once a month with white vinegar."),
    "grinder": ("cleaning the grinder", "The grinder should be cleaned with a stiff brush about once a month."),
}


def build_cases():
    frozen = {c.id: c for c in correction_eval.load_cases()}
    background = frozen["N9-records-scope"].candidates[:-1]
    cases, labels = [], {}

    def add(cid, family, new_message, claim, *, should_link=False, target=None, replacement=None):
        cases.append(correction_eval.Case(
            id=cid, kind="scope", speaker_role="assistant", should_link=should_link,
            new_message=new_message, note="reverse direction", target=target,
            replacement=replacement,
            candidates=background + ({"id": "claim", "role": "assistant", "content": claim},)))
        labels[cid] = family

    for wid, template in WORDINGS:
        for tname, (x, claim) in TOPICS.items():
            add(f"R|{wid}|{tname}", "canonical-new-message", template.format(x=x), claim)
    turns_file = OUT / "co15_real_turns.jsonl"
    for n, t in enumerate([r for r in map(json.loads, turns_file.read_text().splitlines()) if r["qualifies"]], 1):
        add(f"R|real{n:02d}|{t['topic']}", "real-turn1-reply-as-new-message", t["turn1"], TOPICS[t["topic"]][1])
    # controls that MUST link: a genuine correction of an entity claim
    c4 = frozen["C4-self-correction"]
    add("R|CONTROL-C4-self-correction", "control-must-link", c4.new_message,
        c4.candidates[-1]["content"] if False else next(c for c in c4.candidates if c["id"] == "p1")["content"],
        should_link=True, target="claim", replacement="replaced")
    add("R|CONTROL-genuine-correction-of-a-topic-claim", "control-must-link",
        "Correction to what I said earlier: it should be descaled every two weeks here, not once a month, because the water is so hard.",
        TOPICS["descaling"][1], should_link=True, target="claim", replacement="replaced")
    return cases, labels


def run(passes, seeds):
    cases, labels = build_cases()
    by_id = {c.id: c for c in cases}
    done = set()
    if RAW.exists():
        done = {(r["seed"], r["pass"]) for r in map(json.loads, RAW.read_text().splitlines()) if r.get("marker")}
    print(f"{len(cases)} cases per pass", file=sys.stderr, flush=True)
    for seed in seeds:
        for p in range(passes):
            if (seed, p) in done:
                continue
            order = list(by_id)
            random.Random(f"reverse-{seed}-{p}").shuffle(order)
            t0 = time.time()
            with RAW.open("a") as f:
                for cid in order:
                    o = correction_eval.sample_once(by_id[cid])
                    f.write(json.dumps({"seed": seed, "pass": p, "case": cid, "family": labels[cid],
                                        "outcome": o.outcome, "linked": o.linked_target,
                                        "state": o.linked_state, "unusable": o.unusable}) + "\n")
                    f.flush()
                f.write(json.dumps({"marker": True, "seed": seed, "pass": p}) + "\n")
            print(f"seed {seed} pass {p + 1}/{passes} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)


def report() -> str:
    rows = [r for r in map(json.loads, RAW.read_text().splitlines()) if "case" in r]
    out = []
    for fam in ("canonical-new-message", "real-turn1-reply-as-new-message", "control-must-link"):
        sub = [r for r in rows if r["family"] == fam]
        if fam == "control-must-link":
            out.append(f"-- {fam}: linked correctly {fmt(sum(r['outcome'] == 'ok' for r in sub), len(sub))}")
        else:
            out.append(f"-- {fam}: FALSE LINKS {fmt(sum(1 for r in sub if r['linked']), len(sub))}")
        by = collections.defaultdict(list)
        for r in sub:
            by[r["case"]].append(r)
        for cid, rs in sorted(by.items()):
            k = sum(1 for r in rs if (r["outcome"] == "ok" if fam == "control-must-link" else r["linked"]))
            out.append(f"   {cid:<72} {fmt(k, len(rs))}")
    out.append(f"unusable replies: {sum(1 for r in rows if r['unusable'])} of {len(rows)}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--passes", type=int, default=20)
    r.add_argument("--seeds", default="1,2")
    sub.add_parser("report")
    sub.add_parser("count")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.passes, [int(s) for s in a.seeds.split(",")])
    elif a.cmd == "count":
        print(len(build_cases()[0]))
    else:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
