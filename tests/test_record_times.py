"""A retrieved record says when its conversation happened, in local time (fix plan 3.4b).

Never the chunk's ``created_at``: that is when the chunk was written (an idle close can
be hours after the exchange) and it is stored in UTC. The situation block reports local
time (``app.timezone``), so a UTC date beside it reads as a different day.
"""

from __future__ import annotations

import pytest

from program.engine import prompt
from program.memory import chunking, db, retrieval
from program.tools import moltbook
from tests.test_supersession import _deterministic_embedding


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(chunking.ollama, "embed", _deterministic_embedding)
    monkeypatch.setattr(retrieval.ollama, "embed", _deterministic_embedding)
    db.init_databases()
    return db.create_user("Lyle", role="admin")


def write(user_id, *turns):
    """A closed, chunked conversation; each turn is (role, text, utc_timestamp)."""
    conversation_id = db.start_conversation(user_id)
    for role, text, when in turns:
        db.save_message(conversation_id, user_id, role, text, timestamp=when)
    db.end_conversation(conversation_id)
    chunking.finalise_conversation(conversation_id)
    return conversation_id


def rendered_for(query):
    return prompt.render_retrieved(retrieval.search(query))


def test_a_chunk_crossing_local_midnight_shows_both_local_dates(store):
    # 03:50Z and 04:10Z on 6 October are 23:50 on 5 October and 00:10 on 6 October, EDT.
    write(store, ("user", "the zebra finch question", "2026-10-06T03:50:00+00:00"),
          ("assistant", "about the zebra finch", "2026-10-06T04:10:00+00:00"))

    text = rendered_for("zebra finch")

    assert "Monday 5 October 2026, 23:50 to Tuesday 6 October 2026, 00:10 EDT" in text
    assert "2026-10-06T" not in text and "+00:00" not in text


def test_a_chunk_whose_utc_date_differs_from_its_local_date_shows_the_local_date(store):
    # The fix plan's pinning case: written 00:56Z on 4 October, beside a 21:24 EDT clock on
    # 3 October. Locally it happened on Saturday 3 October.
    write(store, ("user", "the heron by the pond", "2026-10-04T00:56:58+00:00"),
          ("assistant", "the heron again", "2026-10-04T00:57:30+00:00"))

    text = rendered_for("heron pond")

    assert "Saturday 3 October 2026, 20:56 to 20:57 EDT" in text
    assert "Sunday 4 October" not in text and "2026-10-04" not in text


def test_the_record_shows_conversation_time_not_the_time_the_chunk_was_written(store):
    conversation = write(store, ("user", "the otter story", "2026-09-01T14:00:00+00:00"),
                         ("assistant", "otters indeed", "2026-09-01T14:05:00+00:00"))
    written = db.get_conversation_chunks(conversation)[0]["created_at"]
    assert not written.startswith("2026-09-01"), "the chunk must have been written later"

    text = rendered_for("otter story")

    assert "Tuesday 1 September 2026, 10:00 to 10:05 EDT" in text
    assert prompt.local_day(written) not in text


def test_one_instant_and_a_same_day_range_name_the_date_once():
    assert prompt.local_when("2026-10-05T18:02:00+00:00") == "Monday 5 October 2026 at 14:02 EDT"
    assert prompt.local_when("2026-10-05T18:02:00+00:00", "2026-10-05T18:20:00+00:00") == (
        "Monday 5 October 2026, 14:02 to 14:20 EDT")


def test_a_range_across_a_clock_change_names_both_zones():
    # 1 November 2026: clocks go back at 02:00 EDT (06:00Z).
    assert prompt.local_when("2026-11-01T05:30:00+00:00", "2026-11-01T06:30:00+00:00") == (
        "Sunday 1 November 2026, 01:30 EDT to 01:30 EST")


def test_an_unreadable_time_is_said_to_be_unknown_never_guessed():
    assert prompt.local_when(None) == "time unknown"
    assert prompt.local_when("not a time") == "time unknown"


def test_a_correction_annotation_uses_the_local_date():
    # 01:00Z on 4 October is the evening of 3 October locally.
    assert prompt.local_day("2026-10-04T01:00:00+00:00") == "Saturday 3 October 2026"


def test_moltbook_dates_are_local_too():
    assert moltbook._date("2026-10-04T01:00:00Z") == "2026-10-03"
    assert moltbook._date("2026-10-04T15:00:00.123+00:00") == "2026-10-04"
    assert moltbook._date("garbage") == "date unknown"
    assert moltbook._date(None) == "date unknown"
