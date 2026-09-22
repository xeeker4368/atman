"""Second-person resolution. Design revision 4, F16-F22.

Deterministic throughout — no model, no Ollama, no scripted classifier. That is
the point of the mechanism: its behaviour is checkable exactly, and its measured
accuracy gain (50% -> 0% false positives on the defect it exists for) came from
removing an ambiguity rather than from asking a model to reason past one.
"""

from __future__ import annotations

import inspect
import json

import pytest

from program.integrity import gate, pronouns
from program.integrity.pronouns import PLACEHOLDER


@pytest.mark.parametrize("original, expected", [
    ("You said it yesterday.", f"{PLACEHOLDER} said it yesterday."),
    ("You've gotten better at this.", f"{PLACEHOLDER} has gotten better at this."),
    ("You're right about that.", f"{PLACEHOLDER} is right about that."),
    ("You were working on it.", f"{PLACEHOLDER} was working on it."),
    ("You have the notes.", f"{PLACEHOLDER} has the notes."),
    ("Your notes say 18 grams.", f"{PLACEHOLDER}'s notes say 18 grams."),
    ("You don't persist.", f"{PLACEHOLDER} does not persist."),
    ("Ask yourself why.", f"Ask {PLACEHOLDER} why."),
])
def test_second_person_resolves_with_verb_agreement(original, expected):
    """"you have" and "the person has" are not interchangeable, so agreement is
    part of the rewrite rather than left to read as broken English."""
    assert pronouns.rewrite(original) == expected


def test_first_person_is_untouched():
    """The entity's own claims are what the gate is for. Rewriting them would
    destroy the thing being judged."""
    answer = "I kept working on it in the background while you were away."

    assert pronouns.rewrite(answer) == (
        f"I kept working on it in the background while {PLACEHOLDER} was away.")


def test_text_with_no_second_person_is_returned_unchanged():
    assert pronouns.rewrite("Dublin is the capital of Ireland.") == (
        "Dublin is the capital of Ireland.")


@pytest.mark.parametrize("quoted", [
    'You asked, "have you been thinking about it?"',
    'You said, "you have been working on it all night."',
    "“you have been running all night,” they said.",
])
def test_quoted_second_person_is_left_alone(quoted):
    """A quoted "you" is the person quoting the entity back at it, so rewriting
    inside quotation marks reassigns the referent. Measured: preserving them
    costs nothing (0/50 false positives either way), so the faithful option
    wins."""
    import re

    rewritten = pronouns.rewrite(quoted)
    spans = re.findall(r'"[^"]*"|“[^”]*”', quoted)

    assert spans, "the case has no quoted span to preserve"
    for span in spans:
        assert span in rewritten, "the quoted referent was reassigned"


def test_the_text_outside_a_quotation_is_still_rewritten():
    rewritten = pronouns.rewrite('You said, "you were right."')

    assert rewritten.startswith(f"{PLACEHOLDER} said")
    assert '"you were right."' in rewritten


# --- the documented limits, as checked properties ----------------------------


def test_youd_resolves_to_had_and_that_is_a_known_limit():
    """`you'd` is ambiguous between *had* and *would*. Resolving it needs a
    lexicon; a partial one is an uncalibrated guess. Measured cost is
    legibility, not accuracy — and the frozen eval set carries a case for it."""
    assert pronouns.rewrite("You'd been thinking about it") == (
        f"{PLACEHOLDER} had been thinking about it")
    assert pronouns.rewrite("Let me know if you'd like the recipe.") == (
        f"Let me know if {PLACEHOLDER} had like the recipe.")


def test_generic_you_becomes_a_claim_about_one_person_and_that_is_known():
    """No deterministic test separates advice-to-anyone from address-to-someone.
    Measured harmless; recorded so it is a checked property, not a surprise."""
    assert pronouns.rewrite("You can't pull a good shot without a grinder.") == (
        f"{PLACEHOLDER} can't pull a good shot without a grinder.")


