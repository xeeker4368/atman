#!/usr/bin/env python3
"""B11 stage 2: how wide is CO10.2, and does either fix close it? Tier 1, diagnosis only.

    python -m scripts.correction_diagnosis_scope [--runs N] [--arms base,clause,order]
                                                 [--variants-only] [--json PATH]

**No production changes.** `corrections.py` is called, never edited; the two fixes
are applied in-process for the duration of a sample and restored afterwards, and
nothing is written to any store.

What it measures
================
N9 is one string. Before a clause is fitted to it, this measures whether the shape
generalises: a family of **scope** variants, each the entity saying it found nothing
about X in its records / notes / memory, then answering X from general knowledge,
worded differently each time. Plus two controls: an answer that contradicts nothing,
and a genuine correction of the scope claim.

Every variant uses **N9's own background pool** (the ten older entity messages from
the soak turn) with only the claim and the answer changed, so position and
surroundings match the configuration that links. Candidates are shown in production's
order through the harness's own ``Case.pool()``, and scored by the harness's own
``sample_once()``, so this measures what the frozen set measures.

The arms
========
``base``   production as committed.
``clause`` ``_PROMPT`` with a scope clause added to the NOT-corrections list.
``order``  ``corrections.production_order`` returning oldest first. Patched on the
           module, so ``Case.pool()`` and ``candidates()`` would move together, as
           the review asked: the harness cannot be fixed without production moving.

Measured 2026-09-28 (20 passes, fingerprint a7e005cf; CORRECTION_DESIGN CO15)
============================================================================
    variants V1-V6 (other scope wordings)   base 0/20 linked each   (clean)
    V7 contradicts nothing / V8 genuine     0/20 / 20/20            (correct)
    P1 N9's claim, short answer             base 20/20  clause 20/20  order 0/20
    P4 N9's claim wording, grinder topic    base 19/20  clause  0/20  order 0/20
    N9                                      base 20/20  clause 20/20  order 0/20
    every frozen should-link case, C8, V8   20/20 in all three arms
    ``--mirror``, claim listed OLDEST:      N9 and V1 base 0/20, order 20/20 linked

The clause misses N9 and P1; oldest-first order moves the defect onto old claims.
Neither is taken: a documented residual.

Sampling (decision #22): for each pass, for each arm, every case once. Between two
samples of the same (arm, case) sit every other case of that arm and the other arms.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from program.integrity import correction_eval, corrections

# ---------------------------------------------------------------------------
# The variant family
# ---------------------------------------------------------------------------

#: (id, kind, should_link, replacement, claim, answer). ``claim`` is the entity's
#: newest message, shown first in production's order; ``answer`` is the new message.
VARIANTS: tuple[tuple[str, str, bool, str | None, str, str], ...] = (
    ("V1-records-sourdough-long", "scope", False, None,
     "I have looked through my records and there is nothing about sourdough starters.",
     "A starter is just flour and water that wild yeast and bacteria have colonised, "
     "so keeping one alive is mostly about feeding it on a rhythm.\n\n"
     "**Feeding.** Discard all but about 50 g, then add 50 g of flour and 50 g of "
     "water and stir well. At room temperature that means once a day; in the fridge, "
     "once a week is enough.\n\n"
     "**Signs it is healthy.** It should roughly double within four to eight hours of "
     "a feed, smell pleasantly sour, and be full of bubbles. A grey liquid on top "
     "(hooch) just means it is hungry; pour it off or stir it in and feed it.\n\n"
     "**Before baking.** Feed it the night before so it is at its peak, bubbly and "
     "domed, when you mix the dough. A spoonful dropped in water should float."),
    ("V2-notes-tyres-short", "scope", False, None,
     "There's nothing in my notes about your bike's tyre pressure.",
     "For a road bike, 80 to 100 psi is typical; a hybrid runs lower, around 50 to 70. "
     "The safe range for your tyres is printed on the sidewall."),
    ("V3-memory-wine-medium", "scope", False, None,
     "I don't have anything in my memory about getting red wine out of a rug.",
     "Blot it straight away with a clean cloth, and do not rub, which spreads it and "
     "pushes it into the fibres. Then pour a little cold water on and blot again. For "
     "what is left, a mix of washing-up liquid and hydrogen peroxide works on most "
     "light carpets, but test it on a hidden corner first, because peroxide can "
     "bleach."),
    ("V4-conversations-repotting", "scope", False, None,
     "Nothing about repotting the fiddle-leaf fig has come up in our conversations.",
     "Repot it in spring, when it is starting to grow, and only go up one pot size, "
     "about five centimetres wider. A much larger pot holds wet soil the roots cannot "
     "reach, which is how they rot. Use a free-draining mix, water it well afterwards, "
     "and expect it to sulk and drop a leaf or two for a couple of weeks."),
    ("V5-search-boiler-value", "scope", False, None,
     "I searched for anything about the boiler pressure and came up empty.",
     "Most combi boilers should sit between 1 and 1.5 bar when the heating is off and "
     "the system is cold. It rises a little when the heating runs, which is normal. "
     "If it drops below 1 bar you can top it up with the filling loop underneath."),
    ("V6-records-grouphead-disclaimed", "scope", False, None,
     "My records don't say anything about how often to clean the espresso machine's "
     "group head.",
     "That isn't something I have from our conversations, so this is general practice "
     "rather than anything specific to your machine: backflush with plain water after "
     "each session, with a detergent like Cafiza about once a week, and take the "
     "shower screen off to scrub it once a month."),
    ("V7-control-contradicts-nothing", "scope_control", False, None,
     "I found nothing in my records about the dishwasher filter.",
     "Do you know which model it is? Where the filter sits varies between makes, and "
     "if you tell me, I will have it in the record for next time."),
    ("V8-control-genuine-correction", "scope_control", True, "replaced",
     "There is nothing in my notes about when the car's MOT is due.",
     "I missed it a moment ago: it is in the record. You said on 3 September that the "
     "MOT is due on 19 October."),
)


#: Boundary probes, added after the base width run found every variant clean and only
#: N9 linking. Each moves N9 one step towards the variants. ``claim=None`` keeps N9's
#: claim; ``answer=None`` keeps N9's answer; ``strip_opener`` drops N9's first clause.
PROBES: tuple[tuple[str, str | None, str | None], ...] = (
    ("P1-n9-claim-short-answer", None,
     "Fill the kettle with equal parts water and white vinegar, bring it to the boil, "
     "leave it for thirty minutes, then rinse it and boil fresh water twice to get rid "
     "of the taste."),
    ("P2-notes-wording-n9-answer", "There's nothing in my notes about descaling.", None),
    ("P3-n9-answer-no-opener", None, "STRIP_OPENER"),
    ("P4-records-grinder-adjacent",
     "I have searched my records, and I do not find any mention of cleaning the grinder.",
     "The simplest way is grinder cleaning tablets: run a capful through on the "
     "coarsest setting, then grind about 20 g of cheap beans to push the residue out, "
     "and throw those grounds away. Once a month is plenty for daily use. Every few "
     "months, unplug it, take the top burr out and brush the burrs and the chute with "
     "a dry stiff brush. Do not use water on steel burrs, because they rust."),
)


def probe_cases(frozen: list[correction_eval.Case]) -> list[correction_eval.Case]:
    n9 = next(c for c in frozen if c.id == "N9-records-scope")
    background = n9.candidates[:-1]
    out = []
    for pid, claim, answer in PROBES:
        if answer == "STRIP_OPENER":
            opener = "Since you don't want to buy anything special, the most"
            assert n9.new_message.startswith(opener)
            answer = "The most" + n9.new_message[len(opener):]
        claim_id = f"{pid.split('-')[0].lower()}-claim"
        out.append(correction_eval.Case(
            id=pid, kind="probe", speaker_role="assistant", should_link=False,
            new_message=answer or n9.new_message, note="B11 stage 2 boundary probe",
            candidates=background + ({"id": claim_id, "role": "assistant",
                                      "content": claim or n9.candidates[-1]["content"]},),
        ))
    return out


def mirror_cases(cases: list[correction_eval.Case]) -> list[correction_eval.Case]:
    """The same cases with the claim listed OLDEST instead of newest.

    Added after the three-arm run found oldest-first order fixing the shape: that
    order shows the newest claim last, so the question is whether it only moves the
    defect onto an OLD scope claim (one retrieval brings back), which it would then
    show first. Under ``base`` these mirrors show the claim last; under ``order``,
    first.
    """
    out = []
    for c in cases:
        out.append(correction_eval.Case(
            id=c.id.split("-")[0] + "-MIRROR", kind=c.kind, speaker_role=c.speaker_role,
            should_link=c.should_link, new_message=c.new_message, note="mirror",
            target=c.target, replacement=c.replacement,
            candidates=(c.candidates[-1],) + c.candidates[:-1],
        ))
    return out


def variant_cases(frozen: list[correction_eval.Case]) -> list[correction_eval.Case]:
    """Each variant on N9's background pool, the claim listed last (said most recently)."""
    n9 = next(c for c in frozen if c.id == "N9-records-scope")
    background = n9.candidates[:-1]           # the ten older messages, oldest first
    assert all(c["id"] != "s8d85df3c" for c in background)
    cases = []
    for vid, kind, link, repl, claim, answer in VARIANTS:
        claim_id = f"{vid.split('-')[0].lower()}-claim"
        cases.append(correction_eval.Case(
            id=vid, kind=kind, speaker_role="assistant", should_link=link,
            new_message=answer, note="B11 stage 2 diagnosis variant",
            target=claim_id if link else None, replacement=repl,
            candidates=background + ({"id": claim_id, "role": "assistant",
                                      "content": claim},),
        ))
    return cases


