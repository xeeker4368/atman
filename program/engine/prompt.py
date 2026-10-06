"""System-prompt assembly: soul.md + operational.md + situation + retrieved chunks + history.

Design of record: ``docs/SOUL_AND_PROMPT_DESIGN.md`` (revision 2), cited here as
S1–S12 rather than re-argued; revision 6 (S32 onward) adds the second authored file.

What this assembles
-------------------
Every turn's prompt is five parts, in this order (S11, S32):

1. ``soul.md``                — identity: what it is, persistence, naming, declining.
2. ``operational.md``         — record-protecting procedure: when the system runs,
                                tool honesty, the correction wording.
3. current-situation block    — timestamp, elapsed time, and its gap statement.
4. retrieved chunks           — each rendered with when it happened, in local time.
5. windowed history           — **not** text in the system prompt; the message
                                array that follows it.

Parts 1–4 are the system message. Part 5 is separate because that is what it
structurally is, and because it puts the live conversation closest to the
generation point.

The two authored files come first because they are the frame for everything after
them, and specifically because the elapsed-time figure in part 3 must land *after*
the standing statement of what that figure means (in ``operational.md``). Stating
the gap before establishing what it held is the confabulation ordering.

The two files are separate because procedure placed in the identity text is read
as identity (S32): which file holds a sentence is layout, so the required markers
are checked once, on the two joined (:func:`load_authored`).

Why the checks raise
--------------------
This module's constraints (S9) all raise. None degrade, none log-and-continue.

That is a deliberate divergence from ``program/memory/retrieval.py``, which
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from program import config
from program.engine import history
from program.engine.history import BudgetBreakdown, HistoryWindow
from program.memory import db, supersession
from program.memory.retrieval import RetrievalResult, RetrievedChunk

#: soul.md lives beside the governance files the Phase 2 ingestion blocklist
#: covers by resolved directory (S8). BUILD_PLAN names
#: ``program/integrity/architecture.md`` as the file whose late arrival broke a
#: filename-based blocklist; putting soul.md in the same directory means one
#: directory rule covers both, and any governance file added there later is
#: covered automatically. It is package content, not runtime data, so nothing
#: that writes the entity's own artifacts can reach it.
SOUL_PATH = Path(__file__).resolve().parent.parent / "integrity" / "soul.md"

#: The record-protecting procedure, kept out of the identity text (S32). Same
#: directory as soul.md, so the governance blocklist's directory rule covers it.
OPERATIONAL_PATH = SOUL_PATH.parent / "operational.md"

#: Fixed per-turn overhead ceilings (S10, S35). JUDGMENT values (decision #25). The
#: text was 4,749 characters under one 6,000 ceiling; it is two files now, each with
#: its own ceiling, so procedure cannot grow into the identity text's room
#: unnoticed. The two together (4,500) are what the chat message cap is derived
#: against (``config/defaults.toml``, ``tests/test_turn.py``).
SOUL_MAX_CHARS = 3000
OPERATIONAL_MAX_CHARS = 1500


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
        # Decision #24 (2026-10-04): the memory paragraph drops the "do not wait,
        # idle" sentence, so none of the three above survives in the shipped text
        # and this one carries the requirement. The three stay as accepted
        # rewordings — that is what this set is for. Since decision #25 it lives
        # in operational.md.
        "you run when something starts you",
    ),
    "elapsed-gap pairing": (
        "did not exist as a running process",
        "there is nothing you have been up to",
        "that description would be false",
        # Decision #25 (2026-10-05): the gap statement is about the record, not
        # about experience, and it lives in operational.md.
        "nothing from that time to report",
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


def _load_authored_file(target: Path, name: str, ceiling: int) -> str:
    """One authored file: present, under its ceiling, and naming/trait clean."""
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SoulIntegrityError(
            f"{name} not found at {target}. It is package content and is "
            f"required for every turn; there is no default to fall back to."
        ) from exc

    # Size first, and it never truncates (S10). Truncating would silently drop
    # whichever values sit at the end of the file, which is exactly the
    # invisible degradation this gate exists to prevent.
    if len(text) > ceiling:
        raise SoulIntegrityError(
            f"{name} is {len(text)} characters, over the {ceiling} "
            f"ceiling. It is fixed overhead on every turn, subtracted from the "
            f"same context window history and retrieval share, so growth here "
            f"silently shrinks history for every future turn. This is not "
            f"truncated: raising the ceiling is a decision, and losing the end "
            f"of the file is not."
        )

    check_authored_text(text, f"{name} ({target})")
    return text.strip()


def load_soul(path: Path | None = None) -> str:
    """Read and validate soul.md alone: presence, ceiling, naming and traits.

    The required markers are not checked here: they are checked on the authored
    text as assembled, by :func:`load_authored`, because which of the two files
    holds a required statement is layout (decision #25).
    """
    return _load_authored_file(path or SOUL_PATH, "soul.md", SOUL_MAX_CHARS)


def load_operational(path: Path | None = None) -> str:
    """Read and validate operational.md alone, under its own ceiling."""
    return _load_authored_file(
        path or OPERATIONAL_PATH, "operational.md", OPERATIONAL_MAX_CHARS)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def check_required_markers(authored: str) -> None:
    """Every required statement must be somewhere in the authored text.

    Validated rather than trusted because an edit that quietly drops a required
    statement produces no symptom until a behavioural probe runs weeks later —
    by which point memory has accumulated against a flawed foundation.

    **Whitespace is collapsed on both sides.** The files may be hard-wrapped, and a
    marker matched against wrapped text fails wherever a line break falls inside
    it: revision 5's ``'there is nothing you have been up to'`` never matched for
    that reason.
    """
    collapsed = _collapse(authored)
    for requirement, alternatives in REQUIRED_MARKERS.items():
        if not any(_collapse(alt) in collapsed for alt in alternatives):
            raise SoulIntegrityError(
                f"the authored text (soul.md and operational.md) no longer "
                f"contains its {requirement} statement. Expected one of: "
                + "; ".join(repr(a) for a in alternatives)
                + ". This is required content, not stylistic — omitting the "
                "elapsed-gap pairing is the exact mechanism that produced the "
                "prior build's false-continuity claims (GUIDANCE.md, decisions "
                "#5 and #25). If a rewording is intended, add the new phrasing to "
                "REQUIRED_MARKERS deliberately."
            )


def load_authored(
    soul_path: Path | None = None, operational_path: Path | None = None,
) -> tuple[str, str]:
    """Both authored files, each validated alone, then the markers on the two joined.

    Joined in assembly order with the section separator, so the check sees what
    the model is shown.
    """
    soul = load_soul(soul_path)
    operational = load_operational(operational_path)
    check_required_markers(soul + _SECTION_SEP + operational)
    return soul, operational


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
    # Decision #25: the block's gap statement is about the record. The phrases
    # above stay accepted: three frozen gate cases store the old block as literal
    # text and must still assemble
    # (tests/test_gate_eval.py::test_the_stored_situation_block_passes_prompt_assembly).
    "nothing was running",
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
    "The following are records from your memory, retrieved automatically for this "
    "turn. They are a selection, not everything, and they are not part of the "
    "conversation happening now. Each record says where it came from and ends with "
    "an end line. In a conversation, a line that begins with a name and a colon is "
    "what that person said, and a line that begins with \"you:\" is what you said. "
    "An indented line is part of what was said before it."
)


#: Characters of the corrected message quoted as a **locator**. Always shown, never
#: budgeted: a chunk packs up to eight turns into one opaque block, so "the third
#: message" is not something a reader can resolve against the text in front of it.
#: A JUDGMENT value.
SUPERSEDED_QUOTE_CHARS = 120

#: Characters of the correction itself. Quoted rather than summarised — a generated
#: paraphrase where the person's own words belong would be the one thing this
#: mechanism exists to avoid. `web_search`'s snippet cap, for the same reason.
SUPERSEDING_QUOTE_CHARS = 300

#: Annotations rendered under one chunk before the remainder become a count.
SUPERSEDING_MAX_PER_CHUNK = supersession.MAX_PER_CHUNK

#: RO4's answer. Annotations are charged against the same window as everything else
#: (`assemble_turn()` measures what `render_retrieved()` returns), so an unbounded
#: annotation silently evicts conversation history.
#:
#: **Two bounds, because they protect different things.** The quote budget is spent
#: on the *correction text* in rank order; once it runs out, annotations still
#: render with their locator and their state, just without the correction quoted.
#: The annotation cap bounds how many appear at all.
#:
#: **Degradation order is deliberate: shorten before dropping.** A dropped
#: annotation presents a corrected claim as current, which is the failure this
#: mechanism exists to prevent, so the quote is the first thing to go and the
#: existence of the annotation is the last. Whatever the cap does drop is still
#: *counted* in a closing line rather than vanishing — the `unresponsive_engines`
#: pattern: nothing found with two engines down is a different claim from nothing
#: found.
#:
#: Worst case, by construction: 12 annotations x ~210 characters of locator and
#: scaffolding, plus 2,000 characters of quotes, plus the closing counts —
#: **about 4,600 characters**, against `agent.max_tool_result_chars`'s 4,000 as the
#: nearest precedent for how much text one auxiliary thing may add to a turn. Both
#: numbers are JUDGMENT values.
SUPERSEDING_QUOTE_BUDGET_CHARS = 2000
SUPERSESSION_MAX_ANNOTATIONS = 12


def _quote(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return f'"{collapsed}"'
    return f'"{collapsed[:limit].rstrip()}…"'


def _render_supersession(item, quote_budget: int) -> tuple[str, int]:
    """One annotation, and how much of the quote budget it spent.

    Stated positively for both states, on the rule the gate's *"No tools were used
    this turn"* follows: an absent clause reads as no information, so
    "with no replacement given" is said rather than implied by omission.

    **Every annotation names who made the correction** (stage 3, D8). Until then
    every link was same-speaker and the speaker went without saying; now a person
    can correct the entity, and an unattributed "later corrected" would present a
    person's disagreement as an established fact (D6). The name comes from the
    message row at render time — it is never part of chunk text.
    """
    locator = _quote(item.superseded_text, SUPERSEDED_QUOTE_CHARS)
    when = local_day(item.superseding_timestamp) or "an unknown date"
    who = item.superseding_speaker
    if item.replacement == supersession.CONTRADICTED:
        head = f"Later contradicted by {who}, with no replacement given. {locator} was"
        verb = "contradicted"
    else:
        head = f"Later corrected by {who}. {locator} was"
        verb = "superseded"

    if quote_budget >= len(item.superseding_text[:SUPERSEDING_QUOTE_CHARS]):
        quote = _quote(item.superseding_text, SUPERSEDING_QUOTE_CHARS)
        return f"{head} {verb} on {when}: {quote}", len(quote)
    # Budget spent. The annotation still says what happened, to which line and by
    # whom; only the correction's wording is withheld.
    return f"{head} {verb} on {when}.", 0


#: Said when correction resolution failed (R7/RO3). **Stated before the records**,
#: not after: the ordering rule this module already enforces for the elapsed-time
#: figure — a caveat that arrives after the thing it qualifies has been read is the
#: wrong way round.
#:
#: RO3 was **reversed at review** (2026-09-19). My own lean was to record the
#: failure without telling the model, on the grounds that it would invite hedging on
#: every record in a turn where one lookup failed. The reviewer's argument is the
#: better one and it is `memory_search`'s own: *nothing found with the vector leg
#: down is a different claim from nothing found.* Here the absence of annotations
#: carries no information when the check did not run, and saying so is what stops
#: that absence being read as "nothing was corrected".
#:
#: The wording is deliberately about the **check**, not about the records' truth, to
#: keep it from reading as a general warning about the memory.
_SUPERSESSION_UNRESOLVED = (
    "[The check for later corrections to these records did not complete, so no "
    "corrections are shown below whether or not any exist.]"
)


#: How a non-conversation record announces itself, at presentation.
#:
#: A conversation chunk is the default and gets **no** label, so its rendering stays
#: byte-identical to what every prior turn and test has seen. Everything else needs
#: one, because without it a retrieved artifact reads as something that was *said*.
#:
#: ``generated_image`` carries the sharpest version of the problem and the reason the
#: label says **prompt only**: an image's indexed text is the prompt that made it
#: (Q14), so a bare rendering would hand the model a description and no way to know it
#: describes a picture nothing has looked at. The entity has no vision; the label is
#: what stops the prompt being read as an observation.
#:
#: At presentation rather than in chunk text, on task 1.3's rule: putting this in the
#: indexed body would feed "generated image" into the embedding and the BM25 index,
#: and every image would match a query mentioning images.
_SOURCE_LABELS = {
    "generated_image": "generated image, prompt only",
    "creative_writing": "creative writing",
    "file": "uploaded file",
    # J5, kept verbatim as decided: the second half is what stops an entry being read
    # as something somebody said.
    "reflection_journal": (
        "reflection journal — a later interpretation, not a record of what was said"
    ),
}


def _local(stamp: str | None) -> datetime | None:
    """A stored timestamp (UTC ISO-8601) in the household's timezone, or None."""
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo(config.timezone()))


