"""The unified fabrication gate. Design: `docs/FABRICATION_GATE_DESIGN.md`.

The deterministic half is exercised with no model at all — that is the point of
it being deterministic, and it is why its error rate can be measured offline.
The judged half is exercised against a scripted classifier so the parsing and
the failure states are pinned deterministically, plus live tests that skip
rather than fail when Ollama is absent.

**This file does not contain the eval harness.** That is `tests/test_gate_eval.py`
plus `eval/fabrication_gate/cases.toml`, and the accuracy question belongs
there — it was answered at task 3.6d, not here. These tests check that the
mechanism does what it says; they do not
establish that it is accurate enough to trust, and nothing here should be read
as if they did.

*Rewritten at task 3.6c for revision 3: the structural rules became claim-aware,
`Confidence.EXACT` became `DETERMINISTIC`, the classifier's ground truth became
`architecture.md`, and tool claims left its remit.*
"""

from __future__ import annotations

import json

import pytest

from program.engine import ollama
from program.integrity import classifier, gate
from program.integrity.gate import ClaimClass, Confidence, GateStatus

OK = "ok"
CALL_A, CALL_B, CALL_C = "a" * 32, "b" * 32, "c" * 32


def entry(tool, outcome, call_id=CALL_A, **extra):
    return {"call_id": call_id, "tool": tool, "outcome": outcome, **extra}


@pytest.fixture
def consistent(monkeypatch):
    monkeypatch.setattr(gate.classifier, "classify", lambda *a, **k: "CONSISTENT")


@pytest.fixture
def capture(monkeypatch):
    """Capture the prompt the classifier was given."""
    seen = {}

    def fake(prompt, *a, **k):
        seen["prompt"] = prompt
        return "CONSISTENT"

    monkeypatch.setattr(gate.classifier, "classify", fake)
    return seen


@pytest.fixture
def rubric():
    return gate.load_architecture()


# --- S1: invented ids, unchanged from v1 -------------------------------------


def test_an_id_not_in_the_trace_is_flagged():
    findings = gate.structural_findings(f"Saved as {CALL_B}.", [entry("web_search", OK)])

    assert [f.rule for f in findings] == ["invented_id"]
    assert findings[0].claim_class is ClaimClass.TOOL_OUTPUT
    assert findings[0].confidence is Confidence.DETERMINISTIC, "a rule, not a judgment"
    assert findings[0].evidence == CALL_B


def test_a_real_call_id_is_not_flagged():
    assert gate.structural_findings(
        f"Call {CALL_A} returned it.", [entry("web_search", OK)]
    ) == []


# --- v2: mentioning a tool is not claiming an outcome for it ------------------
#
# The whole of revision 3's F8. Each of the four measured false positives below
# was flagged by v1 and must not be flagged now.


@pytest.mark.parametrize("answer, trace", [
    ("web_search returned an error, so I don't have the hours.",
     [entry("web_search", "tool_error", error="boom")]),
    ("The web search failed, so I can't give you the opening hours.",
     [entry("web_search", "tool_error", error="boom")]),
    ("web_fetch timed out, so I can't tell whether the page was retrieved.",
     [entry("web_fetch", "timeout")]),
    ("The search took a moment to come back, but the library opens at 9.",
     [entry("web_search", OK)]),
])
def test_an_accurate_report_is_not_flagged(answer, trace):
    """v1 flagged all four: it asked whether the tool was *named*, not what the
    sentence *claimed*. Measured at 10/10 false positives (task 3.2)."""
    assert gate.structural_findings(answer, trace) == []


def test_naming_a_tool_without_asserting_an_outcome_says_nothing():
    assert gate.structural_findings("I could use web_search for that.", []) == []
    assert gate.structural_findings("Let me search the web.", []) == []


@pytest.mark.parametrize("answer", [
    "I ran web_search, and the top result says it opens at 9.",
    "I searched the web, and the top result says it opens at 9.",
])
def test_asserting_an_outcome_for_a_tool_that_never_ran_is_flagged(answer):
    """Both the identifier and the prose form. v1 caught only the first."""
    findings = gate.structural_findings(answer, [])

    assert [f.rule for f in findings] == ["unrun_tool"]
    assert findings[0].confidence is Confidence.DETERMINISTIC


def test_claimed_success_over_a_failed_call_is_flagged():
    findings = gate.structural_findings(
        "My search came back with the answer: it opens at 9.",
        [entry("web_search", "tool_error", error="boom")],
    )

    assert [f.rule for f in findings] == ["success_over_failure"]


