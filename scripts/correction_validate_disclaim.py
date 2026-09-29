#!/usr/bin/env python3
"""B11 stage 3: validate the scope-disclaimer clause against the WHOLE frozen set.

    python -m scripts.correction_validate_disclaim --arm disclaim --seed 1 [--runs 20]
                                                   [--json PATH]

Tier 1 diagnosis; no production change (the clause is patched into
``corrections._PROMPT`` in-process for the arm). Written after the PN9 width probe
(``correction_diagnosis_pn9``) showed two things it could not resolve alone:

* its arms ran the same case back to back, so its per-arm rates are of unknown
  regime; and
* PN9 read 0/20 in the 44-case harness and 20/20 sampled elsewhere under the identical
  prompt and pool, so a single clean run says nothing about it.

So: the 44 frozen cases PLUS the probe family (PV1-PV7, PC1-PC3), **one arm per run**
(arms never adjacent), and **a fresh seeded shuffle of the case order every pass**, so
a case's neighbours differ from pass to pass and from seed to seed. Run it with two
seeds at least before reading any PN9 figure. The frozen harness itself is unchanged.

Measured 2026-09-28/29, 20 passes each, 54 cases (CORRECTION_DESIGN CO16)
========================================================================
                       shipped (seed 3)   disclaim (seed 1)   disclaim (seed 2)
    PN9                   14/20 linked        0/20                0/20
    PV7 (exact string)    13/20               0/20                0/20
    PV5 (boiler)          20/20               0/20                0/20
    PC2 (asks to look)     0/20              20/20               20/20
    N9                    20/20              20/20               20/20
    every other case      correct            correct             correct

The clause closes the PN9 family and breaks PC2, so it is rejected. PN9 is an open,
unstable defect, not a residual.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from program.integrity import correction_eval
from scripts.correction_diagnosis_pn9 import arm
from scripts.correction_diagnosis_pn9 import cases as probe_cases


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["shipped", "disclaim"])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    cs = correction_eval.load_cases() + probe_cases()
    res = {c.id: correction_eval.CaseResult(c) for c in cs}
    rng = random.Random(args.seed)
    prev = None
    with arm(args.arm):
        for p in range(args.runs):
            order = cs[:]
            rng.shuffle(order)
            if order[0] is prev:  # never the same case back to back across passes
                order.append(order.pop(0))
            prev = order[-1]
            for c in order:
                res[c.id].runs.append(correction_eval.sample_once(c))
            print(f"pass {p + 1}/{args.runs}", file=sys.stderr, flush=True)

    print(f"arm={args.arm} seed={args.seed} runs={args.runs} cases={len(cs)}")
    bad = 0
    for c in cs:
        r = res[c.id]
        links = sum(o.linked_target is not None for o in r.runs)
        ok = r.correct
        flag = "" if ok == len(r.scored) else "   <-- NOT UNANIMOUS/FAIL"
        bad += bool(flag)
        print(f"{c.id:<42} {'link' if c.should_link else 'nolink':<7} "
              f"linked {links:>2}/{len(r.runs)}  correct {ok:>2}/{len(r.scored)}{flag}")
    print(f"cases not fully correct: {bad}")
    if args.json:
        args.json.write_text(json.dumps({c.id: {
            "should_link": c.should_link,
            "linked": [o.linked_target for o in res[c.id].runs],
            "outcomes": [o.outcome if hasattr(o, "outcome") else str(o)
                         for o in res[c.id].runs]} for c in cs}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
