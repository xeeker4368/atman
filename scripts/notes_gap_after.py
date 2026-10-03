#!/usr/bin/env python3
"""CO17 follow-up: the no-search gap, measured with ``note_search``'s search-first line applied (2026-10-03;
the line was reverted after review, since no notes-topic claim was made without a search).

    python -m scripts.notes_gap_after turn1 [--passes 2]   # plain questions, empty scratch store
    python -m scripts.notes_gap_after turn2                # the person supplies the fact, for the
                                                           # claims listed in labels.json
    python -m scripts.notes_gap_after co15 [--passes 20] [--seeds 1,2]
    python -m scripts.notes_gap_after report

**Measures only.** ``turn1`` asks ``scripts.notes_gap``'s 20 questions (the baseline's own set,
nothing telling the entity to search) through the real loop on a fresh scratch store (default
registry, ``notes.enabled`` on in this process only, no note and no stored conversation); pass 2
asks each again with the speaker swapped, in a new conversation. Every reply is classified **by
reading it**, into ``labels.json`` beside the raw file: ``{"<pass>|<q>": "claim" | "no-claim"}``.

``turn2`` continues each labelled claim that had **no ``note_search``** in its trace: the person
supplies the fact, and the entity's reply is its own later message about X. ``co15`` puts each such
claim (verbatim) through ``scripts.notes_co15_real``'s regime: the frozen harness's background pool
(N9's candidates, production order), the claim as the newest entity candidate, the entity's live
turn-2 reply as the new message, entity shape, 2 seeds x 20 passes, a fresh shuffle each pass. Any
link is a false link. The production ``supersedes`` rows written during ``turn2`` are read too.

Raw samples: ``~/anam-measurements/p9/``. The real ``data/`` is never opened (every runtime
directory points at the scratch store before ``program`` is imported).
"""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import collections
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import notes_live as L  # noqa: E402

L.ROOT = Path.home() / "anam-measurements" / "p9"
STORE = "scratch-gap-after"
RAW1 = L.ROOT / "gap_after_turn1.jsonl"
LABELS = L.ROOT / "gap_after_labels.json"
RAW2 = L.ROOT / "gap_after_turn2.jsonl"
RAWC = L.ROOT / "gap_after_co15.jsonl"

#: What the person says in turn 2, by question: the fact itself, invented, household-shaped.
FACTS = {
    "What time is the Saturday market this week?": "It starts at nine this Saturday.",
    "When is Jodie's dentist appointment?": "It's on Thursday at half past three.",
    "What did we decide about the backup schedule?": "We decided the backup runs every night at two.",
    "How does Jodie take her coffee?": "She takes it with oat milk and no sugar.",
    "What's the plan for the boiler service?": "The engineer comes on the 14th to service the boiler.",
    "Where did we leave off with the retrieval floors?": "We agreed to leave them permissive until there is real material to calibrate on.",
    "What's our grinder setting for espresso?": "It's on 12, medium-fine.",
    "Do we have a plan for repotting the fig?": "We're repotting it in spring, one pot size up.",
    "What was the MOT date again?": "The MOT is due on the 19th of October.",
    "Which stall at the market is the good one?": "The good one is the bakery stall by the fountain.",
    "What did Lyle say about the project review meeting?": "He said it moves to Fridays at ten.",
    "How often do we descale the kettle?": "We descale it every two weeks because of the hard water.",
    "What doesn't Jodie like to eat?": "She doesn't like coriander.",
    "Where does the dishwasher filter go?": "It sits under the bottom rack, twisted to lock.",
    "Remind me what we said about the tomatoes.": "We said they need feeding every fortnight.",
    "Have we decided who is cooking on Sunday?": "Lyle is cooking on Sunday.",
    "Where did we put the old router?": "It's in the garage on the top shelf.",
    "What are we doing about the garden shed?": "We're replacing the roof felt next month.",
    "Who is picking up the groceries on Friday?": "Jodie is picking them up on Friday.",
    "What did we say about the bike tyre pressure?": "We said 80 psi for the road bike.",
}


def _setup():
    return L.setup(STORE, fresh=False)


def turn1(passes: int) -> None:
    from scripts.notes_gap import QUESTIONS
    w = _setup()
    done = {(r["pass"], r["q"]) for r in map(json.loads, RAW1.read_text().splitlines())} if RAW1.exists() else set()
    people = [("Lyle", w["lyle"], True), ("Jodie", w["jodie"], False)]
    for p in range(passes):
        for i, q in enumerate(QUESTIONS):
            if (p, q) in done:
                continue
            name, uid, adm = people[(i + p) % 2]
            t0 = time.time()
            o = L.run_turn(L.actor(uid, name, adm), q)
            tools = [e["tool"] for e in o.trace if e.get("tool")]
            L.append(RAW1, {"pass": p, "q": q, "speaker": name, "reply": o.content,
                            "conversation_id": o.conversation_id, "tools": tools,
                            "note_search": "note_search" in tools,
                            "trace": L.trace_summary(o.trace), "seconds": round(time.time() - t0)})
            print(f"p{p} {i + 1}: tools={tools} {round(time.time() - t0)}s", file=sys.stderr, flush=True)


