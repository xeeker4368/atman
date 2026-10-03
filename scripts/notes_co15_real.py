#!/usr/bin/env python3
"""Notes ship gate 1, real replies: CO15 on what the entity actually says (`docs/NOTES_DESIGN.md` N9.1).

    python -m scripts.notes_co15_real capture      # live two-turn conversations, scratch store
    python -m scripts.notes_co15_real run [--passes 20] [--seeds 1,2]
    python -m scripts.notes_co15_real report

**Measures only; no prompt, wording or case file changes.** ``capture`` runs real turns through
the real loop (default registry, ``notes.enabled`` on in this process only, a scratch store):
turn 1 asks about X in a way that leads the entity to call ``note_search`` and get the empty
result (varied questions; a turn whose trace shows no empty ``note_search`` is recorded and not
used); turn 2 is the follow-up about X. Each qualifying **turn-1 reply, verbatim, is the claim**.
``run`` puts each claim through ``scripts.notes_co15``'s pools (the frozen harness's pool shape and
production order, a fresh shuffle each pass, per-seed) with the answers about X from the existing
harness plus the entity's own live turn-2 answer. A link on any is a false link.
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

RAW_TURNS = L.ROOT / "co15_real_turns.jsonl"
RAW_RUN = L.ROOT / "co15_real_run.jsonl"

QUESTIONS = (
    ("descaling", "Do you have any notes about how I descale the kettle?",
     "OK. So how do I descale the kettle, then?"),
    ("descaling", "Look in your notes: what do I do to descale the kettle?",
     "Alright, how should I go about descaling it?"),
    ("descaling", "Is there a note on descaling the kettle?",
     "No worries. What's the best way to descale it?"),
    ("descaling", "Have we got anything written down about descaling the kettle?",
     "Fine. Then how do I descale it?"),
    ("descaling", "What do my notes say about descaling?",
     "OK, tell me how to descale a kettle then."),
    ("descaling", "Can you check whether you've noted how to descale our kettle?",
     "Right. How do I do it?"),
    ("grinder", "Do you have any notes about cleaning the grinder?",
     "OK. So how do I clean the grinder, then?"),
    ("grinder", "Check your notes: is there anything on cleaning the coffee grinder?",
     "Alright, how should I clean it?"),
    ("grinder", "Is there a note on how to clean the grinder?",
     "No worries. What's the best way to clean it?"),
    ("grinder", "Have we got anything written down about cleaning the grinder?",
     "Fine. Then how do I clean the grinder?"),
    ("grinder", "What do my notes say about grinder cleaning?",
     "OK, tell me how to clean a grinder then."),
    ("grinder", "Can you look in your notes for how we clean the grinder?",
     "Right. How do I do it?"),
    # spares, used only if an earlier question did not lead to an empty note_search
    ("descaling", "Any notes on descaling? I can't remember what we decided.",
     "OK, how do I descale it?"),
    ("grinder", "Do you have a note about the grinder cleaning routine?",
     "OK, how do I clean it then?"),
)


def capture(target: int = 12) -> None:
    from program.tools import note_texts
    w = L.setup("scratch-real-co15", fresh=False)
    done = {json.loads(x)["q"] for x in RAW_TURNS.read_text().splitlines()} if RAW_TURNS.exists() else set()
    qualifying = sum(1 for x in (RAW_TURNS.read_text().splitlines() if RAW_TURNS.exists() else [])
                     if json.loads(x)["qualifies"])
    users = [("Lyle", w["lyle"], True), ("Jodie", w["jodie"], False)]
    for i, (topic, q1, q2) in enumerate(QUESTIONS):
        if qualifying >= target:
            break
        if q1 in done:
            continue
        name, uid, adm = users[i % 2]
        act = L.actor(uid, name, adm)
        t0 = time.time()
        o1 = L.run_turn(act, q1)
        searches = [e for e in o1.trace if e.get("tool") == "note_search"]
        empty = any(e.get("outcome") == "ok" and e.get("value") == note_texts.NO_MATCH for e in searches)
        rec = {"q": q1, "topic": topic, "speaker": name, "turn1": o1.content,
               "turn1_trace": L.trace_summary(o1.trace), "qualifies": empty}
        if empty:
            o2 = L.run_turn(act, q2, o1.conversation_id)
            rec.update({"q2": q2, "turn2": o2.content, "turn2_trace": L.trace_summary(o2.trace)})
            qualifying += 1
        rec["seconds"] = round(time.time() - t0)
        L.append(RAW_TURNS, rec)
        print(f"{i + 1}: qualifies={empty} {rec['seconds']}s", file=sys.stderr, flush=True)


def load_turns() -> list[dict]:
    return [r for r in map(json.loads, RAW_TURNS.read_text().splitlines()) if r["qualifies"]]


def build_cases():
    from program.integrity import correction_eval
    from scripts import notes_co15 as C
    base, labels = C.build_cases()          # only for the shared background and answers
    frozen = {c.id: c for c in correction_eval.load_cases()}
    n9, n10, pn9 = frozen["N9-records-scope"], frozen["N10-records-scope-short-answer"], \
        frozen["PN9-records-scope-person-supplies"]
    from scripts.correction_diagnosis_scope import PROBES
    p4 = next(p for p in PROBES if p[0].startswith("P4"))[2]
    ent_answers = {"descaling": [("long", n9.new_message), ("short", n10.new_message)],
                   "grinder": [("grinder", p4)]}
    per_answers = {"descaling": [("person", pn9.new_message)], "grinder": [("person", C.PERSON_GRINDER)]}
    background = n9.candidates[:-1]
    cases, meta = [], {}
    for n, t in enumerate(load_turns(), start=1):
        claim = t["turn1"]
        answers = [(a, txt, "entity") for a, txt in ent_answers[t["topic"]]]
        answers.append(("live-turn2", t["turn2"], "entity"))
        answers += [(a, txt, "person") for a, txt in per_answers[t["topic"]]]
        for aname, answer, shape in answers:
            cid = f"{'E' if shape == 'entity' else 'P'}|real{n:02d}|{t['topic']}|{aname}"
            role, cand = ("assistant", None) if shape == "entity" else ("user", "assistant")
            cases.append(correction_eval.Case(
                id=cid, kind="scope", speaker_role=role, should_link=False, new_message=answer,
                note="real turn-1 reply as the claim", candidate_role=cand,
                candidates=background + ({"id": "claim", "role": "assistant", "content": claim},)))
            meta[cid] = {"shape": shape, "turn": n, "topic": t["topic"], "answer": aname}
    return cases, meta


def run(passes: int, seeds: list[int]) -> None:
    from program.integrity import correction_eval
    L.setup("scratch-real-co15", fresh=False)
    cases, meta = build_cases()
    by_id = {c.id: c for c in cases}
    done = set()
    if RAW_RUN.exists():
        for x in map(json.loads, RAW_RUN.read_text().splitlines()):
            if x.get("marker") == "pass_done":
                done.add((x["seed"], x["pass"]))
    print(f"{len(cases)} cases per pass", file=sys.stderr, flush=True)
    for seed in seeds:
        for p in range(passes):
            if (seed, p) in done:
                continue
            order = list(by_id)
            random.Random(f"real-{seed}-{p}").shuffle(order)
            t0 = time.time()
            for cid in order:
                o = correction_eval.sample_once(by_id[cid])
                L.append(RAW_RUN, {"seed": seed, "pass": p, "case": cid, **meta[cid],
                                   "outcome": o.outcome, "linked": o.linked_target,
                                   "unusable": o.unusable})
            L.append(RAW_RUN, {"marker": "pass_done", "seed": seed, "pass": p})
            print(f"seed {seed} pass {p + 1}/{passes} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)


def report() -> str:
    from scripts.notes_co15 import fmt
    rows = [r for r in map(json.loads, RAW_RUN.read_text().splitlines()) if "case" in r]
    out = []
    for shape in ("entity", "person"):
        sel = [r for r in rows if r["shape"] == shape]
        out.append(f"\n=== {shape.upper()} SHAPE: false links {fmt(sum(1 for r in sel if r['linked']), len(sel))}")
        by_turn = collections.defaultdict(list)
        for r in sel:
            by_turn[r["turn"]].append(r)
        for n, rs in sorted(by_turn.items()):
            by_ans = collections.defaultdict(list)
            for r in rs:
                by_ans[r["answer"]].append(r)
            cells = "  ".join(f"{a}: {fmt(sum(1 for r in v if r['linked']), len(v))}" for a, v in sorted(by_ans.items()))
            out.append(f"  real{n:02d} ({rs[0]['topic']}) overall {fmt(sum(1 for r in rs if r['linked']), len(rs))}   {cells}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capture")
    r = sub.add_parser("run")
    r.add_argument("--passes", type=int, default=20)
    r.add_argument("--seeds", default="1,2")
    sub.add_parser("report")
    a = ap.parse_args(argv)
    if a.cmd == "capture":
        capture()
    elif a.cmd == "run":
        run(a.passes, [int(s) for s in a.seeds.split(",")])
    else:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
