"""A pending note proposal written the way the note pass writes one (Notes v2).

Since ``note_propose`` was removed, the only writer of a proposal is the pass
(``program/memory/note_pass.py``). Tests that need a pending proposal without a model call use
this: the quote is resolved by the real resolver, the identity verdict is the real
``gate.check_identity`` (the caller scripts the classifier), and the row is written by the pass's
own ``_write``, with its ``note_passes`` row, in one transaction.
"""

from __future__ import annotations

import json

from program.integrity import gate
from program.memory import db, note_pass
from program.tools import note_quotes


def pending_proposal(conversation_id: str, user_id: str, *, action: str = "add",
                     subject_kind: str = "person", subject: str = "Jodie",
                     text: str | None = "Takes her coffee with oat milk.",
                     line: str | None = "Takes her coffee with oat milk.",
                     quote: str = "Jodie takes her coffee with oat milk",
                     context_ids: frozenset[str] = frozenset(),
                     target_note_id: str | None = None,
                     untrusted: list[str] | None = None) -> str:
    """Write one pending proposal and return its id."""
    resolved = note_quotes.resolve_quote(quote, context_ids)
    verdict = gate.check_identity(text) if text else None
    row = {
        "id": db.new_id(), "action": action, "target_note_id": target_note_id,
        "subject_kind": subject_kind, "subject": subject,
        "text": text if action != "retire" else None,
        "line": line if action != "retire" else None,
        "evidence": json.dumps([resolved.to_dict()]),
        "conversation_id": conversation_id, "user_message_id": resolved.message_id,
        "user_id": user_id,
        "untrusted_context": json.dumps(untrusted if untrusted is not None else []),
        "integrity_check": verdict.to_json() if verdict is not None else None,
    }
    note_pass._write(conversation_id, "proposals", [row], pieces=1, replies=["(test)"],
                     cut_pieces=[], cut_blocks=[], set_aside=[], notes_list_cut=False,
                     error=None, seconds=0.0)
    return row["id"]
