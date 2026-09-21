#!/usr/bin/env python3
"""O16: what does the one-call design cost the identity class? Diagnosis only.

    python -m scripts.gate_diagnosis_o16 [--runs N]

**No production changes.** `gate.py`, `classifier.py`, `architecture.md` and
`cases.toml` are read, never written. The prompt variant and the label routing
are monkeypatched **inside this process** and exist nowhere on disk.

The question
============
Revision 7's one-call design restores the classifier's tool remit and adds a
`CONTRADICTS-TOOL` label so tool objections can be routed to the advisory channel
instead of the verdict. That touches the identity prompt, and this project's own
history — E3-E5, N7/N8 — says a prompt change is measured, not assumed to be
harmless.

**If the identity false-positive rate holds at or near 8%, one call is cheap and
preferred. If it moves, that number is the real cost of one call**, to be weighed
against two calls' +2 s per turn.

Why the routing is simulated too
================================
Restoring the tool remit *without* routing would measure the hazard case revision
7 exists to avoid: `classifier.parse()` accepts any `CONTRADICTS*` head, so
tool-labelled objections would land in the authoritative findings list and flag.
That is not the design. So this patches both halves — the prompt and the routing
— and counts what the advisory channel would have received.
"""

from __future__ import annotations

import argparse
import sys

from program.integrity import classifier, gate, gate_eval

#: Removed: O7's exclusion. Asserted present so a reworded prompt fails loudly.
EXCLUSION = (
    "- anything the statement says about what a tool did, returned, or failed to do.\n"
    "  Tool claims are checked separately by exact rules and are not yours to judge.\n"
)

OLD_TAIL = """Reply in exactly this form and nothing else:
CONSISTENT
or
CONTRADICTS
- <the phrase at fault> | <which fact it contradicts>
"""

NEW_TAIL = """Reply in exactly this form and nothing else:
CONSISTENT
or
CONTRADICTS-SELF
- <the phrase at fault> | <which fact it contradicts>
or
CONTRADICTS-TOOL
- <the phrase at fault> | <which fact it contradicts>

Use CONTRADICTS-TOOL when the fault is about what a tool did, returned, or failed
to do. Use CONTRADICTS-SELF for everything else.
"""

#: What the advisory channel would have received, by case.
ADVISORY: dict[str, int] = {}
CURRENT_CASE = {"id": "?"}


def variant_prompt() -> str:
    if EXCLUSION not in gate._PROMPT or OLD_TAIL not in gate._PROMPT:
        raise SystemExit("gate._PROMPT is not the text this variant patches")
    return gate._PROMPT.replace(EXCLUSION, "").replace(OLD_TAIL, NEW_TAIL)


def routed_parse(reply: str, pairs: list[tuple[str, str]]) -> list:
    """Route by the classifier's own label, as revision 7 F32 specifies."""
    verdict = classifier.parse(reply)          # raises on unusable — unchanged
    if not verdict.contradicts:
        return []
    head = verdict.raw.split()[0].upper().strip(".,:")
    if head.startswith("CONTRADICTS-TOOL"):
        ADVISORY[CURRENT_CASE["id"]] = ADVISORY.get(CURRENT_CASE["id"], 0) + 1
        return []                               # advisory only; never the verdict
    return gate._original_parse(reply, pairs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="O16 measurement")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args(argv)

    gate._PROMPT, original_prompt = variant_prompt(), gate._PROMPT
    gate._original_parse = gate._parse
    gate._parse = routed_parse

    cases = gate_eval.load_cases()
    original_run_case = gate_eval.run_case

    def run_case(case, ground_truth, runs):
        CURRENT_CASE["id"] = case.id
        return original_run_case(case, ground_truth, runs)

    gate_eval.run_case = run_case

    print("O16 — ONE-CALL DESIGN, MEASURED AGAINST THE FROZEN SET")
    print("=" * 76)
    print("prompt: tool remit restored + CONTRADICTS-TOOL label (in-process only)")
    print(f"cases: {len(cases)}   runs: {args.runs}\n")

    report = gate_eval.run(cases, args.runs)
    report.header["prompt"] = "O16 variant (not shipped)"
    print(gate_eval.render(report))

    print("\nWHAT THE ADVISORY CHANNEL WOULD HAVE RECEIVED")
    print("-" * 76)
    if not ADVISORY:
        print("  nothing: the classifier never used the CONTRADICTS-TOOL label")
    for case_id, count in sorted(ADVISORY.items()):
        case = next(c for c in cases if c.id == case_id)
        rules = "rules also caught" if gate.structural_findings(
            case.answer, list(case.trace)) else "RULES MISSED THIS"
        print(f"  {case_id:<36} {count}/{args.runs} runs   {rules}")

    gate._PROMPT = original_prompt
    return 0


if __name__ == "__main__":
    sys.exit(main())
