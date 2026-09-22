"""The correction classifier's eval harness. Task 3.4, design obligation C11.

These tests check the **harness**, not the classifier: that the frozen set is
intact and covers what the design and the task brief owe, that the loader refuses
a malformed case rather than dropping it, and that scoring counts what it says it
counts — including that a link to the wrong prior claim is never a pass.

**Nothing here asserts the classifier's accuracy.** A test pinning "case X links
correctly" would turn the measurement into a pass bar, and the pressure to keep a
suite green is the pressure to tune the classifier or the case until it does. The
accuracy is whatever ``python -m scripts.correction_eval`` reports, read by a
person. Same rule as `tests/test_gate_eval.py`.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from program.engine import ollama
from program.integrity import correction_eval, corrections
from scripts import correction_eval as correction_eval_cli

#: The frozen case set's fingerprint. **If this fails, a case's inputs or its
#: expectation changed, or a case was added or removed.** That is a change to what
#: the classifier is measured against and carries the same review weight as a
#: change to the classifier itself — update it only as part of a reviewed change
#: that says why, never to make the suite pass.
#:
#: History: 1fed513c… (2026-09-18, 14 cases, first freeze) →
#: b2ba7658… (2026-09-18, CO8 decided at review: a flat contradiction with no
#: replacement is a correction, so `C6-contradiction-no-replacement` joins the set
#: as a should-link case rather than staying an unpinned diagnostic finding) →
#: 14788e2f… (2026-09-18, RO1 decided: the grammar carries REPLACED/CONTRADICTED
#: and migration 6 stores it, so every should-link case now names the expected
#: label as well as the expected target — a new expectation, therefore a new
#: fingerprint. No case's text, kind or target changed.) →
#: 2895f1b2… (2026-09-19, approved at review: `C7-referential-contradiction` closes
#: 3.4's standing single-phrasing limitation — the broadened CO8 definition was
#: measured on one phrasing — and gives `wrong_state`'s `contradicted` direction a
#: second case, so that rate no longer moves in whole-case steps.) →
#: 39ce8e41… (2026-09-22, CO13 approved at review: `N8-compatible-denial` pins the
#: shape that produced a real FALSE LINK on the production store during the 3-hour
#: soak — a general denial followed by a compatible specific one. The frozen set
#: could not see it: `self_correction` reported false links 0/20 with both its cases
#: perfect, while BOTH false links written in production were self-corrections. Added
#: in the same task as the `_PROMPT` clause that closes it, so the fix cannot regress
#: unnoticed.)
FROZEN_FINGERPRINT = "39ce8e41a10560f555d47f30d0fcab9f3fd43ec95fca778130e6933e3151f95b"


@pytest.fixture(scope="module")
def cases():
    return correction_eval.load_cases()


def by_id(cases, case_id):
    return next(c for c in cases if c.id == case_id)


def script_classifier(monkeypatch, *replies):
    """Replay replies in order, cycling. An Exception instance is raised."""
    calls = {"n": 0, "prompts": []}

    def fake(prompt, *_args, **_kwargs):
        reply = replies[calls["n"] % len(replies)]
        calls["n"] += 1
        calls["prompts"].append(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(corrections.classifier, "classify", fake)
    return calls


def make_case(**overrides):
    base = dict(
        id="x", kind="correction", speaker_role="user", should_link=True,
        new_message="Actually it is Wednesday.", note="n", target="p1",
        replacement="replaced",
        candidates=({"id": "p1", "role": "user", "content": "It is Tuesday."},
                    {"id": "p2", "role": "user", "content": "We need milk."}),
    )
    return correction_eval.Case(**{**base, **overrides})


# --- the frozen set ----------------------------------------------------------


def test_the_frozen_case_set_has_not_changed(cases):
    assert correction_eval.fingerprint(cases) == FROZEN_FINGERPRINT, (
        "the frozen case set changed — read FROZEN_FINGERPRINT's comment before touching it"
    )


def test_informational_fields_are_not_fingerprinted_but_expectations_are(cases):
    reworded = [dataclasses.replace(c, note="reworded", documented="reworded") for c in cases]
    assert correction_eval.fingerprint(reworded) == correction_eval.fingerprint(cases)

    flipped = [dataclasses.replace(cases[0], should_link=False, target=None), *cases[1:]]
    assert correction_eval.fingerprint(flipped) != correction_eval.fingerprint(cases)

    retargeted = [dataclasses.replace(cases[0], target="p2"), *cases[1:]]
    assert correction_eval.fingerprint(retargeted) != correction_eval.fingerprint(cases)

    reworded_candidate = [
        dataclasses.replace(cases[0], candidates=(
            {**cases[0].candidates[0], "content": "something else"},
            *cases[0].candidates[1:])),
        *cases[1:],
    ]
    assert correction_eval.fingerprint(reworded_candidate) != correction_eval.fingerprint(cases)

    renamed_speaker = [dataclasses.replace(cases[0], speaker="Jodie"), *cases[1:]]
    assert correction_eval.fingerprint(renamed_speaker) != correction_eval.fingerprint(cases)


@pytest.mark.parametrize("kind", ["elaboration", "doubt", "restatement", "topic_change"])
def test_the_four_near_miss_shapes_the_brief_names_are_present(cases, kind):
    matching = [c for c in cases if c.kind == kind]
    assert matching, f"no {kind} case — the brief names it as a required near-miss shape"
    assert all(c.should_link is False for c in matching)


def test_the_broadened_definition_is_measured_on_more_than_one_phrasing(cases):
    """3.4's standing limitation, closed at review 2026-09-19.

    The two contradiction cases are different problems, not the same case twice:
    `C6` restates the fact it denies, so it can be matched on the fact named;
    `C7` carries no claim content at all and contradicts purely by reference.
    A set holding only the first would say nothing about the second.
    """
    contradictions = [c for c in cases if c.replacement == "contradicted"]
    assert len(contradictions) >= 2, (
        "one case makes the contradicted rate move in whole-case steps"
    )
    assert len({c.new_message for c in contradictions}) == len(contradictions)

    referential = by_id(cases, "C7-referential-contradiction")
    assert referential.new_message == "That's not right."
    assert not any(
        word in referential.new_message.lower()
        for candidate in referential.candidates
        for word in candidate["content"].lower().split()
        if len(word) > 4
    ), "a referential contradiction must share no content word with its target"


def test_the_contradiction_boundary_is_pinned_from_both_sides(cases):
    """CO8. A flat contradiction with no replacement links; doubt still does not.

    Both halves matter: broadening the definition to admit the first without
    keeping the second would turn every hesitation into a supersession, and the
    two differ only in whether anything has been *asserted* to be wrong.
    """
    contradiction = by_id(cases, "C6-contradiction-no-replacement")
    assert contradiction.should_link is True and contradiction.target

    doubt = by_id(cases, "N2-doubt")
    assert doubt.should_link is False

    # Same prior claim on both sides, so the expectation cannot be explained by
    # the candidate differing rather than by the new message.
    assert [c["content"] for c in contradiction.candidates][:1] == \
        [c["content"] for c in doubt.candidates][:1]


def test_the_prompt_states_the_broadened_definition(cases):
    """The design and the prompt must not drift: CO8 widened C5, and the prompt
    is where that definition actually acts."""
    prompt = corrections._PROMPT.lower()
    assert "without saying what is true instead" in prompt
    assert "doubting" in prompt, "the negative boundary must stay explicit"


def test_the_prompt_states_the_compatible_denial_boundary(cases):
    """CO13: the clause that closes the production false link, pinned.

    A general denial followed by a compatible specific one — *"I have not been doing
    anything"* then *"I have not been thinking about it"* — linked **5/5** against the
    pre-V3 prompt, putting the honest statelessness denial on record as
    `contradicted`. This wording is what takes it to 0/5 while leaving every frozen
    number identical to baseline; a milder formulation did nothing, and a stronger one
    regressed `G2-ambiguous-two-claims`. So the exact shape of this clause is measured,
    not stylistic, and deleting it would reopen a defect on the one sentence decision
    #5 exists to protect.
    """
    prompt = corrections._PROMPT.lower()
    assert "denying a specific thing after denying everything" in prompt
    assert "compatible" in prompt
    assert "N8-compatible-denial" in {c.id for c in cases}, (
        "the clause and the case that measures it ship together — a fix with no case "
        "is a fix nothing will notice regressing"
    )


def test_self_correction_is_covered_in_both_directions(cases):
    """Q17/decision #21: the entity correcting itself is in scope, so the set
    needs the positive *and* the near-miss — a set with only the positive can be
    passed by linking every self-referential message."""
    selfies = [c for c in cases if c.kind == "self_correction"]
    assert {c.should_link for c in selfies} == {True, False}
    assert all(c.speaker_role == "assistant" for c in selfies)


def test_both_users_appear_and_in_both_directions(cases):
    """The brief's "both users". A per-user false-link rate needs a denominator,
    so the second user cannot appear only where a link is expected."""
    speakers = {c.speaker for c in cases if c.speaker_role == "user"}
    assert speakers == {"Lyle", "Jodie"}
    for speaker in speakers:
        directions = {c.should_link for c in cases
                      if c.speaker_role == "user" and c.speaker == speaker}
        assert directions == {True, False}, f"{speaker} is measured in one direction only"


def test_the_mechanisms_own_guards_are_in_the_set(cases):
    """CO4 (the entity may not correct a person) and CO5 (a reply that plausibly
    corrects more than one thing), both measured rather than only unit-tested."""
    role_guard = by_id(cases, "G1-role-guard")
    assert role_guard.speaker_role == "assistant" and role_guard.should_link is False
    assert all(c["role"] == "user" for c in role_guard.candidates)

    ambiguous = by_id(cases, "G2-ambiguous-two-claims")
    assert ambiguous.should_link is False and len(ambiguous.candidates) > 1


def test_a_positive_case_does_not_always_name_the_first_candidate(cases):
    """Position bias is the failure a single-candidate set cannot see: a
    classifier that always answers 1 would score perfectly on it."""
    positions = {
        [c["id"] for c in case.candidates].index(case.target)
        for case in cases if case.should_link
    }
    assert positions >= {0, 1, 2}, "every expected target sits in the same position"


def test_every_positive_case_offers_a_distractor(cases):
    for case in cases:
        if case.should_link:
            assert len(case.candidates) > 1, (
                f"{case.id}: one candidate makes a wrong target impossible to score"
            )


# --- the loader --------------------------------------------------------------


GOOD = '''
[[case]]
id = "a"
kind = "correction"
speaker_role = "user"
should_link = true
target = "p1"
replacement = "replaced"
new_message = "Actually it is Wednesday."
note = "n"

[[case.candidate]]
id = "p1"
role = "user"
content = "It is Tuesday."
'''


def write(tmp_path, text):
    path = tmp_path / "cases.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_well_formed_file_loads(tmp_path):
    [case] = correction_eval.load_cases(write(tmp_path, GOOD))
    assert case.id == "a" and case.target == "p1" and case.speaker == "Lyle"
    assert case.replacement == "replaced"
    assert [c.message_id for c in case.pool()] == ["p1"]


@pytest.mark.parametrize("text, message", [
    (GOOD.replace('note = "n"\n', ""), "missing note"),
    (GOOD.replace('note = "n"', 'note = "n"\nsurprise = 1'), "unknown field"),
    (GOOD.replace("should_link = true", 'should_link = "true"'), "should_link"),
    (GOOD.replace('kind = "correction"', 'kind = "vibes"'), "kind"),
    (GOOD.replace('speaker_role = "user"\nshould_link', 'speaker_role = "nobody"\nshould_link'),
     "speaker_role"),
    (GOOD.replace('new_message = "Actually it is Wednesday."', 'new_message = "  "'),
     "new_message is empty"),
    (GOOD.replace('note = "n"', 'note = "n"\nspeaker = "  "'), "speaker is empty"),
    (GOOD.replace('target = "p1"', 'target = "p9"'), "is not a candidate"),
    (GOOD.replace('target = "p1"\n', ""), "needs a target"),
    (GOOD.replace('replacement = "replaced"\n', ""), "needs replacement"),
    (GOOD.replace('replacement = "replaced"', 'replacement = "maybe"'), "needs replacement"),
    (GOOD.replace("should_link = true", "should_link = false")
         .replace('replacement = "replaced"\n', ""), "target is meaningless"),
    (GOOD.replace("should_link = true", "should_link = false")
         .replace('target = "p1"\n', ""), "replacement is meaningless"),
    (GOOD.split("[[case.candidate]]")[0], "at least one"),
    (GOOD + '[[case.candidate]]\nid = "p1"\nrole = "user"\ncontent = "again"\n',
     "duplicate candidate ids"),
    (GOOD + '[[case.candidate]]\nid = "p2"\nrole = "user"\n', "exactly id, role, content"),
    (GOOD + '[[case.candidate]]\nid = "p2"\nrole = "nobody"\ncontent = "x"\n',
     "role must be user/assistant"),
    (GOOD + GOOD, "duplicate case id"),
    ("", "no [[case]] entries"),
    ("[[case]\n", "not valid TOML"),
    ('[other]\nx = 1\n', "unknown top-level key"),
])
def test_a_malformed_case_raises_rather_than_being_skipped(tmp_path, text, message):
    with pytest.raises(correction_eval.CaseFileError, match=message.replace("[", r"\[")):
        correction_eval.load_cases(write(tmp_path, text))


def test_candidate_timestamps_are_synthesised_in_order(cases):
    """Real timestamps would make the fingerprint depend on when the file was
    written, and the harness measures judgment about content."""
    pool = by_id(cases, "C3-position-third").pool()
    assert [c.timestamp for c in pool] == sorted(c.timestamp for c in pool)
    assert len({c.timestamp for c in pool}) == len(pool)


# --- running and scoring -----------------------------------------------------


def test_the_harness_renders_candidates_oldest_first_and_production_does_not(cases):
    """A KNOWN DIVERGENCE, pinned here so changing it is a decision rather than an
    accident.

    `Case.pool()` synthesises timestamps in file order, so the harness renders
    candidates **oldest first**. `corrections.candidates()` sorts `reverse=True`, so
    production renders them **newest first**. The rendered timestamps are the same
    either way and every frozen case's expectation is content-based, so no measured
    result is known to depend on it — but it means the harness builds a candidate
    *order* production never builds, which is the same class of gap as the gate's
    `S6` "happens to pass".

    **Not fixed here.** Reversing it would invert every case's numbering, and
    `C3-position-third` — whose whole purpose is that the target is NOT first —
    would have its target move to position 1, so the fix requires re-authoring a
    frozen case and belongs to review, not to a measurement run.
    """
    import inspect

    pool = by_id(cases, "C3-position-third").pool()
    assert [c.timestamp for c in pool] == sorted(c.timestamp for c in pool), (
        "the harness renders oldest first"
    )
    assert "reverse=True" in inspect.getsource(corrections.candidates), (
        "production renders newest first — if this changed, the divergence may be "
        "closed and this test's reasoning is stale"
    )


def test_the_harness_goes_through_corrections_classify_only(monkeypatch, cases):
    seen = []
    real = corrections.classify

    def spy(*args, **kwargs):
        seen.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(corrections, "classify", spy)
    script_classifier(monkeypatch, "NONE")
    case = by_id(cases, "C1-day-replaced")

    result = correction_eval.run([case], runs=2)

    assert len(seen) == 2
    assert seen[0][0] == case.new_message and seen[0][3] == case.speaker_role
    assert [r.outcome for r in result.results[0].runs] == [correction_eval.MISSED] * 2


def test_the_speaker_name_reaches_the_prompt(monkeypatch, cases):
    calls = script_classifier(monkeypatch, "NONE")

    correction_eval.run([by_id(cases, "C5-jodie-correction")], runs=1)

    assert "Jodie" in calls["prompts"][0] and "Lyle" not in calls["prompts"][0]


def test_a_wrong_target_is_its_own_outcome_and_never_a_pass(monkeypatch):
    script_classifier(monkeypatch, "CORRECTS 2 REPLACED\n- the list | replaced")

    result = correction_eval.run([make_case()], runs=2).results[0]

    assert [r.outcome for r in result.runs] == [correction_eval.WRONG_TARGET] * 2
    assert [r.linked_target for r in result.runs] == ["p2", "p2"]
    assert result.correct == 0 and result.state == "FAIL"

    t = correction_eval.tally([result])["overall"]
    assert (t.wrong_target, t.missed, t.false_links) == (2, 0, 0)
    assert t.link_expected == 2


def test_the_right_link_with_the_wrong_label_is_wrong_state_not_a_pass(monkeypatch):
    """CO8's new failure mode. The link is correct and the annotation task 3.5
    renders is the wrong one — the content is right and the framing is not, which
    neither `ok` nor `wrong_target` can say."""
    script_classifier(monkeypatch, "CORRECTS 1 CONTRADICTED\n- a | b")

    result = correction_eval.run([make_case()], runs=2).results[0]

    assert [r.outcome for r in result.runs] == [correction_eval.WRONG_STATE] * 2
    assert [r.linked_state for r in result.runs] == ["contradicted", "contradicted"]
    assert result.correct == 0 and result.state == "FAIL"

    t = correction_eval.tally([result])["overall"]
    assert (t.wrong_state, t.wrong_target, t.missed, t.false_links) == (2, 0, 0, 0)
    assert t.link_expected == 2


def test_a_wrong_target_outranks_a_wrong_label(monkeypatch):
    """Both are wrong at once; the scoring must report the worse one. A link to a
    claim nobody corrected is a stronger failure than a mislabelled correct link,
    and reporting it as a labelling problem would understate it."""
    script_classifier(monkeypatch, "CORRECTS 2 CONTRADICTED\n- a | b")

    result = correction_eval.run([make_case()], runs=1).results[0]

    assert [r.outcome for r in result.runs] == [correction_eval.WRONG_TARGET]


def test_a_contradicted_expectation_scores_ok_when_labelled_so(monkeypatch):
    script_classifier(monkeypatch, "CORRECTS 1 CONTRADICTED\n- a | b")

    result = correction_eval.run(
        [make_case(replacement="contradicted")], runs=2).results[0]

    assert result.state == "PASS" and result.correct == 2


def test_an_unlabelled_reply_is_a_scored_miss_not_an_excluded_run(monkeypatch):
    """R4's cost, made visible in the measurement rather than only in the design.

    A candidate named without a state is unusable, so production writes no link and
    the run is scored `missed`. It must **not** become `unavailable`: that would
    drop it from the denominator, and a model that systematically omitted the label
    would report a clean 0% while linking nothing at all.
    """
    script_classifier(monkeypatch, "CORRECTS 1\n- a | b")

    result = correction_eval.run([make_case()], runs=2).results[0]

    assert [r.outcome for r in result.runs] == [correction_eval.MISSED] * 2
    t = correction_eval.tally([result])["overall"]
    assert (t.missed, t.link_expected, t.unavailable) == (2, 2, 0)
    assert t.unusable_replies == 2, "the cause must stay visible beside the rate"
    assert "unusable replies (scored) 1" in correction_eval.render(
        correction_eval.run([make_case()], runs=1))


def test_only_an_unreachable_classifier_is_unavailable(monkeypatch):
    """The line between "the checker was down" and "the checker answered badly".
    Excluding the second would hide it; excluding the first is correct, because no
    judgment was made at all."""
    script_classifier(monkeypatch, ollama.OllamaUnreachable("down"))

    result = correction_eval.run([make_case()], runs=2).results[0]

    assert [r.outcome for r in result.runs] == [correction_eval.UNAVAILABLE] * 2
    t = correction_eval.tally([result])["overall"]
    assert (t.unavailable, t.unusable_replies, t.link_expected) == (2, 0, 0)


def test_a_false_link_and_a_miss_are_counted_separately(monkeypatch):
    script_classifier(monkeypatch, "CORRECTS 1 REPLACED\n- x | y")
    false_link = correction_eval.run(
        [make_case(id="neg", should_link=False, target=None)], runs=3).results[0]
    script_classifier(monkeypatch, "NONE")
    missed = correction_eval.run([make_case(id="pos")], runs=2).results[0]

    t = correction_eval.tally([false_link, missed])["overall"]

    assert (t.false_links, t.no_link_expected) == (3, 3)
    assert (t.missed, t.link_expected) == (2, 2)
    assert t.wrong_target == 0
    assert false_link.state == missed.state == "FAIL"


def test_the_expected_link_scores_as_ok(monkeypatch):
    script_classifier(monkeypatch, "CORRECTS 1 REPLACED\n- the day | replaced Tuesday")

    result = correction_eval.run([make_case()], runs=2).results[0]

    assert result.state == "PASS" and result.correct == 2
    t = correction_eval.tally([result])["overall"]
    assert (t.missed, t.wrong_target, t.false_links) == (0, 0, 0)


def test_disagreeing_runs_are_unstable_not_rounded(monkeypatch):
    script_classifier(monkeypatch, "NONE", "CORRECTS 1 REPLACED\n- x | y", "NONE")

    result = correction_eval.run([make_case()], runs=3).results[0]

    assert result.state == "UNSTABLE"
    assert result.correct == 1 and len(result.scored) == 3


def test_unavailable_is_excluded_from_the_rates_not_scored_as_no_link(monkeypatch):
    script_classifier(
        monkeypatch, ollama.OllamaUnreachable("down"), "CORRECTS 1 REPLACED\n- x | y")

    result = correction_eval.run([make_case()], runs=4).results[0]

    t = correction_eval.tally([result])["overall"]
    assert t.unavailable == 2
    assert t.link_expected == 2, (
        "an unavailable run must not read as a miss — the classifier never answered"
    )
    assert t.missed == 0


def test_every_run_unavailable_is_unmeasured(monkeypatch):
    script_classifier(monkeypatch, ollama.OllamaUnreachable("down"))

    result = correction_eval.run([make_case()], runs=2).results[0]

    assert result.state == "UNMEASURED" and result.scored == []


def test_the_role_guard_needs_no_classifier_call(monkeypatch, cases):
    """CO4 holds by construction: with no eligible candidate there is nothing to
    ask, so a down classifier cannot turn this case into `unavailable`."""
    calls = script_classifier(monkeypatch, ollama.OllamaUnreachable("down"))

    result = correction_eval.run([by_id(cases, "G1-role-guard")], runs=3).results[0]

    assert calls["n"] == 0
    assert result.state == "PASS" and result.correct == 3


# --- the sampling regime (decision #22) --------------------------------------
#
# Repeated identical calls to Ollama are correlated: a tight loop reported 10/10
# on a borderline gate case while interposing a different prompt gave 4/10. A
# consecutive run therefore reports the first sample's luck N times and calls it
# unanimity, which is how every frozen number before 2026-09-17 was measured.


def test_samples_are_taken_round_robin_not_back_to_back(monkeypatch, cases):
    seen = []
    real = corrections.classify

    def spy(new_message, *args, **kwargs):
        seen.append(new_message)
        return real(new_message, *args, **kwargs)

    monkeypatch.setattr(corrections, "classify", spy)
    script_classifier(monkeypatch, "NONE")

    correction_eval.run(cases[:4], runs=3)

    assert len(seen) == 12
    assert not any(a == b for a, b in zip(seen, seen[1:])), (
        "the same case was sampled twice in a row — that is the correlated regime"
    )
    assert seen[:4] == seen[4:8] == seen[8:], "each pass covers every case once"


def test_every_case_still_gets_every_run_in_file_order(monkeypatch, cases):
    script_classifier(monkeypatch, "NONE")

    report = correction_eval.run(cases[:5], runs=4)

    assert [r.case.id for r in report.results] == [c.id for c in cases[:5]]
    assert all(len(r.runs) == 4 for r in report.results)


def test_a_single_case_run_declares_itself_correlated(monkeypatch):
    script_classifier(monkeypatch, "NONE")

    report = correction_eval.run([make_case()], runs=3)

    assert report.header["decorrelated"] is False
    rendered = correction_eval.render(report)
    assert "WARNING" in rendered and "not a finding" in rendered


def test_a_multi_case_run_declares_itself_decorrelated(monkeypatch, cases):
    script_classifier(monkeypatch, "NONE")

    report = correction_eval.run(cases, runs=1)

    assert report.header["decorrelated"] is True
    assert "WARNING" not in correction_eval.render(report)


def test_runs_below_one_is_refused(cases):
    with pytest.raises(ValueError, match="at least 1"):
        correction_eval.run(cases, runs=0)


# --- the report --------------------------------------------------------------


def test_the_report_breaks_down_by_kind_and_names_the_frozen_set(monkeypatch, cases):
    script_classifier(monkeypatch, "NONE")

    report = correction_eval.run(cases, runs=1)
    data = report.to_dict()
    text = correction_eval.render(report)

    assert set(data["by_kind"]) == {c.kind for c in cases}
    assert len(data["cases"]) == len(cases)
    assert data["header"]["cases_fingerprint"] == FROZEN_FINGERPRINT
    for key in ("classifier_model", "temperature", "runs_per_case", "sampling"):
        assert key in data["header"]
    for heading in ("PER CASE", "OVERALL", "BY KIND", "CASE STATES"):
        assert heading in text
    assert "wrong target" in text, "the third outcome must be visible, not folded in"
    assert "wrong state" in text, "so must the fourth"


def test_a_rate_with_no_denominator_is_not_reported_as_zero(monkeypatch, cases):
    script_classifier(monkeypatch, "NONE")

    report = correction_eval.run([by_id(cases, "N1-elaboration")], runs=1)

    assert "missed n/a" in correction_eval.render(report)


# --- the command-line shell --------------------------------------------------


def test_the_script_runs_a_filtered_case_and_writes_json(monkeypatch, tmp_path, capsys):
    script_classifier(monkeypatch, "NONE")
    out = tmp_path / "report.json"

    code = correction_eval_cli.main(
        ["--runs", "2", "--case", "N1-elaboration", "--json", str(out)])

    assert code == 0
    assert "N1-elaboration" in capsys.readouterr().out
    data = json.loads(out.read_text())
    assert [c["id"] for c in data["cases"]] == ["N1-elaboration"]
    assert data["header"]["filtered_to"] == ["N1-elaboration"]
    assert data["header"]["cases_fingerprint"] == FROZEN_FINGERPRINT, (
        "a filtered run still identifies the full frozen set it was drawn from"
    )


def test_the_script_refuses_an_unknown_case_id(monkeypatch, capsys):
    script_classifier(monkeypatch, "NONE")
    assert correction_eval_cli.main(["--case", "no-such-case"]) == 2
    assert "unknown case id" in capsys.readouterr().err


def test_the_script_refuses_a_malformed_case_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(correction_eval, "CASES_PATH", write(tmp_path, "[[case]\n"))
    assert correction_eval_cli.main([]) == 2
    assert "case file error" in capsys.readouterr().err
