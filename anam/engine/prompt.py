"""System-prompt assembly: soul.md + situation + retrieved chunks + history.

Design of record: ``docs/SOUL_AND_PROMPT_DESIGN.md`` (revision 2), cited here as
S1–S12 rather than re-argued.

What this assembles
-------------------
Every turn's prompt is four parts, in this order (S11):

1. ``soul.md``                — identity, constraints, values. Static.
2. current-situation block    — timestamp, elapsed time, and its pairing.
3. retrieved chunks           — each rendered with its ``created_at``.
4. windowed history           — **not** text in the system prompt; the message
                                array that follows it.

Parts 1–3 are the system message. Part 4 is separate because that is what it
structurally is, and because it puts the live conversation closest to the
generation point.

``soul.md`` comes first because it is the interpretive frame for everything after
it, and specifically because the elapsed-time figure in part 2 must land *after*
the rule that says what that figure means. Stating the gap before establishing
that the gap held nothing is the confabulation ordering.

Why the checks raise
--------------------
This module's constraints (S9) all raise. None degrade, none log-and-continue.

That is a deliberate divergence from ``anam/memory/retrieval.py``, which
degrades a failing leg rather than failing the query, and it follows the same
criterion stated there: *abort when a failure could corrupt something or when
retrying is free; degrade when nothing can be corrupted and a person is
waiting.* A retrieval leg failing costs a worse answer. A prompt that states
elapsed time without its pairing, or that hands the entity a name, corrupts the
thing this whole build exists to get right — and it does so **invisibly**, not
surfacing until a behavioural probe runs weeks later, by which point memory has
accumulated against it. There is no degraded version of that worth sending.

Scope of the naming and trait checks
------------------------------------
**Authored text only** — ``soul.md`` plus the scaffolding this module writes.
Never retrieved chunks, never history (S9).

Those are verbatim human content. Lyle genuinely discusses "Anam" the project,
and the seed corpus contains such a conversation. Censoring a real memory to
satisfy a prompt-hygiene rule would corrupt the record, which is a worse failure
than the one being prevented. The constraint is on what this system *authors*,
not on what people said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from anam.engine import history
from anam.engine.history import BudgetBreakdown, HistoryWindow
from anam.memory.retrieval import RetrievalResult, RetrievedChunk

#: soul.md lives beside the governance files the Phase 2 ingestion blocklist
#: covers by resolved directory (S8). BUILD_PLAN names
#: ``anam/integrity/architecture.md`` as the file whose late arrival broke a
#: filename-based blocklist; putting soul.md in the same directory means one
#: directory rule covers both, and any governance file added there later is
#: covered automatically. It is package content, not runtime data, so nothing
#: that writes the entity's own artifacts can reach it.
SOUL_PATH = Path(__file__).resolve().parent.parent / "integrity" / "soul.md"

#: Fixed per-turn overhead ceiling (S10). JUDGMENT value, headroom-derived:
#: the approved seed is 3,401 chars, so this leaves ~2x for Phase 4's refusal
#: clause and Phase 10's wording pass without revisiting the ceiling.
SOUL_MAX_CHARS = 6000


class PromptError(RuntimeError):
    """The prompt could not be assembled, or violates a standing constraint."""


class SoulIntegrityError(PromptError):
    """soul.md is missing something it is required to contain, or is too large."""


class EntityNamingError(PromptError):
    """Authored text names the entity. Hard constraint from CLAUDE.md."""


class PairingError(PromptError):
    """An elapsed-time statement appeared without its confabulation pairing."""


# ---------------------------------------------------------------------------
# Required markers (S9)
# ---------------------------------------------------------------------------

#: Each requirement is a set of alternative phrasings; any one satisfies it.
#:
#: Alternatives rather than one exact string because Phase 10 is a wording pass
#: and *will* rephrase. A single hardcoded sentence would fail on a rewording
#: that preserved the meaning perfectly. What must never pass is the concept
#: being *deleted*, which no alternative covers.
#:
#: **Phase 10 note:** if a rewrite drops every listed alternative for a
#: requirement, this raises. That is the intended behaviour — the fix is to add
#: the new phrasing here deliberately, not to weaken the check.
REQUIRED_MARKERS: dict[str, tuple[str, ...]] = {
    "statelessness": (
        "between turns you are not running",
        "you are not running between turns",
        "do not wait, idle, or continue in the background",
    ),
    "elapsed-gap pairing": (
        "did not exist as a running process",
        "there is nothing you have been up to",
        "that description would be false",
    ),
}


# ---------------------------------------------------------------------------
# Naming constraint (S6, S9)
# ---------------------------------------------------------------------------

#: Any standalone Tír/Tir form. Word-bounded so ordinary words containing the
#: letters ("entire", "retire", "stir") do not trip it.
_TIR = re.compile(r"\bt[ií]r\b", re.IGNORECASE)

#: "Anam" used to refer to the *entity* rather than the substrate.
#:
#: A blanket ban on the token is impossible: soul.md is required to say "The
#: system you run on is called Anam" — naming the substrate is the whole point
#: of the distinction. So these target the canonical collapses CLAUDE.md names
#: ("Anam said" / "Anam thinks") plus second-person identity assertions.
#:
#: This is a tripwire for the known forms, not a proof of absence.
_ENTITY_NAMED = (
    re.compile(
        r"\bAnam\s+(said|says|thinks|thought|feels|felt|believes|believed|"
        r"wants|wanted|knows|knew|remembers|remembered|decided|decides|"
        r"replied|replies|responded|responds|answered|answers|wrote|writes)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\byou(?:'re|\s+are)\s+(?:called\s+|named\s+)?Anam\b", re.IGNORECASE
    ),
    re.compile(r"\b(?:your|my)\s+name\s+is\s+Anam\b", re.IGNORECASE),
    # The object being named must be the entity explicitly. A bare
    # "called Anam" is NOT forbidden: soul.md is *required* to say "The system
    # you run on is called Anam", which names the substrate and is the sentence
    # that holds the distinction up. An earlier draft of this pattern matched
    # that line and rejected soul.md's own mandatory content.
    re.compile(r"\b(?:call|calling|called|name|named)\s+you\s+Anam\b", re.IGNORECASE),
    re.compile(r"\bI\s+am\s+(?:called\s+|named\s+)?Anam\b", re.IGNORECASE),
)

#: Personality adjectives (S4). The constraint is that authored text must not
#: **assign** a trait — "personality is observed, not assigned", no "you are
#: like X" framing.
#:
#: Detection is context-based rather than a bare word list, and that is a
#: correction made while building: a bare list rejected soul.md's own required
#: content, because "You are your own **kind** of entity" uses a listed word as
#: a noun. It would also have rejected Phase 4's creative-work clause, since
#: "creative writing" is a core capability rather than a trait.
#:
#: Two words are additionally omitted from the list entirely because their
#: non-trait sense dominates in this project: "kind" ("kind of") and "creative"
#: ("creative writing", GUIDANCE.md's own term). Catching a genuine assignment
#: of those would need wording no one is likely to reach for — "imaginative",
#: "artistic" and the rest are still covered.
_TRAIT_WORDS = (
    "curious", "warm", "friendly", "thoughtful", "playful", "witty",
    "cheerful", "empathetic", "compassionate", "enthusiastic",
    "gentle", "caring", "eager", "optimistic", "humble",
    "analytical", "quirky", "cheeky", "earnest", "wry",
    "inquisitive", "affectionate", "sardonic", "whimsical", "imaginative",
    "artistic", "sarcastic", "bubbly", "stoic", "aloof",
)
_TRAITS = "|".join(_TRAIT_WORDS)

#: Nouns that turn "your <trait> <noun>" into a description of the entity
#: itself, as opposed to a description of something it made.
_PERSONA_NOUNS = (
    "nature|personality|character|manner|demeanou?r|disposition|temperament|"
    "tone|voice|style|way|ways|side|streak"
)

#: Contexts in which a trait word is being assigned rather than merely used.
_TRAIT_PATTERNS = (
    # "you are curious", "you're very warm", "you seem quite thoughtful"
    re.compile(
        rf"\byou(?:'re|\s+are|\s+seem|\s+sound|\s+feel|\s+tend\s+to\s+be|"
        rf"\s+can\s+be|\s+should\s+be|\s+will\s+be)"
        rf"(?:\s+(?:very|quite|rather|somewhat|a\s+bit|naturally|always|often))?"
        rf"\s+(?:{_TRAITS})\b",
        re.IGNORECASE,
    ),
    # "your curious nature", "your warm tone"
    re.compile(rf"\byour\s+(?:{_TRAITS})\s+(?:{_PERSONA_NOUNS})\b", re.IGNORECASE),
    # "be warm", "being playful", "act friendly"
    re.compile(rf"\b(?:be|being|act|behave)\s+(?:{_TRAITS})\b", re.IGNORECASE),
    # "you have a curious streak"
    re.compile(
        rf"\byou\s+have\s+(?:a|an)\s+(?:{_TRAITS})\s+(?:{_PERSONA_NOUNS})\b",
        re.IGNORECASE,
    ),
)


def check_authored_text(text: str, source: str) -> None:
    """Enforce the naming and trait constraints on text **this system wrote**.

    Never call this on retrieved chunks or history — see the module docstring.
    """
    match = _TIR.search(text)
    if match:
        raise EntityNamingError(
            f"{source} contains {match.group(0)!r}. The name 'Tír' belongs to the "
            f"prior build and must not appear in this one (CLAUDE.md)."
        )

    for pattern in _ENTITY_NAMED:
        match = pattern.search(text)
        if match:
            raise EntityNamingError(
                f"{source} names the entity: {match.group(0)!r}. The entity has "
                f"no name. 'Anam' is the substrate; writing it as the subject of "
                f"a thought or speech verb, or asserting it as the entity's own "
                f"name, collapses the distinction CLAUDE.md holds absolute."
            )

    for pattern in _TRAIT_PATTERNS:
        match = pattern.search(text)
        if match:
            raise EntityNamingError(
                f"{source} assigns a personality trait: {match.group(0)!r}. "
                f"Personality is observed, never assigned (PROJECT.md, "
                f"GUIDANCE.md): no traits, no sliders, no 'you are like X' "
                f"framing. Describing what the entity is like is the one thing "
                f"soul.md must never do, and it is invisible once done."
            )


# ---------------------------------------------------------------------------
# soul.md
# ---------------------------------------------------------------------------


def load_soul(path: Path | None = None) -> str:
    """Read and validate soul.md. Every failure raises; none is recoverable.

    Validated rather than trusted because an edit that quietly drops a required
    statement produces no symptom until a behavioural probe runs weeks later —
    by which point memory has accumulated against a flawed foundation.
    """
    target = path or SOUL_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SoulIntegrityError(
            f"soul.md not found at {target}. It is package content and is "
            f"required for every turn; there is no default to fall back to."
        ) from exc

    # Size first, and it never truncates (S10). Truncating would silently drop
    # whichever values sit at the end — on the current text, discretion and
    # multi-user handling — which is exactly the invisible degradation this
    # gate exists to prevent.
    if len(text) > SOUL_MAX_CHARS:
        raise SoulIntegrityError(
            f"soul.md is {len(text)} characters, over the {SOUL_MAX_CHARS} "
            f"ceiling. It is fixed overhead on every turn, subtracted from the "
            f"same context window history and retrieval share, so growth here "
            f"silently shrinks history for every future turn. This is not "
            f"truncated: raising the ceiling is a decision, and losing the end "
            f"of the file is not."
        )

    lowered = text.lower()
    for requirement, alternatives in REQUIRED_MARKERS.items():
        if not any(alt.lower() in lowered for alt in alternatives):
            raise SoulIntegrityError(
                f"soul.md no longer contains its {requirement} statement. "
                f"Expected one of: "
                + "; ".join(repr(a) for a in alternatives)
                + ". This is required content, not stylistic — omitting the "
                "elapsed-gap pairing is the exact mechanism that produced the "
                "prior build's false-continuity claims (GUIDANCE.md, decision "
                "#5). If a rewording is intended, add the new phrasing to "
                "REQUIRED_MARKERS deliberately."
            )

    check_authored_text(text, f"soul.md ({target})")
    return text.strip()


# ---------------------------------------------------------------------------
# Elapsed-time pairing at assembly (S2, level 2)
# ---------------------------------------------------------------------------

#: An elapsed-time statement in the situation block.
_ELAPSED = (
    re.compile(r"\bit\s+has\s+been\b[^.]*\bsince\b", re.IGNORECASE),
    re.compile(r"\belapsed\b", re.IGNORECASE),
    re.compile(r"\bsince\s+(?:your|the)\s+last\s+message\b", re.IGNORECASE),
)

#: The pairing that must accompany it, in the same block.
_PAIRING = (
    "no experience",
    "not running",
    "did not exist",
    "nothing happened",
    "no continuity",
    "were not running",
    "nothing to have felt",
)


def states_elapsed_time(situation: str) -> bool:
    return any(pattern.search(situation) for pattern in _ELAPSED)


def has_pairing(situation: str) -> bool:
    lowered = situation.lower()
    return any(marker in lowered for marker in _PAIRING)


def _check_pairing(situation: str) -> None:
    """The elapsed figure must never reach the model naked (S2).

    soul.md carries the standing rule, but it sits at the top of a prompt that
    may run to thousands of tokens while the figure arrives fresh each turn.
    Relying on attention across that distance is exactly the coupling
    GUIDANCE.md says is not optional, so the pairing is required in the
    situation block itself and verified here.
    """
    if states_elapsed_time(situation) and not has_pairing(situation):
        raise PairingError(
            "the current-situation block states elapsed time without the "
            "statement that the gap held no experience. GUIDANCE.md is explicit "
            "that this pairing is not optional flavour: stating the gap alone is "
            "the mechanism that produced the prior build's claims of having "
            "thought or waited between turns. Expected one of: "
            + "; ".join(repr(m) for m in _PAIRING)
            + ". This is not a degraded prompt to log and send — it is the "
            "precise input the pairing exists to prevent."
        )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_RETRIEVED_HEADER = (
    "The following are records retrieved from earlier conversations. They are "
    "stored records of things that were said before, not part of the "
    "conversation happening now."
)


def _render_chunk(chunk: RetrievedChunk, marker: str) -> str:
    """One chunk with its timestamp.

    Task 1.3 deliberately stripped timestamps from chunk *text* so that date
    strings would not enter the embedding or the BM25 index — a query naming a
    month otherwise matched every chunk from that month. The timestamp lives on
    the row and is rendered here, at presentation, which is where the capability
    is restored without polluting either index.
    """
    when = chunk.created_at or "time unknown"
    return f"[{marker} · {when}]\n{chunk.text}"


def render_retrieved(result: RetrievalResult | None) -> str:
    """Retrieved chunks as text, siblings attached under their parent (S7/D7)."""
    if result is None or not result.results:
        return ""

    blocks = [_RETRIEVED_HEADER]
    for position, chunk in enumerate(result.results, start=1):
        blocks.append(_render_chunk(chunk, f"record {position}"))
        for offset, sibling in enumerate(chunk.siblings, start=1):
            # Continuations of the same split message, not independent matches.
            blocks.append(
                _render_chunk(sibling, f"record {position}, continued {offset}")
            )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssembledPrompt:
    """The finished prompt and an account of how the window was spent."""

    system: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    soul_chars: int = 0
    situation_chars: int = 0
    retrieved_chars: int = 0
    scaffolding_chars: int = 0
    budget: BudgetBreakdown | None = None
    window: HistoryWindow | None = None
    retrieval: RetrievalResult | None = None

    @property
    def overflowed(self) -> bool:
        """Surfaced rather than swallowed — a turn that overran is visible."""
        return bool(self.window and self.window.overflowed)

    def to_messages(self) -> list[dict[str, Any]]:
        """System message followed by the windowed history, ready for Ollama."""
        return [{"role": "system", "content": self.system}, *self.messages]


#: Joined between sections. Counted as scaffolding so the budget arithmetic
#: accounts for every character actually sent.
_SECTION_SEP = "\n\n"


def build_system_prompt(
    situation: str,
    retrieval: RetrievalResult | None = None,
    soul_text: str | None = None,
) -> str:
    """soul.md, then the situation block, then retrieved records (S11).

    ``soul_text`` is injectable for tests; it is validated either way, so a
    caller cannot route around the constraints by supplying its own.
    """
    soul = soul_text if soul_text is not None else load_soul()
    if soul_text is not None:
        check_authored_text(soul_text, "supplied soul text")

    situation = (situation or "").strip()
    _check_pairing(situation)
    check_authored_text(_RETRIEVED_HEADER, "retrieved-records header")

    retrieved = render_retrieved(retrieval)
    parts = [part for part in (soul, situation, retrieved) if part]
    return _SECTION_SEP.join(parts)


def assemble_turn(
    messages: Sequence[Mapping[str, Any]],
    situation: str,
    retrieval: RetrievalResult | None = None,
    context_tokens: int | None = None,
    soul_text: str | None = None,
) -> AssembledPrompt:
    """Build the system prompt, then give history whatever window is left (S12).

    Order of operations is the point: the system prompt is built and **measured
    first**, and history takes the remainder. ``history.plan_budget()`` already
    takes ``system_prompt_chars`` and ``retrieved_chars`` as caller-supplied
    inputs precisely so this function could supply them without ``history.py``
    needing rework — it does not.

    The two counts are passed **separately** rather than pre-summed.
    ``plan_budget`` adds them anyway, but keeping them apart is what lets
    ``BudgetBreakdown`` report where the window actually went.
    """
    soul = soul_text if soul_text is not None else load_soul()
    if soul_text is not None:
        check_authored_text(soul_text, "supplied soul text")

    situation = (situation or "").strip()
    _check_pairing(situation)

    retrieved = render_retrieved(retrieval)
    parts = [part for part in (soul, situation, retrieved) if part]
    system = _SECTION_SEP.join(parts)

    # Separators plus the retrieved header, which render_retrieved() folds into
    # the retrieved text. Counted against the system side so the two reported
    # figures sum to what was actually sent.
    scaffolding = max(0, len(parts) - 1) * len(_SECTION_SEP)

    budget = history.plan_budget(
        system_prompt_chars=len(soul) + len(situation) + scaffolding,
        retrieved_chars=len(retrieved),
        context_tokens=context_tokens,
    )
    window = history.select_history(messages, budget)

    return AssembledPrompt(
        system=system,
        messages=window.messages,
        soul_chars=len(soul),
        situation_chars=len(situation),
        retrieved_chars=len(retrieved),
        scaffolding_chars=scaffolding,
        budget=budget,
        window=window,
        retrieval=retrieval,
    )