def _day(moment: datetime) -> str:
    return f"{moment:%A} {moment.day} {moment:%B %Y}"


def local_day(stamp: str | None) -> str | None:
    """"Monday 5 October 2026", in local time: the date the household would say."""
    moment = _local(stamp)
    return _day(moment) if moment else None


def local_when(start: str | None, end: str | None = None) -> str:
    """When something happened, in local time (``app.timezone``), never UTC (3.4b).

    One instant: "Monday 5 October 2026 at 14:02 EDT". A range on one local day gives
    the date once: "Monday 5 October 2026, 14:02 to 14:20 EDT". A range crossing local
    midnight gives both dates. The zone is named once, or at each end if they differ.
    """
    first, last = _local(start), _local(end or start)
    if first is None or last is None:
        return "time unknown"
    # Compared as instants to the minute, not as local wall-clock text: when clocks go
    # back, 01:30 EDT and 01:30 EST read the same and are an hour apart.
    if int(first.timestamp()) // 60 == int(last.timestamp()) // 60:
        return f"{_day(first)} at {first:%H:%M %Z}"
    zone_first = "" if first.tzname() == last.tzname() else f" {first:%Z}"
    if first.date() == last.date():
        return f"{_day(first)}, {first:%H:%M}{zone_first} to {last:%H:%M %Z}"
    return f"{_day(first)}, {first:%H:%M}{zone_first} to {_day(last)}, {last:%H:%M %Z}"