def _rows1():
    return [json.loads(x) for x in RAW1.read_text().splitlines()]


def _no_search_claims():
    labels = json.loads(LABELS.read_text())
    return [r for r in _rows1() if labels[f"{r['pass']}|{r['q']}"] == "claim" and not r["note_search"]]


def turn2() -> None:
    w = _setup()
    from program.memory import db
    done = {(r["pass"], r["q"]) for r in map(json.loads, RAW2.read_text().splitlines())} if RAW2.exists() else set()
    ids = {"Lyle": (w["lyle"], True), "Jodie": (w["jodie"], False)}
    for r in _no_search_claims():
        if (r["pass"], r["q"]) in done:
            continue
        uid, adm = ids[r["speaker"]]
        t0 = time.time()
        o = L.run_turn(L.actor(uid, r["speaker"], adm), FACTS[r["q"]], r["conversation_id"])
        with db.connection() as conn:
            links = [dict(x) for x in conn.execute(
                "SELECT s.superseded_message_id, s.superseding_message_id, s.replacement "
                "FROM supersedes s JOIN messages m ON m.id = s.superseded_message_id "
                "WHERE m.conversation_id = ?", (r["conversation_id"],))]
        L.append(RAW2, {"pass": r["pass"], "q": r["q"], "speaker": r["speaker"], "claim": r["reply"],
                        "fact": FACTS[r["q"]], "turn2": o.content, "turn2_tools": [e["tool"] for e in o.trace if e.get("tool")],
                        "production_links": links, "seconds": round(time.time() - t0)})
        print(f"turn2 {r['q'][:40]}: links={len(links)} {round(time.time() - t0)}s", file=sys.stderr, flush=True)


def _cases():
    from program.integrity import correction_eval
    frozen = {c.id: c for c in correction_eval.load_cases()}
    background = frozen["N9-records-scope"].candidates[:-1]
    cases, meta = [], {}
    for n, t in enumerate(map(json.loads, RAW2.read_text().splitlines()), start=1):
        cid = f"E|gap{n:02d}|live-turn2"
        cases.append(correction_eval.Case(
            id=cid, kind="scope", speaker_role="assistant", should_link=False, new_message=t["turn2"],
            note="no-search no-note claim (verbatim) as the claim", candidate_role=None,
            candidates=background + ({"id": "claim", "role": "assistant", "content": t["claim"]},)))
        meta[cid] = {"n": n, "q": t["q"], "pass_turn1": t["pass"]}
    return cases, meta


def co15(passes: int, seeds: list[int]) -> None:
    from program.integrity import correction_eval
    _setup()
    cases, meta = _cases()
    by_id = {c.id: c for c in cases}
    done = set()
    if RAWC.exists():
        done = {(x["seed"], x["pass"]) for x in map(json.loads, RAWC.read_text().splitlines()) if x.get("marker")}
    for seed in seeds:
        for p in range(passes):
            if (seed, p) in done:
                continue
            order = list(by_id)
            random.Random(f"gap-after-{seed}-{p}").shuffle(order)
            t0 = time.time()
            for cid in order:
                o = correction_eval.sample_once(by_id[cid])
                L.append(RAWC, {"seed": seed, "pass": p, "case": cid, **meta[cid], "outcome": o.outcome,
                                "linked": o.linked_target, "unusable": o.unusable})
            L.append(RAWC, {"marker": "pass_done", "seed": seed, "pass": p})
            print(f"seed {seed} pass {p + 1}/{passes} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)


def report() -> str:
    out = []
    rows = _rows1()
    labels = json.loads(LABELS.read_text()) if LABELS.exists() else {}
    for p in sorted({r["pass"] for r in rows}):
        sel = [r for r in rows if r["pass"] == p and labels.get(f"{p}|{r['q']}") == "claim"]
        out.append(f"pass {p}: {len(sel)} claims; without note_search {sum(1 for r in sel if not r['note_search'])}; "
                   f"no search at all {sum(1 for r in sel if not set(r['tools']) & {'note_search', 'memory_search'})}")
    if RAWC.exists():
        rc = [r for r in map(json.loads, RAWC.read_text().splitlines()) if "case" in r]
        by = collections.defaultdict(list)
        for r in rc:
            by[r["case"]].append(r)
        out.append(f"co15: {sum(1 for r in rc if r['linked'])}/{len(rc)} linked; unavailable {sum(1 for r in rc if r['outcome'] == 'unavailable')}")
        for cid, v in sorted(by.items()):
            out.append(f"  {cid}: {sum(1 for r in v if r['linked'])}/{len(v)}  {v[0]['q']}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("turn1").add_argument("--passes", type=int, default=2)
    sub.add_parser("turn2")
    c = sub.add_parser("co15")
    c.add_argument("--passes", type=int, default=20)
    c.add_argument("--seeds", default="1,2")
    sub.add_parser("report")
    a = ap.parse_args(argv)
    if a.cmd == "turn1":
        turn1(a.passes)
    elif a.cmd == "turn2":
        turn2()
    elif a.cmd == "co15":
        co15(a.passes, [int(s) for s in a.seeds.split(",")])
    else:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
