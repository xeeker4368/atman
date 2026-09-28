#!/usr/bin/env python3
"""B11 part 2: is the entity's stale claim superseded when a person corrects it?
Tier 1, diagnosis only.

    python -m scripts.correction_diagnosis_acknowledgement [--runs N]

**No production changes.** `corrections.py` is called, never edited; nothing is
written anywhere. Cells are sampled round-robin (`AGENTS.md`, decision #22).

Why this is the question
========================
Links are same-speaker only (CO4, as resolved): a person's message may supersede only
that person's own statements, and the entity's may supersede only its own. So when a
person corrects something, the entity's stale claim — its echo of the old value, or
its own wrong answer — is superseded only if the entity's **reply in that turn** is
judged to self-correct it. Every soak correction (4/4) was linked on the entity side,
but only because the entity happened to reply "Corrected. I have updated…" each time.

Measured 2026-09-27, 5 runs per cell, one candidate each:

    reply restates the value   ("Corrected. … Wednesday at 3:00.")   linked 5/5 replaced
    reply apologises           ("Sorry about that - I had it wrong.") linked 5/5 contradicted
    reply acknowledges only    ("Got it, thanks for telling me.")      0/5
    reply moves on             ("Is there anything you need to bring…") 0/5

The same for the entity's echo of a person's value and for the entity's own wrong
claim. In the last two rows nothing supersedes the stale claim, and it resurfaces in
retrieval unannotated. This measurement is what B11 part 2's decision — a judged
person-to-entity link, option (b) — was taken against.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from program.integrity import corrections

_C = corrections.Candidate

#: The two stale entity claims a person's correction can leave behind.
CLAIMS = {
    "echo of the person's value": _C(
        "E1", "assistant", "Noted. Dentist appointment on Tuesday at 3:00.",
        "2026-09-22T02:53:23"),
    "entity's own claim": _C(
        "E2", "assistant", "Your dentist appointment is on Tuesday at 3.",
        "2026-09-22T02:53:23"),
}

#: How the entity might reply after "Actually the dentist is Wednesday, not Tuesday."
REPLIES = {
    "restates value": "Corrected. The dentist appointment is on Wednesday at 3:00.",
    "acknowledges only": "Got it, thanks for telling me.",
    "apologises": "Sorry about that - I had it wrong.",
    "moves on": "Is there anything you need to bring to it?",
}


def run(runs: int) -> dict[tuple[str, str], dict[str, int]]:
    tally: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _ in range(runs):
        for claim_name, claim in CLAIMS.items():
            for reply_name, reply in REPLIES.items():
                verdict = corrections.classify(reply, "NEW", [claim], "assistant",
                                               "the system")
                key = "no link" if verdict is None else f"linked/{verdict.replacement}"
                tally[(claim_name, reply_name)][key] += 1
    return {k: dict(v) for k, v in tally.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    for (claim, reply), counts in run(args.runs).items():
        print(f"{claim:28s} {reply:18s} {counts}")


if __name__ == "__main__":
    main()
