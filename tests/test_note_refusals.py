"""CO17 tool text: quote guidance and refusals that say nothing was recorded (piece 8 follow-up).

Piece 8 measured 66 refusals in 120 turns (44 quote-not-in-a-message, 18 retire-takes-no-text, 4 no
quote) and 5 of 6 turns for one retire request ending in a false "I have retired the note". The
texts below are the drafts reviewed at CO17, applied exactly. What they change in the entity's
behaviour is measured separately (`changelog/2026-10-03-notes-exclusion-and-tool-text.md`).
"""

from __future__ import annotations

import ast
import inspect

import pytest

from program import config
from program.attribution import AttributionContext
from program.memory import db
from program.origin import OriginContext
from program.tools import note_propose, note_quotes
from program.tools import note_texts as texts
from program.tools.note_propose import NOTE_PROPOSE
from program.tools.note_search import NOTE_SEARCH
from program.tools.registry import ToolRegistry

SUFFIX = (" Nothing was recorded. If you cannot fix this, tell the person plainly that the note "
          "was not proposed.")


def test_the_description_and_the_quotes_parameter_are_the_reviewed_drafts():
    assert NOTE_PROPOSE.description == (
        "Propose a note about a person, topic or project, or a change to one. "
        "The result says what happened. One short fact per note. "
        "Evidence quotes are words a person said, copied exactly from a message "
        "(never a note's text). A retire takes no text.")
    assert NOTE_PROPOSE.parameters["properties"]["quotes"]["description"] == (
        "Words a person said, copied exactly. Not a note's text.")


def test_the_description_still_says_nothing_about_review():
    """It must stay true in both approval modes (piece 6): the result text says what happened."""
    lowered = NOTE_PROPOSE.description.lower()
    for word in ("review", "approv", "pending", "a person reviews"):
        assert word not in lowered


def test_the_three_rewritten_refusals_are_the_reviewed_drafts():
    assert texts.RETIRE_TAKES_NO_TEXT == "a retire takes no text. Leave text out and call again."
    assert texts.QUOTES_REQUIRED == (
        "give at least one quote: words a person said, copied exactly from a message.")
    assert texts.QUOTE_NO_MATCH == (
        "that quote does not appear in any message a person wrote. Quote words a person said, "
        "copied exactly: not the text of a note, and not your own reply.")


def test_the_shared_suffix_is_the_reviewed_draft_and_proposal_refusals_end_with_it():
    assert texts.NOT_RECORDED == SUFFIX
    error = texts.proposal_refused("subject is empty; give a short label such as a name.")
    assert type(error) is texts.NoteRefused, "the class name the model reads must not change"
    assert str(error) == "subject is empty; give a short label such as a name." + SUFFIX


def test_note_search_refusals_do_not_claim_a_proposal_failed(world):
    result = world["reg"].dispatch("note_search", {"query": "   "})
    assert result.outcome.value == "tool_error"
    assert SUFFIX.strip() not in result.error and "not proposed" not in result.error


def test_no_proposal_refusal_site_bypasses_the_suffix():
    """Every refusal raised by the two proposal modules is built by ``proposal_refused``."""
    for module in (note_propose, note_quotes):
        tree = ast.parse(inspect.getsource(module))
        direct = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                  and n.attr == "NoteRefused" and isinstance(n.ctx, ast.Load)]
        assert direct == [], f"{module.__name__} raises NoteRefused without the suffix"
        assert any(isinstance(n, ast.Attribute) and n.attr == "proposal_refused"
                   for n in ast.walk(tree))


@pytest.fixture
def world(isolated_data_dir):
    config.reload()
    db.init_databases()
    lyle = db.create_user("Lyle", role="admin")
    conv = db.start_conversation(lyle)
    msg = db.save_message(conv, lyle, "user", "Please retire the note about my running schedule.",
                          timestamp="2026-10-02T10:00:00+00:00")
    return {"attribution": AttributionContext(user_id=lyle),
            "origin": OriginContext(conv, msg, frozenset({msg})),
            "reg": ToolRegistry([NOTE_SEARCH, NOTE_PROPOSE])}


def propose(world, **arguments):
    base = dict(action="add", subject_kind="person", subject="Jodie",
                text="Takes oat milk.", quotes=["Please retire the note about my running schedule"])
    base.update({k: v for k, v in arguments.items() if v is not None})
    for key in [k for k, v in arguments.items() if v is None]:
        base.pop(key, None)
    return world["reg"].dispatch("note_propose", base, attribution=world["attribution"],
                                 origin=world["origin"])


@pytest.mark.parametrize("arguments, expected", [
    (dict(subject="  "), texts.SUBJECT_EMPTY),
    (dict(quotes=["nobody ever said this at all"]), texts.QUOTE_NO_MATCH),
    (dict(quotes=[]), texts.QUOTES_REQUIRED),
    (dict(action="retire", note_id="deadbeef", text="x"), None),
    (dict(action="wrong"), None),
])
def test_a_real_refused_call_carries_the_suffix(world, arguments, expected):
    result = propose(world, **arguments)
    assert result.outcome.value in ("tool_error", "invalid_arguments")
    if result.outcome.value == "tool_error":
        assert result.error.startswith("NoteRefused: ")
        assert result.error.endswith(SUFFIX)
        if expected:
            assert expected in result.error


def test_a_retire_that_is_given_text_is_told_to_leave_it_out(world):
    from program.memory import note_admin
    nid = note_admin.operator_add("person", "Lyle's running", "Lyle runs on Tuesdays.").note_id
    result = propose(world, action="retire", note_id=nid[:8], text="gone")
    assert texts.RETIRE_TAKES_NO_TEXT in result.error and result.error.endswith(SUFFIX)


def test_an_accepted_proposal_is_unchanged_by_all_this(world, monkeypatch):
    # An accepted add runs the identity gate on its text, which calls the classifier model; script
    # the reply so the test does not call the real Ollama.
    monkeypatch.setattr(note_propose.gate.classifier, "classify", lambda *a, **k: "CONSISTENT")
    result = propose(world)
    assert result.outcome.value == "ok" and result.value == texts.PENDING_ADD