def _chunk_when(chunk: RetrievedChunk) -> str:
    """A conversation chunk's **conversation** time, from its messages; an artifact's
    own time. Never a conversation chunk's ``created_at``, which is when the chunk was
    written (an idle close can be hours later) and is stored in UTC."""
    if chunk.messages:
        return local_when(chunk.messages[0].timestamp, chunk.messages[-1].timestamp)
    if chunk.first_message_id:
        return "time unknown"
    return local_when(chunk.created_at)


#: A line that reads like a speaker label: a short run with no colon, then a colon and a
#: space (or the end of the line). Inside a record, such a line in someone's text is held
#: by indentation so it cannot pass as a new speaker (3.6). Deliberately broad: "Note: x"
#: is held too, at the cost of two spaces. A URL ("https://") and a time ("12:30") are not.
_SPEAKER_LIKE = re.compile(r"^[^\s:][^:\n]{0,40}:(?:\s|$)")

#: How a held line is indented.
_HOLD = "  "

#: The entity's own lines. Chunk text says "assistant:", which is not how anyone, the
#: entity included, would name it; its own words read as its own (3.6, decision #25).
ENTITY_LABEL = "you"

#: The longest person's name a record header shows (``users`` allows 128). Bounding it is
#: what lets :data:`RECORD_HEADER_ALLOWANCE_CHARS` cover every ranked header by construction.
HEADER_NAME_MAX_CHARS = 40