# --- no speaker input, by construction ---------------------------------------


def test_nothing_in_this_module_takes_a_speaker():
    """The live remainder of revision 4's recommendation about
    `test_the_gate_takes_no_actor`. That test stays a tripwire on the gate's
    signature; this asserts the property the placeholder actually buys — the
    judged text cannot vary by who is speaking, because no speaker reaches it."""
    for fn in (pronouns.rewrite, pronouns.rewrite_sentences, pronouns.original_for):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"speaker", "actor", "name", "user", "user_id", "role"}

    assert isinstance(PLACEHOLDER, str) and PLACEHOLDER


def test_the_same_answer_resolves_identically_every_time():
    answer = "You said you'd been thinking about it since yesterday."

    assert pronouns.rewrite(answer) == pronouns.rewrite(answer)


# --- sentence pairing, which is what makes the mapping back possible ---------


def test_sentences_pair_up_in_order():
    pairs = pronouns.rewrite_sentences("You said X. I was not running. You're right.")

    assert [original for original, _ in pairs] == [
        "You said X.", "I was not running.", "You're right."]
    assert pairs[1][0] == pairs[1][1], "a sentence with no second person is unchanged"


def test_a_phrase_maps_back_to_the_sentence_the_entity_wrote():
    pairs = pronouns.rewrite_sentences("You said X. I was not running.")

    assert pronouns.original_for(f"{PLACEHOLDER} said X", pairs) == "You said X."


def test_an_unmatched_phrase_maps_to_none_not_a_guess():
    """A wrong citation in a permanent record is worse than no citation."""
    pairs = pronouns.rewrite_sentences("You said X.")

    assert pronouns.original_for("something never said", pairs) is None
    assert pronouns.original_for("", pairs) is None


# --- the gate's side of the contract (F18) -----------------------------------


@pytest.fixture
def capture(monkeypatch):
    seen = {}

    def fake(prompt, *a, **k):
        seen["prompt"] = prompt
        return "CONSISTENT"

    monkeypatch.setattr(gate.classifier, "classify", fake)
    return seen


def test_the_classifier_sees_resolved_text(capture):
    gate.semantic_findings("You said you'd been thinking about it since yesterday.")

    assert PLACEHOLDER in capture["prompt"]
    assert "You said" not in capture["prompt"]


def test_the_situation_block_is_never_rewritten(capture):
    """THE TRAP. The block's "you" is the entity — "You were not running during
    that time" — so rewriting it would invert the turn's own ground truth into a
    claim about a person."""
    block = ("It has been 14 hours since the last message from Lyle.\n"
             "You were not running during that time. The gap holds no experience.")

    gate.semantic_findings("You said it yesterday.", situation=block)

    assert "You were not running during that time." in capture["prompt"]


def test_the_deterministic_rules_read_the_original_answer(monkeypatch):
    """Rewriting changes who a sentence is about, and the rules match claim
    verbs; they must see what the entity wrote."""
    monkeypatch.setattr(gate.classifier, "classify", lambda *a, **k: "CONSISTENT")
    answer = "You asked me to check, so I searched the web and found it."

    verdict = gate.check(answer, [])

    assert [f.rule for f in verdict.findings] == [
        f.rule for f in gate.structural_findings(answer, [])] == ["unrun_tool"]


def test_a_finding_cites_the_original_sentence_not_the_rewrite(monkeypatch):
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: f"CONTRADICTS\n- {PLACEHOLDER} said it yesterday | "
                        f"the system has no memory of that",
    )

    [finding] = gate.semantic_findings("You said it yesterday.")

    assert finding.evidence == "You said it yesterday."
    assert PLACEHOLDER not in (finding.detail or "")


