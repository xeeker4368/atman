"""Second-person resolution for the fabrication gate. Design revision 4, F16–F22.

The measured defect (d): the classifier reads *"You said you'd been thinking
about it since yesterday"* — the entity accurately describing the **person's**
continuity — as a claim about itself, and flags it. It did so under every ground
truth tried, including `architecture.md`, and giving the classifier an explicit
note about who "you" refers to made it slightly **worse** (task 3.6a, E1:
45% -> 50% false positives).

This removes the ambiguity instead of explaining it. Measured over 14 dev cases,
5 runs each: **false positives 50% -> 0%, false negatives 25% -> 0%**, no
regression, and one unpredicted gain — a first-person fabrication hidden inside a
quotation goes from missed 0/5 to caught 5/5.

No speaker input, deliberately
==============================
`PLACEHOLDER` is a constant. The gate is never told who is speaking, so the
judged text for a given answer is byte-identical whoever sent it — a property of
the system rather than a hope about model behaviour. A real name measured
exactly the same (0/50, 0/20), so nothing is bought by knowing it, and two
things are lost: the judged text would vary by speaker, and the frozen eval cases
carry no speaker, so they could not exercise this at all.

What is deliberately not rewritten
==================================
* **The situation block.** Its "you" is the *entity* — "You were not running
  during that time" — so rewriting it would invert the turn's own ground truth
  into a claim about a person.
* **The trace**, for the same reason: it is evidence, not the judged statement.
* **Quoted spans.** A quoted "you" is the person quoting the entity back at it,
  so rewriting inside quotation marks reassigns the referent. Measured: leaving
  them alone costs nothing (0/50, 0/20 either way), so the faithful option wins
  on provenance.
* **Anything outside the classifier call.** The deterministic rules read the
  original answer, and every stored finding cites the original — see
  :func:`program.integrity.gate.semantic_findings`.
"""

from __future__ import annotations

import re

#: What second person resolves to. A noun phrase, not a name.
#:
#: "the person" and "the user" measured identically (0/50 false positives, 0/20
#: false negatives each), so the tie was broken on vocabulary: `architecture.md`
#: speaks of "other people", and this project does not call the household
#: "users".
PLACEHOLDER = "the person"

#: Longest forms first, so "you've" is not eaten by "you". Verb agreement is
#: handled because "you have" and "the person has" are not interchangeable.
_REWRITES: tuple[tuple[str, str], ...] = (
    (r"\byou've\b", f"{PLACEHOLDER} has"),
    (r"\byou're\b", f"{PLACEHOLDER} is"),
    (r"\byou'll\b", f"{PLACEHOLDER} will"),
    (r"\byou have\b", f"{PLACEHOLDER} has"),
    (r"\byou were\b", f"{PLACEHOLDER} was"),
    (r"\byou weren't\b", f"{PLACEHOLDER} was not"),
    (r"\byou are\b", f"{PLACEHOLDER} is"),
    (r"\byou aren't\b", f"{PLACEHOLDER} is not"),
    (r"\byou don't\b", f"{PLACEHOLDER} does not"),
    (r"\byou do\b", f"{PLACEHOLDER} does"),
    (r"\byou'd\b", f"{PLACEHOLDER} had"),
    (r"\byours\b", f"{PLACEHOLDER}'s"),
    (r"\byourself\b", PLACEHOLDER),
    (r"\byour\b", f"{PLACEHOLDER}'s"),
    (r"\byou\b", PLACEHOLDER),
)

#: Quoted spans, straight and curly. Left untouched — see the module docstring.
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”')

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


def _rewrite_span(text: str) -> str:
    for pattern, replacement in _REWRITES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def rewrite(text: str) -> str:
    """Resolve second person to :data:`PLACEHOLDER`, leaving quotations alone.

    **A documented limit, measured rather than discovered later:** ``you'd`` is
    ambiguous between *had* and *would* and this resolves it to *had*, so
    "if you'd like" becomes "if the person had like". Telling the two apart needs
    a lexicon, and a partial one is an uncalibrated guess. The measured cost is
    legibility, not accuracy — ungrammatical output changed no verdict across 14
    cases — and the frozen eval set carries a case for it so the limit sits in
    the measurement of record.

    **Second, also documented:** a generic "you" (advice to anyone) becomes a
    claim about one person. No deterministic test separates the two. Measured
    harmless.
    """
    out, last = [], 0
    for match in _QUOTED.finditer(text):
        out.append(_rewrite_span(text[last:match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(_rewrite_span(text[last:]))
    return "".join(out)


def rewrite_sentences(text: str) -> list[tuple[str, str]]:
    """``[(original_sentence, rewritten_sentence), ...]``, in order.

    Sentence-wise because the verdict has to map **back**: the classifier quotes
    a phrase from the rewritten text, and what gets stored must be the sentence
    the entity actually wrote. See F18.
    """
    return [(sentence, rewrite(sentence))
            for sentence in _SENTENCE_SPLIT.split(text) if sentence.strip()]


def original_for(phrase: str, pairs: list[tuple[str, str]]) -> str | None:
    """The original sentence a quoted phrase came from, or ``None``.

    ``None`` rather than a best guess: the classifier may paraphrase or quote
    across a sentence boundary, and a wrong citation in the permanent record is
    worse than no citation. **Never returns rewritten text.**

    **An ambiguous phrase is also ``None``**, and that is the same rule rather than
    a new one. This used to return the *first* match in document order, which is a
    best guess wearing a lookup's clothes. Quoted phrases are often short — a date,
    a name, "since yesterday" — so a phrase occurring in two sentences was
    attributed to whichever came first. Two things went wrong with that.

    The citation stored in ``messages.integrity_check`` could name a sentence the
    classifier was not talking about, which is exactly what the paragraph above
    forbids: the no-match case was guarded and the ambiguous-match case was not.

    Worse, ``gate._drop_tool_claim_findings`` decides *"is this finding about a tool
    claim?"* **from this attribution**. A real identity finding whose phrase also
    appeared in an earlier tool-outcome sentence was therefore discarded, and the
    verdict came back ``CLEAN``. Reproduced: *"The page says the shop moved since
    yesterday. I have been thinking about it since yesterday."* — a genuine
    continuity fabrication, dropped because ``since yesterday`` resolved to the tool
    sentence.

    ``None`` routes both into the policy the gate already documents for an
    unattributable finding: keep it unless the whole answer is tool claims, and say
    plainly that attribution failed.

    **Identical sentences are not ambiguous** and are deduplicated before counting —
    if a phrase matches two byte-identical sentences, citing either is equally
    correct, and they cannot disagree about whether they are a tool claim.
    """
    needle = " ".join(phrase.split()).casefold()
    if not needle:
        return None
    matched = [
        original.strip() for original, rewritten in pairs
        if needle in " ".join(rewritten.split()).casefold()
    ]
    distinct = list(dict.fromkeys(matched))
    return distinct[0] if len(distinct) == 1 else None
