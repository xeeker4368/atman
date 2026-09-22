"""Writing with nobody there. Phase 4, task B5.

Design of record: `docs/MEDIA_AND_CREATIVE_DESIGN.md` (Q2c, Q10).

**What this task is NOT.** Decision #10 wants creative writing available in
autonomous and background sessions as well as live conversation. **No such session
mode exists** — there is no scheduler, no reflection cycle and no research runner —
and building a wiring into one would be R2's *"an unmounted gate is worse than an
absent one"*: a seam that reads as support for something that cannot happen.

So the reviewed scope is a **provable property** instead: the writing path works with
no person, no conversation, no turn and no actor. When Phase 5 or 6 builds a real
autonomous session, it calls the tool the way these tests do and nothing here has to
change — and if something ever makes that impossible, these fail and say so.

The last test asserts the absence, so "B5 built nothing" stays a checked fact rather
than a claim in a changelog.
"""

from __future__ import annotations

import hashlib

import pytest

from program.artifacts import indexing, writing
from program.attribution import AttributionContext
from program.engine import prompt
from program.memory import db, retrieval
from program.tools import registry
from program.tools.registry import ToolOutcome

PIECE = "A night with nobody in it. The house cooled by degrees, unobserved."


def deterministic(text, *_a, **_k):
    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[i % len(digest)] / 255.0) for i in range(768)]


@pytest.fixture
def empty_store(isolated_data_dir, monkeypatch):
    """A store with **no users and no conversations**. Deliberately bare: the point
    is that nothing here needs a person to already exist."""
    monkeypatch.setattr(indexing.ollama, "embed", deterministic)
    monkeypatch.setattr(retrieval.ollama, "embed", deterministic)
    db.init_databases()
    assert db.list_users() == []
    return isolated_data_dir


def test_the_store_really_is_empty(empty_store):
    """A guard on the fixture: if it seeded a user, every test below would pass
    without proving anything about the no-person case."""
    assert db.list_users() == []
    assert db.get_open_conversations_with_activity() == []


def test_writing_works_with_no_person_and_no_conversation(empty_store):
    stored = writing.store(PIECE, AttributionContext.for_entity().user_id)

    assert stored.absolute_path.is_file()
    assert stored.indexed
    row = db.get_artifact(stored.artifact_id)
    assert row["artifact_type"] == "creative_writing"
    assert db.get_artifact_chunks(stored.artifact_id)[0]["conversation_id"] is None


def test_the_entity_row_is_created_on_demand_by_the_first_write(empty_store):
    """Nothing seeds it — `for_entity()` is what brings it into existence, and it has
    to, because of the next test."""
    assert db.list_users() == []

    context = AttributionContext.for_entity()

    [row] = db.list_users()
    assert row["id"] == context.user_id
    assert row["name"] == db.ENTITY_USER_NAME
    assert row["password_hash"] is None


def test_the_foreign_key_is_what_makes_that_creation_load_bearing(empty_store):
    """`artifacts.user_id` and `chunks.user_id` both REFERENCES users(id), and
    `PRAGMA foreign_keys` is on — so a write attributed to a user with no row fails.

    That is why `for_entity()` creating the row is not decoration: without it the
    whole no-person path would raise at the first insert.
    """
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        writing.store(PIECE, "a-user-id-that-has-no-row")


def test_the_tool_dispatches_with_no_turn_and_no_actor(empty_store):
    """No `handle_user_message`, no agent loop, no `Actor` — just the registry and an
    attribution context. This is the call an autonomous session would make."""
    registry.reset_default_registry()
    try:
        result = registry.dispatch(
            "creative_write",
            {"text": PIECE, "title": "Unobserved"},
            attribution=AttributionContext.for_entity(),
        )

        assert result.outcome is ToolOutcome.OK
        assert "Saved." in result.value
        [row] = db.list_artifacts()
        assert row["user_id"] == AttributionContext.for_entity().user_id
    finally:
        registry.reset_default_registry()


def test_unattended_work_is_retrievable_and_labelled_like_any_other(empty_store):
    """It is indexed the same way, found the same way, and announces itself the same
    way. Q9: private means not proactively announced, not withheld from retrieval —
    and that does not change because nobody was present when it was written."""
    stored = writing.store(PIECE, AttributionContext.for_entity().user_id,
                           title="Unobserved")

    result = retrieval.search("house cooled unobserved")
    found = [r for r in result.results if r.chunk_id in stored.chunk_ids]

    assert found, "work written with nobody present was not retrievable"
    assert found[0].source_type == "creative_writing"
    assert "creative writing" in prompt.render_retrieved(result)


def test_it_is_attributed_to_the_entity_rather_than_to_a_household_member(empty_store):
    """Q2c. With no person present there is no person to file it under, and the
    entity's row is a real row rather than a sentinel value in a nullable column."""
    lyle = db.create_user("Lyle", role="admin")

    stored = writing.store(PIECE, AttributionContext.for_entity().user_id)

    assert db.get_artifact(stored.artifact_id)["user_id"] != lyle
    assert db.get_artifact(stored.artifact_id)["user_id"] == (
        AttributionContext.for_entity().user_id
    )


def test_a_live_turn_still_files_to_the_person(empty_store):
    """The other half of Q2b/Q2c, so the pair cannot both pass by everything being
    attributed one way."""
    jodie = db.create_user("Jodie", role="user")

    person = writing.store(PIECE, AttributionContext(user_id=jodie).user_id)
    unattended = writing.store(PIECE, AttributionContext.for_entity().user_id)

    assert db.get_artifact(person.artifact_id)["user_id"] == jodie
    assert db.get_artifact(unattended.artifact_id)["user_id"] != jodie


def test_b5_did_not_build_a_session_mode():
    """R2's lesson, kept checkable rather than asserted in prose.

    Decision #10 asks for creative writing in autonomous sessions; **there is no
    autonomous session**, and the reviewed scope for this task was a property rather
    than a wiring. If a scheduler or reflection module ever appears, it should arrive
    with its own task and its own tests — at which point this test is what fails and
    points at the change.
    """
    import pathlib

    from program import config

    engine = config.PROJECT_ROOT / "program" / "engine"
    modules = {p.stem for p in engine.glob("*.py")}
    assert not modules & {"scheduler", "autonomous", "reflection", "session", "daemon"}

    program = config.PROJECT_ROOT / "program"
    assert not any(
        pathlib.Path(p).stem in {"scheduler", "reflection", "autonomous"}
        for p in program.rglob("*.py")
    ), "a session mode appeared; B5's scope was a property, not a wiring"