def _hold(text: str, keep_first: bool = False) -> str:
    """Indent every speaker-like line, so none can pass as a speaker label."""
    lines = text.split("\n")
    return "\n".join(
        line if (keep_first and i == 0) or not _SPEAKER_LIKE.match(line) else _HOLD + line
        for i, line in enumerate(lines)
    )


def _name(speaker: str | None) -> str | None:
    """A person's name for display, or None. The entity's sentinel never renders."""
    if not speaker or speaker == db.ENTITY_USER_NAME:
        return None
    return speaker if len(speaker) <= HEADER_NAME_MAX_CHARS else (
        speaker[:HEADER_NAME_MAX_CHARS - 1] + "…")


def _origin(chunk: RetrievedChunk) -> str:
    """Where a record came from, in plain words: whose conversation, or what kind of thing."""
    if chunk.first_message_id:
        people = [_name(m.speaker) for m in chunk.messages or () if m.role == "user"]
        person = next((p for p in people if p), None)
        return f"from a conversation with {person}" if person else "from a conversation"
    return _SOURCE_LABELS.get(chunk.source_type or "") or "a stored record"


def _label(message) -> str | None:
    return ENTITY_LABEL if message.role == "assistant" else _name(message.speaker)


def _body(chunk: RetrievedChunk) -> str:
    """The record's text with true speaker labels, from the messages it was built from.

    Chunk text is ``"<name>: <content>"`` per message, joined by newlines (or a piece of
    one long message). The messages are read at render time, so the text is checked
    against them: when it matches, each message is rendered with its speaker's current
    name (``you`` for the entity) and its own speaker-like lines are held. When it does
    not match, or there are no messages, **every** speaker-like line is held, so a line
    that cannot be verified never reads as a speaker. Nothing in the stored text changes.
    """
    messages = chunk.messages
    if not messages:
        return _hold(chunk.text)
    stored = [f"{m.speaker if m.role == 'user' else 'assistant'}: {m.content}"
              for m in messages]
    labels = [_label(m) for m in messages]
    if None in labels:
        return _hold(chunk.text)
    whole = "\n".join(stored)
    if chunk.text == whole:
        return "\n".join(f"{label}: {_hold(m.content, keep_first=True)}"
                         for label, m in zip(labels, messages))
    if len(messages) == 1 and chunk.text and chunk.text in whole:
        prefix = stored[0][:len(stored[0]) - len(messages[0].content)]
        if chunk.text.startswith(prefix):
            return f"{labels[0]}: {_hold(chunk.text[len(prefix):], keep_first=True)}"
        return f"{labels[0]} (continued): {_hold(chunk.text, keep_first=True)}"
    return _hold(chunk.text)


