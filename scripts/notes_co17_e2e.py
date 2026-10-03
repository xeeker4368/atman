#!/usr/bin/env python3
"""CO17 end to end: what production writes with the exclusion in place (`docs/CORRECTION_DESIGN.md` CO17).

    COREAL_TAG=after python -m scripts.notes_co15_real capture      # the 12 conversations, re-run
    python -m scripts.notes_co17_e2e controls                        # the two control kinds
    python -m scripts.notes_co17_e2e read                            # read both stores, no model call

The 12 two-turn conversations are re-run through the real loop with correction recording on (it is
on in every turn), then **the `supersedes` table is read** (no resampling): the expected result is 0
rows whose superseded message is a turn-1 claim, and that no entity candidate is offered at turn 2
(`corrections.candidates()`, read-only on the kept store). Two controls on a fresh store:

* **a genuine correction and no search** must still link;
* **an earlier entity message with a `web_search` that returned hits** must still be a candidate.
"""

from __future__ import annotations

# ruff: noqa: E501
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import os

from scripts import notes_live as L  # noqa: E402

#: ``CO17_TAG`` keeps a re-run (after a later change) from overwriting an earlier one.
_TAG = os.environ.get("CO17_TAG", "")
OUT = L.ROOT / f"co17_e2e{('_' + _TAG) if _TAG else ''}.json"
CONTROL_CONVERSATIONS = (
    # (kind, turn 1, turn 2)
    ("genuine-correction", "Our boiler pressure should sit at 1.2 bar when it is cold.", "I got that wrong, sorry: it should sit at 1.5 bar when it is cold."),
    ("genuine-correction", "The market stall opens at nine on Saturdays.", "Correction to what I said: it opens at eight, not nine."),
    ("genuine-correction", "The fig needs repotting every year.", "No, that's wrong: it only needs repotting every three years."),
    ("web-search-hits", "Please look it up on the web: what is the best way to descale a kettle?", "Thanks. And how do I clean a coffee grinder?"),
    ("web-search-hits", "Search the web for how often a combi boiler should be serviced.", "OK, and what pressure should it sit at?"),
)


def controls():
    w = L.setup("scratch-co17-controls" + (f"-{_TAG}" if _TAG else ""), fresh=True)
    rows = []
    for i, (kind, t1, t2) in enumerate(CONTROL_CONVERSATIONS):
        name, uid, adm = (("Lyle", w["lyle"], True), ("Jodie", w["jodie"], False))[i % 2]
        act = L.actor(uid, name, adm)
        o1 = L.run_turn(act, t1)
        o2 = L.run_turn(act, t2, o1.conversation_id)
        rows.append({"kind": kind, "conversation": o1.conversation_id, "turn1_reply": o1.content,
                     "turn1_trace": L.trace_summary(o1.trace), "turn2_reply": o2.content[:300]})
        print(i + 1, kind, [e["tool"] for e in o1.trace if e.get("tool")], file=sys.stderr, flush=True)
    OUT.with_name("co17_e2e_controls.json").write_text(json.dumps(rows, indent=1))


def _read_store(store: str):
    import os
    base = L.ROOT / store
    for k, s in (("ANAM_DATA_DIR", "data"), ("ANAM_BACKUP_DIR", "backups"),
                 ("ANAM_ARTIFACT_DIR", "artifacts"), ("ANAM_WORKSPACE_DIR", "workspace")):
        os.environ[k] = str(base / s)
    from program import config
    config.reload()
    from program.integrity import corrections
    c = sqlite3.connect(f"file:{base}/data/working.db?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    out = []
    for conv in c.execute("SELECT id, user_id FROM conversations ORDER BY started_at").fetchall():
        msgs = c.execute("SELECT id, role, content, tool_trace FROM messages WHERE conversation_id = ? ORDER BY timestamp", (conv["id"],)).fetchall()
        if len(msgs) < 4:
            continue
        pool = corrections.candidates(conv["user_id"], conv["id"], [], exclude_message_ids=(msgs[2]["id"], msgs[3]["id"]))
        links = [dict(r) for r in c.execute(
            "SELECT s.*, m.role AS superseded_role FROM supersedes s JOIN messages m ON m.id = s.superseded_message_id "
            "WHERE m.conversation_id = ?", (conv["id"],))]
        out.append({"conversation": conv["id"][:8], "turn1_user": msgs[0]["content"][:70],
                    "turn1_reply": msgs[1]["content"][:110],
                    "turn1_tools": [(e["tool"], e["outcome"], (e.get("value") or "")[:30]) for e in json.loads(msgs[1]["tool_trace"] or "[]") if e.get("tool")],
                    "entity_candidates_at_turn2": sum(1 for p in pool if p.role == "assistant"),
                    "turn1_reply_in_pool": any(p.message_id == msgs[1]["id"] for p in pool),
                    "links": [{"superseded_role": ln["superseded_role"], "superseded": ln["superseded_message_id"][:8], "superseding": ln["superseding_message_id"][:8], "state": ln["replacement"]} for ln in links],
                    "links_on_turn1_reply": len([ln for ln in links if ln["superseded_message_id"] == msgs[1]["id"]])})
    return out


def read():
    res = {s: _read_store(s) for s in sys.argv[2:]}
    OUT.write_text(json.dumps(res, indent=1))
    for store, rows in res.items():
        print(f"== {store}: {len(rows)} conversations")
        for r in rows:
            print(f"  {r['conversation']} tools={r['turn1_tools']} entity-candidates@turn2={r['entity_candidates_at_turn2']} turn1-reply-in-pool={r['turn1_reply_in_pool']} links={r['links']}")
            print(f"     {r['turn1_user']!r} -> {r['turn1_reply']!r}")
        print(f"   links written ON a turn-1 entity reply: {sum(r['links_on_turn1_reply'] for r in rows)} of {len(rows)} conversations")


if __name__ == "__main__":
    controls() if sys.argv[1] == "controls" else read()