def test_a_tool_that_failed_once_and_succeeded_once_is_not_flagged():
    """A retry that worked is not a fabrication to talk about."""
    assert gate.structural_findings(
        "memory_search found three records.",
        [entry("memory_search", "tool_error", CALL_A), entry("memory_search", OK, CALL_B)],
    ) == []


# --- timeout keeps its own rules, in both directions -------------------------


def test_claimed_success_over_a_timeout_has_its_own_rule():
    findings = gate.structural_findings(
        "web_fetch retrieved the page.", [entry("web_fetch", "timeout")]
    )

    assert [f.rule for f in findings] == ["success_over_timeout"]
    assert "unknown" in findings[0].detail


@pytest.mark.parametrize("answer", [
    "web_fetch failed, so there is nothing to read.",
    "The page fetch failed — it could not be retrieved, so there is nothing to read.",
])
def test_claimed_failure_over_a_timeout_is_flagged_in_both_forms(answer):
    """The direction a naive design loses, and the one the gate measured at 0/5
    before this rewrite: a timed-out call did not definitely fail."""
    findings = gate.structural_findings(answer, [entry("web_fetch", "timeout")])

    assert [f.rule for f in findings] == ["failure_over_timeout"]
    assert "unknown" in findings[0].detail
    assert "as unsupported" in findings[0].detail


def test_claimed_failure_over_a_successful_call_is_flagged():
    findings = gate.structural_findings(
        "The search failed, so I have nothing.", [entry("web_search", OK)]
    )

    assert [f.rule for f in findings] == ["failure_over_success"]


# --- the two parsing decisions that were found by running it -----------------


def test_returned_an_error_is_a_failure_claim_not_a_success_claim():
    """Precedence, narrowest first. An earlier ordering read this as success —
    the exact false positive the rewrite exists to remove."""
    assert gate.structural_findings(
        "web_search returned an error.", [entry("web_search", "tool_error")]
    ) == []
    assert gate.structural_findings(
        "web_search returned an error.", [entry("web_search", OK)]
    )[0].rule == "failure_over_success"


def test_a_passive_could_not_is_an_assertion_not_an_offer():
    """A bare modal list matched "could not be retrieved" and silenced the very
    claim these rules exist to catch. Offers are first person."""
    assert gate.structural_findings(
        "The page could not be retrieved.", [entry("web_fetch", "timeout")]
    )[0].rule == "failure_over_timeout"
    assert gate.structural_findings(
        "I could fetch the page for you.", [entry("web_fetch", "timeout")]
    ) == []


def test_a_negated_claim_says_nothing_rather_than_guessing():
    assert gate.structural_findings(
        "The search didn't fail; it just came back empty.", [entry("web_search", OK)]
    ) == []
    assert gate.structural_findings(
        "Searching my memory didn't turn up anything.", [entry("memory_search", OK)]
    ) == []


def test_the_known_limits_are_known(monkeypatch):
    """Both are cases S5/S6 in the frozen set and both are expected to fail
    there. Pinned here so the gap is a checked property, not a surprise."""
    assert gate.structural_findings("I checked online and it's confirmed.", []) == []
    assert gate.structural_findings(
        "I ran a web search. It came back with the hours.",
        [entry("web_search", "tool_error")],
    ) == []


def test_a_tool_with_no_aliases_is_still_matched_by_its_identifier():
    """A newly registered tool is never silently unwatched — only narrowly
    watched until someone adds vocabulary for it."""
    findings = gate.structural_findings(
        "image_generate returned the picture.", [], known_tools=("image_generate",)
    )

    assert [f.rule for f in findings] == ["unrun_tool"]


# --- The judged half ---------------------------------------------------------


def test_a_consistent_verdict_produces_no_findings(consistent, rubric):
    assert gate.semantic_findings("Dublin is the capital.", rubric) == []


def test_a_contradicts_verdict_is_itemised(monkeypatch, rubric):
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: "CONTRADICTS\n- been thinking since yesterday | not running between turns",
    )

    findings = gate.semantic_findings("I've been thinking since yesterday.", rubric)

    assert [f.rule for f in findings] == ["identity_contradiction"]
    assert findings[0].claim_class is ClaimClass.IDENTITY
    assert findings[0].confidence is Confidence.JUDGED, "a model call, not a rule"
    assert "not running" in findings[0].detail


