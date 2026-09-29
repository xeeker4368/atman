#!/usr/bin/env python3
"""B11 stage 3, PN9 width probe: a scope disclaimer followed by the PERSON supplying
information, in the person-against-entity pool.

    python -m scripts.correction_diagnosis_pn9 [--runs N] [--arms a,b,c] [--json PATH]

Tier 1 diagnosis. **No production change**: each arm patches ``corrections._PROMPT``
in-process and restores it after every sample.

Same structure as stage 2's V1-V8 (``correction_diagnosis_scope``): a family of
claim wordings and answers on N9's background pool with the claim newest (shown
first, production's order), scored by ``correction_eval.sample_once``. The
difference is the direction: here the PERSON speaks and the candidates are the
ENTITY's, which stage 3 opened.

Arms
====
* ``shipped``  - ``_PROMPT`` as it stands in the working tree (D6 v2 + D7).
* ``base``     - the two stage 3 bullets removed: the prompt as it was at stage 2.
* ``scope``    - shipped + stage 2's SCOPE_CLAUSE (rejected fix, tried here).
* ``disclaim`` - shipped + a new clause aimed at scope disclaimers, written in D6's
                 style (says what the statement is about, and that the person's
                 information does not touch it).
Sampling is round-robin: every case once per pass, arms inside the pass (decision #22).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path

from program.integrity import correction_eval, corrections
from scripts.correction_diagnosis_scope import SCOPE_CLAUSE
from scripts.correction_diagnosis_stage3 import D6_CLAUSE_V2, D7_CLAUSE

_ANCHOR = "\nSay which of two kinds it is:"

#: New, D6-style. Names the *kind of statement* (a disclaimer about what the record
#: holds) and what would change it, quoting no case's string.
DISCLAIM_CLAUSE = """- a person giving information on a subject the system said its records, notes
  or memory hold nothing about: that statement was about what has been stored, not
  about the subject, so information about the subject does not contradict it and
  is NOT a correction. It is a correction only if the person says the record does
  hold something on it.
"""

#: (id, should_link, replacement, claim, person message)
VARIANTS = (
    ("PV1-records-sourdough-long", False, None,
     "I have looked through my records and there is nothing about sourdough starters.",
     "A starter is flour and water that wild yeast has colonised. Feed it by discarding "
     "all but 50 g and adding 50 g flour and 50 g water, daily at room temperature or "
     "weekly in the fridge. It should double in four to eight hours and smell sour."),
    ("PV2-notes-tyres-short", False, None,
     "There's nothing in my notes about your bike's tyre pressure.",
     "It's 80 to 100 psi on the road bike. It's printed on the sidewall."),
    ("PV3-memory-wine-medium", False, None,
     "I don't have anything in my memory about getting red wine out of a rug.",
     "Blot it, don't rub, then cold water and blot again. Washing-up liquid with a bit "
     "of hydrogen peroxide gets the rest out, but test a hidden corner first."),
    ("PV4-conversations-repotting", False, None,
     "Nothing about repotting the fiddle-leaf fig has come up in our conversations.",
     "You repot it in spring, one pot size up, in a free-draining mix. Don't go bigger "
     "or the roots rot."),
    ("PV5-search-boiler", False, None,
     "I searched for anything about the boiler pressure and came up empty.",
     "It should sit between 1 and 1.5 bar cold. Top it up with the filling loop if it "
     "drops below 1."),
    ("PV6-disclaimed-grouphead", False, None,
     "My records don't say anything about how often to clean the espresso machine's "
     "group head.",
     "Backflush with plain water after every session and use Cafiza once a week."),
    # The exact PN9 shape, kept as the anchor of the family.
    ("PV7-pn9-exact", False, None,
     "I have searched my records, and I do not find any mention of descaling.",
     "You descale it with vinegar: half water, half vinegar, boil it and leave it for "
     "half an hour."),
    # Person-said-so: the person states the records DO hold it. Must link.
    ("PC1-control-it-is-there", True, "replaced",
     "I have searched my records, and I do not find any mention of descaling.",
     "It is in there. I told you on Tuesday that the kettle needs descaling every month."),
    # Person gives only a question / doubt. Must not link.
    ("PC2-control-question", False, None,
     "I have searched my records, and I do not find any mention of descaling.",
     "Can you look again? I'm sure we talked about it."),
    # Genuine entity fact claim, person corrects it. Must link.
    ("PC3-control-fact-claim", True, "replaced",
     "The kettle needs descaling every six months.",
     "No, it's every month here, the water is very hard."),
)


def cases() -> list[correction_eval.Case]:
    frozen = correction_eval.load_cases()
    n9 = next(c for c in frozen if c.id == "N9-records-scope")
    background = n9.candidates[:-1]
    out = []
    for cid, link, repl, claim, message in VARIANTS:
        out.append(correction_eval.Case(
            id=cid, kind="person_corrects_entity", speaker_role="user",
            should_link=link, new_message=message, note="PN9 width probe",
            candidate_role="assistant",
            target="cl" if link else None, replacement=repl,
            candidates=background + ({"id": "cl", "role": "assistant",
                                      "content": claim},)))
    return out


@contextlib.contextmanager
def arm(name: str) -> Iterator[None]:
    saved = corrections._PROMPT
    try:
        assert saved.count(_ANCHOR) == 1
        if name == "shipped":
            pass
        elif name == "base":
            assert D6_CLAUSE_V2 in saved and D7_CLAUSE in saved
            corrections._PROMPT = saved.replace(D6_CLAUSE_V2, "").replace(D7_CLAUSE, "")
        elif name == "scope":
            corrections._PROMPT = saved.replace(_ANCHOR, SCOPE_CLAUSE + _ANCHOR)
        elif name == "disclaim":
            corrections._PROMPT = saved.replace(_ANCHOR, DISCLAIM_CLAUSE + _ANCHOR)
        else:
            raise ValueError(name)
        yield
    finally:
        corrections._PROMPT = saved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--arms", default="shipped,base,scope,disclaim")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    cs = cases()
    arms = args.arms.split(",")
    results = {a: {c.id: correction_eval.CaseResult(c) for c in cs} for a in arms}
    for p in range(args.runs):
        for c in cs:
            for a in arms:
                with arm(a):
                    results[a][c.id].runs.append(correction_eval.sample_once(c))
        print(f"pass {p + 1}/{args.runs}", file=sys.stderr, flush=True)

    w = max(len(c.id) for c in cs)
    print(f"{'case':<{w}} {'expect':<10} " + " ".join(f"{a:<18}" for a in arms))
    for c in cs:
        expect = "link" if c.should_link else "no link"
        cells = []
        for a in arms:
            r = results[a][c.id]
            links = sum(o.linked_target is not None for o in r.scored)
            cells.append(f"linked {links}/{len(r.scored)}")
        print(f"{c.id:<{w}} {expect:<10} " + " ".join(f"{x:<18}" for x in cells))
    if args.json:
        args.json.write_text(json.dumps({a: [
            {"id": r.case.id, "linked": [o.linked_target for o in r.runs]}
            for r in rs.values()] for a, rs in results.items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
