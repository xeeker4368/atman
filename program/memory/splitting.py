"""Splitting text that exceeds the embedding budget.

Pure text logic, no store access, so it can be tested exhaustively on its own.

This path is meant to be **rare**. The chunk boundary rule targets 2500
characters against a 5000-character embedding ceiling, so an ordinary chunk
never comes near it. Splitting is reached only when a single turn exceeds the
ceiling by itself — a pasted document, a long code block.

Boundary preference, in order: paragraph breaks, then line breaks, then sentence
ends, then whitespace, then a hard character cut. Each step down is a small loss
of coherence, and the last one is genuinely bad — a mid-word cut creates
non-words that pollute the lexical index — so it is a last resort rather than
the default.

**Hard cuts slice in ``str`` space, never bytes.** Python string slicing cannot
split a multi-byte UTF-8 character; byte slicing corrupts emoji, CJK and
accented characters at the cut point. This constraint is carried from the
reference build, whose comment on it was correct and hard-won, even though none
of its code is.
"""

from __future__ import annotations

import re

# Ordered widest-to-narrowest. Each is a regex whose matches are acceptable
# places to cut, and the cut is taken *after* the match.
_BOUNDARIES: tuple[tuple[str, str], ...] = (
    ("paragraph", r"\n\s*\n"),
    ("line", r"\n"),
    ("sentence", r"(?<=[.!?])\s+"),
    ("whitespace", r"\s+"),
)


def _last_boundary_before(text: str, limit: int) -> int | None:
    """Index to cut at, using the widest boundary available before ``limit``.

    Returns None when no boundary exists in range, meaning the caller must cut
    hard. Tries each boundary class in order and takes the *last* match within
    the limit, so a piece is as full as it can be without overflowing.
    """
    window = text[:limit]
    for _, pattern in _BOUNDARIES:
        matches = list(re.finditer(pattern, window))
        if matches:
            cut = matches[-1].end()
            if cut > 0:
                return cut
    return None


def split_text(text: str, budget: int) -> list[str]:
    """Split ``text`` into pieces each at most ``budget`` characters.

    Concatenating the pieces reproduces the input exactly — nothing is dropped
    and nothing is added, so a split chunk still contains everything the
    original message said. Text already within budget comes back as one piece.
    """
    if budget <= 0:
        raise ValueError(f"budget must be positive, got {budget}")
    if len(text) <= budget:
        return [text]

    pieces: list[str] = []
    remaining = text
    while len(remaining) > budget:
        cut = _last_boundary_before(remaining, budget)
        if cut is None:
            # No boundary anywhere in the window — an unbroken run longer than
            # the budget, realistically minified data. Cut hard, in str space.
            cut = budget
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        pieces.append(remaining)
    return pieces


def pack_lines(lines: list[tuple[str, str]], budget: int) -> list[list[tuple[str, str]]]:
    """Group ``(message_id, line)`` pairs into runs that each fit the budget.

    Keeps whole lines together wherever possible: a sub-unit that starts
    mid-message reads as a fragment. A single line longer than the budget is
    returned as its own run, for the caller to split with ``split_text``.
    """
    runs: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_len = 0

    for message_id, line in lines:
        line_len = len(line)
        if line_len > budget:
            if current:
                runs.append(current)
                current, current_len = [], 0
            runs.append([(message_id, line)])
            continue

        # +1 for the newline joining this line to the previous one.
        addition = line_len if not current else line_len + 1
        if current and current_len + addition > budget:
            runs.append(current)
            current, current_len = [], 0
            addition = line_len

        current.append((message_id, line))
        current_len += addition

    if current:
        runs.append(current)
    return runs