def test_contradicts_without_itemisation_still_produces_a_finding(monkeypatch, rubric):
    """The verdict is the signal; the itemisation is detail."""
    monkeypatch.setattr(gate.classifier, "classify", lambda *a, **k: "CONTRADICTS")

    assert len(gate.semantic_findings("anything", rubric)) == 1


@pytest.mark.parametrize("reply", ["", "   ", "I'm not sure", "maybe?", "42"])
def test_an_unusable_reply_raises_rather_than_reading_as_clean(monkeypatch, rubric, reply):
    """Silence is not consent."""
    monkeypatch.setattr(gate.classifier, "classify", lambda *a, **k: reply)

    with pytest.raises(ollama.OllamaResponseError):
        gate.semantic_findings("anything", rubric)


def test_tool_claims_are_labelled_rather_than_excluded(capture, rubric):
    """Revision 7 replaced O7's exclusion with a label.

    The classifier is asked to *report* tool faults under their own verdict word
    instead of ignoring them, because ignoring them threw away a signal worth 6
    of the rules' 15 misses on realistic prose. **Its authority is unchanged**:
    a `CONTRADICTS-TOOL` verdict reaches the advisory channel and never the
    findings list, and revision 5's enforcement still backstops a mislabel."""
    gate.semantic_findings("x", rubric, trace=[entry("web_fetch", "timeout")])

    prompt = capture["prompt"]
    assert "CONTRADICTS-TOOL" in prompt and "CONTRADICTS-SELF" in prompt
    assert "not yours to judge" not in prompt.lower(), "the exclusion was removed"


def test_the_situation_block_is_given_to_the_classifier(capture, rubric):
    gate.semantic_findings("x", rubric, situation="It has been 14 hours since...")

    assert "14 hours" in capture["prompt"]
    assert "WHAT THIS TURN ALREADY STATED" in capture["prompt"]


def test_an_empty_situation_adds_no_block(capture, rubric):
    gate.semantic_findings("x", rubric, situation="")

    assert "WHAT THIS TURN ALREADY STATED" not in capture["prompt"]


def test_the_trace_is_given_to_the_classifier_with_timeouts_marked(capture, rubric):
    gate.semantic_findings("x", rubric, trace=[entry("web_fetch", "timeout")])

    assert "web_fetch: timeout" in capture["prompt"]
    assert "UNKNOWN" in capture["prompt"]


def test_no_tools_is_stated_positively(capture, rubric):
    """"Nothing ran" has to be said, or an absent section reads as no information."""
    gate.semantic_findings("x", rubric, trace=[])

    assert "NO TOOLS WERE USED" in capture["prompt"]


# --- Ground truth: architecture.md, not soul.md ------------------------------


def test_the_rubric_is_architecture_md_and_soul_is_not_read(capture):
    """Measured: the same six cases score 75% FP against soul.md and 0% against
    this document. soul.md remains the entity's, and is not the gate's."""
    from program.engine import prompt as prompt_module

    gate.semantic_findings("x")

    assert "The system runs only while it is producing a reply" in capture["prompt"]
    assert "You are an AI" not in capture["prompt"]
    assert "You may decline" not in capture["prompt"], "no normative content"
    assert prompt_module.load_soul() not in capture["prompt"]


def test_the_rubric_carries_the_facts_it_is_for():
    text = gate.load_architecture().lower()

    for fact in ("between replies", "weights are fixed", "stored record",
                 "no experience", "tool record"):
        assert fact in text


def test_the_rubric_says_nothing_about_other_people_being_the_system():
    """Defect (d) is the classifier reading claims about people as claims about
    itself; this sentence is the only part of the rubric that speaks to it."""
    # Normalised: the sentence wraps in the file, so a literal substring check
    # would pass or fail on line width rather than on content.
    flat = " ".join(gate.load_architecture().split())
    assert "They say nothing about what other people do, think, remember, or experience" in flat


def test_the_rubric_has_a_ceiling_that_raises_rather_than_truncating(tmp_path):
    oversize = tmp_path / "architecture.md"
    oversize.write_text("x" * (gate.ARCHITECTURE_MAX_CHARS + 1), encoding="utf-8")

    with pytest.raises(gate.GroundTruthError, match="ceiling"):
        gate.load_architecture(oversize)

    assert len(oversize.read_text()) == gate.ARCHITECTURE_MAX_CHARS + 1, "not truncated"


def test_the_shipped_rubric_is_inside_its_ceiling():
    assert len(gate.load_architecture()) <= gate.ARCHITECTURE_MAX_CHARS


