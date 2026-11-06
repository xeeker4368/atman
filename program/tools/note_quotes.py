"""Resolving a pass quote to the person's messages it came from. N4, as amended by the
note-quote-check piece and its amendment (2026-11-05, 2026-11-06).

The caller is the note pass (``program/memory/note_pass.py``), which passes the closed
conversation's message ids. The model never sees message ids, so evidence arrives as **exact
quotes** and this turns each into message ids, or refuses. A fabricated quote therefore cannot
enter a proposal. In order:

1. **Strip** surrounding whitespace and wrapping quote marks (straight and curly).
2. **Split into fragments** on ellipses (two or more dots, or ``…``), on line breaks and on
   sentence ends (``.``, ``!`` or ``?`` followed by whitespace, closing quote marks allowed
   between). Each fragment is stripped the same
   way and loses trailing ``.``, ``!``, ``?`` and ``,``; empty fragments are dropped. The model
   quotes a person who spoke in several messages by joining the parts, with an ellipsis or as
   sentences, and adds end punctuation the person did not type (run 5).
3. **Every fragment appears inside one message a person wrote in this conversation**, after
   normalising both sides: whitespace runs to one space, curly quotes and apostrophes to straight,
   case to lower. Nothing looser: no stemming, no fuzzy match. A paraphrase is not a quote.
   Different fragments may come from different messages. The entity's own replies, tool output
   and note text never count, and no other conversation is searched.
4. **At least one fragment has** ``notes.min_fragment_words`` words or more. Shorter fragments
   are allowed beside it, and must still match.

A fragment found in several of the person's messages resolves to the most recent, with the
count of messages identical to it. The refusal names the fragment that failed when there is
more than one. The quote is stored as the model gave it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from program import config
from program.memory import notes
from program.tools import note_texts as texts

_CURLY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_QUOTE_MARKS = "\"'‘’“”"
_SPLIT = re.compile(r"\.{2,}|…|\r\n|\r|\n|(?<=[.!?])[\"'’”]*\s+")
_TRAILING = ".!?,"


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_CURLY).lower()).strip()


def _strip(text: str) -> str:
    """Surrounding whitespace and wrapping quote marks, repeatedly (`  "'x'" ` -> `x`)."""
    previous = None
    while previous != text:
        previous, text = text, text.strip().strip(_QUOTE_MARKS)
    return text


def _strip_fragment(text: str) -> str:
    """:func:`_strip`, and trailing sentence punctuation, repeatedly (`"ok."` -> `ok`)."""
    previous = None
    while previous != text:
        previous, text = text, _strip(text).rstrip(_TRAILING)
    return text


def fragments(quote: str) -> list[str]:
    """The quote's fragments as the model gave them, stripped, empty ones dropped."""
    return [f for f in (_strip_fragment(part) for part in _SPLIT.split(_strip(quote))) if f]


def _words(fragment: str) -> int:
    return sum(1 for w in normalise(fragment).split(" ") if re.search(r"\w", w))


@dataclass(frozen=True)
class Fragment:
    text: str            # as the model gave it, stripped of marks and trailing punctuation
    message_id: str
    identical_count: int


@dataclass(frozen=True)
class Resolved:
    quote: str           # as the model gave it
    fragments: tuple[Fragment, ...]

    @property
    def message_id(self) -> str:
        """The message the first fragment resolved to (the proposal's ``user_message_id``)."""
        return self.fragments[0].message_id

    def evidence(self) -> list[dict]:
        """The stored evidence: one entry per fragment, each carrying the whole quote."""
        return [{"quote": self.quote, "fragment": f.text, "message_id": f.message_id,
                 "tier": "context", "identical_count": f.identical_count}
                for f in self.fragments]


def _refuse(reason: str, index: int, parts: list[str]):
    if len(parts) > 1:
        reason = f"fragment {index} of {len(parts)} ({parts[index - 1]!r}): {reason}"
    raise texts.QuoteRefused(reason)


def resolve_quote(quote: str, context_message_ids: frozenset[str] = frozenset()) -> Resolved:
    """What ``quote`` came from, or :class:`note_texts.QuoteRefused` with the reason."""
    parts = fragments(quote)
    minimum = config.notes_min_fragment_words()
    if not parts:
        raise texts.QuoteRefused(texts.quote_too_short(minimum))

    rows = notes.messages_by_ids(sorted(context_message_ids))
    persons = [r for r in rows if r["role"] == "user"]
    replies = [r for r in rows if r["role"] == "assistant"]
    found: list[Fragment] = []
    for i, part in enumerate(parts, start=1):
        needle = normalise(part)
        matches = [r for r in persons if needle in normalise(r["content"])]
        if not matches:
            said_by_entity = any(needle in normalise(r["content"]) for r in replies)
            _refuse(texts.QUOTE_NOT_A_PERSONS_WORDS if said_by_entity else texts.QUOTE_NO_MATCH,
                    i, parts)
        newest = max(matches, key=lambda m: (m["timestamp"], m["id"]))
        same = sum(1 for m in matches if normalise(m["content"]) == normalise(newest["content"]))
        found.append(Fragment(part, newest["id"], same))
    if all(_words(part) < minimum for part in parts):
        reason = texts.quote_too_short(minimum)
        if len(parts) > 1:
            reason = f"all {len(parts)} fragments ({', '.join(map(repr, parts))}): {reason}"
        raise texts.QuoteRefused(reason)
    return Resolved(quote, tuple(found))
