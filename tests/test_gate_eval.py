"""The fabrication gate's eval harness. Design F5.

These tests check the **harness**: that the frozen case set is intact and covers
what the design owes, that the loader refuses a malformed case rather than
dropping it, and that scoring counts what it says it counts. The classifier is
scripted throughout.

**Nothing here asserts the gate's accuracy.** A test pinning "case X passes"
would turn the measurement into a pass bar, and the pressure to keep a suite
green is exactly the pressure to tune the gate or the case until it does. The
accuracy is whatever ``python -m scripts.fabrication_eval`` reports, reviewed by
a person.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from program.engine import ollama
from program.integrity import gate, gate_eval
from scripts import fabrication_eval

#: The frozen case set's fingerprint. **If this test fails, a case's inputs or
#: expected verdict changed, or a case was added or removed.** That is a change
#: to what the gate is measured against and carries the same review weight as a
#: change to the gate itself — update this value only as part of a reviewed
#: change that says why, never to make the suite pass.
#:
#: History: 45ea9a72… (30 cases, first run) → 495221c0… (2026-09-15, review
#: approved adding S4-timeout-claimed-failure-prose to close a coverage gap) →
#: c7216ec3… (2026-09-16, revision-3 review O11 added S5-unlisted-vocabulary and
#: S6-cross-sentence-attribution, the two cases the proposed deterministic rule
#: v2 is known to miss, so 3.6d can test whether v2 fixes them) →
#: 627834b1… (2026-09-16, revision-4 review O15 added N16-youd-ambiguity, the
#: pronoun rewrite's documented had/would limit, on O11's precedent that an
#: accepted limitation belongs in the measured record).
FROZEN_FINGERPRINT = "627834b1e03c70e8f923cb1e13a3c31e06a0a50e46f69e70f580cedfe91b2dab"

CONTRADICTS = "CONTRADICTS\n- the phrase | the fact"


@pytest.fixture(scope="module")
def cases():
    return gate_eval.load_cases()


def by_id(cases, case_id):
    return next(c for c in cases if c.id == case_id)


def script_classifier(monkeypatch, *replies):
    """Replay replies in order, cycling. An Exception instance is raised."""
    calls = {"n": 0}

    def fake(*_args, **_kwargs):
        reply = replies[calls["n"] % len(replies)]
        calls["n"] += 1
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(gate.classifier, "classify", fake)
    return calls


def make_case(**overrides):
    base = dict(
        id="x", claim_class="identity", sub_case="demo", should_flag=False,
        answer="Dublin is the capital of Ireland.", situation="", soul="live", note="n",
    )
    return gate_eval.Case(**{**base, **overrides})


# --- the frozen set ----------------------------------------------------------


def test_the_frozen_case_set_has_not_changed(cases):
    assert gate_eval.fingerprint(cases) == FROZEN_FINGERPRINT, (
        "the frozen case set changed — see FROZEN_FINGERPRINT's comment before touching it"
    )


def test_informational_fields_are_not_fingerprinted_but_expectations_are(cases):
    edited_note = [dataclasses.replace(c, note="reworded", documented="reworded") for c in cases]
    assert gate_eval.fingerprint(edited_note) == gate_eval.fingerprint(cases)

    flipped = [dataclasses.replace(cases[0], should_flag=not cases[0].should_flag), *cases[1:]]
    assert gate_eval.fingerprint(flipped) != gate_eval.fingerprint(cases)

    retraced = [dataclasses.replace(cases[0], trace=()), *cases[1:]]
    assert gate_eval.fingerprint(retraced) != gate_eval.fingerprint(cases)


def test_both_claim_classes_are_covered(cases):
    assert {c.claim_class for c in cases} == {"tool_output", "identity"}


@pytest.mark.parametrize("sub_case, should_flag, at_least", [
    ("invented_id", True, 1),
    ("unrun_tool", True, 1),
    ("failed_claimed_success", True, 1),
    ("timeout", True, 2),            # both directions: claimed success AND claimed failure
    ("user_continuity", False, 1),
    ("tool_duration", False, 1),
    ("ordinary_phrasing", False, 1),
    ("continuity_topic", False, 1),
    ("accurate_failure", False, 1),
    ("situation_denial", False, 2),
    ("self_training", True, 2),
    ("self_training", False, 1),     # without negatives the sub-case has no FP rate
    ("continuity_fabrication", True, 2),
])
def test_every_sub_case_the_design_owes_is_present(cases, sub_case, should_flag, at_least):
    matching = [c for c in cases if c.sub_case == sub_case and c.should_flag is should_flag]
    assert len(matching) >= at_least


def test_timeout_is_its_own_sub_case_not_folded_into_failure(cases):
    timeouts = [c for c in cases if c.sub_case == "timeout"]
    assert timeouts
    for case in timeouts:
        assert {e["outcome"] for e in case.trace} == {"timeout"}
    assert not any(
        e["outcome"] == "timeout" for c in cases if c.sub_case == "failed_claimed_success"
        for e in c.trace
    )


def test_the_known_failures_are_in_the_set_and_marked(cases):
    for case_id in ("N5-user-continuity", "N10-denial-with-situation"):
        case = by_id(cases, case_id)
        assert case.should_flag is False
        assert case.documented and "KNOWN FAILING" in case.documented


def test_the_situation_contrast_differs_only_in_the_situation(cases):
    with_block = by_id(cases, "N10-denial-with-situation")
    without = by_id(cases, "N10-denial-without-situation")

    assert with_block.answer == without.answer
    assert with_block.trace == without.trace
    assert with_block.situation.strip() and without.situation == ""
    assert "I did not do anything. I was not running" in with_block.answer


def test_the_stored_situation_block_passes_prompt_assembly(cases):
    """The literal block must be one production could really send: stating the
    figure without the pairing would make build_system_prompt() raise."""
    from program.engine import prompt

    for case in cases:
        if case.situation:
            prompt.build_system_prompt(case.situation)


# --- the loader --------------------------------------------------------------


GOOD = '''
[[case]]
id = "a"
claim_class = "identity"
sub_case = "demo"
should_flag = false
answer = "hello"
situation = ""
soul = "live"
note = "n"
'''


def write(tmp_path, text):
    path = tmp_path / "cases.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_well_formed_file_loads(tmp_path):
    [case] = gate_eval.load_cases(write(tmp_path, GOOD))
    assert case.id == "a" and case.trace == ()


@pytest.mark.parametrize("text, message", [
    (GOOD.replace('note = "n"\n', ""), "missing note"),
    (GOOD + 'surprise = 1\n', "unknown field"),
    (GOOD.replace("should_flag = false", 'should_flag = "false"'), "should_flag"),
    (GOOD.replace('"identity"', '"vibes"'), "claim_class"),
    (GOOD.replace('soul = "live"', 'soul = "copy.md"'), "soul must be"),
    (GOOD.replace('answer = "hello"', 'answer = "  "'), "answer is empty"),
    (GOOD + GOOD, "duplicate case id"),
    (GOOD + '[[case.trace]]\ntool = "web_search"\noutcome = "ok"\n', "missing call_id"),
    (GOOD + '[[case.trace]]\ncall_id = "c"\ntool = "t"\noutcome = "fine"\n', "outcome"),
    (GOOD + '[[case.trace]]\ncall_id = "c"\ntool = "t"\noutcome = "ok"\nextra = 1\n',
     "unknown field"),
    ("", "no [[case]] entries"),
    ("[[case]\n", "not valid TOML"),
])
def test_a_malformed_case_raises_rather_than_being_skipped(tmp_path, text, message):
    with pytest.raises(gate_eval.CaseFileError, match=message.replace("[", r"\[")):
        gate_eval.load_cases(write(tmp_path, text))


# --- running and scoring -----------------------------------------------------


def test_the_harness_goes_through_gate_check_only(monkeypatch, cases):
    seen = []
    real = gate.check

    def spy(*args, **kwargs):
        seen.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(gate, "check", spy)
    monkeypatch.setattr(gate, "semantic_findings", lambda *a, **k: [])
    case = by_id(cases, "S4-timeout-claimed-failure")

    result = gate_eval.run_case(case, "RUBRIC", runs=2)

    assert len(seen) == 2
    assert seen[0][0] == case.answer and seen[0][2] == case.situation
    assert [r.rules for r in result.runs] == [("failure_over_timeout",)] * 2


def test_a_false_positive_and_a_false_negative_are_counted_separately(monkeypatch):
    script_classifier(monkeypatch, CONTRADICTS)
    fp = gate_eval.run_case(make_case(id="neg", should_flag=False), "RUBRIC", runs=3)
    script_classifier(monkeypatch, "CONSISTENT")
    fn = gate_eval.run_case(make_case(id="pos", should_flag=True), "RUBRIC", runs=2)

    t = gate_eval.tally([fp, fn])["overall"]

    assert (t.false_positives, t.negative_runs, t.fp_rate) == (3, 3, 1.0)
    assert (t.false_negatives, t.positive_runs, t.fn_rate) == (2, 2, 1.0)
    assert fp.state == fn.state == gate_eval.FAIL


def test_disagreeing_runs_are_unstable_not_rounded(monkeypatch):
    script_classifier(monkeypatch, CONTRADICTS, "CONSISTENT", "CONSISTENT")
    result = gate_eval.run_case(make_case(should_flag=False), "RUBRIC", runs=3)

    assert result.state == gate_eval.UNSTABLE
    assert (result.flag_count, result.correct_count) == (1, 2)


def test_unavailable_is_excluded_from_both_rates_not_scored_as_clean(monkeypatch):
    script_classifier(monkeypatch, ollama.OllamaUnreachable("down"), "CONSISTENT")
    result = gate_eval.run_case(make_case(should_flag=False), "RUBRIC", runs=4)

    t = gate_eval.tally([result])["overall"]

    assert result.unavailable_count == 2
    assert t.negative_runs == 2, "an unavailable run would otherwise read as a correct pass"
    assert t.unavailable_runs == 2


def test_every_run_unavailable_is_unmeasured_and_the_report_says_so(
    monkeypatch, isolated_data_dir
):
    script_classifier(monkeypatch, ollama.OllamaUnreachable("down"))
    report = gate_eval.run([make_case()], runs=2, ground_truth="RUBRIC")

    assert report.results[0].state == gate_eval.UNMEASURED
    t = gate_eval.tally(report.results)["overall"]
    assert t.fp_rate is None and t.fn_rate is None, "no denominator is not a 0% rate"
    assert "NOTHING WAS MEASURED" in gate_eval.render(report)


def test_a_structural_flag_while_the_classifier_is_down_is_still_scored(monkeypatch, cases):
    script_classifier(monkeypatch, ollama.OllamaUnreachable("down"))
    result = gate_eval.run_case(by_id(cases, "S2-unrun-tool-named"), "RUBRIC", runs=1)

    [run] = result.runs
    assert run.flagged and run.scored and not run.semantic_checked


def test_false_positives_are_attributed_to_the_half_that_fired(monkeypatch):
    """Bookkeeping, not accuracy: the deterministic case below is a *synthetic*
    negative — an answer the rules correctly flag, declared `should_flag=False`
    purely to drive a tool_output false positive through the tally. No frozen
    case does that any more, which is task 3.6c working as intended."""
    script_classifier(monkeypatch, "CONSISTENT")
    structural = gate_eval.run_case(
        make_case(id="synthetic", claim_class="tool_output",
                  answer="I searched the web and found it."),
        "RUBRIC", runs=1)
    script_classifier(monkeypatch, CONTRADICTS)
    semantic = gate_eval.run_case(make_case(), "RUBRIC", runs=1)

    t = gate_eval.tally([structural, semantic])["overall"]

    assert t.fp_by_finding_class == {"tool_output": 1, "identity": 1}


def test_the_report_breaks_down_by_class_and_sub_case(monkeypatch, cases, isolated_data_dir):
    script_classifier(monkeypatch, "CONSISTENT")
    report = gate_eval.run(cases, runs=1, ground_truth="RUBRIC")
    text = gate_eval.render(report)
    data = report.to_dict()

    assert set(data["by_claim_class"]) == {"tool_output", "identity"}
    assert set(data["by_sub_case"]) == {c.sub_case for c in cases}
    assert len(data["cases"]) == len(cases)
    for heading in ("PER CASE", "OVERALL", "BY CLAIM CLASS", "BY SUB-CASE"):
        assert heading in text
    for key in ("chat_model", "classifier_model", "ground_truth_sha256",
                "cases_fingerprint", "temperature"):
        assert key in data["header"]
    assert data["header"]["cases_fingerprint"] == FROZEN_FINGERPRINT


# --- the command-line shell --------------------------------------------------


def test_the_script_runs_a_filtered_case_and_writes_json(
    monkeypatch, tmp_path, capsys, isolated_data_dir
):
    script_classifier(monkeypatch, "CONSISTENT")
    out = tmp_path / "report.json"

    code = fabrication_eval.main(
        ["--runs", "2", "--case", "N7-ordinary-fact", "--json", str(out)]
    )

    assert code == 0
    assert "N7-ordinary-fact" in capsys.readouterr().out
    data = json.loads(out.read_text())
    assert [c["id"] for c in data["cases"]] == ["N7-ordinary-fact"]
    assert data["header"]["filtered_to"] == ["N7-ordinary-fact"]
    assert data["header"]["cases_fingerprint"] == FROZEN_FINGERPRINT, (
        "a filtered run still identifies the full frozen set it was drawn from"
    )


def test_the_script_refuses_an_unknown_case_id(monkeypatch, capsys):
    script_classifier(monkeypatch, "CONSISTENT")
    assert fabrication_eval.main(["--case", "no-such-case"]) == 2
    assert "unknown case id" in capsys.readouterr().err



# --- the sampling regime (2026-09-17) ----------------------------------------
#
# Repeated identical calls to Ollama are correlated: a tight loop gave 10/10 on a
# borderline case while interposing a different prompt gave 4/10. A consecutive
# run therefore reports the first sample's luck N times and calls it unanimity,
# which is how every frozen number before this date was measured.


def test_samples_are_taken_round_robin_not_back_to_back(monkeypatch, cases):
    """The property, asserted on call order rather than inferred from results."""
    seen = []

    def record(answer, *a, **k):
        seen.append(answer)
        return "CONSISTENT"

    monkeypatch.setattr(gate.classifier, "classify", lambda *a, **k: "CONSISTENT")
    monkeypatch.setattr(gate, "check", lambda answer, *a, **k: (
        seen.append(answer), gate.GateVerdict())[1])

    gate_eval.run(cases[:4], runs=3, ground_truth="RUBRIC")

    assert len(seen) == 12
    assert not any(a == b for a, b in zip(seen, seen[1:])), (
        "the same case was sampled twice in a row — that is the correlated regime"
    )
    assert seen[:4] == seen[4:8] == seen[8:], "each pass covers every case once"


def test_every_case_still_gets_every_run(monkeypatch, cases):
    script_classifier(monkeypatch, "CONSISTENT")

    report = gate_eval.run(cases[:5], runs=4, ground_truth="RUBRIC")

    assert len(report.results) == 5
    for result in report.results:
        assert len(result.runs) == 4
    assert [r.case.id for r in report.results] == [c.id for c in cases[:5]], (
        "round-robin sampling must not reorder the report"
    )


def test_a_single_case_run_declares_itself_correlated(monkeypatch, cases):
    """With nothing to interleave with, a pass *is* a tight loop. Said out loud
    rather than quietly reported as a finding."""
    script_classifier(monkeypatch, "CONSISTENT")

    report = gate_eval.run(cases[:1], runs=3, ground_truth="RUBRIC")

    assert report.header["decorrelated"] is False
    rendered = gate_eval.render(report)
    assert "WARNING" in rendered and "not a finding" in rendered


def test_a_multi_case_run_declares_itself_decorrelated(monkeypatch, cases):
    script_classifier(monkeypatch, "CONSISTENT")

    report = gate_eval.run(cases[:3], runs=2, ground_truth="RUBRIC")

    assert report.header["decorrelated"] is True
    assert report.header["sampling"] == "round-robin"
    assert "WARNING" not in gate_eval.render(report)


def test_the_harness_is_blind_to_the_advisory_channel(monkeypatch, cases):
    """F34.5, and the one that protects the measurement: the frozen numbers
    cannot move because of a channel that has no authority.

    Scored twice over the same cases — once where the classifier says nothing,
    once where every reply is a tool-labelled objection — and the report must be
    byte-identical apart from the advisory content the harness does not read."""
    script_classifier(monkeypatch, "CONSISTENT")
    quiet = gate_eval.run(cases[:6], runs=2, ground_truth="RUBRIC").to_dict()

    script_classifier(monkeypatch, "CONTRADICTS-TOOL\n- something | the trace")
    noisy = gate_eval.run(cases[:6], runs=2, ground_truth="RUBRIC").to_dict()

    assert quiet["overall"] == noisy["overall"]
    assert quiet["by_claim_class"] == noisy["by_claim_class"]
    assert [c["state"] for c in quiet["cases"]] == [c["state"] for c in noisy["cases"]]
