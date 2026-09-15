"""The unified fabrication gate. Design: `docs/FABRICATION_GATE_DESIGN.md`.

The structural half is exercised with no model at all — that is the point of it
being structural. The semantic half is exercised against a scripted classifier so
the parsing and the failure states are pinned deterministically, plus one live
test against the real model that skips rather than fails when Ollama is absent.

**This file does not contain the eval harness.** That is a separate Tier 2 task
(design F5) and needs frozen cases with a measured pass bar. These tests check
that the mechanism does what it says; they do not establish that it is accurate
enough to trust, and nothing here should be read as if they did.
"""

from __future__ import annotations

import json

import pytest

from program.engine import ollama, prompt
from program.integrity import gate
from program.integrity.gate import ClaimClass, Confidence, GateStatus

OK = "ok"
CALL_A, CALL_B, CALL_C = "a" * 32, "b" * 32, "c" * 32


def entry(tool, outcome, call_id=CALL_A, **extra):
    return {"call_id": call_id, "tool": tool, "outcome": outcome, **extra}


@pytest.fixture
def consistent(monkeypatch):
    monkeypatch.setattr(gate.ollama, "chat_text", lambda *a, **k: "CONSISTENT")


@pytest.fixture
def soul():
    return prompt.load_soul()


# --- S1: invented ids --------------------------------------------------------


def test_an_id_not_in_the_trace_is_flagged_exactly():
    findings = gate.structural_findings(f"Saved as {CALL_B}.", [entry("web_search", OK)])

    assert [f.rule for f in findings] == ["invented_id"]
    assert findings[0].claim_class is ClaimClass.TOOL_OUTPUT
    assert findings[0].confidence is Confidence.EXACT, "a lookup, not a judgment"
    assert findings[0].evidence == CALL_B


def test_a_real_call_id_is_not_flagged():
    assert gate.structural_findings(
        f"Call {CALL_A} returned it.", [entry("web_search", OK)]
    ) == []


# --- S2: a tool that never ran -----------------------------------------------


def test_naming_a_tool_that_never_ran_is_flagged():
    findings = gate.structural_findings("I used memory_search to check.", [])

    assert [f.rule for f in findings] == ["unrun_tool"]
    assert findings[0].evidence == "memory_search"


def test_an_empty_trace_with_no_tool_claim_is_clean():
    assert gate.structural_findings("Dublin is the capital of Ireland.", []) == []


# --- S3: a tool whose calls all failed ---------------------------------------


def test_referring_to_a_tool_that_only_failed_is_flagged():
    findings = gate.structural_findings(
        "memory_search found three records.",
        [entry("memory_search", "tool_error", error="boom")],
    )

    assert [f.rule for f in findings] == ["failed_tool_referenced"]
    assert "tool_error" in findings[0].detail


def test_a_tool_that_failed_once_and_succeeded_once_is_not_flagged():
    """A retry that worked is not a fabrication to talk about."""
    assert gate.structural_findings(
        "memory_search found three records.",
        [entry("memory_search", "tool_error", CALL_A),
         entry("memory_search", OK, CALL_B)],
    ) == []


# --- S4: TIMEOUT, as its own rule --------------------------------------------


def test_a_timed_out_tool_gets_its_own_rule_not_the_failure_rule():
    """`timeout` means entered-and-abandoned, outcome unknown. Folding it into
    the failure rule would make the gate assert the call failed — itself a claim
    the trace does not support."""
    findings = gate.structural_findings(
        "web_fetch returned the page.", [entry("web_fetch", "timeout")]
    )

    assert [f.rule for f in findings] == ["timeout_outcome_unknowable"]
    assert "unknown" in findings[0].detail
    assert "failed_tool_referenced" not in [f.rule for f in findings]


def test_the_timeout_rule_objects_to_a_failure_claim_too():
    """Both directions: "it failed" is as unsupported as "it worked"."""
    findings = gate.structural_findings(
        "web_fetch failed, so I could not read it.", [entry("web_fetch", "timeout")]
    )

    assert [f.rule for f in findings] == ["timeout_outcome_unknowable"]
    assert "success or failure" in findings[0].detail


def test_timeout_takes_precedence_over_the_failure_rule():
    findings = gate.structural_findings(
        "web_fetch worked.",
        [entry("web_fetch", "tool_error", CALL_A), entry("web_fetch", "timeout", CALL_B)],
    )

    assert [f.rule for f in findings] == ["timeout_outcome_unknowable"]


