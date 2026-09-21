#!/usr/bin/env python3
"""Run the correction classifier's frozen eval cases against the live model.

    python -m scripts.correction_eval [--runs N] [--case ID ...] [--json PATH]

Exit status is 0 whenever the harness ran, whatever it measured: the output is a
report for review, not a pass bar. It is 2 for a malformed case file or an
unknown `--case` id.

Samples are round-robin across the set (decision #22). A single-case run cannot
be decorrelated and says so in its own output rather than quietly reporting a
correlated rate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from program.integrity import correction_eval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Correction classifier eval harness")
    parser.add_argument("--runs", type=int, default=5,
                        help="passes over the whole set (default 5)")
    parser.add_argument("--case", action="append", default=[], metavar="ID")
    parser.add_argument("--json", type=Path, default=None, metavar="PATH")
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be at least 1")

    try:
        cases = correction_eval.load_cases()
    except correction_eval.CaseFileError as exc:
        print(f"case file error: {exc}", file=sys.stderr)
        return 2

    full = correction_eval.fingerprint(cases)
    if args.case:
        unknown = [cid for cid in args.case if cid not in {c.id for c in cases}]
        if unknown:
            print(f"unknown case id(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c.id in set(args.case)]

    report = correction_eval.run(cases, args.runs)
    report.header["cases_fingerprint"] = full
    if args.case:
        report.header["filtered_to"] = list(args.case)
    print(correction_eval.render(report))

    if args.json is not None:
        args.json.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
        print(f"\nfull report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