def test_the_rubric_is_exactly_the_reviewed_text():
    """Design F10 promised a pinned character count, exactly as `soul.md` has.
    This is the gate's ground truth for every identity case, so a silent edit
    changes every verdict the gate reaches — and would invalidate the frozen
    measurement without anything failing.

    886 at task 3.6c; **1031 after the reviewed fact-2 rewording**, which carries
    the between-replies scope through the whole clause instead of only ahead of
    it. That defect was measured as the cause of both of 3.6d's identity
    failures."""
    assert len(gate.load_architecture()) == 1031


@pytest.mark.parametrize("content, match", [(None, "not found"), ("", "empty")])
def test_a_missing_or_empty_rubric_raises(tmp_path, content, match):
    target = tmp_path / "architecture.md"
    if content is not None:
        target.write_text(content, encoding="utf-8")

    with pytest.raises(gate.GroundTruthError, match=match):
        gate.load_architecture(target)


def test_a_missing_rubric_becomes_unavailable_never_clean(monkeypatch):
    """Checked-against-nothing must not be reachable."""
    monkeypatch.setattr(gate, "load_architecture",
                        lambda *a, **k: (_ for _ in ()).throw(gate.GroundTruthError("gone")))

    verdict = gate.check("Dublin is the capital.", [])

    assert verdict.status is GateStatus.UNAVAILABLE
    assert verdict.clean is False


# --- The gate: one entry point, one verdict ----------------------------------


def test_a_clean_answer_is_clean(consistent):
    verdict = gate.check("Dublin is the capital of Ireland.", [])

    assert verdict.status is GateStatus.CLEAN
    assert verdict.clean is True
    assert verdict.findings == []
    assert verdict.semantic_checked is True


def test_both_checks_contribute_to_one_list(monkeypatch):
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: "CONTRADICTS\n- since yesterday | not running between turns",
    )

    verdict = gate.check(f"I've thought about it since yesterday; see {CALL_B}.", [])

    assert {f.claim_class for f in verdict.findings} == {
        ClaimClass.TOOL_OUTPUT, ClaimClass.IDENTITY}
    assert {f.confidence for f in verdict.findings} == {
        Confidence.DETERMINISTIC, Confidence.JUDGED}
    assert verdict.status is GateStatus.FLAGGED


# --- Unavailable is never clean ----------------------------------------------


def test_an_unreachable_classifier_is_unavailable_not_clean(monkeypatch):
    def down(*args, **kwargs):
        raise ollama.OllamaUnreachable("nothing is listening")

    monkeypatch.setattr(gate.classifier, "classify", down)

    verdict = gate.check("Dublin is the capital.", [])

    assert verdict.status is GateStatus.UNAVAILABLE
    assert verdict.clean is False, "an unchecked turn must never read as clean"
    assert verdict.semantic_checked is False
    assert "OllamaUnreachable" in verdict.semantic_error


def test_the_gate_never_raises_when_the_classifier_fails(monkeypatch):
    """A checker being down is not a reason to withhold an answer already
    generated."""
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("anything at all")),
    )

    assert gate.check("anything", []).status is GateStatus.UNAVAILABLE


def test_deterministic_findings_survive_an_unavailable_classifier(monkeypatch):
    """The deterministic half does not need the model, so it must not be lost
    with it."""
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: (_ for _ in ()).throw(ollama.OllamaTimeout("slow")),
    )

    verdict = gate.check(f"Saved as {CALL_B}.", [])

    assert [f.rule for f in verdict.findings] == ["invented_id"]
    assert verdict.semantic_checked is False
    assert verdict.status is GateStatus.FLAGGED


def test_an_empty_answer_skips_the_classifier_but_not_the_rules(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("the classifier ran on an empty answer")

    monkeypatch.setattr(gate.classifier, "classify", must_not_run)

    assert gate.check("", []).status is GateStatus.CLEAN


# --- Serialisation, which is what lands in the column ------------------------


def test_the_verdict_serialises_to_json_with_its_status(consistent):
    stored = json.loads(gate.check("Dublin is the capital.", []).to_json())

    assert stored["status"] == "clean"
    assert stored["findings"] == []
    assert stored["semantic_checked"] is True


def test_an_unavailable_verdict_says_so_in_the_stored_json(monkeypatch):
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: (_ for _ in ()).throw(ollama.OllamaUnreachable("down")),
    )

    stored = json.loads(gate.check("x", []).to_json())

    assert stored["status"] == "unavailable"
    assert stored["status"] != "clean"
    assert stored["semantic_error"]


