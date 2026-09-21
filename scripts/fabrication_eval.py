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
import sys
from pathlib import Path

from program.integrity import gate_eval


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

    report = gate_eval.run(cases, args.runs, cases_fingerprint=full_fingerprint)
    if args.case:
        report.header["filtered_to"] = list(args.case)
    print(gate_eval.render(report))

    if args.json is not None:
        args.json.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
        print(f"\nfull report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