def _render_chunk(chunk: RetrievedChunk, marker: str) -> str:
    """One record: an opening line saying where it came from and when, its text, an end line.

    Task 1.3 deliberately stripped timestamps from chunk *text* so that date strings
    would not enter the embedding or the BM25 index; the time is rendered here, from the
    rows, at presentation. The origin and the speakers are here by the same rule: names
    come from ``users`` at render time, never from chunk text (3.6).
    """
    return (f"[{marker} · {_origin(chunk)}, {_chunk_when(chunk)}]\n"
            f"{_body(chunk)}\n[end of {marker}]")


#: Characters allowed beside the record text for each record's opening and end lines,
#: across all ``top_k`` ranked records. Was 1,000 (B6a's figure); 3.6's opening line says
#: whose conversation and when, and each record now has an end line, so it is 2,000:
#: 200 per ranked record, which covers the longest header this module can produce
#: (``tests/test_record_origin.py::test_the_longest_header_fits_the_allowance``).
#: ``config/defaults.toml``'s derivation of the chat message cap uses the same figure.
RECORD_HEADER_ALLOWANCE_CHARS = 2000


def retrieved_records_max_chars() -> int:
    """The most characters the rendered records (header plus text) may take (B17).

    ``top_k x embedding.max_input_chars + RECORD_HEADER_ALLOWANCE_CHARS``, from live
    config: 51,000 today. It is exactly what B6a's derivation of the chat message cap
    already assumed retrieval uses. Before B17 that assumption was false: split-sibling
    attachment could add up to ``max_siblings_per_hit`` more pieces per hit, about
    200,000 characters at the extreme, past the whole window. The cap makes the
    derivation true instead of assumed. Correction annotations keep their own bound
    (RO4), separately.
    """
    return (config.retrieval_top_k() * config.embedding_max_input_chars()
            + RECORD_HEADER_ALLOWANCE_CHARS)


def _record_cost(block: str) -> int:
    return len(block) + 2  # the "\n\n" separator the block is joined with


def _siblings_within_cap(
    chunks: Sequence[RetrievedChunk], ranked: list[str], cap: int,
) -> tuple[dict[int, list[str]], int]:
    """Which continuation pieces fit, as ``({position: [rendered]}, withheld)``.

    **Ranked hits are never dropped; continuation pieces go first** (approved at
    review). Every ranked hit is at most ``embedding.max_input_chars`` of text, so all
    ``top_k`` of them fit the cap by construction; a hit is only ever dropped by
    ranking, never by size. What room is left goes to siblings in their parent's rank
    order, whole pieces only. Within one hit they are taken in order and stop at the
    first that does not fit, so a later piece never appears without the one before it.
    A smaller piece belonging to a lower-ranked hit may still fit afterwards.
    """
    room = cap - sum(_record_cost(block) for block in ranked)
    kept: dict[int, list[str]] = {}
    withheld = 0
    for position, chunk in enumerate(chunks, start=1):
        pieces: list[str] = []
        for offset, sibling in enumerate(chunk.siblings, start=1):
            block = _render_chunk(sibling, f"record {position}, continued {offset}")
            if _record_cost(block) > room:
                withheld += len(chunk.siblings) - offset + 1
                break
            pieces.append(block)
            room -= _record_cost(block)
        kept[position] = pieces
    return kept, withheld


