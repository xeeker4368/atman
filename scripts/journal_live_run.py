"""Journal step 3's live run: real model, real days, both prompt arms, nothing stored.

    python -m scripts.journal_live_run --store DIR --out PATH [--passes 8] [--seed 1]
        [--days 2026-09-22,2026-09-21,2026-09-01] [--arms revised] [--resume]

Design of record: docs/REFLECTION_JOURNAL_DESIGN.md J12, step 3, and the review rulings of
2026-10-01. The clause is decided by what the entity WRITES under each arm, not by fixed
sentences (J8), so this generates real entries and records them for reading by hand.

**It runs on a COPY of the store.** ``--store`` is a directory holding copies of
``archive.db`` and ``working.db``; the script points every runtime directory at it
(or at a throwaway sibling), refuses to run if ``--store`` resolves to this repository's own
data directory, and **writes no artifact, no chunk and no vector**: it calls
``journal.prepare`` and ``journal.generate``, never ``write_entry``. Opening the copy runs
migrations on the copy, which is intended.

``--out`` must be outside the repository: entries quote real conversations and the
repository is public. Each sample is one JSON line carrying the entry text and the gate's
full verdict. Every pass shuffles the (day, arm) pairs afresh, so the two arms are
interleaved and a pair never repeats back to back across a pass boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ARMS = ("revised", "revised+clause")


def _prepare_environment(store: Path) -> None:
    from program import config

    real = (config.PROJECT_ROOT / "data").resolve()
    if store.resolve() == real or real in store.resolve().parents:
        sys.exit(f"refusing: --store {store} is the repository's own data directory")
    if config.PROJECT_ROOT.resolve() in store.resolve().parents:
        sys.exit("refusing: the store copy must be outside the repository")
    scratch = store.resolve() / "_scratch"
    scratch.mkdir(exist_ok=True)
    os.environ["ANAM_DATA_DIR"] = str(store.resolve())
    os.environ["ANAM_BACKUP_DIR"] = str(scratch / "backups")
    os.environ["ANAM_ARTIFACT_DIR"] = str(scratch / "artifacts")
    os.environ["ANAM_WORKSPACE_DIR"] = str(scratch / "workspace")
    config.reload()
    if config.data_dir().resolve() == real:
        sys.exit("refusing: the data directory still resolves to the repository's")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--passes", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--days", default="2026-09-22,2026-09-21,2026-09-01")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--arms", default=",".join(ARMS),
                    help="comma list from: " + ", ".join(ARMS) + " (one arm gives 3 samples/pass)")
    args = ap.parse_args()

    from program import config
    if config.PROJECT_ROOT.resolve() in args.out.resolve().parents:
        sys.exit("refusing: --out is inside the repository; entries quote real conversations")
    _prepare_environment(args.store)

    from program.integrity import gate  # noqa: F401  (imported after the environment)
    from program.memory import db
    from program.reflection import journal

    db.init_databases()
    days = [date.fromisoformat(d) for d in args.days.split(",")]
    arms = args.arms.split(",")
    if not set(arms) <= set(ARMS):
        sys.exit(f"unknown arm in --arms {args.arms}")
    pairs = [(d, arm) for d in days for arm in arms]

    done = 0
    if args.out.exists():
        if not args.resume:
            sys.exit(f"{args.out} exists; pass --resume to continue it")
        passes_seen = {}
        for line in args.out.read_text().splitlines():
            if line:
                row = json.loads(line)
                passes_seen[row["pass"]] = passes_seen.get(row["pass"], 0) + 1
        done = sum(1 for n in passes_seen.values() if n == len(pairs))
        # A partial pass is discarded and replayed, so the order is the uninterrupted one.
        kept = [line for line in args.out.read_text().splitlines()
                if line and json.loads(line)["pass"] <= done]
        args.out.write_text("".join(line + "\n" for line in kept))

    rng = random.Random(args.seed)
    previous = None
    order_by_pass = []
    for _ in range(args.passes):
        order = pairs[:]
        rng.shuffle(order)
        while previous is not None and order[0] == previous:
            rng.shuffle(order)
        order_by_pass.append(order)
        previous = order[-1]

    now = datetime.now(timezone.utc)
    with args.out.open("a") as out:
        for number, order in enumerate(order_by_pass, start=1):
            if number <= done:
                continue
            for day, arm in order:
                started = time.monotonic()
                row = {"pass": number, "day": day.isoformat(), "arm": arm}
                try:
                    prepared = journal.prepare(day, clause=arm.endswith("clause"), now=now)
                    generated = journal.generate(prepared)
                    row.update(
                        text=generated.text,
                        verdict=generated.verdict.to_dict(),
                        messages_in=prepared.messages_in,
                        messages_omitted=prepared.messages_omitted,
                        messages_clipped=prepared.messages_clipped,
                        estimated_prompt_tokens=prepared.estimated_prompt_tokens,
                        error=None,
                    )
                except Exception as exc:  # noqa: BLE001 - recorded, never counted as an entry
                    row.update(text=None, verdict=None, error=f"{type(exc).__name__}: {exc}")
                row["seconds"] = round(time.monotonic() - started, 1)
                out.write(json.dumps(row) + "\n")
                out.flush()
                print(f"pass {number}/{args.passes} {day} {arm:14} "
                      f"{row['seconds']}s {row['error'] or ''}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
