"""Resolving a quote to the message it came from. Notes piece 3; N4 as amended 2026-10-02.

The model never sees message ids, so evidence arrives as **short exact quotes** and this turns
each into a message id, or refuses. A fabricated quote therefore cannot enter a proposal. In order:

1. **Normalise both sides**: collapse whitespace, fold curly quotes and apostrophes, ignore case.
   Nothing looser: no stemming, no fuzzy match. A paraphrase is not a quote.
2. **A minimum length** (``notes.min_quote_chars`` and ``notes.min_quote_words``), checked before
   any search.
3. **Prefer what the entity was shown this turn.** The quote is matched first among
   ``origin.context_message_ids``; only if it matches nothing there is the rest of the store
   searched.
4. **Uniqueness within the tier where it matched.** Several matches are refused, with one
   exception: every match the **same full text from the same user**, which is one person's one
   piece of evidence, recorded as the most recent with its count. The same words from two people
   are two people's evidence, and are refused.
5. **Only a person's words**: matching is restricted to ``role = 'user'`` messages (N17 #23). A
   quote that appears only in the entity's own reply is refused as such, so the entity cannot cite
   itself.

The prefilter for the store tier is a ``LIKE`` on the quote's longest plain word followed by the
exact normalised check in Python. N4 named ``chunks_fts`` for this; that index misses messages
chunking has not yet sealed (the open trailing group), and the evidence for a proposal made this
turn is exactly such a message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from program import config
from program.memory import notes
from program.origin import OriginContext
from program.tools import note_texts as texts

_CURLY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_CURLY).lower()).strip()


@dataclass(frozen=True)
class Resolved:
    quote: str
    message_id: str
    tier: str            # "context" or "store"
    identical_count: int

    def to_dict(self) -> dict:
        return {"quote": self.quote, "message_id": self.message_id, "tier": self.tier,
                "identical_count": self.identical_count}


def _longest_word(normalised: str) -> str:
    words = re.findall(r"[a-z0-9]+", normalised)
    return max(words, key=len) if words else normalised.split(" ")[0]


def _matching(rows, needle: str):
    return [r for r in rows if needle in normalise(r["content"])]


def _pick(matches, quote: str, tier: str) -> Resolved:
    """N4 step 4's uniqueness rule within one tier."""
    if len(matches) == 1:
        return Resolved(quote, matches[0]["id"], tier, 1)
    same_text = len({normalise(m["content"]) for m in matches}) == 1
    same_user = len({m["user_id"] for m in matches}) == 1
    if same_text and same_user:
        newest = max(matches, key=lambda m: (m["timestamp"], m["id"]))
        return Resolved(quote, newest["id"], tier, len(matches))
    raise texts.proposal_refused(texts.quote_ambiguous(len(matches)))


def resolve_quote(quote: str, origin: OriginContext) -> Resolved:
    clean = quote.strip()
    needle = normalise(clean)
    if (len(needle) < config.notes_min_quote_chars()
            or len(needle.split(" ")) < config.notes_min_quote_words()):
        raise texts.proposal_refused(texts.quote_too_short(
            config.notes_min_quote_chars(), config.notes_min_quote_words()))

    in_context = [r for r in notes.messages_by_ids(sorted(origin.context_message_ids))]
    persons = _matching([r for r in in_context if r["role"] == "user"], needle)
    if persons:
        return _pick(persons, clean, "context")

    stored = notes.messages_containing(_longest_word(needle), ("user",))
    context_ids = origin.context_message_ids
    persons = _matching([r for r in stored if r["id"] not in context_ids], needle)
    if persons:
        return _pick(persons, clean, "store")

    # No person said it. Say why: did the entity?
    replies = _matching([r for r in in_context if r["role"] == "assistant"], needle) or \
        _matching(notes.messages_containing(_longest_word(needle), ("assistant",)), needle)
    if replies:
        raise texts.proposal_refused(texts.QUOTE_NOT_A_PERSONS_WORDS)
    raise texts.proposal_refused(texts.QUOTE_NO_MATCH)