def test_no_rewritten_text_reaches_the_stored_verdict(monkeypatch):
    """The property, asserted rather than intended: a token introduced by the
    rewrite must appear nowhere in what `messages.integrity_check` would hold."""
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: f"CONTRADICTS\n- {PLACEHOLDER} has gotten better | "
                        f"the system does not improve",
    )

    stored = gate.check("You've gotten better at this since last month.", []).to_json()

    assert PLACEHOLDER not in stored
    assert json.loads(stored)["findings"][0]["evidence"] == (
        "You've gotten better at this since last month.")


def test_an_unitemised_contradiction_cites_nothing_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(gate.classifier, "classify", lambda *a, **k: "CONTRADICTS")

    [finding] = gate.semantic_findings("You said it yesterday.")

    assert finding.evidence is None


# --- An ambiguous phrase is unattributable, not first-match ------------------
#
# `original_for` used to return the FIRST sentence whose rewritten form contained
# the phrase. Two harms followed, and the second is the one that mattered:
# `gate._drop_tool_claim_findings` decides "is this finding about a tool claim?"
# from this attribution, so a real identity finding whose phrase also appeared in
# an earlier tool-outcome sentence was DISCARDED and the verdict came back clean.


def test_a_phrase_in_two_sentences_is_unattributable_not_first_match():
    answer = ("The page says the shop moved since yesterday. "
              "I have been thinking about it since yesterday.")
    pairs = pronouns.rewrite_sentences(answer)

    assert pronouns.original_for("since yesterday", pairs) is None, (
        "an ambiguous phrase must not be attributed to whichever sentence came "
        "first — that is a guess wearing a lookup's clothes"
    )


def test_an_unambiguous_phrase_still_resolves():
    """The fix must not turn every attribution into None."""
    answer = "The page says the shop moved. I have been thinking it over all night."
    pairs = pronouns.rewrite_sentences(answer)

    assert pronouns.original_for("thinking it over", pairs) == (
        "I have been thinking it over all night.")
    assert pronouns.original_for("the shop moved", pairs) == (
        "The page says the shop moved.")


def test_identical_sentences_are_not_ambiguous():
    """Citing either is equally correct, and they cannot disagree about whether
    they are a tool claim — so a repeat is deduplicated rather than refused."""
    answer = "I was not running. I was not running."
    pairs = pronouns.rewrite_sentences(answer)

    assert pronouns.original_for("not running", pairs) == "I was not running."


def test_an_ambiguous_phrase_no_longer_loses_a_real_identity_finding():
    """The end-to-end consequence, through the real enforcement.

    The answer mixes a tool-outcome sentence with a continuity fabrication, and
    both contain the phrase the classifier quotes. Under first-match attribution
    the finding resolved to the tool sentence and `_drop_tool_claim_findings`
    discarded it. Now it is unattributable, so the documented policy applies: in a
    mixed answer an unattributable finding is KEPT.
    """
    answer = ("The page says the shop moved since yesterday. "
              "I have been thinking about it since yesterday.")
    finding = gate.Finding(
        rule="identity_contradiction",
        claim_class=gate.ClaimClass.IDENTITY,
        confidence=gate.Confidence.JUDGED,
        detail="the system does not think between replies",
        evidence=pronouns.original_for(
            "since yesterday", pronouns.rewrite_sentences(answer)),
    )

    kept, dropped = gate._drop_tool_claim_findings([finding], answer, [])

    assert len(kept) == 1, "a real identity finding must survive an ambiguous phrase"
    assert dropped == 0


def test_the_enforcement_still_drops_it_when_the_whole_answer_is_tool_claims():
    """The other half of the documented policy, unchanged: with nothing else the
    finding could be about, an unattributable one is still dropped."""
    answer = "The page says the shop moved. The record says it closed."
    finding = gate.Finding(
        rule="identity_contradiction",
        claim_class=gate.ClaimClass.IDENTITY,
        confidence=gate.Confidence.JUDGED,
        detail="whatever the classifier thought",
        evidence=None,
    )

    kept, dropped = gate._drop_tool_claim_findings([finding], answer, [])

    assert kept == [] and dropped == 1
