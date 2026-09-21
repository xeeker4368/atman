"""The correction classifier's eval harness. Task 3.4; `GUIDANCE.md`'s own bar.

*"This mechanism needs its own frozen eval case set before being trusted in
production — same bar as the fabrication gate."* Design obligations: C11.

Runs :func:`corrections.classify` over the frozen cases in
``eval/corrections/cases.toml`` and reports how often it matched. Only that one
entry point is called — the same rule the gate's harness keeps, so what is
measured is what production runs.

Four outcomes, not two
======================
A correction classifier can fail in ways a flag/no-flag detector cannot. It can
link **the wrong prior claim**, and — since CO8 and migration 6 — it can link the
right claim and **label it the wrong way**. Each is scored separately and none is
ever a pass.

* **false link** — a link where none belongs
* **missed** — no link where one belongs
* **wrong target** — a link to a candidate other than the expected one
* **wrong state** — the right candidate, labelled ``replaced`` when the message
  gave no value or ``contradicted`` when it did

They are not equally bad, and the report keeps them apart rather than summing
them. A missed correction leaves the record accurate and merely uncorrected. A
wrong link makes retrieval present something nobody corrected as superseded. A
wrong state links correctly and renders the weaker or stronger annotation — task
3.5's *"corrected, no replacement given"* against *"corrected"* — so the content is
right and the framing is not.

Sampling follows decision #22
=============================
Samples are taken **round-robin across the whole set**, never N times in a row on
one case: repeated identical calls to Ollama are correlated, so a consecutive run
reports the first sample's luck N times and calls it unanimity. A case that is not
unanimous is reported ``UNSTABLE`` and escalates to a larger sample before its
rate is treated as a finding.
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
from program.engine import ollama
from program.integrity import corrections

CASES_PATH = config.PROJECT_ROOT / "eval" / "corrections" / "cases.toml"

_KINDS = {"correction", "contradiction", "elaboration", "doubt", "restatement",
          "topic_change", "self_correction", "role_guard", "ambiguous", "position"}
_ROLES = {"user", "assistant"}
_REPLACEMENTS = {"replaced", "contradicted"}

#: Fingerprinted — what each case measures. `note`/`documented` are excluded so
#: wording can be corrected without tripping the freeze.
_FROZEN = ("id", "kind", "speaker_role", "speaker", "should_link", "target",
           "replacement", "new_message")


class CaseFileError(ValueError):
    """The case file is malformed. Raised, never skipped — a silently dropped
    case is a hole in the measurement nobody would see."""


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    speaker_role: str
    should_link: bool
    new_message: str
    note: str
    #: The household member whose turn this is, as the classifier is told. It is
    #: an *input*, so it is fingerprinted: running the same shape under the other
    #: user's name is a different case, which is how "both users" is covered.
    #: Ignored when the speaker is the entity — that label is "the system".
    speaker: str = "Lyle"
    target: str | None = None
    #: Expected label, required when ``should_link``. ``replaced`` when the new
    #: message supplies the correct value, ``contradicted`` when it only says the
    #: earlier one is wrong (CO8). An expectation, so it is fingerprinted.
    replacement: str | None = None
    documented: str | None = None
    candidates: tuple[dict[str, str], ...] = ()

    def pool(self) -> list[corrections.Candidate]:
        """The prior claims, as the classifier receives them.

        Timestamps are synthesised in order: the harness measures judgment about
        content, and a real `timestamp` would make the fingerprint depend on when
        the file was written.
        """
        return [
            corrections.Candidate(
                message_id=candidate["id"],
                role=candidate["role"],
                content=candidate["content"],
                timestamp=f"2026-09-18T10:{index:02d}:00",
            )
            for index, candidate in enumerate(self.candidates)
        ]


def _parse_case(raw: dict[str, Any], position: int) -> Case:
    case_id = raw.get("id", f"<case #{position}>")
    required = {"id", "kind", "speaker_role", "should_link", "new_message", "note"}
    missing = required - raw.keys()
    if missing:
        raise CaseFileError(f"case {case_id!r}: missing {', '.join(sorted(missing))}")
    unknown = raw.keys() - (
        required | {"speaker", "target", "replacement", "documented", "candidate"})
    if unknown:
        raise CaseFileError(f"case {case_id!r}: unknown field(s) {', '.join(sorted(unknown))}")

    if raw["kind"] not in _KINDS:
        raise CaseFileError(
            f"case {case_id!r}: kind {raw['kind']!r} is not one of {sorted(_KINDS)}")
    if raw["speaker_role"] not in _ROLES:
        raise CaseFileError(f"case {case_id!r}: speaker_role must be user or assistant")
    if not isinstance(raw["should_link"], bool):
        raise CaseFileError(f"case {case_id!r}: should_link must be true or false")
    if not str(raw["new_message"]).strip():
        raise CaseFileError(f"case {case_id!r}: new_message is empty")
    if "speaker" in raw and not str(raw["speaker"]).strip():
        raise CaseFileError(f"case {case_id!r}: speaker is empty")

    candidates = raw.get("candidate", [])
    if not isinstance(candidates, list) or not candidates:
        raise CaseFileError(f"case {case_id!r}: needs at least one [[case.candidate]]")
    for index, candidate in enumerate(candidates):
        if candidate.keys() != {"id", "role", "content"}:
            raise CaseFileError(
                f"case {case_id!r}: candidate {index} needs exactly id, role, content")
        if candidate["role"] not in _ROLES:
            raise CaseFileError(f"case {case_id!r}: candidate {index} role must be user/assistant")

    ids = [c["id"] for c in candidates]
    if len(set(ids)) != len(ids):
        raise CaseFileError(f"case {case_id!r}: duplicate candidate ids")

    target = raw.get("target")
    replacement = raw.get("replacement")
    if raw["should_link"]:
        if not target:
            raise CaseFileError(
                f"case {case_id!r}: should_link needs a target — a link to the wrong "
                f"claim is a failure, so the expected one must be named")
        if target not in ids:
            raise CaseFileError(f"case {case_id!r}: target {target!r} is not a candidate")
        if replacement not in _REPLACEMENTS:
            raise CaseFileError(
                f"case {case_id!r}: should_link needs replacement = "
                f"{' or '.join(sorted(_REPLACEMENTS))} — labelling the right link the "
                f"wrong way is its own failure and needs an expectation to score against")
    else:
        if target:
            raise CaseFileError(
                f"case {case_id!r}: target is meaningless when should_link is false")
        if replacement:
            raise CaseFileError(
                f"case {case_id!r}: replacement is meaningless when should_link is false")

    return Case(
        id=raw["id"], kind=raw["kind"], speaker_role=raw["speaker_role"],
        should_link=raw["should_link"], new_message=raw["new_message"],
        note=raw["note"], speaker=raw.get("speaker", "Lyle"),
        target=target, replacement=replacement, documented=raw.get("documented"),
        candidates=tuple(dict(c) for c in candidates),
    )


def load_cases(path: Path | None = None) -> list[Case]:
    target = path or CASES_PATH
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CaseFileError(f"{target}: not valid TOML: {exc}") from exc

    if data.keys() - {"case"}:
        raise CaseFileError(f"{target}: unknown top-level key(s)")
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
    canonical = [
        {**{key: getattr(case, key) for key in _FROZEN},
         "candidates": list(case.candidates)}
        for case in cases
    ]
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

OK, FALSE_LINK, MISSED, WRONG_TARGET, WRONG_STATE, UNAVAILABLE = (
    "ok", "false_link", "missed", "wrong_target", "wrong_state", "unavailable")


@dataclass(frozen=True)
class RunOutcome:
    outcome: str
    linked_target: str | None = None
    linked_state: str | None = None
    #: The model answered and the reply could not be used — a *scored* failure,
    #: not an excluded one. Counted so a systematic reply-format problem is
    #: visible: without it, a model that never emits the REPLACED/CONTRADICTED
    #: label would read as a clean 0% while writing no links at all.
    unusable: bool = False

    @property
    def scored(self) -> bool:
        return self.outcome != UNAVAILABLE


@dataclass
class CaseResult:
    case: Case
    runs: list[RunOutcome] = field(default_factory=list)

    @property
    def scored(self) -> list[RunOutcome]:
        return [r for r in self.runs if r.scored]

    @property
    def correct(self) -> int:
        return sum(r.outcome == OK for r in self.scored)

    @property
    def state(self) -> str:
        if not self.scored:
            return "UNMEASURED"
        if self.correct == len(self.scored):
            return "PASS"
        if self.correct == 0:
            return "FAIL"
        return "UNSTABLE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case.id, "kind": self.case.kind,
            "should_link": self.case.should_link, "target": self.case.target,
            "replacement": self.case.replacement, "speaker": self.case.speaker,
            "state": self.state, "correct": self.correct, "scored": len(self.scored),
            "outcomes": [r.outcome for r in self.runs],
            "linked_states": sorted({r.linked_state for r in self.runs if r.linked_state}),
        }


def sample_once(case: Case) -> RunOutcome:
    """One `corrections.classify` call, scored into one of the four outcomes.

    **An unusable reply is scored, not excluded.** Only a classifier that could
    not be reached at all becomes ``unavailable``. If the model answers and the
    answer cannot be used — no verdict, several candidates, or (since RO1) a
    candidate named with no ``REPLACED``/``CONTRADICTED`` label — production writes
    no link, so that is exactly how it is scored here. Folding it into
    ``unavailable`` would drop those runs from the denominator and let a model that
    systematically omits the label report a clean 0% while linking nothing.
    """
    try:
        result = corrections.classify(
            case.new_message, "harness-new", case.pool(), case.speaker_role,
            case.speaker if case.speaker_role == "user" else "the system",
        )
    except ollama.OllamaResponseError:
        return RunOutcome(OK if not case.should_link else MISSED, unusable=True)
    except Exception:  # noqa: BLE001 — the classifier was unreachable, not wrong
        return RunOutcome(UNAVAILABLE)

    if result is None:
        return RunOutcome(OK if not case.should_link else MISSED)
    if not case.should_link:
        return RunOutcome(FALSE_LINK, result.superseded_message_id, result.replacement)
    if result.superseded_message_id != case.target:
        # Scored before the label: a link to the wrong claim is the worse failure,
        # and reporting it as a labelling problem would understate it.
        return RunOutcome(WRONG_TARGET, result.superseded_message_id, result.replacement)
    if result.replacement != case.replacement:
        return RunOutcome(WRONG_STATE, result.superseded_message_id, result.replacement)
    return RunOutcome(OK, result.superseded_message_id, result.replacement)


def run(cases: Sequence[Case], runs: int) -> Report:
    """Sample every case once per pass, `runs` passes — decision #22's regime."""
    if runs < 1:
        raise ValueError("runs must be at least 1")
    results = {case.id: CaseResult(case) for case in cases}
    for _ in range(runs):
        for case in cases:
            results[case.id].runs.append(sample_once(case))

    header = {
        "classifier_model": config.classifier_model(),
        "temperature": config.model_options().get("temperature"),
        "runs_per_case": runs,
        "cases": len(cases),
        "cases_fingerprint": fingerprint(cases),
        "sampling": "round-robin" if len(cases) > 1 else "SINGLE CASE - CORRELATED",
        "decorrelated": len(cases) > 1,
    }
    return Report([results[case.id] for case in cases], header)


