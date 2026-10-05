#!/usr/bin/env python3
"""Run the fabrication gate's frozen eval cases against the live classifier.

    python -m scripts.fabrication_eval [--runs N] [--case ID ...] [--json PATH]

The classifier is the configured chat model. To measure a different one without
touching config or the settings table, set ``ANAM_CHAT_MODEL`` for the run (it
only takes effect while the settings table holds no ``models.chat`` row — the
report header prints the model actually resolved).

Exit status is 0 whenever the harness ran, whatever it measured: the output is a
report for review, not a pass bar. It is 2 when the case file is malformed or a
``--case`` id does not exist.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from program.integrity import gate, gate_eval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fabrication gate eval harness")
    parser.add_argument(
        "--runs", type=int, default=5,
        help="gate.check() calls per case; the classifier samples, so one is not enough "
             "(default 5)",
    )
    parser.add_argument(
        "--case", action="append", default=[], metavar="ID",
        help="run only this case id (repeatable)",
    )
    parser.add_argument("--json", type=Path, default=None, metavar="PATH",
                        help="also write the full report, every run included, as JSON")
    parser.add_argument(
        "--shuffle", type=int, default=None, metavar="SEED",
        help="shuffle the sampling order with this seed. The harness is decorrelated "
             "(round-robin) but iterates the case list in file order, so a case's "
             "neighbours never change; this varies them. The fingerprint is computed "
             "from FILE order, so shuffling does not move the freeze",
    )
    parser.add_argument(
        "--rubric", type=Path, default=None, metavar="PATH",
        help="judge identity claims against this rubric instead of the shipped "
             "program/integrity/architecture.md. The control arm: the previous rubric, "
             "in the same session, rather than swapping the file on disk mid-measurement",
    )
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be at least 1")

    try:
        cases = gate_eval.load_cases()
    except gate_eval.CaseFileError as exc:
        print(f"case file error: {exc}", file=sys.stderr)
        return 2

    # Fingerprint the full frozen set even when filtering, so the header always
    # identifies which case set a partial run was drawn from.
    full_fingerprint = gate_eval.fingerprint(cases)
    if args.case:
        known = {c.id for c in cases}
        unknown = [cid for cid in args.case if cid not in known]
        if unknown:
            print(f"unknown case id(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c.id in set(args.case)]

    ground_truth = None
    label = None
    if args.rubric is not None:
        try:
            ground_truth = gate.load_architecture(args.rubric)
        except gate.GroundTruthError as exc:
            print(f"rubric error: {exc}", file=sys.stderr)
            return 2
        label = str(args.rubric)

    if args.shuffle is not None:
        # The fingerprint above was taken in file order, deliberately: `fingerprint`
        # serialises the list, so hashing a shuffled one would move the freeze.
        cases = list(cases)
        random.Random(args.shuffle).shuffle(cases)

    report = gate_eval.run(cases, args.runs, ground_truth=ground_truth,
                           cases_fingerprint=full_fingerprint,
                           shuffle_seed=args.shuffle, ground_truth_label=label)
    if args.case:
        report.header["filtered_to"] = list(args.case)
    print(gate_eval.render(report))

    if args.json is not None:
        args.json.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
        print(f"\nfull report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
