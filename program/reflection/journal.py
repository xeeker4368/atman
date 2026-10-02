"""The reflection journal: read one day's records, write one entry about them.

Design of record: ``docs/REFLECTION_JOURNAL_DESIGN.md`` (J1–J8, J4 revision 2 as amended
at review 2026-10-01). Journal step 3.

One function does the work, :func:`write_entry`, and the operator's command
(``scripts/write_journal.py``) is a shell around it. Phase 6 calls the same function.

What a run is
=============
Not a turn. Nobody is present, it has no tools, and it reads **raw messages from the
store, not retrieval** (J3): retrieval would pull earlier journal chunks into the run
and build interpretations on interpretations. All users' conversations are included
(decision #20).

Order, which is the design's
============================
generate → gate (J8) → bytes → row **with the verdict** (J7) → **no indexing**.
The verdict is written in the same insert as the row. The entry is indexed later, only
when the operator has read it and runs ``--index`` (J7, approved 2026-10-01).

The gate here is ``check_identity``: **a noisy aid, not a control** (ruling 2026-10-01).
The control is the operator reading the entry before it is indexed.

Two layers, so the live measurement can sample
==============================================
:func:`prepare` renders the day and builds the prompt (no model call, no write).
:func:`generate` makes the one model call and runs the gate (no write).
:func:`write_entry` composes them with the idempotency check and the store. A harness
that wants many entries for one day calls ``prepare`` and ``generate`` and never touches
the store.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from program import config
from program.artifacts import journal as storage
from program.attribution import AttributionContext
from program.engine import history, loop, ollama, prompt
from program.integrity import gate
from program.memory import db
from program.tools import receipts
from program.tools.registry import side_effect_tools

logger = logging.getLogger(__name__)

#: Recorded in ``extraction_note`` so an entry says which wording produced it.
PROMPT_REVISION = "J4r2"
PROMPT_REVISION_WITH_CLAUSE = "J4r2+clause"

#: The clause review removed from revision 2 and the live run is deciding about.
CLAUSE = ", and there was no thinking about it in between"


class JournalError(RuntimeError):
    """A run could not complete. Nothing was stored."""


class DateRefused(JournalError):
    """Today or a future day: it can still receive messages, so an entry would be partial."""


# ---------------------------------------------------------------------------
# The journal block and the records' wording (J4 revision 2, as amended)
# ---------------------------------------------------------------------------

#: Authored text: ``prompt.check_authored_text`` applies and a test runs it. These are
#: the strings J8 measured, and a test asserts they are **identical** to the dev
#: script's, so what shipped is what was measured.
_INTRO = (
    "This is not a conversation, and nobody is present. The system has started a "
    "single run to write a journal entry about the conversations recorded on {day}. "
    "Those records follow. They are being read now, in this run: nothing of that day "
    "was lived through as it passed, and nothing has happened since"
)
_REST = (
    "The entry is about the records. Write:\n"
    "- what the records show happened: who talked about what, and what was asked, "
    "decided or corrected;\n"
    "- what is missing: questions with no answer, things raised and not followed up;\n"
    "- what is unclear: where the records do not settle what was meant.",
    "State only what the records show, and say so when something is an inference from "
    "them. Do not say what anyone felt unless they said so. Use \"I\" only for what "
    "was said in the records as replies. No more than about 400 words.",
    "The entry is kept in the journal. It is not announced to anyone, and it is not "
    "hidden: the person who runs this system reads it, and decides whether it is "
    "added to memory.",
)

#: Given facts (2026-10-02, after the live run). The first live run found one repeatable
#: error: entries stated a conversation count the records contradicted ("four distinct
#: conversations" over a day holding eight). The counts are now stated, and the entity is
#: told to use only them. **This deliberately breaks the pin that the block is
#: character-identical to J8's text**: J8's numbers describe the block without this
#: paragraph. Authored text: `prompt.check_authored_text` applies.
_COUNTS = (
    "The records below hold {conversations} and {messages}. These counts are given: "
    "when you say how many conversations or messages there were, use these, and do "
    "not count anything else yourself."
)

_RECORDS_HEADER = (
    "RECORDS OF {day}: stored messages, not the present situation. Lines marked "
    "\"You\" are replies you gave. Lines marked \"system record\" were written by the "
    "system, not by anyone in the conversation."
)
_OMITTED_NOTE = (
    " The {n} earliest messages of the day are not shown, because they did not fit."
)
_USER_MESSAGE = (
    "(Written by the system, not by a person.) Write the journal entry for {day}."
)


def long_date(day: date) -> str:
    """``Monday 21 September 2026``, the form the measured block used."""
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')}"


def local_time_text(moment: datetime, tz: ZoneInfo) -> str:
    """``Wednesday 30 September 2026, 21:40``, the form the measured block used."""
    local = moment.astimezone(tz)
    return f"{long_date(local.date())}, {local.strftime('%H:%M')}"


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def counts_paragraph(conversations: int, messages: int) -> str:
    return _COUNTS.format(conversations=_plural(conversations, "conversation"),
                          messages=_plural(messages, "message"))


def journal_block(
    now_text: str, day_text: str, *, conversations: int, messages: int, clause: bool = False
) -> str:
    """The journal block: the entity's situation **and** the gate's ground truth.

    One function builds both, so the entity and the gate cannot be shown different
    wording. ``conversations`` and ``messages`` are the counts of the records **as shown**
    (a message omitted to fit is not counted; the omission is stated separately).
    (If the live run finds the clause primes the entity, the fallback is to
    give the gate the clause and the entity not; that needs this split and a test that
    the two cannot drift. Not built: it is costed only if the run says so.)
    """
    intro = _INTRO.format(day=day_text) + (CLAUSE if clause else "") + "."
    return "\n\n".join(
        [f"Current time: {now_text}.", intro, counts_paragraph(conversations, messages), *_REST]
    )


# ---------------------------------------------------------------------------
# The covered day
# ---------------------------------------------------------------------------


def covered_window(covered: date, tz: ZoneInfo) -> tuple[str, str]:
    """``[start, end)`` of a local calendar day, as UTC ``isoformat`` strings.

    Both ends are local midnights converted to UTC, so a day that is 23 or 25 hours long
    on a clock change is its real length rather than 24 hours from the start.
    """
    start = datetime.combine(covered, time.min, tzinfo=tz)
    end = datetime.combine(covered + timedelta(days=1), time.min, tzinfo=tz)
    return (start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat())


def default_covered_date(now: datetime, tz: ZoneInfo) -> date:
    return now.astimezone(tz).date() - timedelta(days=1)


def check_date(covered: date, now: datetime, tz: ZoneInfo) -> None:
    today = now.astimezone(tz).date()
    if covered >= today:
        raise DateRefused(
            f"{covered.isoformat()} is {'today' if covered == today else 'in the future'} "
            f"(local date {today.isoformat()}). A day that is not over can still receive "
            f"messages, so an entry for it would be partial, and idempotency would then "
            f"block the complete one."
        )


# ---------------------------------------------------------------------------
# Rendering the day (J3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Unit:
    """One rendered message, with the system-record lines that belong to it."""

    conversation_id: str
    owner: str
    when: datetime
    speaker: str
    text: str
    system_lines: tuple[str, ...] = ()
    #: The message was longer than ``journal.max_message_chars`` and was cut.
    clipped: bool = False


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]} … [clipped: {len(text) - limit} more characters]"


def _outcome_words(entry: dict[str, Any]) -> str:
    tool, outcome = str(entry.get("tool")), str(entry.get("outcome", ""))
    if tool in side_effect_tools():
        # The row is the primary record, not the trace's label (O23): a write that failed
        # after its row was committed is `tool_error` in the trace and `saved` here.
        found = receipts.for_trace([entry])
        words = {receipts.SAVED: "ran — saved", receipts.NOT_SAVED: "did not save anything",
                 receipts.UNKNOWN: "ran — outcome unknown"}
        return "; ".join(words[r.outcome] for r in found) or "ran — outcome unknown"
    return {
        "ok": "ran — ok",
        "tool_error": "ran — failed",
        "timeout": "ran — timed out, outcome unknown",
    }.get(outcome, "did not run")


def system_record_lines(tool_trace: str | None) -> tuple[str, ...]:
    """``(system record: <tool> ran — saved)`` lines: tool name and outcome, **never
    arguments** (J3). An unreadable trace gives no lines rather than guessing."""
    if not tool_trace:
        return ()
    try:
        trace = json.loads(tool_trace)
    except ValueError:
        return ()
    if not isinstance(trace, list):
        return ()
    return tuple(
        f"(system record: {entry.get('tool')} {_outcome_words(entry)})"
        for entry in loop.call_entries(trace)
        if isinstance(entry, dict) and entry.get("tool")
    )


def _units(rows, tz: ZoneInfo) -> list[_Unit]:
    clip = config.journal_max_message_chars()
    units = []
    for row in rows:
        when = datetime.fromisoformat(row["timestamp"])
        is_entity = row["role"] == "assistant"
        owner = row["owner_name"] or "someone"
        units.append(_Unit(
            conversation_id=row["conversation_id"],
            owner=owner,
            when=when.astimezone(tz),
            speaker="You" if is_entity else owner,
            text=_clip(row["content"], clip),
            clipped=len(row["content"]) > clip,
            system_lines=system_record_lines(row["tool_trace"]) if is_entity else (),
        ))
    return sorted(units, key=lambda u: (u.when, u.conversation_id))


def render_records(units: list[_Unit]) -> str:
    """Per conversation, in time order; conversations ordered by when they began."""
    by_conversation: dict[str, list[_Unit]] = {}
    for unit in units:
        by_conversation.setdefault(unit.conversation_id, []).append(unit)
    blocks = []
    for group in sorted(by_conversation.values(), key=lambda g: g[0].when):
        first, last = group[0].when, group[-1].when
        lines = [f"Conversation with {group[0].owner}, "
                 f"{first.strftime('%H:%M')}–{last.strftime('%H:%M')}"]
        for unit in group:
            stamp = unit.when.strftime("%H:%M")
            lines.append(f"[{stamp}] {unit.speaker}: {unit.text}")
            lines.extend(f"[{stamp}] {line}" for line in unit.system_lines)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Preparing the prompt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    covered: date
    window: tuple[str, str]
    messages_in: int
    messages_omitted: int
    conversations: int
    #: Of the messages **shown to the model**, how many were cut at
    #: ``journal.max_message_chars``. An omitted message is not counted here.
    messages_clipped: int
    clause: bool
    block: str
    system: str
    user: str
    estimated_prompt_tokens: int
    model: str
    prompt_revision: str

    def note(self) -> dict[str, Any]:
        """What goes into ``extraction_note`` (J2). ``messages_in`` is every message the
        day held; ``messages_omitted`` is how many of those did not fit, and
        ``messages_clipped`` is how many of the ones shown were cut at
        ``journal.max_message_chars``."""
        return {
            "messages_in": self.messages_in,
            "messages_omitted": self.messages_omitted,
            "messages_clipped": self.messages_clipped,
            "conversations": self.conversations,
            "model": self.model,
            "prompt_revision": self.prompt_revision,
        }


def _block_for(now_text: str, day_text: str, shown: list[_Unit], clause: bool) -> str:
    return journal_block(
        now_text, day_text, clause=clause,
        conversations=len({u.conversation_id for u in shown}), messages=len(shown),
    )


def _assemble(block: str, day_text: str, shown: list[_Unit], omitted: int) -> tuple[str, str]:
    header = _RECORDS_HEADER.format(day=day_text)
    if omitted:
        header += _OMITTED_NOTE.format(n=omitted)
    prompt.check_authored_text(header, "journal records header")
    system = prompt.build_system_prompt(block)
    system = "\n\n".join(part for part in (system, header, render_records(shown)) if part)
    return system, _USER_MESSAGE.format(day=day_text)


def _fits(system: str, user: str) -> bool:
    return not history.plan_budget(system_prompt_chars=len(system) + len(user)).over_committed


def prepare(
    covered: date, *, clause: bool = False, now: datetime | None = None
) -> Prepared | None:
    """Render the day and build the prompt. No model call, no write.

    Returns ``None`` for a day with no messages: an entry about an empty day invites
    filler, and filler about the entity's own day is where confabulation starts (J1).
    Refuses today and future days.

    **Budget (J3):** the system prompt is measured first. If the day does not fit the
    window, the **earliest messages are dropped, whole messages only**, and the count is
    stated in the prompt and in the row. Nothing is deleted, only not sent.
    """
    tz = ZoneInfo(config.timezone())
    now = now or datetime.now(timezone.utc)
    check_date(covered, now, tz)

    start, end = covered_window(covered, tz)
    rows = db.get_messages_between(start, end)
    if not rows:
        return None
    units = _units(rows, tz)

    day_text = long_date(covered)
    now_text = local_time_text(now, tz)
    prompt.check_authored_text(_block_for(now_text, day_text, units, clause), "journal block")
    prompt.check_authored_text(_USER_MESSAGE.format(day=day_text), "journal user message")

    # Smallest number of earliest messages to drop so that the prompt fits. Cost only
    # falls as more are dropped, so this is a search, not a scan.
    def fits(k: int) -> bool:
        shown = units[k:]
        return _fits(*_assemble(_block_for(now_text, day_text, shown, clause), day_text, shown, k))

    if not fits(len(units)):
        raise JournalError(
            "soul.md and the journal block alone do not fit the context window; nothing "
            "can be shown to the model"
        )
    low, high = 0, len(units)
    while low < high:
        mid = (low + high) // 2
        if fits(mid):
            high = mid
        else:
            low = mid + 1
    omitted = low
    if omitted == len(units):
        raise JournalError(
            f"not even the latest message of {covered.isoformat()} fits the context "
            f"window beside soul.md and the journal block"
        )

    shown = units[omitted:]
    block = _block_for(now_text, day_text, shown, clause)
    system, user = _assemble(block, day_text, shown, omitted)
    budget = history.plan_budget(system_prompt_chars=len(system) + len(user))
    return Prepared(
        covered=covered,
        window=(start, end),
        messages_in=len(units),
        messages_omitted=omitted,
        messages_clipped=sum(u.clipped for u in units[omitted:]),
        conversations=len({u.conversation_id for u in units}),
        clause=clause,
        block=block,
        system=system,
        user=user,
        estimated_prompt_tokens=budget.system_prompt_tokens,
        model=config.chat_model(),
        prompt_revision=PROMPT_REVISION_WITH_CLAUSE if clause else PROMPT_REVISION,
    )


# ---------------------------------------------------------------------------
# Generating
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Generated:
    text: str
    verdict: gate.GateVerdict


def generate(prepared: Prepared) -> Generated:
    """One model call, then the identity gate. Writes nothing.

    A reply that reached the output cap is **never used** (B8): it raises
    ``OllamaOutputTruncated`` and the caller stores nothing. An empty reply raises too.
    The gate sees the entry text and the **same block the entity was given**, as the
    situation (J8).
    """
    cap = loop.output_cap()
    response = ollama.chat(
        [{"role": "system", "content": prepared.system},
         {"role": "user", "content": prepared.user}],
        options={"num_predict": cap},
    )
    if response.get("done_reason") == "length":
        raise ollama.OllamaOutputTruncated(
            f"The journal run's reply reached the {cap}-token output cap without "
            f"finishing (eval_count {response.get('eval_count')}). Nothing was stored; "
            f"a re-run is safe."
        )
    text = ((response.get("message") or {}).get("content") or "").strip()
    if not text:
        raise JournalError("the model returned an empty entry; nothing was stored")
    return Generated(text=text, verdict=gate.check_identity(text, situation=prepared.block))


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteResult:
    #: ``written`` | ``exists`` | ``empty`` | ``dry_run``
    status: str
    covered: date
    prepared: Prepared | None = None
    entry: storage.StoredEntry | None = None
    verdict: gate.GateVerdict | None = None
    text: str | None = None
    existing_id: str | None = None
    #: ``dry_run`` only: a message about the existing entry that a real run would not replace.
    notes: list[str] = field(default_factory=list)


def write_entry(
    covered: date | None = None,
    *,
    clause: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> WriteResult:
    """Write the journal entry for one local day. Idempotent per covered date (J1).

    ``covered`` defaults to yesterday. Today and future days raise :class:`DateRefused`.
    An existing entry for the date is reported and nothing is done. A day with no
    messages writes nothing. ``dry_run`` makes no model call and writes nothing.

    **The entry is stored but not indexed.** It reaches memory only through
    ``indexing.index_existing`` after the operator has read it (J7).
    """
    tz = ZoneInfo(config.timezone())
    now = now or datetime.now(timezone.utc)
    covered = covered or default_covered_date(now, tz)
    check_date(covered, now, tz)

    existing = storage.find_entry(covered)
    if existing is not None and not dry_run:
        return WriteResult("exists", covered, existing_id=existing["id"])

    prepared = prepare(covered, clause=clause, now=now)
    if prepared is None:
        return WriteResult("empty", covered)
    if dry_run:
        notes = [f"an entry for this date already exists ({existing['id']})"] if existing else []
        return WriteResult("dry_run", covered, prepared=prepared,
                           existing_id=existing["id"] if existing else None, notes=notes)

    generated = generate(prepared)
    stored = storage.store(
        generated.text,
        covered,
        prepared.note(),
        generated.verdict.to_json(),
        AttributionContext.for_entity().user_id,
    )
    return WriteResult("written", covered, prepared=prepared, entry=stored,
                       verdict=generated.verdict, text=generated.text)
