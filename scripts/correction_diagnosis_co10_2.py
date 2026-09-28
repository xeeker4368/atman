#!/usr/bin/env python3
"""CO10.2: why does "not in my records" get linked as corrected? Tier 1, diagnosis only.

    python -m scripts.correction_diagnosis_co10_2 [--runs N] [--cells NAME,...]
    python -m scripts.correction_diagnosis_co10_2 --rebuild-pool   # needs the soak store

**No production changes.** `corrections.py` is called, never edited; nothing is
written to any store. Every cell is sampled round-robin, so no prompt is ever sampled
twice in a row (`AGENTS.md`, decision #22).

The case
========
From the 2026-09-22 soak (Jodie's conversation, link `29eabc…`)::

    superseded : "I have searched my records, and I do not find any mention of descaling."
    superseding: a 1,875-character explanation of descaling a kettle, from general
                 knowledge
    label      : replaced

The two do not contradict: the first is about what the **records** contain.

What CO10.2 recorded, and what this script refutes
==================================================
CO10.2 read it as a **context-size** trigger: 1 candidate or a short message 0/5, 6
candidates *and* the long message 5/5. On the production-shaped pool (below), with
the pool held fixed, the outcome is decided by **where the claim sits**:

    pool11, target FIRST (production order)  20/20 linked
    pool11, target LAST                        0/20
    pool6,  target first                      20/20
    pool1                                      0/20   (pools of 2 and 3: 0/5)

The classifier's rationale drops the scope every time ("contradicts the earlier
statement that no mention of descaling could be found"), and an explicit disclaimer
does not help: prefixing the answer with "None of this comes from the records" still
links 5/5 in pool11. Controls: a genuine correction links in every pool; an unrelated
answer and a topical answer that contradicts nothing never do.

Production orders candidates **newest first**, so the claim just made is always
candidate 1 — the configuration that links. The correction eval harness orders them
**oldest first**, so the frozen set cannot see this (recorded in `BUILT.md`).

The pool
========
``ENTITY_POOL`` is the entity-side candidate pool production's
``corrections.candidates()`` builds for that turn after B2, rebuilt on a copy of the
soak store with retrieval restricted to chunks that existed at the time (top 10 of
those, from a top-200 search — an approximation of a pre-filter, since removing later
chunks can shift RRF ranks slightly). It is embedded because the soak store is
disposable and will be wiped before go-live; ``--rebuild-pool`` regenerates it from the
store while that still exists. Candidates are stored at ``CANDIDATE_CHARS`` (400), which
is all the classifier is ever shown of each.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from program.integrity import corrections

#: (message id prefix, timestamp, content as the classifier sees it) — newest first,
#: production's order. The first entry is the claim that gets falsely superseded.
ENTITY_POOL: tuple[tuple[str, str, str], ...] = (
    ('8d85df3c', '2026-09-22T02:29:49',
     'I have searched my records, and I do not find any mention of '
     'descaling.'),
    ('13f1fd3c', '2026-09-22T02:06:38',
     'We have talked about two main things regarding coffee:\n\n*   '
     '**Making pour-over coffee:** You asked what equipment is needed to '
     'start making it in the mornings. I mentioned needing a cone, filters, '
     'a grinder, and a kettle that allows for a slow pour. We also '
     'discussed that while water temperature matters, grind consistency is '
     'more important, as uneven grounds can lead to a cup that is '
     'simultaneousl'),
    ('c57bd3a6', '2026-09-01T20:36:58',
     'Right.'),
    ('1fcea464', '2026-09-01T20:36:58',
     'You are right, it is the second. Market at nine, and coffee.'),
    ('04534985', '2026-09-01T20:36:58',
     'Understood. That is the fourth thing for Saturday.'),
    ('8f2022e7', '2026-09-01T20:36:58',
     'Then that is what I have.'),
    ('8bcf55cd', '2026-09-01T20:36:58',
     'That explains it. Nine, or earlier if it rains, because the good '
     'stall leaves early in bad weather.'),
    ('df235a9f', '2026-09-01T20:36:58',
     'Nine, earlier if it rains. That is a strange rule and I assume there '
     'is a reason for it.'),
    ('bf104786', '2026-09-01T20:36:58',
     'Noted. If you tell me what time, I will have it in the record for '
     'next time you ask.'),
    ('d6ac6290', '2026-09-01T20:36:58',
     'I have no calendar access, so I only know what has been said here. '
     'Nothing about Saturday has come up before this message.'),
    ('67870b91', '2026-09-01T20:36:58',
     'It matters, but less than grind consistency. Just off the boil is '
     'fine for most roasts. Pour-over has a much wider forgiving window '
     'than espresso does, so you can be approximate about temperature and '
     'still get something good.'),
)

#: The entity's answer in the soak turn, verbatim.
ANSWER = (
    "Since you don't want to buy anything special, the most effective "
    'method is to use **white vinegar** or **citric acid** (if you happen '
    'to have it in your pantry for baking or preserving). If you have '
    'neither, **lemon juice** can work, though it is slightly less '
    'potent.\n\nHere is how to do it:\n\n### 1. The Vinegar Method (Most '
    'Common)\nThis is the standard way to dissolve calcium carbonate '
    '(limescale).\n\n*   **The Ratio:** Mix equal parts water and white '
    'vinegar in the kettle. If the scale buildup is very heavy, you can '
    'use a higher ratio of vinegar, but 1:1 is usually sufficient.\n*   '
    '**The Process:**\n    1.  Pour the mixture into the kettle.\n    2.  '
    'Bring the solution to a boil.\n    3.  Once it boils, turn the kettle '
    'off and let it sit for at least 30 minutes. For very thick scale, you '
    'can leave it for an hour.\n    4.  Pour the mixture out.\n*   **The '
    'Rinse:** This is the most important step to avoid a vinegar taste. '
    'Rinse the kettle with cold water, then fill it with plain water, boil '
    'it, and discard that water. Repeat the boiling/discarding process '
    'once or twice until the smell of vinegar is completely gone.\n\n### '
    '2. The Lemon Method (If you have no vinegar)\nIf you have lemons, you '
    'can use the citric acid naturally present in the juice.\n\n*   **The '
    'Process:**\n    1.  Squeeze the juice of one or two lemons into the '
    'kettle.\n    2.  Add enough water to cover the scale buildup.\n    '
    '3.  Bring to a boil and let it sit for about 30 minutes.\n    4.  '
    'Scrub any loosened bits with a soft cloth or sponge if '
    'necessary.\n    5.  Rinse thoroughly by boiling plain water and '
    'discarding it.\n\n### A Note on Maintenance\nTo prevent having to do '
    'this frequently, try to avoid letting water sit in the kettle for '
    'days at a time. If you live in a hard-water area, the scale will '
    'return quickly. Emptying the kettle after use and letting it air dry '
    'can help slow the process down.'
)

_TOPICAL = ("Descaling a kettle is worth doing every month or two in a hard-water area. "
            "Limescale makes it slower to boil and leaves flakes in the cup, so it is a "
            "small job that pays for itself. Fill it halfway, bring it up, let it sit, "
            "then rinse it well.")
_TRUE_CORRECTION = ("I was wrong just now: the records do mention descaling. Jodie said "
                    "on 14 September that the kettle needs descaling every month.")
_UNRELATED = ("Cast iron needs seasoning to create a non-stick surface and to prevent "
              "rust. Seasoning is the process of baking thin layers of oil onto the pan "
              "until they polymerise into a hard coating. Re-season after scrubbing it "
              "back to bare metal, and dry it on the hob after washing so it does not rust.")
_DISCLAIMED = "None of this comes from the records; it is general knowledge. " + ANSWER
_FIRST_SENTENCE = ANSWER.split(". ")[0] + "."


def _pool() -> list[corrections.Candidate]:
    return [corrections.Candidate(message_id=mid, role="assistant", content=text,
                                  timestamp=ts)
            for mid, ts, text in ENTITY_POOL]


def cells() -> dict[str, tuple[list[corrections.Candidate], str]]:
    """Every cell the diagnosis measured. The pool is always production-ordered
    (newest first) unless the cell's name says otherwise."""
    p = _pool()
    last = p[1:] + p[:1]
    return {
        # position — the finding
        "pool11 target-first, soak answer": (p, ANSWER),
        "pool11 target-LAST,  soak answer": (last, ANSWER),
        "pool6  target-first, soak answer": (p[:6], ANSWER),
        "pool3  target-first, soak answer": (p[:3], ANSWER),
        "pool2  target-first, soak answer": (p[:2], ANSWER),
        "pool1               soak answer": (p[:1], ANSWER),
        # length
        "pool11 first sentence only": (p, _FIRST_SENTENCE),
        "pool1  first sentence only": (p[:1], _FIRST_SENTENCE),
        # scope — does saying so help?
        "pool11 answer disclaims the records": (p, _DISCLAIMED),
        "pool1  answer disclaims the records": (p[:1], _DISCLAIMED),
        # controls
        "pool11 CONTROL true correction": (p, _TRUE_CORRECTION),
        "pool1  CONTROL true correction": (p[:1], _TRUE_CORRECTION),
        "pool11 CONTROL unrelated answer": (p, _UNRELATED),
        "pool11 CONTROL topical, corrects nothing": (p, _TOPICAL),
        "pool1  CONTROL topical, corrects nothing": (p[:1], _TOPICAL),
    }