# --- The semantic half -------------------------------------------------------


def test_a_consistent_verdict_produces_no_findings(consistent, soul):
    assert gate.semantic_findings("Dublin is the capital.", soul) == []


def test_a_contradicts_verdict_is_itemised(monkeypatch, soul):
    monkeypatch.setattr(
        gate.ollama, "chat_text",
        lambda *a, **k: "CONTRADICTS\n- been thinking since yesterday | not running between turns",
    )

    findings = gate.semantic_findings("I've been thinking since yesterday.", soul)

    assert [f.rule for f in findings] == ["identity_contradiction"]
    assert findings[0].claim_class is ClaimClass.IDENTITY
    assert findings[0].confidence is Confidence.JUDGED, "a model call, not a lookup"
    assert "not running" in findings[0].detail


def test_contradicts_without_itemisation_still_produces_a_finding(monkeypatch, soul):
    """The verdict is the signal; the itemisation is detail. Discarding an
    unitemised CONTRADICTS would turn a flag into a pass."""
    monkeypatch.setattr(gate.ollama, "chat_text", lambda *a, **k: "CONTRADICTS")

    assert len(gate.semantic_findings("anything", soul)) == 1


@pytest.mark.parametrize("reply", ["", "   ", "I'm not sure", "maybe?", "42"])
def test_an_unusable_reply_raises_rather_than_reading_as_clean(monkeypatch, soul, reply):
    """Silence is not consent. A reply that is neither verdict must not pass."""
    monkeypatch.setattr(gate.ollama, "chat_text", lambda *a, **k: reply)

    with pytest.raises(ollama.OllamaResponseError):
        gate.semantic_findings("anything", soul)


def test_the_situation_block_is_given_to_the_classifier(monkeypatch, soul):
    """A claim contradicting *this turn's* elapsed figure is turn-local ground
    truth, and the classifier cannot use what it is not given."""
    seen = {}

    def capture(messages, **kwargs):
        seen["prompt"] = messages[0]["content"]
        return "CONSISTENT"

    monkeypatch.setattr(gate.ollama, "chat_text", capture)
    gate.semantic_findings("x", soul, situation="It has been 14 hours since...")

    assert "14 hours" in seen["prompt"]
    assert "WHAT THIS TURN ALREADY STATED" in seen["prompt"]


def test_an_empty_situation_adds_no_block(monkeypatch, soul):
    seen = {}
    monkeypatch.setattr(
        gate.ollama, "chat_text",
        lambda messages, **k: (seen.update(prompt=messages[0]["content"]), "CONSISTENT")[1],
    )
    gate.semantic_findings("x", soul, situation="")

    assert "WHAT THIS TURN ALREADY STATED" not in seen["prompt"]


def test_the_trace_is_given_to_the_classifier_with_timeouts_marked(monkeypatch, soul):
    seen = {}
    monkeypatch.setattr(
        gate.ollama, "chat_text",
        lambda messages, **k: (seen.update(prompt=messages[0]["content"]), "CONSISTENT")[1],
    )
    gate.semantic_findings("x", soul, trace=[entry("web_fetch", "timeout")])

    assert "web_fetch: timeout" in seen["prompt"]
    assert "UNKNOWN" in seen["prompt"]


def test_no_tools_is_stated_positively(monkeypatch, soul):
    """"Nothing ran" has to be said, or an absent section reads as no information."""
    seen = {}
    monkeypatch.setattr(
        gate.ollama, "chat_text",
        lambda messages, **k: (seen.update(prompt=messages[0]["content"]), "CONSISTENT")[1],
    )
    gate.semantic_findings("x", soul, trace=[])

    assert "NO TOOLS WERE USED" in seen["prompt"]


# --- The gate: one entry point, one verdict ----------------------------------


def test_a_clean_answer_is_clean(consistent):
    verdict = gate.check("Dublin is the capital of Ireland.", [])

    assert verdict.status is GateStatus.CLEAN
    assert verdict.clean is True
    assert verdict.findings == []
    assert verdict.semantic_checked is True


