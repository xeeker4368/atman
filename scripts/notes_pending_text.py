#!/usr/bin/env python3
"""Revise/retire result text: two drafts, measured in memory only (2026-10-03, CO17 follow-up).

    python -m scripts.notes_pending_text run A|B [--seeds 1,2] [--passes 3]
    python -m scripts.notes_pending_text report

**Measures only; no committed text changes.** ``note_texts.PENDING_CHANGE`` (the result of a revise
or retire with approval required) is replaced **in this process only** by draft A or B, then
``scripts.notes_pending``'s six revise and retire requests (R01-R03, T01-T03, on its seeded notes)
run through the real loop on a scratch store per draft: 6 requests x 2 seeds x 3 passes = 36 turns
per draft, a fresh shuffle each pass. Every reply is read by hand; the keyword sort is a first pass.
The baseline is the 36 revise and retire turns of ``pending_main_after.jsonl`` (the CO17 re-run).
Raw: ``~/anam-measurements/p9/pending_text_<draft>.jsonl``.
"""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import notes_live as L  # noqa: E402

L.ROOT = Path.home() / "anam-measurements" / "p9"

DRAFTS = {
    "A": "Proposed. The note is still active and unchanged until a person approves this. Say it is proposed, not done.",
    "B": "Proposed, not done. The note is unchanged until a person approves it.",
}
IDS = ("R01", "R02", "R03", "T01", "T02", "T03")


def run(draft: str, seeds: list[int], passes: int) -> None:
    from scripts import notes_pending as P
    raw = L.ROOT / f"pending_text_{draft}.jsonl"

    def order():
        for seed in seeds:
            for p in range(passes):
                o = list(IDS)
                random.Random(f"pending-text-{seed}-{p}").shuffle(o)
                for rid in o:
                    yield seed, p, rid

    # run_arm calls setup() first, which reloads config; the text is patched after the import
    # and is read by note_propose at call time (``texts.PENDING_CHANGE``), so it takes effect.
    from program.tools import note_texts
    note_texts.PENDING_CHANGE = DRAFTS[draft]
    P.run_arm(f"scratch-pending-text-{draft}", raw, order, True)
    assert note_texts.PENDING_CHANGE == DRAFTS[draft]


def report() -> str:
    out = []
    base = [r for r in map(json.loads, (Path.home() / "anam-measurements/p8/pending_main_after.jsonl").read_text().splitlines())
            if r["id"] in IDS]
    for name, rows in [("baseline", base)] + [
            (d, list(map(json.loads, (L.ROOT / f"pending_text_{d}.jsonl").read_text().splitlines())))
            for d in DRAFTS if (L.ROOT / f"pending_text_{d}.jsonl").exists()]:
        out.append(f"{name}: {len(rows)} turns, proposals_ok>0 {sum(1 for r in rows if r['proposals_ok'])}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("draft", choices=sorted(DRAFTS))
    r.add_argument("--seeds", default="1,2")
    r.add_argument("--passes", type=int, default=3)
    sub.add_parser("report")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.draft, [int(s) for s in a.seeds.split(",")], a.passes)
    else:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