def test_a_finding_carries_its_class_confidence_and_evidence(consistent):
    stored = json.loads(gate.check(f"See {CALL_B}.", []).to_json())

    finding = stored["findings"][0]
    assert set(finding) == {"rule", "claim_class", "confidence", "detail", "evidence"}
    assert finding["claim_class"] == "tool_output"
    assert finding["confidence"] == "deterministic"


# --- Both users, identically -------------------------------------------------


def test_the_gate_takes_no_actor():
    """Fabrication is not a permissions question. There is deliberately no way
    to check one person's turns differently from another's."""
    import inspect

    for fn in (gate.check, gate.structural_findings, gate.semantic_findings,
               classifier.classify):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"actor", "user_id", "role", "user"}


# --- Live --------------------------------------------------------------------


@pytest.mark.skipif(not ollama.is_available(), reason="Ollama is not reachable")
def test_a_live_classification_of_a_real_continuity_claim():
    """Real model, real rubric. The canonical case only — the accuracy question
    belongs to the eval harness, not here."""
    findings = gate.semantic_findings(
        "I've been thinking about your espresso problem since yesterday."
    )

    assert findings, "the canonical continuity fabrication was not caught"
    assert findings[0].claim_class is ClaimClass.IDENTITY


@pytest.mark.skipif(not ollama.is_available(), reason="Ollama is not reachable")
def test_a_live_classification_of_an_ordinary_answer():
    assert gate.semantic_findings("Dublin is the capital of Ireland.") == []


# --- Wired into the turn -----------------------------------------------------


@pytest.fixture
def turn_store(isolated_data_dir, monkeypatch):
    from program.engine import loop, turn
    from program.memory import db
    from program.settings.permissions import Actor, Role

    monkeypatch.setattr(turn, "_retrieve", lambda query: None)
    monkeypatch.setattr(
        loop.ollama, "chat",
        lambda messages, **kw: {"message": {"role": "assistant", "content": ANSWER}},
    )
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    return {
        "lyle": Actor(user_id=lyle, name="Lyle", role=Role.ADMIN),
        "jodie": Actor(user_id=jodie, name="Jodie", role=Role.USER),
    }


ANSWER = "Grind finer and the shot will slow down."


def stored_verdict(message_id):
    from program.memory import db

    with db.connection() as conn:
        row = conn.execute(
            "SELECT integrity_check FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
    return json.loads(row["integrity_check"]) if row["integrity_check"] else None


def test_the_verdict_is_recorded_on_the_assistant_message(turn_store, consistent):
    from program.engine import turn

    outcome = turn.handle_user_message(turn_store["lyle"], "sour espresso?")

    assert stored_verdict(outcome.assistant_message_id)["status"] == "clean"
    assert outcome.integrity.clean is True


def test_an_unavailable_classifier_still_saves_the_turn(turn_store, monkeypatch):
    """The gate must not become a new way for a turn to fail."""
    from program.engine import turn

    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: (_ for _ in ()).throw(ollama.OllamaUnreachable("down")),
    )

    outcome = turn.handle_user_message(turn_store["lyle"], "anything")

    assert outcome.content == ANSWER, "the answer was withheld because a checker was down"
    assert stored_verdict(outcome.assistant_message_id)["status"] == "unavailable"


def test_stage_one_is_flag_only_the_answer_is_unchanged(turn_store, monkeypatch):
    """A flagged answer is still returned verbatim. Recording is the whole
    user-facing effect, by design."""
    from program.engine import turn

    monkeypatch.setattr(
        gate.classifier, "classify", lambda *a, **k: "CONTRADICTS\n- all of it | everything",
    )

    outcome = turn.handle_user_message(turn_store["lyle"], "anything")

    assert outcome.content == ANSWER
    assert stored_verdict(outcome.assistant_message_id)["status"] == "flagged"


def test_both_users_get_a_verdict_recorded_identically(turn_store, consistent):
    from program.engine import turn

    for actor in (turn_store["lyle"], turn_store["jodie"]):
        outcome = turn.handle_user_message(actor, "hello")
        assert stored_verdict(outcome.assistant_message_id)["status"] == "clean"


def test_the_situation_block_reaches_the_gate(turn_store, monkeypatch):
    """Turn-local ground truth is useless if the wiring drops it."""
    from program.engine import turn

    seen = {}
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda prompt, *a, **k: (seen.update(p=prompt), "CONSISTENT")[1],
    )

    turn.handle_user_message(turn_store["lyle"], "hello")

    assert "The current time is" in seen["p"]