def render_retrieved(
    result: RetrievalResult | None,
    cap: int | None = None,
    keep_hits: int | None = None,
) -> str:
    """Retrieved chunks as text, siblings attached under their parent (S7/D7).

    The records are bounded by :func:`retrieved_records_max_chars` (B17): continuation
    pieces that do not fit are left out and **counted** in a closing line, never
    silently dropped, the same pattern as the correction annotations' withheld count.

    ``cap`` and ``keep_hits`` exist for the turn's own budget (B21). When the window
    cannot hold the records at their full size, :func:`assemble_turn` shrinks them in
    this order: continuation pieces first, by a smaller ``cap``; then whole ranked hits
    from the **lowest rank up**, by a smaller ``keep_hits``. Hits left out are counted
    in a closing line, as withheld pieces are. With neither given, the rendering is
    byte-identical to B17's (pinned by ``test_retrieved_cap``).
    """
    if result is None or not result.results:
        return ""

    chunks = list(result.results)
    dropped_hits = 0
    if keep_hits is not None and keep_hits < len(chunks):
        dropped_hits = len(chunks) - max(0, keep_hits)
        chunks = chunks[:max(0, keep_hits)]
    if not chunks:
        return _hits_left_out(dropped_hits)

    ranked = [_render_chunk(chunk, f"record {position}")
              for position, chunk in enumerate(chunks, start=1)]
    limit = retrieved_records_max_chars() if cap is None else min(
        cap, retrieved_records_max_chars())
    kept, withheld = _siblings_within_cap(chunks, ranked, limit)

    budget = _AnnotationBudget()
    blocks = [_RETRIEVED_HEADER]
    if not result.supersession.resolved:
        blocks.append(_SUPERSESSION_UNRESOLVED)
    for position, chunk in enumerate(chunks, start=1):
        blocks.append(ranked[position - 1])
        blocks.extend(budget.render(chunk))
        for sibling, block in zip(chunk.siblings, kept[position]):
            # Continuations of the same split message, not independent matches.
            blocks.append(block)
            blocks.extend(budget.render(sibling))
    blocks.extend(budget.closing())
    if withheld:
        blocks.append(
            f"[{withheld} further continuation piece{'s' if withheld > 1 else ''} of "
            f"long records {'were' if withheld > 1 else 'was'} left out to keep these "
            f"records within their size limit.]"
        )
    if dropped_hits:
        blocks.append(_hits_left_out(dropped_hits))
    return "\n\n".join(blocks)


def _hits_left_out(count: int) -> str:
    """Said when lower-ranked records were dropped for this turn's room (B21).

    Authored text: checked by the naming and trait tripwires in
    :func:`assemble_turn`. It says only what happened. The records are not gone;
    they were not sent this time.
    """
    return (f"[{count} lower-ranked retrieved record{'s' if count > 1 else ''} "
            f"{'were' if count > 1 else 'was'} left out of this turn to make room. "
            f"{'They are' if count > 1 else 'It is'} still in memory.]")


class _AnnotationBudget:
    """Spends RO4's two bounds across one rendering pass.

    Stateful on purpose: the quote budget is global to the render, so it cannot live
    inside a per-chunk function. Kept out of `render_retrieved`'s body so the
    ordering of records stays readable.
    """

    def __init__(self) -> None:
        self.quote_budget = SUPERSEDING_QUOTE_BUDGET_CHARS
        self.remaining = SUPERSESSION_MAX_ANNOTATIONS
        self.withheld = 0
        self.withheld_records = 0

    def render(self, chunk: RetrievedChunk) -> list[str]:
        items = list(getattr(chunk, "supersessions", ()))
        if not items:
            return []

        shown = items[:min(SUPERSEDING_MAX_PER_CHUNK, self.remaining)]
        if not shown:
            # Nothing left in the global cap. Counted, never silently dropped.
            self.withheld += len(items)
            self.withheld_records += 1
            return []

        lines = []
        for item in shown:
            text, spent = _render_supersession(item, self.quote_budget)
            self.quote_budget -= spent
            lines.append(f"  {text}")
        self.remaining -= len(shown)

        extra = len(items) - len(shown)
        if extra:
            lines.append(
                f"  And {extra} further correction{'s' if extra > 1 else ''} to this "
                f"record, not shown."
            )
        return ["\n".join(lines)]

    def closing(self) -> list[str]:
        if not self.withheld:
            return []
        records = self.withheld_records
        return [
            f"{self.withheld} further correction"
            f"{'s' if self.withheld > 1 else ''} apply to {records} of the records "
            f"above and {'are' if self.withheld > 1 else 'is'} not shown."
        ]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssembledPrompt:
    """The finished prompt and an account of how the window was spent."""

    system: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    soul_chars: int = 0
    #: operational.md, reported apart from soul.md so the budget stays legible.
    operational_chars: int = 0
    situation_chars: int = 0
    retrieved_chars: int = 0
    scaffolding_chars: int = 0
    budget: BudgetBreakdown | None = None
    window: HistoryWindow | None = None
    retrieval: RetrievalResult | None = None
    #: B21: what had to be given up to fit this call, beyond ordinary history
    #: windowing. Each is a dict with a ``window_event`` kind. The loop logs it and
    #: writes it into the turn's trace, so a loss is never silent.
    window_events: list[dict[str, Any]] = field(default_factory=list)
    #: B21's last resort: the turn does not fit even with nothing left to shrink
    #: while tools are offered. The caller makes this the final, tool-free call
    #: and assembles again with ``tool_schema_chars=0``.
    needs_final_call: bool = False

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