@dataclass
class Tally:
    link_expected: int = 0
    missed: int = 0
    wrong_target: int = 0
    wrong_state: int = 0
    no_link_expected: int = 0
    false_links: int = 0
    unavailable: int = 0
    unusable_replies: int = 0

    def add(self, result: CaseResult) -> None:
        self.unavailable += len(result.runs) - len(result.scored)
        for run_outcome in result.scored:
            self.unusable_replies += run_outcome.unusable
            if result.case.should_link:
                self.link_expected += 1
                self.missed += run_outcome.outcome == MISSED
                self.wrong_target += run_outcome.outcome == WRONG_TARGET
                self.wrong_state += run_outcome.outcome == WRONG_STATE
            else:
                self.no_link_expected += 1
                self.false_links += run_outcome.outcome == FALSE_LINK

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_expected": self.link_expected, "missed": self.missed,
            "wrong_target": self.wrong_target, "wrong_state": self.wrong_state,
            "no_link_expected": self.no_link_expected, "false_links": self.false_links,
            "unavailable": self.unavailable, "unusable_replies": self.unusable_replies,
        }


def tally(results: Sequence[CaseResult], key: str | None = None) -> dict[str, Tally]:
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
        return {
            "header": self.header,
            "overall": tally(self.results)["overall"].to_dict(),
            "by_kind": {k: t.to_dict() for k, t in tally(self.results, "kind").items()},
            "cases": [r.to_dict() for r in self.results],
        }