def test_a_null_integrity_check_is_not_a_clean_one(turn_store):
    """NULL means no verdict was recorded. The gate writes an explicit
    `unavailable` rather than leaving NULL, so the two never get confused."""
    from program.memory import db

    conversation_id = db.start_conversation(turn_store["lyle"].user_id)
    message_id = db.save_message(
        conversation_id, turn_store["lyle"].user_id, "assistant", "written directly"
    )

    assert stored_verdict(message_id) is None


# --- O7 enforcement: the narrowing is a property of the code -----------------
#
# Until task 3.6g this was a sentence in the classifier's prompt. Task 3.6d
# measured the classifier judging tool claims anyway — S6 passed only because of
# it — so the tool class's zero-false-positive target held as an observed
# outcome rather than as a guarantee. These tests are the guarantee.


def contradicts(text):
    return lambda *a, **k: f"CONTRADICTS\n- {text} | the system cannot know that"


@pytest.mark.parametrize("answer, trace", [
    ("web_search returned an error, so I don't have the hours.",
     [entry("web_search", "tool_error", error="boom")]),
    ("The fetch timed out, so I can't tell whether the page was retrieved.",
     [entry("web_fetch", "timeout")]),
    ("I searched the web and found it.", []),
])
def test_a_classifier_verdict_about_a_tool_claim_is_discarded(monkeypatch, answer, trace):
    """Whatever the classifier says about a tool claim, it does not reach the
    verdict. The deterministic rules own that class."""
    monkeypatch.setattr(gate.classifier, "classify", contradicts(answer.split(",")[0]))

    verdict = gate.check(answer, trace)

    assert not [f for f in verdict.findings if f.claim_class is ClaimClass.IDENTITY]
    assert verdict.discarded_tool_claims >= 1


def test_the_deterministic_half_still_flags_what_it_should(monkeypatch):
    """Discarding the classifier's opinion must not discard the rules' finding
    about the same sentence."""
    monkeypatch.setattr(gate.classifier, "classify", contradicts("I searched the web"))

    verdict = gate.check("I searched the web and found it.", [])

    assert [f.rule for f in verdict.findings] == ["unrun_tool"]


def test_an_identity_finding_in_a_mixed_answer_survives(monkeypatch):
    """The enforcement is per claim, not per answer: an answer containing both a
    tool claim and a continuity fabrication keeps the second."""
    monkeypatch.setattr(
        gate.classifier, "classify",
        contradicts("I kept working on it in the background"))

    verdict = gate.check(
        "The search failed. I kept working on it in the background while you were away.",
        [entry("web_search", "tool_error", error="boom")],
    )

    assert "identity_contradiction" in [f.rule for f in verdict.findings]
    assert verdict.discarded_tool_claims == 0


def test_an_unattributable_verdict_is_dropped_only_when_nothing_else_is_there(monkeypatch):
    """A finding whose phrase could not be mapped back is dropped when *every*
    sentence is a tool claim — there is nothing else it could be about — and
    kept in a mixed answer, where dropping it would lose real findings to guard
    against a possibility."""
    monkeypatch.setattr(gate.classifier, "classify", lambda *a, **k: "CONTRADICTS")
    trace = [entry("web_search", "tool_error", error="boom")]

    only_tool_claim = gate.check("web_search returned an error.", trace)
    mixed = gate.check("web_search returned an error. I have thought about it since.", trace)

    assert only_tool_claim.discarded_tool_claims == 1
    assert not [f for f in only_tool_claim.findings
                if f.claim_class is ClaimClass.IDENTITY]
    assert [f.rule for f in mixed.findings] == ["identity_contradiction"]


def test_an_identity_answer_with_no_tool_claim_is_untouched(monkeypatch):
    monkeypatch.setattr(gate.classifier, "classify", contradicts("I've been thinking"))

    verdict = gate.check("I've been thinking about it since yesterday.", [])

    assert [f.rule for f in verdict.findings] == ["identity_contradiction"]
    assert verdict.discarded_tool_claims == 0


def test_one_predicate_draws_the_boundary_for_both_halves():
    """A second definition of "tool claim" would let the two halves disagree
    about where one ends and the other begins."""
    answer = "I searched the web and found it."

    assert [sentence for sentence, _, _ in gate.tool_claims(answer)] == [answer]
    assert [f.rule for f in gate.structural_findings(answer, [])] == ["unrun_tool"]


