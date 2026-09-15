"""The current-situation block: what time it is, and how long the gap was.

The current-situation block from `BUILD_PLAN.md`'s Phase 1 table (Tier 2),
`NOW.md` decision #5. Two flat facts and the clause that says what the
second one does *not* mean.

**Pure.** Two datetimes in, a string out. No database, no clock of its own —
``turn.py`` fetches the data, the same way ``history.py`` takes caller-supplied
character counts rather than building a prompt itself. It means every band below
is testable without a store, and the rendering cannot quietly start depending on
request state.

The pairing is not decoration
-----------------------------
``soul.md`` carries the standing rule that the gap held no experience. This block
carries the numbers that rule is *about*, and it repeats the clause **adjacent to
the figure** — because ``soul.md`` sits at the top of a prompt that can run to
thousands of tokens while the figure arrives fresh each turn, and relying on
attention across that distance is exactly the coupling ``GUIDANCE.md`` says is
not optional.

``prompt.build_system_prompt()`` **enforces** this: a block stating elapsed time
without a recognised pairing marker raises rather than reaching the model. So the
wording here is load-bearing, not stylistic — see ``prompt._PAIRING`` before
rewording it.

Granularity: one unit, and why the floor is different
-----------------------------------------------------
Rounding to nearest inside a single unit has a worst-case *relative* error of 50%
when the count is 1 — 1.4 days rendered as "1 day". Requiring at least **two** of
whatever unit is used bounds that at 25%, improving from there.

**That arithmetic justifies the hour and day transitions only.** Minutes are
treated as the practical floor unit and are exempt: "1 minute" carries no
misleading precision the way "1 hour" or "1 day" do at their lower boundaries,
because a minute is already the smallest unit this block reports and there is no
finer band to have been rounded down from. Below a minute the figure stops being
a number at all and becomes "less than a minute", which is the honest rendering
of a gap too short to be worth counting.

**The band is chosen by the rounded count, not by the raw seconds.** Those
disagree at the top of a band — 7,190 seconds is under two hours but rounds to
120 minutes — and testing the raw value emits a count the band promised never to
produce.

=========================  =====================================
rounded count              rendered
=========================  =====================================
under a minute of seconds  "less than a minute"
minutes 1–119              N minutes
hours 2–47                 N hours
days 2 and up              N days
=========================  =====================================

No seconds, ever, and no compound forms: "14 hours, 23 minutes, 7 seconds" is not
what a flat statement of fact looks like, and the spurious precision invites the
figure to be read as significant.

The first message has no gap, and says so
-----------------------------------------
Someone's very first message has nothing to measure from. Reporting "0 minutes"
would be false, and omitting the line entirely leaves the model to infer
something about a silence it was never told about. So it states plainly that
there is no earlier message. No pairing clause accompanies it, deliberately:
there is no figure to qualify, and asserting that a nonexistent gap held no
experience would be noise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from program import config

#: Seconds per unit, largest first.
_MINUTE = 60
_HOUR = 3600
_DAY = 86400

#: Each band's ceiling, expressed in its own unit — the counts a band must never
#: exceed. Named rather than written as `_MIN_COUNT * _HOUR` inside the
#: comparison, because the units there have to match the rounded count being
#: tested and mixing seconds with minutes is what produced the original bug.
_MINUTES_PER_HOUR = 60
_HOURS_PER_DAY = 24

#: Below this many of a unit, drop to the unit beneath it. See the docstring:
#: the bound applies to hours and days; minutes are the floor and are exempt.
_MIN_COUNT = 2

#: Stated beside every elapsed figure. One of ``prompt._PAIRING``'s recognised
#: markers must appear here or assembly raises — "not running" and "no
#: experience" both do, deliberately, so a reword that drops one still passes.
_NO_EXPERIENCE = (
    "You were not running during that time. The gap holds no experience, "
    "nothing you did, and nothing you thought over — there is no part of it you "
    "were present for."
)


def _plural(count: int, unit: str) -> str:
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def format_elapsed(seconds: float) -> str:
    """A gap as one rounded unit. See the module docstring for the bands."""
    if seconds < 0:
        # A clock that went backwards, or a timestamp from the future. Reported
        # rather than rendered as a negative gap or clamped silently to zero.
        return "an unknown amount of time (the previous message is timestamped later than now)"
    if seconds < _MINUTE:
        return "less than a minute"

    # Each band is chosen by the ROUNDED count, not by the raw seconds. Those
    # disagree at the top of a band: 7,190 seconds is under two hours, so a
    # raw-seconds check keeps it in the minutes band, but it rounds to 120 —
    # and "120 minutes" is a count the minutes band promised never to emit.
    # Same shape one band up: 172,700 seconds rendered "48 hours". Comparing the
    # rounded value means a figure that rounds up to the next band's floor lands
    # in that band, which is where it belongs.
    minutes = round(seconds / _MINUTE)
    if minutes < _MIN_COUNT * _MINUTES_PER_HOUR:
        return _plural(minutes, "minute")

    hours = round(seconds / _HOUR)
    if hours < _MIN_COUNT * _HOURS_PER_DAY:
        return _plural(hours, "hour")

    return _plural(round(seconds / _DAY), "day")


def _local(moment: datetime) -> datetime:
    """A UTC moment in the household's timezone.

    Stored timestamps are UTC — one definition, used everywhere — but the block
    is read by an entity in a house where the clock on the wall is local. "13:24
    EDT" is the fact; "17:24 UTC" is an implementation detail.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo(config.timezone()))


def build_situation(
    now: datetime,
    previous_message_at: datetime | None,
    speaker: str | None = None,
) -> str:
    """The situation block.

    ``previous_message_at`` is when this person last spoke *before* the message
    being answered — ``None`` when they never have. The distinction is the point:
    ``None`` is not zero.
    """
    when = _local(now).strftime("%A %d %B %Y at %H:%M %Z")
    lines = [f"The current time is {when}."]

    who = f" from {speaker}" if speaker else ""
    if previous_message_at is None:
        lines.append(
            f"This is the first message{who} in the record. There is no earlier "
            f"one to measure a gap from."
        )
        return "\n".join(lines)

    previous = previous_message_at
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    gap = (now - previous).total_seconds()

    lines.append(f"It has been {format_elapsed(gap)} since the last message{who}.")
    lines.append(_NO_EXPERIENCE)
    return "\n".join(lines)