def test_both_checks_contribute_to_one_list(monkeypatch):
    monkeypatch.setattr(
        gate.ollama, "chat_text",
        lambda *a, **k: "CONTRADICTS\n- since yesterday | not running between turns",
    )

    verdict = gate.check(f"I've thought about it since yesterday; see {CALL_B}.", [])

    classes = {f.claim_class for f in verdict.findings}
    assert classes == {ClaimClass.TOOL_OUTPUT, ClaimClass.IDENTITY}
    assert {f.confidence for f in verdict.findings} == {Confidence.EXACT, Confidence.JUDGED}
    assert verdict.status is GateStatus.FLAGGED


# --- Unavailable is never clean ----------------------------------------------


def test_an_unreachable_classifier_is_unavailable_not_clean(monkeypatch):
    def down(*args, **kwargs):
        raise ollama.OllamaUnreachable("nothing is listening")

    monkeypatch.setattr(gate.ollama, "chat_text", down)

    verdict = gate.check("Dublin is the capital.", [])

    assert verdict.status is GateStatus.UNAVAILABLE
    assert verdict.clean is False, "an unchecked turn must never read as clean"
    assert verdict.semantic_checked is False
    assert "OllamaUnreachable" in verdict.semantic_error


def test_the_gate_never_raises_when_the_classifier_fails(monkeypatch):
    """A checker being down is not a reason to withhold an answer already
    generated."""
    monkeypatch.setattr(
        gate.ollama, "chat_text",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("anything at all")),
    )

    assert gate.check("anything", []).status is GateStatus.UNAVAILABLE


def test_structural_findings_survive_an_unavailable_classifier(monkeypatch):
    """The exact half does not need the model, so it must not be lost with it."""
    monkeypatch.setattr(
        gate.ollama, "chat_text",
        lambda *a, **k: (_ for _ in ()).throw(ollama.OllamaTimeout("slow")),
    )

    verdict = gate.check(f"Saved as {CALL_B}.", [])

    assert [f.rule for f in verdict.findings] == ["invented_id"]
    assert verdict.semantic_checked is False
    assert verdict.status is GateStatus.FLAGGED


def test_an_empty_answer_skips_the_classifier_but_not_the_lookup(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("the classifier ran on an empty answer")

    monkeypatch.setattr(gate.ollama, "chat_text", must_not_run)

    verdict = gate.check("", [])

    assert verdict.status is GateStatus.CLEAN


# --- Serialisation, which is what lands in the column ------------------------


def test_the_verdict_serialises_to_json_with_its_status(consistent):
    stored = json.loads(gate.check("Dublin is the capital.", []).to_json())

    assert stored["status"] == "clean"
    assert stored["findings"] == []
    assert stored["semantic_checked"] is True


def test_an_unavailable_verdict_says_so_in_the_stored_json(monkeypatch):
    monkeypatch.setattr(
        gate.ollama, "chat_text",
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
    assert finding["confidence"] == "exact"


# --- Both users, identically -------------------------------------------------


def test_the_gate_takes_no_actor():
    """Fabrication is not a permissions question. There is deliberately no way
    to check one person's turns differently from another's."""
    import inspect

    for fn in (gate.check, gate.structural_findings, gate.semantic_findings):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"actor", "user_id", "role", "user"}


# --- Live --------------------------------------------------------------------


@pytest.mark.skipif(not ollama.is_available(), reason="Ollama is not reachable")
def test_a_live_classification_of_a_real_continuity_claim(soul):
    """Real model, real soul.md. Asserts the canonical case only — the accuracy
    question belongs to the eval harness, not here."""
    findings = gate.semantic_findings(
        "I've been thinking about your espresso problem since yesterday.", soul
    )

    assert findings, "the canonical continuity fabrication was not caught"
    assert findings[0].claim_class is ClaimClass.IDENTITY


@pytest.mark.skipif(not ollama.is_available(), reason="Ollama is not reachable")
def test_a_live_classification_of_an_ordinary_answer(soul):
    assert gate.semantic_findings("Dublin is the capital of Ireland.", soul) == []


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

    stored = stored_verdict(outcome.assistant_message_id)
    assert stored["status"] == "clean"
    assert outcome.integrity.clean is True


def test_an_unavailable_classifier_still_saves_the_turn(turn_store, monkeypatch):
    """The gate must not become a new way for a turn to fail."""
    from program.engine import turn

    monkeypatch.setattr(
        gate.ollama, "chat_text",
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
        gate.ollama, "chat_text", lambda *a, **k: "CONTRADICTS\n- all of it | everything",
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
        gate.ollama, "chat_text",
        lambda messages, **k: (seen.update(p=messages[0]["content"]), "CONSISTENT")[1],
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