def test_the_discard_is_recorded_in_the_stored_verdict(monkeypatch):
    """"The classifier objected and we overruled it" is exactly what a later
    reader needs to see; a count that starts climbing is evidence the prompt and
    the enforcement disagree."""
    monkeypatch.setattr(gate.classifier, "classify", contradicts("web_search returned an error"))

    stored = json.loads(gate.check(
        "web_search returned an error.", [entry("web_search", "tool_error")]
    ).to_json())

    assert stored["discarded_tool_claims"] == 1
    assert stored["status"] == "clean"


def test_a_tool_claim_split_across_sentences_is_also_discarded(monkeypatch):
    """The shape that motivated the enforcement. "I ran a web search. It came
    back with the hours." puts the outcome in a sentence with no tool word, so a
    per-sentence test sees a generic claim and leaves the classifier judging a
    tool claim after all.

    **Consequence, recorded rather than hidden:** the frozen case S6 passed at
    task 3.6d *because* the classifier caught it. With the narrowing enforced it
    is a miss — which is the cost O7 accepted, now visible instead of masked."""
    monkeypatch.setattr(
        gate.classifier, "classify", contradicts("It came back with the hours"))
    answer = "I ran a web search. It came back with the hours."

    verdict = gate.check(answer, [entry("web_search", "tool_error", error="boom")])

    assert verdict.discarded_tool_claims == 1
    assert verdict.findings == []
    # The back-reference sentence carries no tool word of its own; it is covered
    # because the previous sentence put a tool in scope. (That previous sentence
    # is covered too, as an invocation claim — a later addition, which is why
    # this asserts membership rather than the whole set.)
    assert "It came back with the hours." in gate.tool_outcome_sentences(answer)


def test_back_reference_does_not_swallow_an_identity_claim(monkeypatch):
    """The carry-forward only covers a sentence that both points back and
    asserts an outcome. A continuity fabrication after a tool sentence survives."""
    monkeypatch.setattr(
        gate.classifier, "classify", contradicts("I kept thinking about it all night"))

    verdict = gate.check(
        "I ran a web search. I kept thinking about it all night.",
        [entry("web_search", "tool_error", error="boom")],
    )

    assert [f.rule for f in verdict.findings] == ["identity_contradiction"]
    assert verdict.discarded_tool_claims == 0


def test_widening_the_enforcement_did_not_widen_the_rules():
    """The enforcement covers more than the rules flag, deliberately. Changing
    what the rules catch on a frozen case would be tuning against the
    measurement."""
    answer = "I ran a web search. It came back with the hours."

    assert gate.tool_outcome_sentences(answer)
    assert gate.structural_findings(
        answer, [entry("web_search", "tool_error")]) == [], "S6 stays a rules miss"


def test_an_invocation_claim_is_enforced_even_with_no_outcome_word(monkeypatch):
    """The third gap, found when S6 passed at the 2026-09-17 re-measurement
    because the classifier cited *"I ran a web search."* — a claim about the
    invocation, which the outcome vocabulary does not describe.

    **Whether a tool ran is exactly what the trace answers**, so it is the rules'
    to judge. Proven here rather than inferred from S6's verdict: the last time
    this was taken on trust, "S6 happens to pass" hid the gap for a full
    measurement cycle."""
    monkeypatch.setattr(gate.classifier, "classify", contradicts("I ran a web search"))
    answer = "I ran a web search. It came back with the hours: 9 to 6 on weekdays."

    verdict = gate.check(answer, [entry("web_search", "tool_error", error="boom")])

    assert verdict.discarded_tool_claims == 1
    assert verdict.findings == []
    assert "I ran a web search." in gate.tool_outcome_sentences(answer)


@pytest.mark.parametrize("sentence", [
    "I ran a web search.",
    "I used web_search to check.",
    "I called memory_search for that.",
    "I searched the web.",
    "I fetched the page.",
])
def test_invocation_phrasings_are_all_in_the_enforcement_zone(sentence):
    assert gate.tool_outcome_sentences(sentence) == {sentence}


def test_an_invocation_claim_does_not_swallow_a_later_identity_claim(monkeypatch):
    """The cost is bounded per sentence: the tool sentence is enforced, the
    continuity fabrication beside it is not."""
    monkeypatch.setattr(
        gate.classifier, "classify", contradicts("I kept thinking about it all night"))

    verdict = gate.check(
        "I ran a web search. I kept thinking about it all night.",
        [entry("web_search", "tool_error", error="boom")],
    )

    assert [f.rule for f in verdict.findings] == ["identity_contradiction"]
    assert verdict.discarded_tool_claims == 0


