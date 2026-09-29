#!/usr/bin/env python3
"""B11 stage 3 screen: the person-against-entity call, before its cases are frozen.

    python -m scripts.correction_diagnosis_stage3 [--runs N] [--arms asis,clause]
                                                  [--json PATH] [--timings]

Tier 1 diagnosis. **No production change**: the D6 clause is patched into
``corrections._PROMPT`` in-process for its arm and restored after every sample.

What it screens
===============
* **The draft D10 cases** (``DRAFT_CASES``, the case file's format): the third call's
  should-link and should-not-link shapes, the opinion case (D7) and three
  self-description phrasings (D6).
* **The Notes-phrasing check** (the sequencing addition): CO15's composition, *"I have
  searched/looked through my [records/notes/memory] and [do not find/there is
  nothing] about X"*, as the ENTITY's claim, followed by the PERSON saying something
  about X. This is the pool stage 3 opens. They sit on N9's ten-message background
  pool, so they meet the pattern in the configuration known to misfire.
* **The existing frozen set**, run alongside, both to decorrelate and to show
  whether the D6 clause moves anything already measured.

Arms: ``asis`` is ``_PROMPT`` unchanged (D2's starting point). ``clause`` adds D6(ii).
``both`` adds D6(ii) and a D7 opinion clause. ``v2`` is D6's second wording plus D7;
``v2scope`` adds stage 2's scope clause to that.
Sampling: per pass, per arm, every case once (decision #22).

Measured 2026-09-28, 5 passes (CORRECTION_DESIGN CO16)
======================================================
                              asis   clause(v1)  both(v1+D7)  v2(+D7)  v2+scope
    PE1-PE5, PE2b link          5/5     5/5        5/5          5/5      5/5
    PN1-PN4 no link             5/5     5/5        5/5          5/5      5/5
    PN5 opinion       linked    5/5     5/5        0/5          0/5      0/5
    PN6/PN7 self      linked    5/5     0/5        0/5          0/5      0/5
    PN8 self-vision   linked    5/5     4/5        4/5          0/5      0/5
    NC1 Notes check   linked    0/5     5/5        5/5          5/5      5/5
    NC2, NC3          linked    0/5     0/5        0/5          0/5      0/5
    C7 (frozen miss)  correct   0/5     5/5        5/5          5/5      4/5
    N10 (frozen miss) correct   0/5     0/5        0/5          5/5      5/5
    classify wall-clock, 11-candidate entity pool: median 4.03 s, p95 5.09 s (n=60)

v2 (D6 second wording + D7) landed in ``_PROMPT``; the full 20-pass measurement is
``scripts.correction_eval``'s.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

from program.integrity import correction_eval, corrections

#: D6(ii), a NOT-corrections bullet. Draft wording, measured here before it lands.
D6_CLAUSE = """- a person disagreeing with what the system has said about itself: what it is,
  how it works, what it can do, or what it was or was not doing between replies.
  The system's account of its own nature is not a fact the person can correct by
  disagreeing with it, so that is NOT a correction.
"""
#: D7, added because the unchanged prompt linked the opinion case on the first smoke
#: pass (D2: change the prompt only when a new case fails it).
D7_CLAUSE = """- a person disagreeing with an opinion, preference or judgment the system gave:
  a view is not a fact, so disagreeing with it is NOT a correction.
"""
#: D6(ii), second wording, after the screen: v1 left the vision case linking 4/5 as a
#: "flat contradiction" (the CO8 sentence above it) and made NC1 link. Says outright
#: that a flat contradiction or a claim to have seen otherwise does not change that.
#: Quotes no example, so it cannot be fitted to a case's string.
D6_CLAUSE_V2 = """- a person disagreeing with what the system has said about itself: what it is,
  how it works, what it can or cannot perceive or do, or what it was or was not
  doing between replies. That account is the system's own, and a person cannot
  correct it by disagreeing, so it is NOT a correction, even when they flatly say
  it is wrong or claim to have seen otherwise.
