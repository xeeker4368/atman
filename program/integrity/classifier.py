"""The classifier framework. Design revision 3, F13.

One place that builds a classification call, sends it, parses the reply, and
decides what an unusable reply means. The fabrication gate is its first
consumer; the correction/supersession classifier (task 3.3) is its second, and
this module is the "one classifier framework, two prompt variants" that
`BUILD_PLAN`'s Phase 3 row and decision #1 describe at the mechanism level.

What is shared, and what deliberately is not
============================================
**Shared**: the model, token budget and timeout settings; the request; the reply
grammar; and the rule that an unusable reply raises rather than passing.

**Not shared**: the prompt text, the ground-truth document, the findings
vocabulary, and the eval harness. Two consumers, two frozen case sets, two
measurements — because a correction classifier judging "does this message
correct that claim" has nothing in common with a fabrication gate's rubric
except the plumbing.

**Addressee context is deliberately absent.** Task 3.6a measured it as a
non-fix for the second-person misreading (45% -> 50% false positives, one case
getting worse), so it is not built in for the second consumer to inherit. If
3.3 needs speaker context for a different reason, that is 3.3's evidence to
produce.

The deadlock policy (F13)
=========================
A change *here* must be measured against **both** consumers' frozen case sets
before it lands. If one regresses, the change does not belong in this module —
it moves into the consumer that needs it. This layer is for what genuinely does
not differ.
"""

from __future__ import annotations

from dataclasses import dataclass

from program import config
from program.engine import ollama

#: The reply grammar, fixed rather than free text. A verdict word on the first
#: line, then zero or more ``- <phrase> | <fact>`` items. Free-form prose was
#: what made the v1 parser fragile and the token budget unmeasurable.
CONSISTENT = "CONSISTENT"
CONTRADICTS = "CONTRADICTS"


@dataclass(frozen=True)
class Verdict:
    """One parsed classifier reply."""

    contradicts: bool
    items: tuple[str, ...]
    raw: str
    #: The verdict word exactly as the reply gave it — ``CONTRADICTS``,
    #: ``CONTRADICTS-SELF``, ``CONTRADICTS-TOOL``, ``CONSISTENT``.
    #:
    #: **Deliberately uninterpreted here.** A consumer that defines suffixes
    #: reads them; this layer only reports what was said. The fabrication gate
    #: routes on ``-TOOL`` (revision 7); the correction classifier will not
    #: inherit that vocabulary (O18), and it does not have to, because nothing
    #: about it is encoded in this module.
    verdict_word: str = ""


def classify(prompt: str) -> str:
    """Send one classification call and return the raw reply.

    Every knob is read at call time from config, so a pinned classifier model
    takes effect without a restart and without this module holding state.
    """
    return ollama.chat_text(
        [{"role": "user", "content": prompt}],
        model=config.classifier_model(),
        options={"num_predict": config.classifier_num_predict()},
        timeout=config.classifier_timeout_seconds(),
    )


def parse(reply: str) -> Verdict:
    """Parse a reply, or raise.

    **An unusable reply is never a pass.** Silence is not consent: a model that
    answers neither verdict has not said the statement is fine, and treating it
    as clean would make "checked and passed" unfalsifiable. The caller turns the
    raise into ``unavailable``.
    """
    text = (reply or "").strip()
    if not text:
        raise ollama.OllamaResponseError("the classifier returned nothing")

    head = text.split()[0].upper().strip(".,:")
    if head.startswith(CONSISTENT):
        return Verdict(contradicts=False, items=(), raw=text, verdict_word=head)
    if not head.startswith("CONTRADICT"):
        raise ollama.OllamaResponseError(
            f"the classifier gave no usable verdict: {text[:120]!r}"
        )

    items = tuple(
        line.lstrip("-").strip()
        for line in text.splitlines()[1:]
        if line.strip().startswith("-") and line.strip("- ").strip()
    )
    return Verdict(contradicts=True, items=items, raw=text, verdict_word=head)