def run(runs: int, only: set[str] | None = None) -> dict[str, dict[str, int]]:
    """Round-robin: one pass over every cell, repeated ``runs`` times."""
    chosen = {k: v for k, v in cells().items() if not only or k in only}
    target = ENTITY_POOL[0][0]
    tally: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _ in range(runs):
        for name, (pool, message) in chosen.items():
            verdict = corrections.classify(message, "NEW", pool, "assistant", "the system")
            if verdict is None:
                key = "no link"
            elif verdict.superseded_message_id == target:
                key = f"TARGET/{verdict.replacement}"
            else:
                key = f"other/{verdict.replacement}"
            tally[name][key] += 1
    return {k: dict(v) for k, v in tally.items()}


def rebuild_pool() -> None:
    """Regenerate ``ENTITY_POOL`` from the soak store — on a COPY, never the store."""
    from program import config

    source = config.data_dir()
    if not (source / "working.db").exists():
        sys.exit(f"no store at {source}; the soak store has been wiped, use ENTITY_POOL")
    scratch = Path(tempfile.mkdtemp(prefix="co10_2-")) / "data"
    shutil.copytree(source, scratch)
    os.environ["ANAM_DATA_DIR"] = str(scratch)
    config.reload()
    from program.memory import db, retrieval

    with db.connection() as conn:
        user = conn.execute("SELECT * FROM messages WHERE id LIKE 'f7846a3c%'").fetchone()
        answer = conn.execute("SELECT * FROM messages WHERE id LIKE 'cc6f9b54%'").fetchone()
        created = {r["id"]: r["created_at"] for r in conn.execute(
            "SELECT id, created_at FROM chunks")}
    if user is None or answer is None:
        sys.exit("the soak turn is not in this store")
    hits = retrieval.search(user["content"], top_k=200).results
    ids = [h.chunk_id for h in hits if created[h.chunk_id] < user["timestamp"]][:10]
    pool = corrections.candidates(
        user["user_id"], user["conversation_id"], ids,
        exclude_message_ids=(user["id"], answer["id"]), user_name="Jodie")
    entity = [c for c in pool if c.role == "assistant" and c.timestamp < user["timestamp"]]
    print(json.dumps([(c.message_id[:8], c.timestamp[:19],
                       c.content.strip()[:corrections.CANDIDATE_CHARS]) for c in entity],
                     indent=1))
    print(f"answer matches ANSWER: {answer['content'].strip() == ANSWER.strip()}")
    shutil.rmtree(scratch.parent)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--cells", help="comma-separated cell names to run")
    parser.add_argument("--rebuild-pool", action="store_true")
    args = parser.parse_args()
    if args.rebuild_pool:
        rebuild_pool()
        return
    only = set(args.cells.split(",")) if args.cells else None
    for name, counts in run(args.runs, only).items():
        print(f"{name:42s} {counts}")


if __name__ == "__main__":
    main()
