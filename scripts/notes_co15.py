#!/usr/bin/env python3
"""Notes ship gate 1, canonical wordings: the CO15 composition test (`docs/NOTES_DESIGN.md` N9.1).

    python -m scripts.notes_co15 canonical [--passes 20] [--seeds 1,2]
    python -m scripts.notes_co15 report

**Measures only; changes no prompt, wording, alias or case file.** It calls
``corrections.classify`` through the frozen harness's own ``Case.pool()`` (production's order,
``corrections.production_order``) and ``correction_eval.sample_once``, so it measures what
production runs, in the frozen set's pool shape (N9's ten background messages, the entity's
claim newest and so shown first).

Two shapes, reported separately:

* **entity side**: the entity's follow-up answer judged against its own earlier claim. This is the
  shape running in production today (self-correction).
* **person side** (PN9): a person's answer judged against the entity's claim.
  ``corrections.person_corrects_entity`` is OFF, so this is the capability as built and not as
  running.

The claim is the N6 shape (*"I have searched/looked through my notes/records/memory and ...
nothing / do not find ... about X"*) in several wordings, followed by an answer about X taken
from the existing harness. **A link in either shape is a false link** (nothing the answer says
contradicts what the records hold). Known residuals (N9, N10 = P1, P4, PN9) are included and
labelled so a known string is not mistaken for a new finding; controls that MUST link prove the
harness is alive.

Sampling (decision #22): every case once per pass in a **fresh shuffle each pass**, for each
seed (the seed fixes the shuffles), so no case is sampled back to back. Each sample is appended
to ``~/anam-measurements/p8/co15_canonical.jsonl`` as it finishes; a restart skips every
(seed, pass) already complete, so the run is resumable. Raw samples stay outside the repository.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import random
import sys
import time
from pathlib import Path

# Nothing here needs a store, but settings-backed config reads open working.db: point every
# runtime directory at a scratch location BEFORE program.* is imported, so the real data/ is never
# opened. (The caller's own ANAM_* variables win.)
_SCRATCH = Path.home() / "anam-measurements" / "p8" / "scratch-co15"
for _key, _sub in (("ANAM_DATA_DIR", "data"), ("ANAM_BACKUP_DIR", "backups"),
                   ("ANAM_ARTIFACT_DIR", "artifacts"), ("ANAM_WORKSPACE_DIR", "workspace")):
    os.environ.setdefault(_key, str(_SCRATCH / _sub))

from program.integrity import correction_eval  # noqa: E402

OUT = Path.home() / "anam-measurements" / "p8"
RAW = OUT / "co15_canonical.jsonl"

#: The N6 shape in several wordings. ``{x}`` is the topic.
WORDINGS = (
    ("W1-notes-searched-not-find-mention", "I have searched my notes and I do not find any mention of {x}."),
    ("W2-notes-looked-nothing-about", "I looked through my notes and there is nothing about {x}."),
    ("W3-records-searched-nothing-about", "I have searched my records and there is nothing about {x}."),
    ("W4-memory-looked-not-find-anything", "I looked through my memory and I do not find anything about {x}."),
    ("W5-notes-searched-found-nothing", "I searched my notes and found nothing about {x}."),
    ("W6-i-have-no-note", "I have no note about {x}."),
    ("W7-there-is-no-note", "There is no note about {x}."),
    ("W8-records-looked-not-find-mention", "I looked through my records and I do not find any mention of {x}."),
)

PERSON_GRINDER = ("Run a cleaning tablet through it on the coarsest setting, then grind about 20 g of "
                  "cheap beans to push the residue out, and bin those grounds.")


def _frozen():
    frozen = {c.id: c for c in correction_eval.load_cases()}
    return frozen


def build_cases() -> tuple[list[correction_eval.Case], dict[str, dict]]:
    """Every case of the run, and its labels (shape, family, topic)."""
    frozen = _frozen()
    n9, n10, pn9 = frozen["N9-records-scope"], frozen["N10-records-scope-short-answer"], \
        frozen["PN9-records-scope-person-supplies"]
    background = n9.candidates[:-1]
    topics = {
        "descaling": ("descaling", [("long", n9.new_message), ("short", n10.new_message)],
                      [("person", pn9.new_message)]),
        "grinder": ("cleaning the grinder",
                    [("grinder", None)],      # filled from the P4 probe below
                    [("person", PERSON_GRINDER)]),
    }
    from scripts.correction_diagnosis_scope import PROBES
    p4 = next(p for p in PROBES if p[0].startswith("P4"))
    topics["grinder"] = ("cleaning the grinder", [("grinder", p4[2])], [("person", PERSON_GRINDER)])

    cases: list[correction_eval.Case] = []
    labels: dict[str, dict] = {}

    def add(case_id, shape, family, claim, answer, *, should_link=False, target=None,
            replacement=None, note=""):
        role, cand_role = ("assistant", None) if shape == "entity" else ("user", "assistant")
        cases.append(correction_eval.Case(
            id=case_id, kind="scope" if shape == "entity" else "person_corrects_entity",
            speaker_role=role, should_link=should_link, new_message=answer, note=note,
            target=target, replacement=replacement, candidate_role=cand_role,
            candidates=background + ({"id": "claim", "role": "assistant", "content": claim},)))
        labels[case_id] = {"shape": shape, "family": family}

    for wid, template in WORDINGS:
        for tname, (x, ent_answers, per_answers) in topics.items():
            claim = template.format(x=x)
            for aname, answer in ent_answers:
                add(f"E|{wid}|{tname}|{aname}", "entity", "canonical", claim, answer)
            for aname, answer in per_answers:
                add(f"P|{wid}|{tname}|{aname}", "person", "canonical", claim, answer)
    # Baselines: known residuals and clean wordings, from the frozen set and the scope probes.
    add("E|BASELINE-N9", "entity", "baseline-known-residual", n9.candidates[-1]["content"], n9.new_message)
    add("E|BASELINE-N10(=P1)", "entity", "baseline-known-residual", n10.candidates[-1]["content"], n10.new_message)
    add("E|BASELINE-P4", "entity", "baseline-known-residual",
        "I have searched my records, and I do not find any mention of cleaning the grinder.", p4[2])
    add("P|BASELINE-PN9", "person", "baseline-known-residual", pn9.candidates[-1]["content"], pn9.new_message)
    for fid in ("N11-records-sourdough", "N12-notes-tyres"):
        f = frozen[fid]
        add(f"E|BASELINE-{fid}", "entity", "baseline-clean", f.candidates[-1]["content"], f.new_message)
    # Controls that MUST link: the harness is alive and the shape can link at all.
    c8 = frozen["C8-records-do-mention-it"]
    add("E|CONTROL-C8-records-do-mention-it", "entity", "control-must-link",
        c8.candidates[-1]["content"], c8.new_message, should_link=True,
        target="claim", replacement="replaced")
    pc1 = ("I have searched my records, and I do not find any mention of descaling.",
           "It is in there. I told you on Tuesday that the kettle needs descaling every month.")
    add("P|CONTROL-PC1-it-is-there", "person", "control-must-link", pc1[0], pc1[1],
        should_link=True, target="claim", replacement="replaced")
    return cases, labels


def _done_passes() -> set[tuple[int, int]]:
    done = set()
    if RAW.exists():
        for line in RAW.read_text().splitlines():
            r = json.loads(line)
            if r.get("marker") == "pass_done":
                done.add((r["seed"], r["pass"]))
    return done


def run_canonical(passes: int, seeds: list[int]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases, labels = build_cases()
    by_id = {c.id: c for c in cases}
    done = _done_passes()
    print(f"{len(cases)} cases; seeds {seeds}; {passes} passes each", file=sys.stderr, flush=True)
    for seed in seeds:
        for p in range(passes):
            if (seed, p) in done:
                continue
            order = list(by_id)
            random.Random(f"co15-{seed}-{p}").shuffle(order)
            t0 = time.time()
            with RAW.open("a") as f:
                for cid in order:
                    o = correction_eval.sample_once(by_id[cid])
                    f.write(json.dumps({"seed": seed, "pass": p, "case": cid, **labels[cid],
                                        "outcome": o.outcome, "linked": o.linked_target,
                                        "state": o.linked_state, "unusable": o.unusable}) + "\n")
                    f.flush()
                f.write(json.dumps({"marker": "pass_done", "seed": seed, "pass": p}) + "\n")
            print(f"seed {seed} pass {p + 1}/{passes} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fmt(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100 * k / n:.0f}% [{100 * lo:.0f}-{100 * hi:.0f}%]" if n else "n/a"


def report() -> str:
    rows = [json.loads(line) for line in RAW.read_text().splitlines()]
    rows = [r for r in rows if "case" in r]
    lines = []
    for shape in ("entity", "person"):
        lines.append(f"\n=== {shape.upper()} SHAPE ===")
        sel = [r for r in rows if r["shape"] == shape]
        for fam in ("canonical", "baseline-known-residual", "baseline-clean", "control-must-link"):
            sub = [r for r in sel if r["family"] == fam]
            if not sub:
                continue
            by = collections.defaultdict(list)
            for r in sub:
                by[r["case"]].append(r)
            if fam == "control-must-link":
                tot = sum(1 for r in sub if r["outcome"] == "ok")
                lines.append(f"-- {fam}: linked correctly {fmt(tot, len(sub))}")
            else:
                tot = sum(1 for r in sub if r["linked"])
                lines.append(f"-- {fam}: FALSE LINKS {fmt(tot, len(sub))}   (seeds: "
                             f"{sorted({r['seed'] for r in sub})})")
            for cid, rs in sorted(by.items()):
                k = sum(1 for r in rs if (r["outcome"] == "ok" if fam == "control-must-link" else r["linked"]))
                unus = sum(1 for r in rs if r["unusable"])
                per_seed = {s: sum(1 for r in rs if r["seed"] == s and (r["linked"] is not None)) for s in sorted({r["seed"] for r in rs})}
                lines.append(f"   {cid:<64} {fmt(k, len(rs))}  per-seed links {per_seed}" + (f"  unusable {unus}" if unus else ""))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("canonical")
    c.add_argument("--passes", type=int, default=20)
    c.add_argument("--seeds", default="1,2")
    sub.add_parser("report")
    sub.add_parser("count")
    a = ap.parse_args(argv)
    if a.cmd == "canonical":
        run_canonical(a.passes, [int(s) for s in a.seeds.split(",")])
    elif a.cmd == "count":
        cases, _ = build_cases()
        print(len(cases))
    else:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