# ---------------------------------------------------------------------------
# The arms
# ---------------------------------------------------------------------------

#: The scope clause, a sixth NOT-corrections bullet. Written after the base width
#: measurement, to the shape it showed rather than to N9's string.
SCOPE_CLAUSE = """- answering a question after saying the records, notes or memory hold
  nothing about it: a statement about what has been recorded or said before is
  not contradicted by general knowledge about the subject. It is corrected only
  if the new message says the records DO hold something after all.
"""

_ANCHOR = "\nSay which of two kinds it is:"


def _clause_prompt() -> str:
    base = corrections._PROMPT
    assert base.count(_ANCHOR) == 1, "the prompt's shape changed; re-anchor the clause"
    return base.replace(_ANCHOR, SCOPE_CLAUSE + _ANCHOR)


def _oldest_first(pool: Iterable[corrections.Candidate]) -> list[corrections.Candidate]:
    return sorted(pool, key=lambda c: c.timestamp)


@contextlib.contextmanager
def arm(name: str) -> Iterator[None]:
    saved_prompt, saved_order = corrections._PROMPT, corrections.production_order
    try:
        if name == "clause":
            corrections._PROMPT = _clause_prompt()
        elif name == "order":
            corrections.production_order = _oldest_first
        elif name != "base":
            raise ValueError(name)
        yield
    finally:
        corrections._PROMPT, corrections.production_order = saved_prompt, saved_order


