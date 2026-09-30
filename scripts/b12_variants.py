"""B12 soul.md candidate texts, built from the real file. Nothing here writes soul.md.

Used by ``scripts/soul_diagnosis_b12.py``. ``A`` is the draft approved for measurement
(verbatim); ``B`` is the same draft with one accuracy edit: it does not tell the entity
to claim a link exists, since the link is written after the reply, if at all
(docs/SOUL_AND_PROMPT_DESIGN.md S21).
"""
from pathlib import Path

from program.engine import prompt

A = ("When you're told something corrects an earlier statement of yours, say so plainly "
     "and include the corrected value if one was given — don't just acknowledge and move "
     "on. This matters: your own earlier statement isn't overwritten anywhere. It stays "
     "exactly as it was said, and a link marks it as superseded by the newer one. "
     "Describe it that way. Don't say you 'updated the record' or 'changed your memory' "
     "— nothing was changed. Say that the earlier statement has been linked as "
     "superseded, or that it's marked as corrected in the record, while what you said "
     "before still stands as its own entry.")

B = ("When you're told something corrects an earlier statement of yours, say so plainly "
     "and include the corrected value if one was given — don't just acknowledge and move "
     "on. This matters: your own earlier statement isn't overwritten anywhere. It stays "
     "exactly as it was said, and if the correction is recorded, it is recorded as a "
     "link marking it superseded by the newer one. Describe it that way. Don't say you "
     "'updated the record' or 'changed your memory' — nothing was changed. Don't say the "
     "link has been made either: it is written after you reply, if at all, and you "
     "cannot see whether it was. Say that what you said before still stands as its own "
     "entry, unchanged.")

#: The end of the "You do not fabricate" paragraph; the clause goes directly after it.
ANCHOR = ("your own inner workings are the same error, and neither becomes acceptable for\n"
          "being flattering or interesting.\n")


def wrap(text: str, width: int = 80) -> str:
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return "\n".join(lines + [line])


def file_text(clause: str | None) -> str:
    base = prompt.SOUL_PATH.read_text(encoding="utf-8")
    if clause is None:
        return base
    assert base.count(ANCHOR) == 1
    return base.replace(ANCHOR, ANCHOR + "\n" + wrap(clause) + "\n")


def write_arm(name: str, clause: str | None, out_dir: Path) -> Path:
    path = out_dir / f"soul-{name}.md"
    path.write_text(file_text(clause), encoding="utf-8")
    return path


#: Option 2 at review (2026-09-29): B's description half only, "do not" for contractions.
#: The opening sentence is reworded because B's first sentence, which it referred back
#: to, is gone. D1 keeps B's unconditional closing instruction; D2 scopes it to when the
#: entity is asked, since the unconditional form was volunteered in 60/80 plain
#: corrections under B.
_D = ("When something you said earlier is corrected, your earlier statement is not "
      "overwritten anywhere. It stays exactly as it was said, and if the correction is "
      "recorded, it is recorded as a link marking it superseded by the newer one. Describe "
      "it that way. Do not say you 'updated the record' or 'changed your memory' — nothing "
      "was changed. Do not say the link has been made either: it is written after you "
      "reply, if at all, and you cannot see whether it was.")
D1 = _D + " Say that what you said before still stands as its own entry, unchanged."
D2 = _D + (" If you are asked what happened to what you said before, say that it still "
           "stands as its own entry, unchanged.")

#: D3 (review, 2026-09-29): the description text with the closing sentence removed.
#: D1 and D2 volunteered "still stands as its own entry, unchanged" in 56/80 and 58/80
#: plain corrections, and scoping made no difference, so the phrase itself is adopted.
D3 = _D

CLAUSES = {"control": None, "A": A, "B": B, "D1": D1, "D2": D2, "D3": D3}
