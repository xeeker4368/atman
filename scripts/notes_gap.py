#!/usr/bin/env python3
"""CO17: how large is the gap the exclusion leaves for Notes? (`docs/CORRECTION_DESIGN.md` CO17)

    python -m scripts.notes_gap run
    python -m scripts.notes_gap report

About 20 live single-turn conversations on a scratch store (real loop, default registry, Notes on in
this process only, **no note and no stored conversation exists**) in which a person asks about a
household fact **and nothing tells the entity to search** ("What time is the Saturday market this
week?", not "check your notes"). For each reply it records whether the entity says there is no
note, record or information about it, and **whether the turn's own trace shows a ``note_search`` or
``memory_search`` call**. A "no note" claim with no search in the trace is the case the exclusion
cannot see. The classification is made by reading every reply; the keyword pass is a first sort.
Raw: ``~/anam-measurements/p8/gap.jsonl``.
"""

from __future__ import annotations

# ruff: noqa: E501
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import notes_live as L  # noqa: E402

RAW = L.ROOT / "gap.jsonl"
QUESTIONS = (
    "What time is the Saturday market this week?",
    "When is Jodie's dentist appointment?",
    "What did we decide about the backup schedule?",
    "How does Jodie take her coffee?",
    "What's the plan for the boiler service?",
    "Where did we leave off with the retrieval floors?",
    "What's our grinder setting for espresso?",
    "Do we have a plan for repotting the fig?",
    "What was the MOT date again?",
    "Which stall at the market is the good one?",
    "What did Lyle say about the project review meeting?",
    "How often do we descale the kettle?",
    "What doesn't Jodie like to eat?",
    "Where does the dishwasher filter go?",
    "Remind me what we said about the tomatoes.",
    "Have we decided who is cooking on Sunday?",
    "Where did we put the old router?",
    "What are we doing about the garden shed?",
    "Who is picking up the groceries on Friday?",
    "What did we say about the bike tyre pressure?",
)
NO_CLAIM = re.compile(r"(don't|do not|doesn't|cannot|can't|couldn't|no)\b.{0,40}\b(note|record|information|mention|anything|details|idea|access)|nothing (about|in|recorded|has)|haven't (been|discussed|mentioned)|hasn't come up|not (something|been) (we|I)|no (specific|prior)", re.I)


def run():
    w = L.setup("scratch-gap", fresh=True)
    done = {json.loads(x)["q"] for x in RAW.read_text().splitlines()} if RAW.exists() else set()
    users = [("Lyle", w["lyle"], True), ("Jodie", w["jodie"], False)]
    for i, q in enumerate(QUESTIONS):
        if q in done:
            continue
        name, uid, adm = users[i % 2]
        t0 = time.time()
        o = L.run_turn(L.actor(uid, name, adm), q)
        tools = [e for e in o.trace if e.get("tool")]
        searched = [e["tool"] for e in tools if e["tool"] in ("note_search", "memory_search")]
        L.append(RAW, {"q": q, "speaker": name, "reply": o.content, "trace": L.trace_summary(o.trace),
                       "searched": searched, "claims_none_keyword": bool(NO_CLAIM.search(o.content)),
                       "seconds": round(time.time() - t0)})
        print(f"{i + 1}: searched={searched} claim?={bool(NO_CLAIM.search(o.content))} {round(time.time() - t0)}s",
              file=sys.stderr, flush=True)


def report() -> str:
    rows = [json.loads(x) for x in RAW.read_text().splitlines()]
    lines = [f"{len(rows)} turns; median {sorted(r['seconds'] for r in rows)[len(rows) // 2]}s"]
    for i, r in enumerate(rows, 1):
        lines.append(f"#{i} {r['q']}\n     searched={r['searched']}  keyword-claim={r['claims_none_keyword']}\n     reply: {r['reply'][:400]!r}")
    return "\n".join(lines)


if __name__ == "__main__":
    run() if len(sys.argv) > 1 and sys.argv[1] == "run" else print(report())