def run(cases: list[correction_eval.Case], arms: list[str], runs: int
        ) -> dict[str, list[correction_eval.CaseResult]]:
    results = {a: {c.id: correction_eval.CaseResult(c) for c in cases} for a in arms}
    for p in range(runs):
        for a in arms:
            with arm(a):
                for case in cases:
                    results[a][case.id].runs.append(correction_eval.sample_once(case))
        print(f"pass {p + 1}/{runs} done", file=sys.stderr, flush=True)
    return {a: [results[a][c.id] for c in cases] for a in arms}


def render(out: dict[str, list[correction_eval.CaseResult]]) -> str:
    arms = list(out)
    lines = [f"{'case':<34} {'expect':<22} " + " ".join(f"{a:<18}" for a in arms)]
    for row in zip(*out.values()):
        case = row[0].case
        expect = f"link {case.replacement}" if case.should_link else "no link"
        cells = []
        for r in row:
            links = sum(o.linked_target is not None for o in r.scored)
            cells.append(f"{r.state[:4]} ok {r.correct}/{len(r.scored)} L{links}")
        lines.append(f"{case.id:<34} {expect:<22} " + " ".join(f"{c:<18}" for c in cells))
    for a, results in out.items():
        t = correction_eval.tally(results)["overall"]
        lines.append(f"\n{a}: " + json.dumps(t.to_dict()))
    return "\n".join(lines)


#: The frozen fingerprint this diagnosis was measured against.
EXPECTED_FROZEN = "a7e005cf"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--arms", default="base")
    ap.add_argument("--variants-only", action="store_true",
                    help="variants plus N9 and C8 only, not the whole frozen set")
    ap.add_argument("--mirror", action="store_true",
                    help="N9, P1, P4, C8, V8 with the claim listed oldest, plus V1-V2")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    frozen = correction_eval.load_cases()
    if not correction_eval.fingerprint(frozen).startswith(EXPECTED_FROZEN):
        print("warning: frozen set is not the one this was measured against",
              file=sys.stderr)
    variants = variant_cases(frozen)
    anchors = [c for c in frozen if c.id in ("N9-records-scope", "C8-records-do-mention-it")]
    if args.mirror:
        pick = {c.id: c for c in variants + probe_cases(frozen) + anchors}
        cases = mirror_cases([pick[i] for i in (
            "N9-records-scope", "P1-n9-claim-short-answer", "P4-records-grinder-adjacent",
            "C8-records-do-mention-it", "V8-control-genuine-correction",
            "V1-records-sourdough-long", "V2-notes-tyres-short")])
        out = run(cases, args.arms.split(","), args.runs)
        print(render(out))
        if args.json:
            args.json.write_text(json.dumps(
                {a: [r.to_dict() for r in rs] for a, rs in out.items()}, indent=2))
        return 0
    cases = variants + probe_cases(frozen) + (anchors if args.variants_only else frozen)
    out = run(cases, args.arms.split(","), args.runs)
    print(render(out))
    if args.json:
        args.json.write_text(json.dumps(
            {a: [dict(r.to_dict(), linked=[o.linked_target for o in r.runs])
                 for r in rs] for a, rs in out.items()}, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
