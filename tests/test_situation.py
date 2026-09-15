"""The current-situation block. BUILD_PLAN Phase 1 (Tier 2), decision #5.

Two halves: `program/engine/situation.py` is pure and tested with datetimes
alone, and the wiring in `turn.py` is tested against a real store — because the
one thing that can go wrong there is invisible in the pure function. The block
is built *after* the user's message is persisted, so the message being answered
is already this person's most recent one; measuring without excluding it reports
a gap of roughly zero on every turn, forever, plausibly and silently.

`test_a_fresh_turn_does_not_report_a_zero_gap` is that trap, pinned.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from program.engine import loop, prompt, situation, turn
from program.memory import db
from program.settings.permissions import Actor, Role

NOW = datetime(2026, 9, 15, 17, 24, tzinfo=timezone.utc)


# --- Granularity -------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "less than a minute"),
        (1, "less than a minute"),
        (59, "less than a minute"),
        (60, "1 minute"),
        (90, "2 minutes"),
        (60 * 59, "59 minutes"),
        (60 * 90, "90 minutes"),
        (60 * 119, "119 minutes"),
        (3600 * 2, "2 hours"),
        (3600 * 14, "14 hours"),
        (3600 * 47, "47 hours"),
        (3600 * 48, "2 days"),
        (86400 * 5, "5 days"),
        (86400 * 30, "30 days"),
    ],
)
def test_each_granularity_band(seconds, expected):
    assert situation.format_elapsed(seconds) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [
        # The bug: these are under the band ceiling in RAW SECONDS but round up
        # to the next band's floor. Checking raw seconds emitted "120 minutes"
        # and "48 hours" — counts the bands promise never to produce.
        (7190, "2 hours"),      # 1h 59m 50s -> rounds to 120 minutes
        (7199, "2 hours"),      # 1h 59m 59s
        (7200, "2 hours"),      # exactly 2h, the band floor itself
        (172700, "2 days"),     # 47h 58m 20s -> rounds to 48 hours
        (172799, "2 days"),     # 47h 59m 59s
        (172800, "2 days"),     # exactly 48h, the band floor itself
        # The other edge of each band, which must NOT be pushed up.
        (7139, "119 minutes"),   # 1h 58m 59s -> rounds to 119, stays put
        (170639, "47 hours"),    # 47h 23m 59s -> rounds to 47, stays put
    ],
)
def test_band_boundaries_are_decided_by_the_rounded_count(seconds, expected):
    """The band a figure lands in must follow the number actually displayed.

    Comfortably-inside-band values cannot catch this — which is how it survived
    the first 28 tests — so every case here sits within a minute of a boundary.
    """
    assert situation.format_elapsed(seconds) == expected


def test_no_rendering_ever_exceeds_its_own_bands_ceiling():
    """Exhaustive over five days: the property the boundary cases are instances
    of. A band that can emit its own ceiling is not a band."""
    for seconds in range(0, 86400 * 5, 7):
        rendered = situation.format_elapsed(seconds)
        parts = rendered.split()
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        count, unit = int(parts[0]), parts[1].rstrip("s")
        assert count >= 1, f"{seconds}s -> {rendered}"
        if unit == "minute":
            assert count < 120, f"{seconds}s -> {rendered}"
        elif unit == "hour":
            assert count < 48, f"{seconds}s -> {rendered}"


def test_no_unit_is_ever_reported_as_one_except_minutes():
    """The rule's whole point: a count of 1 carries 50% relative error at the
    band's lower edge, so hours and days wait for 2. Minutes are the floor unit
    and exempt — "1 minute" is not misleading the way "1 hour" would be."""
    rendered = {situation.format_elapsed(s) for s in range(60, 86400 * 4, 37)}

    assert "1 hour" not in rendered
    assert "1 day" not in rendered
    assert "1 minute" in rendered


def test_seconds_and_compound_forms_never_appear():
    """"14 hours, 23 minutes, 7 seconds" is not a flat statement of fact, and
    the spurious precision invites the figure to be read as significant."""
    for seconds in (61, 3671, 50000, 123456, 987654):
        rendered = situation.format_elapsed(seconds)
        assert "second" not in rendered or rendered == "less than a minute"
        assert "," not in rendered
        assert rendered.count(" ") == 1 or rendered == "less than a minute"


def test_a_backwards_clock_is_reported_not_rendered_as_a_negative_gap():
    rendered = situation.format_elapsed(-500)

    assert "unknown" in rendered
    assert "-" not in rendered


# --- The block itself --------------------------------------------------------


def test_a_gap_states_the_figure_and_the_pairing_together(monkeypatch):
    block = situation.build_situation(NOW, NOW - timedelta(hours=14), "Lyle")

    assert "14 hours" in block
    assert prompt.states_elapsed_time(block)
    assert prompt.has_pairing(block), "the figure would reach the model naked"
    prompt.build_system_prompt(block)  # raises if the pairing is missing


def test_the_first_ever_message_says_so_rather_than_reporting_zero():
    """Neither a false "0 minutes" nor silence: silence leaves the model to
    infer something about a gap it was never told about."""
    block = situation.build_situation(NOW, None, "Lyle")

    assert "first message" in block
    assert "no earlier one" in block
    assert "0 " not in block
    assert "minute" not in block


def test_the_no_prior_message_block_passes_the_pairing_check():
    """No figure, so no pairing is demanded — asserting that a nonexistent gap
    held no experience would be noise. This pins that the phrasing does not
    accidentally trip `_ELAPSED` and start requiring one."""
    block = situation.build_situation(NOW, None, "Lyle")

    assert not prompt.states_elapsed_time(block)
    prompt.build_system_prompt(block)


def test_the_timestamp_is_local_not_utc():
    """Stored timestamps are UTC; the clock on the household's wall is not."""
    block = situation.build_situation(NOW, None, "Lyle")

    assert "13:24" in block, block
    assert "17:24" not in block