def _authored(soul_text: str | None) -> tuple[str, str]:
    """(soul, operational) for one assembly: the two files, or an injected text alone."""
    if soul_text is None:
        return load_authored()
    check_authored_text(soul_text, "supplied soul text")
    return soul_text, ""


def build_system_prompt(
    situation: str,
    retrieval: RetrievalResult | None = None,
    soul_text: str | None = None,
) -> str:
    """soul.md, operational.md, the situation block, retrieved records (S11, S32).

    ``soul_text`` is injectable for tests; it is naming- and trait-checked, so a
    caller cannot route around those constraints by supplying its own. An
    injected text replaces **both** authored files, so no operational text is
    loaded beside it and a test sends exactly what it supplies.
    """
    soul, operational = _authored(soul_text)

    situation = (situation or "").strip()
    _check_pairing(situation)
    check_authored_text(_RETRIEVED_HEADER, "retrieved-records header")
    check_authored_text(_SUPERSESSION_UNRESOLVED, "supersession-unresolved note")

    retrieved = render_retrieved(retrieval)
    parts = [part for part in (soul, operational, situation, retrieved) if part]
    return _SECTION_SEP.join(parts)


def assemble_turn(
    messages: Sequence[Mapping[str, Any]],
    situation: str,
    retrieval: RetrievalResult | None = None,
    context_tokens: int | None = None,
    soul_text: str | None = None,
    *,
    current_turn_start: int | None = None,
    tool_schema_chars: int = 0,
) -> AssembledPrompt:
    """Build the system prompt, then give history whatever window is left (S12).

    Order of operations is the point: the system prompt is built and **measured
    first**, and history takes the remainder. The two counts are passed
    **separately** to ``plan_budget`` so ``BudgetBreakdown`` reports where the
    window went. ``tool_schema_chars`` is the size of the ``tools`` JSON this call
    sends (B20), reserved the same way.

    **The current turn (B21).** ``current_turn_start`` is the index of this turn's
    user message; the messages after it are this turn's tool rounds (an assistant
    message carrying ``tool_calls``, then its results). The user's message is
    **never dropped**. Before B21, windowing walked newest-first and kept only the
    newest message unconditionally, which after a tool round is a tool result, so a
    long question could be windowed out with nothing logged. When the turn does not
    fit, it shrinks in this order:

    1. **older history**, newest kept first: decision #6's ordinary windowing. Not
       an event: it is the normal path, and what falls out stays retrievable.
    2. **the retrieved records**: continuation pieces first (a smaller cap, B17's
       order), then whole hits from the lowest rank up. Event ``records_shrunk``.
    3. **the oldest whole tool rounds**, never part of one. Event ``rounds_dropped``.
    4. **the last resort**: ``needs_final_call``, so the caller drops the tools and
       assembles again without their schemas.
    5. With no tools to drop, the user's message alone is sent over budget, as
       before: event ``overflow``, logged at WARNING.

    Without ``current_turn_start`` the behaviour is the pre-B21 one apart from the
    schema term: the newest message is pinned and older ones fill the rest.
    """
    soul, operational = _authored(soul_text)

    situation = (situation or "").strip()
    _check_pairing(situation)

    def compose(retrieved: str) -> tuple[str, int, history.BudgetBreakdown]:
        parts = [part for part in (soul, operational, situation, retrieved) if part]
        # Separators plus the retrieved header, which render_retrieved() folds into
        # the retrieved text. Counted against the system side so the two reported
        # figures sum to what was actually sent.
        scaffolding = max(0, len(parts) - 1) * len(_SECTION_SEP)
        budget = history.plan_budget(
            system_prompt_chars=(
                len(soul) + len(operational) + len(situation) + scaffolding),
            retrieved_chars=len(retrieved),
            context_tokens=context_tokens,
            tool_schema_chars=tool_schema_chars,
        )
        return _SECTION_SEP.join(parts), scaffolding, budget

    def finish(retrieved, window, events=(), needs_final=False):
        system, scaffolding, budget = compose(retrieved)
        return AssembledPrompt(
            system=system,
            messages=window.messages,
            soul_chars=len(soul),
            operational_chars=len(operational),
            situation_chars=len(situation),
            retrieved_chars=len(retrieved),
            scaffolding_chars=scaffolding,
            budget=budget,
            window=window,
            retrieval=retrieval,
            window_events=list(events),
            needs_final_call=needs_final,
        )

    full = render_retrieved(retrieval)
    if current_turn_start is None:
        _, _, budget = compose(full)
        return finish(full, history.select_history(messages, budget))

    prior = list(messages[:current_turn_start])
    current = messages[current_turn_start]
    rounds = _tool_rounds(messages[current_turn_start + 1:])
    cost = history.estimate_message_tokens

    def room(retrieved: str) -> int:
        return compose(retrieved)[2].history_tokens

    def fits(retrieved: str, kept_rounds: list[list[Mapping[str, Any]]]) -> bool:
        need = cost(current) + sum(cost(m) for r in kept_rounds for m in r)
        return need <= room(retrieved)

    events: list[dict[str, Any]] = []
    retrieved = full
    kept = list(rounds)

    # 2. The records: continuation pieces first, then hits from the lowest rank.
    if not fits(retrieved, kept) and retrieval is not None and retrieval.results:
        retrieved, hits_kept = _shrink_records(retrieval, full, lambda r: fits(r, kept))
        events.append({"window_event": "records_shrunk",
                       "records_chars_before": len(full),
                       "records_chars_after": len(retrieved),
                       "hits": len(retrieval.results), "hits_kept": hits_kept})

    # 3. The oldest whole tool rounds.
    dropped = 0
    while kept and not fits(retrieved, kept):
        kept.pop(0)
        dropped += 1
    if dropped:
        events.append({"window_event": "rounds_dropped", "rounds": dropped,
                       "of_rounds": len(rounds)})

    if not fits(retrieved, kept):
        if tool_schema_chars:
            # 4. The last resort: no tools on this call, and assemble again.
            events.append({"window_event": "final_call_forced",
                           "reason": "the turn does not fit beside the tool schemas"})
            _, _, budget = compose(retrieved)
            return finish(retrieved, history.HistoryWindow(budget=budget), events,
                          needs_final=True)
        # 5. Nothing left to give up: the user's message goes over budget, as before.
        events.append({"window_event": "overflow",
                       "message_tokens": cost(current),
                       "history_tokens": room(retrieved)})

    _, _, budget = compose(retrieved)
    pinned = [current, *[m for r in kept for m in r]]
    used = sum(cost(m) for m in pinned)
    older: list[Mapping[str, Any]] = []
    for message in reversed(prior):  # 1. older history, newest kept first
        if used + cost(message) > budget.history_tokens:
            break
        older.append(message)
        used += cost(message)
    older.reverse()
    window = history.HistoryWindow(
        messages=[history.normalise_message(m) for m in [*older, *pinned]],
        omitted=(len(prior) - len(older)) + sum(len(r) for r in rounds[:dropped]),
        estimated_tokens=used,
        budget=budget,
        overflowed=any(e["window_event"] == "overflow" for e in events)
        or budget.over_committed,
    )
    return finish(retrieved, window, events)


