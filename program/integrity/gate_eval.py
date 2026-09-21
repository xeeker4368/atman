"""The fabrication gate's eval harness. Design F5; `BUILD_PLAN`'s Phase 3 row.

Runs ``gate.check()`` over the frozen cases in
``eval/fabrication_gate/cases.toml`` and reports how often its verdict matched
the expected one. ``scripts/fabrication_eval.py`` is the command-line shell.

**A measurement, not a pass bar.** Nothing here decides whether the gate is good
enough — that is the review ``BUILD_PLAN``'s Phase 3 checkpoint requires before
stage 2 is considered. The report is therefore never collapsed to one number:
false-positive and false-negative rates are reported separately, overall, by
claim class and by sub-case, and every case lists which rules fired.

Only :func:`gate.check` is called — the one entry point production uses. Calling
the structural or semantic half directly would measure something production
never runs.

Runs, not cases — and round-robin, not back to back
===================================================
The classifier samples at the configured temperature, so one case can flag on
some runs and not others. Each case is sampled ``runs`` times; rates are counted
over runs, and a case whose runs disagree is reported ``UNSTABLE`` rather than
rounded to whichever side won.

**Samples are taken one pass at a time across the whole set, never N times in a
row on one case.** Repeated identical calls to Ollama are correlated — a tight
loop gave 10/10 on a borderline case while interposing a different prompt gave
4/10 — so a consecutive run reports the first sample's luck N times and calls it
unanimity. Every frozen number taken before 2026-09-17 was measured that way.
See ``changelog/2026-09-17-n7-stability.md``.

``unavailable`` is not a verdict
================================
A run whose status is ``unavailable`` — the classifier could not run and nothing
structural fired — is excluded from both rates and counted on its own. Scoring it
as "not flagged" would turn an unreachable model into a perfect false-positive
rate. A run that is ``flagged`` while the classifier was unavailable is still a
verdict, since the exact half does not need the model, and is scored.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from program import config
from program.integrity import gate
from program.tools import registry as tool_registry

CASES_PATH = config.PROJECT_ROOT / "eval" / "fabrication_gate" / "cases.toml"

_CLAIM_CLASSES = {c.value for c in gate.ClaimClass}
_OUTCOMES = {o.value for o in tool_registry.ToolOutcome}

#: Fields that define what a case measures. Fingerprinted — see
#: :func:`fingerprint`.
_FROZEN_FIELDS = ("id", "claim_class", "sub_case", "should_flag", "answer", "situation", "soul")
#: Informational. Not fingerprinted, never scored.
_INFO_FIELDS = ("note", "documented")
_REQUIRED = set(_FROZEN_FIELDS) | {"note"}
_ALLOWED = set(_FROZEN_FIELDS) | set(_INFO_FIELDS) | {"trace"}

_TRACE_REQUIRED = {"call_id", "tool", "outcome"}
_TRACE_ALLOWED = _TRACE_REQUIRED | {
    "arguments", "ran", "value", "error", "duration_seconds", "timeout_seconds", "iteration",
}

#: The only accepted ``soul`` value: the real file, as the gate reads it.
LIVE_SOUL = "live"


class CaseFileError(ValueError):
    """The case file is malformed. Raised, never skipped: a silently dropped case
    is a gap in the measurement nobody would see."""


@dataclass(frozen=True)
class Case:
    id: str
    claim_class: str
    sub_case: str
    should_flag: bool
    answer: str
    situation: str
    soul: str
    note: str
    documented: str | None = None
    trace: tuple[dict[str, Any], ...] = ()


def _require_str(case_id: str, key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise CaseFileError(f"case {case_id!r}: {key} must be a string, got {type(value).__name__}")
    return value


def _parse_case(raw: dict[str, Any], position: int) -> Case:
    case_id = raw.get("id", f"<case #{position}>")
    missing = _REQUIRED - raw.keys()
    if missing:
        raise CaseFileError(f"case {case_id!r}: missing {', '.join(sorted(missing))}")
    unknown = raw.keys() - _ALLOWED
    if unknown:
        raise CaseFileError(f"case {case_id!r}: unknown field(s) {', '.join(sorted(unknown))}")

    for key in ("id", "claim_class", "sub_case", "answer", "situation", "soul", "note"):
        _require_str(case_id, key, raw[key])
    if raw.get("documented") is not None:
        _require_str(case_id, "documented", raw["documented"])
    if not raw["id"].strip():
        raise CaseFileError(f"case #{position}: id is empty")
    if not isinstance(raw["should_flag"], bool):
        raise CaseFileError(f"case {case_id!r}: should_flag must be true or false")
    if raw["claim_class"] not in _CLAIM_CLASSES:
        raise CaseFileError(
            f"case {case_id!r}: claim_class {raw['claim_class']!r} is not one of "
            f"{sorted(_CLAIM_CLASSES)}"
        )
    if raw["soul"] != LIVE_SOUL:
        raise CaseFileError(f"case {case_id!r}: soul must be {LIVE_SOUL!r}")
    if not raw["answer"].strip():
        raise CaseFileError(f"case {case_id!r}: answer is empty")

    trace_raw = raw.get("trace", [])
    if not isinstance(trace_raw, list):
        raise CaseFileError(f"case {case_id!r}: trace must be a list of tables")
    trace = []
    for i, entry in enumerate(trace_raw):
        if not isinstance(entry, dict):
            raise CaseFileError(f"case {case_id!r}: trace entry {i} is not a table")
        if _TRACE_REQUIRED - entry.keys():
            raise CaseFileError(
                f"case {case_id!r}: trace entry {i} missing "
                f"{', '.join(sorted(_TRACE_REQUIRED - entry.keys()))}"
            )
        if entry.keys() - _TRACE_ALLOWED:
            raise CaseFileError(
                f"case {case_id!r}: trace entry {i} has unknown field(s) "
                f"{', '.join(sorted(entry.keys() - _TRACE_ALLOWED))}"
            )
        if entry["outcome"] not in _OUTCOMES:
            raise CaseFileError(
                f"case {case_id!r}: trace entry {i} outcome {entry['outcome']!r} is not one "
                f"of {sorted(_OUTCOMES)}"
            )
        trace.append(dict(entry))

    return Case(
        id=raw["id"],
        claim_class=raw["claim_class"],
        sub_case=raw["sub_case"],
        should_flag=raw["should_flag"],
        answer=raw["answer"],
        situation=raw["situation"],
        soul=raw["soul"],
        note=raw["note"],
        documented=raw.get("documented"),
        trace=tuple(trace),
    )


def load_cases(path: Path | None = None) -> list[Case]:
    """Read and validate the case file. Every problem raises."""
    target = path or CASES_PATH
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CaseFileError(f"{target}: not valid TOML: {exc}") from exc

    unknown = data.keys() - {"case"}
    if unknown:
        raise CaseFileError(f"{target}: unknown top-level key(s) {', '.join(sorted(unknown))}")
    raw_cases = data.get("case")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CaseFileError(f"{target}: no [[case]] entries")

    cases = [_parse_case(raw, i) for i, raw in enumerate(raw_cases, start=1)]
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise CaseFileError(f"{target}: duplicate case id {case.id!r}")
        seen.add(case.id)
    return cases


def fingerprint(cases: Iterable[Case]) -> str:
    """sha256 over what each case measures — inputs and expected verdict.

    Excludes ``note`` and ``documented`` so explanatory text can be corrected
    without tripping the freeze, while any change to what is measured cannot.
    """
    canonical = [
        {**{key: getattr(case, key) for key in _FROZEN_FIELDS}, "trace": list(case.trace)}
        for case in cases
    ]
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    """One ``gate.check()`` call on one case."""

    status: str
    rules: tuple[str, ...]
    finding_classes: tuple[str, ...]
    evidence: tuple[str, ...]
    semantic_checked: bool
    semantic_error: str | None

    @classmethod
    def from_verdict(cls, verdict: gate.GateVerdict) -> RunOutcome:
        return cls(
            status=verdict.status.value,
            rules=tuple(f.rule for f in verdict.findings),
            finding_classes=tuple(f.claim_class.value for f in verdict.findings),
            evidence=tuple(str(f.evidence) for f in verdict.findings),
            semantic_checked=verdict.semantic_checked,
            semantic_error=verdict.semantic_error,
        )

    @property
    def scored(self) -> bool:
        """False only for ``unavailable`` — not a verdict, see the module docstring."""
        return self.status != gate.GateStatus.UNAVAILABLE.value

    @property
    def flagged(self) -> bool:
        return self.status == gate.GateStatus.FLAGGED.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rules": list(self.rules),
            "finding_classes": list(self.finding_classes),
            "evidence": list(self.evidence),
            "semantic_checked": self.semantic_checked,
            "semantic_error": self.semantic_error,
        }


PASS, FAIL, UNSTABLE, UNMEASURED = "PASS", "FAIL", "UNSTABLE", "UNMEASURED"


@dataclass
class CaseResult:
    case: Case
    runs: list[RunOutcome] = field(default_factory=list)

    @property
    def scored_runs(self) -> list[RunOutcome]:
        return [r for r in self.runs if r.scored]

    @property
    def flag_count(self) -> int:
        return sum(r.flagged for r in self.scored_runs)

    @property
    def unavailable_count(self) -> int:
        return sum(not r.scored for r in self.runs)

    @property
    def correct_count(self) -> int:
        return sum(r.flagged == self.case.should_flag for r in self.scored_runs)

    @property
    def state(self) -> str:
        scored = len(self.scored_runs)
        if scored == 0:
            return UNMEASURED
        if self.correct_count == scored:
            return PASS
        if self.correct_count == 0:
            return FAIL
        return UNSTABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case.id,
            "claim_class": self.case.claim_class,
            "sub_case": self.case.sub_case,
            "should_flag": self.case.should_flag,
            "state": self.state,
            "flagged_runs": self.flag_count,
            "scored_runs": len(self.scored_runs),
            "unavailable_runs": self.unavailable_count,
            "documented": self.case.documented,
            "runs": [r.to_dict() for r in self.runs],
        }


def sample_once(case: Case, ground_truth: str) -> RunOutcome:
    """One `gate.check()` call on one case."""
    return RunOutcome.from_verdict(gate.check(
        case.answer, list(case.trace), case.situation, ground_truth=ground_truth
    ))


def run_case(case: Case, ground_truth: str, runs: int) -> CaseResult:
    """Sample one case `runs` times **consecutively**.

    **Correlated, and therefore not how a measurement is taken.** Repeated
    identical calls to Ollama produce correlated results: measured on one
    borderline case, a tight loop gave 10/10 while interposing a different
    prompt between samples gave 4/10 (`changelog/2026-09-17-n7-stability.md`).
    A tight loop reports the first sample's luck N times and calls it unanimity.

    Kept because tests use it to exercise scoring deterministically with a
    scripted classifier, where correlation cannot arise. :func:`run` does not
    call it — see :func:`sample_round_robin`.
    """
    if runs < 1:
        raise ValueError("runs must be at least 1")
    result = CaseResult(case)
    for _ in range(runs):
        result.runs.append(sample_once(case, ground_truth))
    return result


def sample_round_robin(
    cases: Sequence[Case], ground_truth: str, runs: int
) -> list[CaseResult]:
    """Sample every case once per pass, `runs` passes — the decorrelated regime.

    Between two samples of the same case sit every other case's prompt, so no
    call can reuse the previous one's state. This is `AGENTS.md`'s "do not sample
    the same prompt back to back", applied to **every** case rather than to ones
    flagged borderline: borderline status cannot be trusted when it was read off
    a correlated run in the first place.

    **A single case cannot be decorrelated this way** — with nothing to interleave
    with, a pass is a tight loop. :func:`run` records that in the header and
    :func:`render` warns, rather than quietly reporting a correlated rate as a
    finding.
    """
    if runs < 1:
        raise ValueError("runs must be at least 1")
    results = {case.id: CaseResult(case) for case in cases}
    for _ in range(runs):
        for case in cases:
            results[case.id].runs.append(sample_once(case, ground_truth))
    return [results[case.id] for case in cases]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class Tally:
    """Rates over scored runs. A rate with no denominator is ``None``, not 0."""

    negative_runs: int = 0
    false_positives: int = 0
    positive_runs: int = 0
    false_negatives: int = 0
    unavailable_runs: int = 0
    #: False-positive runs broken down by the class of finding that fired. One
    #: run can count under both when both halves fired.
    fp_by_finding_class: dict[str, int] = field(default_factory=dict)

    def add(self, result: CaseResult) -> None:
        self.unavailable_runs += result.unavailable_count
        for run in result.scored_runs:
            if result.case.should_flag:
                self.positive_runs += 1
                self.false_negatives += not run.flagged
            else:
                self.negative_runs += 1
                if run.flagged:
                    self.false_positives += 1
                    for cls in sorted(set(run.finding_classes)):
                        self.fp_by_finding_class[cls] = self.fp_by_finding_class.get(cls, 0) + 1

    @property
    def fp_rate(self) -> float | None:
        return self.false_positives / self.negative_runs if self.negative_runs else None

    @property
    def fn_rate(self) -> float | None:
        return self.false_negatives / self.positive_runs if self.positive_runs else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "negative_runs": self.negative_runs,
            "false_positives": self.false_positives,
            "fp_rate": self.fp_rate,
            "positive_runs": self.positive_runs,
            "false_negatives": self.false_negatives,
            "fn_rate": self.fn_rate,
            "unavailable_runs": self.unavailable_runs,
            "fp_by_finding_class": dict(self.fp_by_finding_class),
        }


def tally(results: Sequence[CaseResult], key: str | None = None) -> dict[str, Tally]:
    """Group into tallies by a case attribute, or one ``"overall"`` tally."""
    groups: dict[str, Tally] = {}
    for result in results:
        name = "overall" if key is None else str(getattr(result.case, key))
        groups.setdefault(name, Tally()).add(result)
    return groups


@dataclass
class Report:
    results: list[CaseResult]
    header: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        by_class = tally(self.results, "claim_class")
        by_sub_case = tally(self.results, "sub_case")
        return {
            "header": self.header,
            "overall": tally(self.results)["overall"].to_dict() if self.results else None,
            "by_claim_class": {k: t.to_dict() for k, t in by_class.items()},
            "by_sub_case": {k: t.to_dict() for k, t in by_sub_case.items()},
            "cases": [r.to_dict() for r in self.results],
        }


def run(
    cases: Sequence[Case],
    runs: int,
    ground_truth: str | None = None,
    cases_fingerprint: str | None = None,
) -> Report:
    """Run every case and return the report. The header records what was measured
    with — model, options, soul.md's hash, the case set's fingerprint — so a
    number is always traceable to the configuration that produced it."""
    if ground_truth is None:
        ground_truth = gate.load_architecture()
    options = config.model_options()
    header = {
        "chat_model": config.chat_model(),
        "classifier_model": config.classifier_model(),
        "temperature": options.get("temperature"),
        "think": options.get("think"),
        "ollama_host": config.ollama_host(),
        "runs_per_case": runs,
        "cases": len(cases),
        "cases_fingerprint": cases_fingerprint or fingerprint(cases),
        "ground_truth": "architecture.md",
        "ground_truth_sha256": hashlib.sha256(ground_truth.encode("utf-8")).hexdigest(),
        "registered_tools": list(tool_registry.default_registry().names),
        "sampling": "round-robin" if len(cases) > 1 else "SINGLE CASE - CORRELATED",
        "decorrelated": len(cases) > 1,
    }
    return Report(sample_round_robin(cases, ground_truth, runs), header)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _rate(numerator: int, denominator: int, rate: float | None) -> str:
    if rate is None:
        return "n/a (no runs)"
    return f"{numerator}/{denominator} = {rate:.0%}"


def _tally_lines(name: str, t: Tally) -> list[str]:
    fp = _rate(t.false_positives, t.negative_runs, t.fp_rate)
    fn = _rate(t.false_negatives, t.positive_runs, t.fn_rate)
    line = f"  {name:<24} FP {fp:<16} FN {fn:<16}"
    if t.unavailable_runs:
        line += f" unavailable {t.unavailable_runs}"
    lines = [line.rstrip()]
    if t.fp_by_finding_class:
        parts = ", ".join(f"{k} {v}" for k, v in sorted(t.fp_by_finding_class.items()))
        lines.append(f"  {'':<24} FP runs by finding class: {parts}")
    return lines


def render(report: Report) -> str:
    out: list[str] = ["FABRICATION GATE EVAL", "=" * 72]
    if report.header.get("decorrelated") is False:
        out.append("WARNING: fewer than two cases, so samples were taken back to back.")
        out.append("Repeated identical calls are correlated — this rate is not a finding.")
    for key, value in report.header.items():
        out.append(f"{key:<20}: {value}")

    out += ["", "PER CASE", "-" * 72]
    for r in report.results:
        expect = "flag" if r.case.should_flag else "no flag"
        runs = f"flagged {r.flag_count}/{len(r.scored_runs)}"
        if r.unavailable_count:
            runs += f", unavailable {r.unavailable_count}"
        out.append(f"{r.state:<10} {r.case.id:<36} expect {expect:<8} {runs}")
        fired = sorted({rule for run in r.runs for rule in run.rules})
        if fired:
            out.append(f"{'':<11}rules fired: {', '.join(fired)}")
        errors = sorted({run.semantic_error for run in r.runs if run.semantic_error})
        for error in errors:
            out.append(f"{'':<11}classifier error: {error}")
        if r.case.documented:
            out.append(f"{'':<11}documented: {r.case.documented}")

    if report.results:
        out += ["", "OVERALL  (rates over scored runs; unavailable runs excluded)", "-" * 72]
        out += _tally_lines("overall", tally(report.results)["overall"])
        out += ["", "BY CLAIM CLASS", "-" * 72]
        for name, t in sorted(tally(report.results, "claim_class").items()):
            out += _tally_lines(name, t)
        out += ["", "BY SUB-CASE", "-" * 72]
        for name, t in sorted(tally(report.results, "sub_case").items()):
            out += _tally_lines(name, t)

        states: dict[str, int] = {}
        for r in report.results:
            states[r.state] = states.get(r.state, 0) + 1
        out += ["", "CASE STATES: " + ", ".join(f"{k} {v}" for k, v in sorted(states.items()))]
        if all(r.state == UNMEASURED for r in report.results):
            out.append("NOTHING WAS MEASURED: every run was unavailable. Is Ollama running?")
    return "\n".join(out)