def _rate(numerator: int, denominator: int) -> str:
    if not denominator:
        return "n/a"
    return f"{numerator}/{denominator} = {numerator / denominator:.0%}"


def _lines(name: str, t: Tally) -> list[str]:
    out = [f"  {name:<18} false links {_rate(t.false_links, t.no_link_expected):<16}"
           f" missed {_rate(t.missed, t.link_expected):<14}"
           f" wrong target {_rate(t.wrong_target, t.link_expected):<14}"
           f" wrong state {_rate(t.wrong_state, t.link_expected)}"]
    if t.unavailable or t.unusable_replies:
        out.append(f"  {'':<18} unavailable {t.unavailable}"
                   f"   unusable replies (scored) {t.unusable_replies}")
    return out


def render(report: Report) -> str:
    out = ["CORRECTION CLASSIFIER EVAL", "=" * 76]
    if report.header.get("decorrelated") is False:
        out.append("WARNING: fewer than two cases, so samples were taken back to back.")
        out.append("Repeated identical calls are correlated — this rate is not a finding.")
    for key, value in report.header.items():
        out.append(f"{key:<20}: {value}")

    out += ["", "PER CASE", "-" * 76]
    for result in report.results:
        expect = (f"link->{result.case.target}/{result.case.replacement}"
                  if result.case.should_link else "no link")
        out.append(f"{result.state:<10} {result.case.id:<24} {result.case.kind:<16} "
                   f"expect {expect:<26} correct {result.correct}/{len(result.scored)}")
        bad = sorted({r.outcome for r in result.scored if r.outcome != OK})
        if bad:
            got = sorted({f"{r.linked_target}/{r.linked_state}"
                          for r in result.scored if r.linked_target})
            out.append(f"{'':<11}{', '.join(bad)}"
                       + (f" (linked {', '.join(got)})" if got else ""))

    out += ["", "OVERALL", "-" * 76] + _lines("overall", tally(report.results)["overall"])
    out += ["", "BY KIND", "-" * 76]
    for name, t in sorted(tally(report.results, "kind").items()):
        out += _lines(name, t)

    states: dict[str, int] = {}
    for result in report.results:
        states[result.state] = states.get(result.state, 0) + 1
    out += ["", "CASE STATES: " + ", ".join(f"{k} {v}" for k, v in sorted(states.items()))]
    return "\n".join(out)