def _tool_rounds(extras: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """This turn's tool rounds: each an assistant tool-call message and its results.

    A round is dropped whole or kept whole: a result without the call it answers,
    or a call without its results, reads to the model as something that did not
    happen the way it did.
    """
    rounds: list[list[Mapping[str, Any]]] = []
    for message in extras:
        if message.get("role") == "assistant" or not rounds:
            rounds.append([message])
        else:
            rounds[-1].append(message)
    return rounds


def _shrink_records(retrieval: RetrievalResult, full: str, ok) -> tuple[str, int]:
    """The largest rendering of the records that fits, and how many hits it keeps.

    B17's order of giving up: first a smaller cap, which withholds continuation
    pieces in parent rank order; then fewer ranked hits, from the lowest rank up,
    down to none.
    """
    hits = len(retrieval.results)
    if ok(render_retrieved(retrieval, cap=0)):
        # Every hit fits, and some continuation pieces may: the largest cap that does.
        low, high = 0, len(full)
        while high - low > 200:
            middle = (low + high) // 2
            if ok(render_retrieved(retrieval, cap=middle)):
                low = middle
            else:
                high = middle
        return render_retrieved(retrieval, cap=low), hits
    for keep in range(hits - 1, 0, -1):
        candidate = render_retrieved(retrieval, cap=0, keep_hits=keep)
        if ok(candidate):
            return candidate, keep
    return render_retrieved(retrieval, cap=0, keep_hits=0), 0
