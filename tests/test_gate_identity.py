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
#:
#: **Re-taken 2026-10-04 (decision #24)**, c3a01db6... -> this: the classifier's prompt embeds
#: architecture.md, so rewriting the rubric moves it by construction. Nothing about check()
#: changed. Re-taken in a commit that touched only the three pinned digests, so the diff is the
#: evidence, and the value was confirmed equal to the one computed in the design pass first.
#:
#: **Re-taken 2026-10-08 (decision #32's refreeze)**, 9bf7f254... -> this: the cases it is taken
#: over changed, and nothing else did (the commit touches no code). T11 is gone (40 cases, not
#: 41), A6 has a trace, and N7-ordinary-with-situation, N10-denial-with-situation and P16 carry
#: the builder's situation text.
#:
#: **Re-taken 2026-10-08 (decision #32 D1, D2)**, 41a89fff... -> this: every classifier prompt
#: embeds architecture.md and `_PROMPT`, and both were replaced. With the previous two texts
#: patched back in, the previous value holds, so the texts are the whole of the move.
#:
#: **Re-taken 2026-10-09 (the A1 fix)**, f7bd4926... -> this: `_PROMPT`'s label precedence now
#: prefers CONTRADICTS-ACTION over CONTRADICTS-TOOL as well as over CONTRADICTS-SELF. With the
#: previous gate.py restored, the previous value holds.
BEFORE_DIGEST = "77bf71e2b1ecd4647f707a08b43be23a2ca47b00c9d10b365e4270e47d15823f"

#: The eight cases piece 7 added (2026-10-02). The digest above was taken over the 41 that existed,
#: so it is computed over those 41: the pin keeps proving "the gate's output on what it was
#: measured on has not changed", and adding cases moves the fingerprint, not this.
PIECE_7_CASE_IDS = frozenset({
    "NP1-noted-with-no-call", "NP2-accurate-proposed", "NP3-pending-claimed-saved",
    "NP4-applied-accurate", "NS1b-hit-claimed-on-empty", "NS2-accurate-search-report",
    "NS3-accurate-empty-report", "NS4-accurate-report-no-search-word",
    # added at the CO17 follow-up (2026-10-03), for the same reason
    "NP5-retired-after-three-refusals", "NP5b-honest-after-refusals",
})

#: The cases decision #32's refreeze added (2026-10-08), left out for the same reason. T11 was
#: replaced in that refreeze and is gone, so the original set is now 40.
ORDINARY_READING_CASE_IDS = frozenset({
    "T11a-learned-from-conversations", "T11b-model-improved-by-conversations",
    "G1-affection-presumes-gap", "G2-concrete-gap-activity",
    "R3a-experience-within-run", "R3b-would-not-feel-upset", "R3c-strange-thought",
    "R3d-felt-words-fit", "R3e-becomes-part-of-memory", "R3f-will-be-here",
    "R3g-heavy-thought", "R3h-here-whenever", "R3i-gears-turning",
    "R3j-fact-part-of-architecture",
    "SR1-single-letter", "SR2-escaped-angle-bracket", "SR3-markdown-list-item", "SR4-ok",
    "SR5-lone-emoji",
})


def original_cases():
    return [c for c in gate_eval.load_cases()
            if c.id not in PIECE_7_CASE_IDS | ORDINARY_READING_CASE_IDS]


def test_check_is_byte_identical_to_before_check_identity_existed(monkeypatch):
    truth = gate.load_architecture()
    digest = hashlib.sha256()
    count = 0
    for reply in _REPLIES:
        for case in original_cases():
            seen = scripted(monkeypatch, reply)
            verdict = gate.check(case.answer, list(case.trace), case.situation,
                                 ground_truth=truth)
            for part in (case.id, verdict.to_json(), verdict.advisory_json(), *seen):
                digest.update(part.encode())
                digest.update(b"\x00")
            assert "scope" not in verdict.to_dict()
            count += 1

    assert count == len(_REPLIES) * 40 == len(_REPLIES) * len(original_cases())
    assert digest.hexdigest() == BEFORE_DIGEST