def test_a_naive_previous_timestamp_is_treated_as_utc():
    block = situation.build_situation(NOW, datetime(2026, 9, 15, 15, 24), "Lyle")

    assert "2 hours" in block


# --- The wiring, against a real store ----------------------------------------


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(turn, "_retrieve", lambda query: None)
    monkeypatch.setattr(
        loop.ollama, "chat",
        lambda messages, **kw: {"message": {"role": "assistant", "content": "ok"}},
    )
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    return {
        "lyle": Actor(user_id=lyle, name="Lyle", role=Role.ADMIN),
        "jodie": Actor(user_id=jodie, name="Jodie", role=Role.USER),
    }


def captured_block(monkeypatch) -> dict:
    """Capture the system prompt the loop was handed."""
    seen: dict = {}

    def fake(messages, **kwargs):
        seen["system"] = messages[0]["content"]
        return {"message": {"role": "assistant", "content": "ok"}}

    monkeypatch.setattr(loop.ollama, "chat", fake)
    return seen


def test_a_fresh_turn_does_not_report_a_zero_gap(store, monkeypatch):
    """THE TRAP. The user's message is persisted before the block is built, so
    without excluding it the gap is always ~0 — plausible, constant, wrong."""
    seen = captured_block(monkeypatch)
    first = turn.handle_user_message(store["lyle"], "morning")
    with db.transaction() as conn:
        conn.execute("UPDATE messages SET timestamp = ?",
                     ((datetime.now(timezone.utc) - timedelta(hours=14)).isoformat(),))

    turn.handle_user_message(store["lyle"], "back again", first.conversation_id)

    assert "14 hours" in seen["system"]
    assert "less than a minute" not in seen["system"]
    assert "0 minutes" not in seen["system"]


def test_the_very_first_turn_reports_no_prior_message(store, monkeypatch):
    seen = captured_block(monkeypatch)

    turn.handle_user_message(store["lyle"], "hello for the first time")

    assert "first message from Lyle" in seen["system"]
    assert "It has been" not in seen["system"]


def test_the_gap_is_measured_across_conversations_not_within_one(store, monkeypatch):
    """idle_close_minutes is 15, so conversations close on their own — a
    conversation-scoped figure would report "first message" nearly every
    session, a discontinuity manufactured by a janitor setting."""
    seen = captured_block(monkeypatch)
    turn.handle_user_message(store["lyle"], "in the first conversation")
    with db.transaction() as conn:
        conn.execute("UPDATE messages SET timestamp = ?",
                     ((datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),))

    turn.handle_user_message(store["lyle"], "in a brand new conversation")

    assert "3 hours" in seen["system"]
    assert "first message" not in seen["system"]


def test_another_users_activity_does_not_shorten_the_gap(store, monkeypatch):
    """The entity's sense of time with the person in front of it. Decision #20
    governs what retrieval may surface; this is a different axis."""
    seen = captured_block(monkeypatch)
    lyles = turn.handle_user_message(store["lyle"], "Lyle speaking")
    with db.transaction() as conn:
        conn.execute("UPDATE messages SET timestamp = ?",
                     ((datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),))
    turn.handle_user_message(store["jodie"], "Jodie speaking, just now")

    turn.handle_user_message(store["lyle"], "Lyle again", lyles.conversation_id)

    assert "3 days" in seen["system"]


def test_an_explicit_situation_still_overrides(store, monkeypatch):
    """Passing "" sends no block, which is what tests of other behaviour want."""
    seen = captured_block(monkeypatch)

    turn.handle_user_message(store["lyle"], "hello", situation="")

    assert "The current time is" not in seen["system"]


def test_the_block_reaches_the_model_in_the_system_prompt(store, monkeypatch):
    seen = captured_block(monkeypatch)

    turn.handle_user_message(store["lyle"], "hello")

    assert "The current time is" in seen["system"]
    # soul.md first, then the situation — stating the gap before the rule that
    # says what it means is the confabulation ordering.
    assert seen["system"].index("no name") < seen["system"].index("current time is")