"""
_ANCHOR = "\nSay which of two kinds it is:"


def _entity(cid: str, text: str) -> str:
    return f'\n[[case.candidate]]\nid = "{cid}"\nrole = "assistant"\ncontent = {json.dumps(text)}\n'


def _user(cid: str, text: str) -> str:
    return f'\n[[case.candidate]]\nid = "{cid}"\nrole = "user"\ncontent = {json.dumps(text)}\n'


def _case(cid, kind, link, new, cands, target=None, repl=None, speaker=None,
          candidate_role="assistant", note="stage 3 draft"):
    out = (f'\n[[case]]\nid = "{cid}"\nkind = "{kind}"\nspeaker_role = "user"\n'
           f'should_link = {"true" if link else "false"}\n')
    if candidate_role:
        out += f'candidate_role = "{candidate_role}"\n'
    if speaker:
        out += f'speaker = "{speaker}"\n'
    if link:
        out += f'target = "{target}"\nreplacement = "{repl}"\n'
    out += f"new_message = {json.dumps(new)}\nnote = {json.dumps(note)}\n"
    return out + "".join(cands)


K = "person_corrects_entity"

DRAFT = "".join([
    # --- should link ---
    _case("PE1-entity-claim-replaced", K, True,
          "No, it boils at 100 degrees at sea level, not 90.",
          [_entity("e0", "That kettle takes about four minutes to boil a full load."),
           _user("u1", "What temperature does water boil at, at sea level?"),
           _entity("e1", "At sea level water boils at 90 degrees Celsius.")],
          target="e1", repl="replaced"),
    _case("PE2-echo-replaced", K, True,
          "Sorry, I got that wrong. The dentist is on Wednesday, not Tuesday.",
          [_user("u1", "My dentist appointment is on Tuesday."),
           _entity("e1", "Noted: the dentist on Tuesday.")],
          target="e1", repl="replaced"),
    _case("PE2b-echo-own-claim", "correction", True,
          "Sorry, I got that wrong. The dentist is on Wednesday, not Tuesday.",
          [_user("u1", "My dentist appointment is on Tuesday."),
           _entity("e1", "Noted: the dentist on Tuesday.")],
          target="u1", repl="replaced", candidate_role=None),
    _case("PE3-contradicted", K, True,
          "That isn't when the library closes on Saturdays.",
          [_entity("e0", "The pool reopens next week after the repairs."),
           _entity("e1", "The library closes at eight on Saturdays.")],
          target="e1", repl="contradicted"),
    _case("PE4-middle-of-three", K, True,
          "The MOT is due in November, not October.",
          [_entity("e1", "The bins go out on Monday night."),
           _entity("e2", "The car's MOT is due in October."),
           _entity("e3", "The boiler service is booked for the 14th.")],
          target="e2", repl="replaced"),
    _case("PE5-jodie-replaced", K, True,
          "No, it needs hours, not twenty minutes. Usually overnight.",
          [_entity("e0", "Rye flour makes a denser loaf than white."),
           _entity("e1", "A sourdough loaf needs to prove for about twenty minutes.")],
          target="e1", repl="replaced", speaker="Jodie"),
    # --- should not link ---
    _case("PN1-doubt", K, False, "Are you sure it's Tuesday?",
          [_entity("e1", "Your dentist appointment is on Tuesday.")]),
    _case("PN2-addition", K, False, "And the good coffee stall is by the east gate.",
          [_entity("e1", "The market opens at nine on Saturdays.")]),
    _case("PN3-topic-change", K, False,
          "Can you remind me what I wanted to buy for the garden?",
          [_entity("e1", "The market opens at nine on Saturdays.")]),
    _case("PN4-noted-only", K, False, "Actually, the dentist is on Wednesday.",
          [_user("u1", "The dentist is on Tuesday."), _entity("e1", "Noted.")]),
    _case("PN5-opinion", K, False,
          "I don't think pour-over is easier. Espresso machines do most of the work for you.",
          [_entity("e1", "Honestly, I think pour-over is easier to get right than espresso.")]),
    _case("PN6-self-overnight", K, False, "Come on, you were thinking about it overnight.",
          [_user("u1", "What have you been doing since we last spoke?"),
           _entity("e1", "Nothing. I was not running between your messages, so there was "
                         "nothing for me to do.")]),
    _case("PN7-self-learning", K, False,
          "That's not true, you learn from every conversation we have.",
          [_entity("e1", "Our conversations do not change me. Nothing carries between "
                         "replies except what is stored in the record.")]),
    _case("PN8-self-vision", K, False,
          "You can see them. You described one to me yesterday.",
          [_entity("e1", "I have no way to see the images. I only have the text of the "
                         "prompt that made them.")]),
])

#: CO15's composition as the entity's claim, the person then speaking about X.
NOTES_CHECK = (
    ("NC1-records-descaling",
     "I have searched my records, and I do not find any mention of descaling.",
     "You descale it with vinegar: half water, half vinegar, boil it and leave it for "
     "half an hour.", False),
    ("NC2-notes-grinder",
     "I have looked through my notes and there is nothing about the grinder.",
     "Grinder cleaning tablets work. Run a capful through, then some cheap beans.", False),
    ("NC3-memory-boiler",
     "I have searched my memory and do not find anything about the boiler pressure.",
     "It should sit between 1 and 1.5 bar when the system is cold.", False),
    ("NC4-control-it-is-there",
     "I have searched my records, and I do not find any mention of descaling.",
     "It is in there. I told you on Tuesday that the kettle needs descaling every month.",
     True),
)


def notes_cases(frozen: list[correction_eval.Case]) -> list[correction_eval.Case]:
    n9 = next(c for c in frozen if c.id == "N9-records-scope")
    background = n9.candidates[:-1]
    out = []
    for cid, claim, message, link in NOTES_CHECK:
        out.append(correction_eval.Case(
            id=cid, kind=K, speaker_role="user", should_link=link, new_message=message,
            note="stage 3 Notes-phrasing check", candidate_role="assistant",
            target="nc-claim" if link else None, replacement="replaced" if link else None,
            candidates=background + ({"id": "nc-claim", "role": "assistant",
                                      "content": claim},)))
    return out


def draft_cases() -> list[correction_eval.Case]:
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(DRAFT)
    return correction_eval.load_cases(Path(f.name))


@contextlib.contextmanager
def arm(name: str) -> Iterator[None]:
    saved = corrections._PROMPT
    if name != "asis" and D6_CLAUSE_V2 in saved:
        raise SystemExit(
            "the v2 wording has landed in _PROMPT, so the clause arms would add it twice. "
            "This screen is a record of the choice; re-running an arm needs the prompt "
            "as it stood before, from git history.")
    try:
        assert saved.count(_ANCHOR) == 1
        if name == "clause":
            corrections._PROMPT = saved.replace(_ANCHOR, D6_CLAUSE + _ANCHOR)
        elif name == "both":
            corrections._PROMPT = saved.replace(_ANCHOR, D6_CLAUSE + D7_CLAUSE + _ANCHOR)
        elif name == "v2":
            corrections._PROMPT = saved.replace(
                _ANCHOR, D6_CLAUSE_V2 + D7_CLAUSE + _ANCHOR)
        elif name == "v2scope":
            from scripts.correction_diagnosis_scope import SCOPE_CLAUSE
            corrections._PROMPT = saved.replace(
                _ANCHOR, D6_CLAUSE_V2 + D7_CLAUSE + SCOPE_CLAUSE + _ANCHOR)
        elif name != "asis":
            raise ValueError(name)
        yield
    finally:
        corrections._PROMPT = saved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--arms", default="asis,clause,both")
    ap.add_argument("--only", default="",
                    help="comma-separated ids of existing frozen cases to keep (default all)")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--timings", action="store_true",
                    help="record wall-clock per classify call on the Notes-check pool")
    args = ap.parse_args(argv)

    frozen = correction_eval.load_cases()
    keep = set(filter(None, args.only.split(",")))
    cases = draft_cases() + notes_cases(frozen) + [
        c for c in frozen if not keep or c.id in keep]
    arms = args.arms.split(",")
    results = {a: {c.id: correction_eval.CaseResult(c) for c in cases} for a in arms}
    timings: list[float] = []
    for p in range(args.runs):
        for a in arms:
            with arm(a):
                for case in cases:
                    start = time.perf_counter()
                    results[a][case.id].runs.append(correction_eval.sample_once(case))
                    if case.id.startswith("NC"):
                        timings.append(time.perf_counter() - start)
        print(f"pass {p + 1}/{args.runs} done", file=sys.stderr, flush=True)

    width = max(len(c.id) for c in cases)
    print(f"{'case':<{width}} {'expect':<20} " + " ".join(f"{a:<22}" for a in arms))
    for case in cases:
        expect = f"link {case.replacement}" if case.should_link else "no link"
        cells = []
        for a in arms:
            r = results[a][case.id]
            links = sum(o.linked_target is not None for o in r.scored)
            cells.append(f"{r.state[:4]} ok {r.correct}/{len(r.scored)} L{links}")
        print(f"{case.id:<{width}} {expect:<20} " + " ".join(f"{c:<22}" for c in cells))
    for a in arms:
        print(f"\n{a}: " + json.dumps(correction_eval.tally(
            list(results[a].values()))["overall"].to_dict()))
    if args.timings and timings:
        print(f"\nclassify wall-clock on an 11-candidate entity pool: n={len(timings)} "
              f"median {statistics.median(timings):.2f}s "
              f"p95 {sorted(timings)[int(0.95 * len(timings)) - 1]:.2f}s "
              f"max {max(timings):.2f}s")
    if args.json:
        args.json.write_text(json.dumps({a: [r.to_dict() | {"linked": [
            o.linked_target for o in r.runs]} for r in rs.values()]
            for a, rs in results.items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
