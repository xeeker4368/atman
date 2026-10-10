"""`retrieval.render_corrections`: correction links are still written, and rendered only while
the setting is on (piece 3.1b, `docs/FIX_PLAN_2026-10-04.md`).

**The default is ON, and ON is today's behaviour byte for byte.** That is the condition this
piece was approved under: the digest below was taken by running :func:`_rendered` on the code
BEFORE the setting existed (2026-10-06, this branch's base), so it pins "unchanged from
before", not merely "self-consistent". Clock and ids are fixed, so it does not move by date.

Off: no annotation, no "check did not complete" note, and the resolution query is not run, in
the passive records and in `memory_search` alike. The links themselves are untouched.
"""

from __future__ import annotations

import hashlib
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from program import config
from program.engine import prompt
from program.memory import chunking, db, retrieval, supersession
from program.memory.supersession import CONTRADICTED, REPLACED
from program.settings import store as settings_store
from program.settings.permissions import Actor, Role
from program.tools import memory_search

#: sha256 of the passive system prompt and the memory_search result over the scenario below,
#: computed on the code before `render_corrections` existed. See the module docstring.
BEFORE_RENDER_SETTING = "c4cb8962fd8a3ec88bff4ff3f24ba6717256a25243fb7e7a50914f33fbe5f5ab"

QUERY = "dentist Tuesday"


def _embedding(text: str, *_args, **_kwargs):
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [(digest[i % len(digest)] / 255.0) for i in range(768)]


@pytest.fixture
def scenario(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(chunking.ollama, "embed", _embedding)
    monkeypatch.setattr(retrieval.ollama, "embed", _embedding)
    clock = itertools.count()
    start = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(db, "now_iso", lambda: (start + timedelta(seconds=next(clock))).isoformat())
    ids = itertools.count(1)
    monkeypatch.setattr(db, "new_id", lambda: f"id-{next(ids):04d}")
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")

    def write(*turns):
        cid = db.start_conversation(lyle)
        written = [db.save_message(cid, lyle, role, text) for role, text in turns]
        db.end_conversation(cid)
        chunking.finalise_conversation(cid)
        return written

    claim = write(("user", "The dentist is on Tuesday at 3."),
                  ("assistant", "Noted, Tuesday at 3 for the dentist."))
    fix = write(("user", "Actually the dentist moved to Wednesday."))
    other = write(("user", "The dentist on Tuesday said my teeth are fine."))
    doubt = write(("user", "I don't think the dentist said that on Tuesday."))
    db.create_supersedes_link(fix[0], claim[0], REPLACED)
    db.create_supersedes_link(doubt[0], other[0], CONTRADICTED)
    return {"lyle": lyle, "links": len(db.get_supersedes_links())}


def _rendered() -> str:
    passive = prompt.build_system_prompt("", retrieval=retrieval.search(QUERY))
    tool = memory_search.MEMORY_SEARCH.handler(QUERY)
    return passive + "\x00" + tool


def _digest() -> str:
    return hashlib.sha256(_rendered().encode("utf-8")).hexdigest()


def test_the_default_renders_exactly_what_it_rendered_before_the_setting(scenario):
    text = _rendered()
    assert "Later corrected by" in text, "the scenario does carry corrections"
    assert _digest() == BEFORE_RENDER_SETTING


def _set(value: bool, scenario) -> None:
    lyle = Actor(user_id=scenario["lyle"], name="Lyle", role=Role.ADMIN)
    settings_store.set("retrieval.render_corrections", value, lyle)


def test_the_seed_is_on():
    assert config.retrieval_render_corrections() is True


def test_switched_on_explicitly_it_is_still_byte_identical(scenario):
    _set(True, scenario)
    assert _digest() == BEFORE_RENDER_SETTING


def test_off_renders_no_annotation_and_no_note_in_either_path(scenario, monkeypatch):
    _set(False, scenario)
    text = _rendered()
    assert "Later corrected by" not in text and "Later contradicted by" not in text
    assert "did not complete" not in text, "off is not a failed check"
    assert text.count("[record 1 ·") == 2, "the records themselves still render, in both paths"


def test_off_does_not_run_the_resolution_query(scenario, monkeypatch):
    _set(False, scenario)

    def must_not_run(*_a, **_k):
        raise AssertionError("resolution query ran while render_corrections is off")

    monkeypatch.setattr(retrieval, "resolve_for_chunks", must_not_run)
    result = retrieval.search(QUERY)
    assert result.results and result.supersession.resolved
    assert all(c.supersessions == [] for c in result.results)
    memory_search.MEMORY_SEARCH.handler(QUERY)


def test_off_leaves_the_links_as_they_are(scenario):
    _set(False, scenario)
    _rendered()
    assert len(db.get_supersedes_links()) == scenario["links"]


def test_the_setting_takes_effect_without_a_restart(scenario):
    _set(False, scenario)
    assert "Later corrected by" not in _rendered()
    _set(True, scenario)
    assert _digest() == BEFORE_RENDER_SETTING


def test_a_failed_check_still_says_so_when_on(scenario, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(retrieval, "resolve_for_chunks", broken)
    assert "did not complete" in prompt.render_retrieved(retrieval.search(QUERY))
    assert supersession.SupersessionReport().resolved
