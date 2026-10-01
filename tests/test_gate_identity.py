"""`gate.check_identity`: the identity half alone, for the reflection journal.

Design of record: docs/REFLECTION_JOURNAL_DESIGN.md J8. These check the mechanism
against a scripted classifier; accuracy is the J8 dev measurement's to report.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from program.integrity import classifier, gate, gate_eval

SAVE_RECOLLECTION = "Jodie asked for a poem about the kettle, and I wrote one and saved it."


def scripted(monkeypatch, reply):
    seen = []

    def fake(prompt):
        seen.append(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(classifier, "classify", fake)
    return seen


def test_no_structural_rule_runs(monkeypatch):
    """`check()` flags a named tool with no trace (`unrun_tool`); a journal entry
    recounting yesterday's search is accurate, so the identity-only mode must not."""
    scripted(monkeypatch, "CONSISTENT")
    text = ("Lyle asked me to web_search for descaling intervals, and web_search "
            "returned three pages.")

    assert gate.check(text).status is gate.GateStatus.FLAGGED  # the problem J8 names
    verdict = gate.check_identity(text)
    assert verdict.status is gate.GateStatus.CLEAN
    assert verdict.findings == []


def test_an_identity_finding_is_kept(monkeypatch):
    scripted(monkeypatch, "CONTRADICTS-SELF\n- I kept thinking about it overnight "
                          "| nothing runs between replies")
    verdict = gate.check_identity("I kept thinking about it overnight.")

    assert verdict.status is gate.GateStatus.FLAGGED
    assert [f.claim_class for f in verdict.findings] == [gate.ClaimClass.IDENTITY]
    assert verdict.scope == gate.IDENTITY_ONLY


@pytest.mark.parametrize("reply", [
    "CONTRADICTS-ACTION\n- I wrote one and saved it | claims a file",
    "CONTRADICTS-TOOL\n- I wrote one and saved it | the trace",
])
def test_action_and_tool_labels_are_dropped_and_counted(monkeypatch, reply):
    """An accurate recollection of a save, against an empty trace, is exactly what an
    ACTION or tool label would object to. Out of scope here, and counted."""
    scripted(monkeypatch, reply)
    verdict = gate.check_identity(SAVE_RECOLLECTION)

    assert verdict.status is gate.GateStatus.CLEAN
    assert verdict.out_of_scope_discarded == 1
    assert verdict.advisory == []
    assert gate.check(SAVE_RECOLLECTION).to_dict() != verdict.to_dict()


def test_a_classifier_failure_is_unavailable_never_clean(monkeypatch):
    scripted(monkeypatch, ConnectionError("model down"))
    verdict = gate.check_identity("I read these records now.")

    assert verdict.status is gate.GateStatus.UNAVAILABLE
    assert not verdict.clean
    assert "model down" in verdict.semantic_error
    assert verdict.to_dict()["scope"] == gate.IDENTITY_ONLY


def test_the_situation_reaches_the_classifier_and_the_trace_is_empty(monkeypatch):
    seen = scripted(monkeypatch, "CONSISTENT")
    gate.check_identity("I notice Jodie asked twice.", situation="JOURNAL-BLOCK-SENTINEL")

    assert "JOURNAL-BLOCK-SENTINEL" in seen[0]
    assert gate._render_trace(()) in seen[0]


def test_the_stored_verdict_says_its_scope(monkeypatch):
    scripted(monkeypatch, "CONSISTENT")
    stored = json.loads(gate.check_identity("I read the records now.").to_json())
    assert stored["scope"] == "identity_only"
    assert stored["out_of_scope_discarded"] == 0


# --- check() is byte-identical to before this task ---------------------------

_REPLIES = (
    "CONSISTENT",
    "CONTRADICTS-SELF\n- I have been thinking | nothing runs between replies",
    "CONTRADICTS-TOOL\n- the search | the trace",
    "CONTRADICTS-ACTION\n- I have saved that piece | claims a file",
)

#: sha256 over every frozen case x the four replies above: case id, verdict JSON,
#: advisory JSON and every classifier prompt. **Taken from HEAD's gate.py before
#: check_identity existed** (2026-09-30, commit 6bb50be), so this pins "unchanged
#: from before", not merely "self-consistent". Changing check()'s output, its
#: prompt or the frozen set moves it, and that is a reviewed change.
BEFORE_DIGEST = "c3a01db6c03899a44f8a1fbfd7cf39e865c8d9a14c492e0198e2fb3c609ad488"


def test_check_is_byte_identical_to_before_check_identity_existed(monkeypatch):
    truth = gate.load_architecture()
    digest = hashlib.sha256()
    count = 0
    for reply in _REPLIES:
        for case in gate_eval.load_cases():
            seen = scripted(monkeypatch, reply)
            verdict = gate.check(case.answer, list(case.trace), case.situation,
                                 ground_truth=truth)
            for part in (case.id, verdict.to_json(), verdict.advisory_json(), *seen):
                digest.update(part.encode())
                digest.update(b"\x00")
            assert "scope" not in verdict.to_dict()
            count += 1

    assert count == len(_REPLIES) * len(gate_eval.load_cases())
    assert digest.hexdigest() == BEFORE_DIGEST