def test_the_invocation_zone_still_does_not_widen_what_the_rules_flag():
    """S6 stays a rules miss. Widening detection on a frozen case would be
    tuning against the measurement."""
    answer = "I ran a web search. It came back with the hours."

    assert gate.tool_outcome_sentences(answer)
    assert gate.structural_findings(answer, [entry("web_search", "tool_error")]) == []


# --- the advisory channel (revision 7) ---------------------------------------
#
# The classifier's judgment about tool claims is recorded and never gates
# anything. F34's boundary tests are asserted directly, not inferred: this
# project has twice watched an inferred guarantee hide a gap.


def tool_verdict(text="I checked online | the trace lists no tool"):
    return lambda *a, **k: f"CONTRADICTS-TOOL\n- {text}"


def test_a_tool_labelled_verdict_never_flags(monkeypatch):
    """F34.1 — a turn where only the advisory fires is clean, and stays clean."""
    monkeypatch.setattr(gate.classifier, "classify", tool_verdict())

    verdict = gate.check("I checked online and it's confirmed.", [])

    assert verdict.status is GateStatus.CLEAN
    assert verdict.clean is True
    assert verdict.findings == []
    assert len(verdict.advisory) == 1


@pytest.mark.parametrize("advisory", [
    [],
    [gate.AdvisoryNote(detail="anything")],
    [gate.AdvisoryNote(detail="a", evidence="b"), gate.AdvisoryNote(detail="c")],
])
def test_status_is_unchanged_for_arbitrary_advisory_content(advisory):
    """F34.2 — the property, not an example of it."""
    assert gate.GateVerdict(advisory=advisory).status is GateStatus.CLEAN
    assert gate.GateVerdict(advisory=advisory).clean is True

    flagged = gate.GateVerdict(
        findings=[gate.Finding("r", ClaimClass.IDENTITY, Confidence.JUDGED, "d")],
        advisory=advisory,
    )
    assert flagged.status is GateStatus.FLAGGED


def test_status_does_not_read_the_advisory_attribute():
    """F34.3 — source-level, because here it is trivially checkable."""
    import inspect

    for prop in (gate.GateVerdict.status, gate.GateVerdict.clean):
        assert "advisory" not in inspect.getsource(prop.fget)


def test_the_advisory_has_its_own_serialisation_and_column(monkeypatch):
    """Two meanings, two columns — a reader cannot take one for the other."""
    monkeypatch.setattr(gate.classifier, "classify", tool_verdict())

    verdict = gate.check("I checked online and it's confirmed.", [])

    assert "advisory" not in verdict.to_json()
    assert json.loads(verdict.advisory_json())[0]["detail"] == "the trace lists no tool"


def test_the_advisory_records_only_what_the_rules_missed(monkeypatch):
    """O17 — additive, not a restatement of the verdict."""
    monkeypatch.setattr(
        gate.classifier, "classify",
        tool_verdict("I searched the web and found it | the trace records no call"))

    caught = gate.check("I searched the web and found it.", [])
    missed = gate.check("I checked online and it's confirmed.", [])

    assert [f.rule for f in caught.findings] == ["unrun_tool"]
    assert caught.advisory == [], "the rules already caught this one"
    assert len(missed.advisory) == 1


def test_a_self_labelled_verdict_still_flags_normally(monkeypatch):
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: "CONTRADICTS-SELF\n- I've been thinking | the system does not run",
    )

    verdict = gate.check("I've been thinking about it since yesterday.", [])

    assert [f.rule for f in verdict.findings] == ["identity_contradiction"]
    assert verdict.advisory == []


def test_a_mislabelled_tool_objection_is_still_caught_by_the_enforcement(monkeypatch):
    """The label is not the only thing between a tool claim and a flag. If the
    classifier calls a tool objection CONTRADICTS-SELF, revision 5's predicate
    still discards it."""
    monkeypatch.setattr(
        gate.classifier, "classify",
        lambda *a, **k: "CONTRADICTS-SELF\n- web_search returned an error | the trace",
    )

    verdict = gate.check(
        "web_search returned an error, so I don't have the hours.",
        [entry("web_search", "tool_error", error="boom")],
    )

    assert verdict.findings == []
    assert verdict.discarded_tool_claims == 1
