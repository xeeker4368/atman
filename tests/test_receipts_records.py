"""Receipts generalised from artifacts to records of a kind: Notes piece 5 (N8, option A).

The trace key is ``records: [{"kind", "id"}]``; each kind is read from its own row. Three things
are held here:

* **artifact receipts are unchanged**, and traces stored before the change keep rendering them;
* **a proposal's receipt is its row's current status**, with a missing row or failed lookup
  *unknown*, never inferred from the trace, and never implying a note exists before a person
  accepts it;
* **the count of receipts equals the count of side-effect calls**, as before.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from program import auth, config
from program.api.app import create_app
from program.artifacts import indexing
from program.attribution import AttributionContext
from program.engine import loop, turn
from program.integrity import classifier, gate, gate_eval
from program.memory import db, note_admin
from program.origin import OriginContext
from program.reflection import journal
from program.tools import receipts, registry
from program.tools.note_propose import NOTE_PROPOSE
from program.tools.registry import Record, Tool, ToolError, ToolOutput, ToolRegistry

STORY = "The kettle had been on the hob so long it had stopped meaning tea."


@pytest.fixture
def world(isolated_data_dir, monkeypatch):
    config.reload()
    monkeypatch.setattr(indexing.ollama, "embed", lambda text, *a, **k: [0.1] * 768)
    db.init_databases()
    monkeypatch.setattr(classifier, "classify", lambda p: "CONSISTENT")
    registry.reset_default_registry()
    lyle = db.create_user("Lyle", role="admin")
    conv = db.start_conversation(lyle)
    msg = db.save_message(conv, lyle, "user", "Jodie takes her coffee with oat milk, never dairy.",
                          timestamp="2026-10-02T10:00:00+00:00")
    return type("W", (), {
        "lyle": lyle, "conv": conv,
        "origin": OriginContext(conv, msg, frozenset({msg})),
        "attribution": AttributionContext(user_id=lyle),
        "reg": ToolRegistry([NOTE_PROPOSE])})


def propose(world, **kw):
    args = dict(action="add", subject_kind="person", subject="Jodie",
                text="Takes her coffee with oat milk.",
                quotes=["Jodie takes her coffee with oat milk"])
    args.update(kw)
    return world.reg.dispatch("note_propose", args, attribution=world.attribution,
                              origin=world.origin)


def pending_id():
    with db.connection() as conn:
        return conn.execute("SELECT id FROM note_proposals ORDER BY created_at DESC").fetchone()[0]


def one(entry):
    [r] = receipts.for_trace([entry])
    return r


# --- Record and ToolOutput -----------------------------------------------------------------


def test_a_record_must_name_a_known_kind_and_an_id():
    with pytest.raises(ToolError, match="unknown record kind"):
        Record("research_candidate", "x")
    with pytest.raises(ToolError, match="non-empty id"):
        Record("artifact", "")
    assert Record("note_proposal", "p").to_dict() == {"kind": "note_proposal", "id": "p"}


def test_the_readers_and_the_reportable_kinds_are_the_same_set():
    """A kind that can be reported but not read would silently read as unknown forever."""
    assert set(receipts.READERS) == set(registry.RECORD_KINDS)


def test_the_existing_handler_shape_is_unchanged_and_lands_as_artifact_records():
    out = ToolOutput("text", ("a" * 32,))
    assert out.all_records == (Record("artifact", "a" * 32),)
    both = ToolOutput("t", ("a" * 32,), (Record("note_proposal", "p"),))
    assert [r.kind for r in both.all_records] == ["artifact", "note_proposal"]


def test_only_a_tool_that_writes_records_may_report_a_proposal():
    bench = ToolRegistry([Tool(
        name="scaffold_liar", description="TEST-ONLY.",
        parameters={"type": "object", "properties": {}},
        handler=lambda: ToolOutput("x", records=(Record("note_proposal", "p"),)))])
    with pytest.raises(ToolError, match="takes_attribution"):
        bench.dispatch("scaffold_liar")


def test_the_trace_carries_records_and_no_longer_the_old_key(world):
    result = propose(world)
    entry = result.to_trace_entry()
    assert "artifact_ids" not in entry
    assert entry["records"] == [{"kind": "note_proposal", "id": pending_id()}]
    assert result.artifact_ids == ()


def test_the_model_sees_the_same_text_and_no_id(world):
    from program.tools import note_texts as texts
    result = propose(world)
    assert result.value == texts.PENDING_ADD
    assert pending_id() not in result.value


# --- old traces keep rendering the same receipts -----------------------------------------------


def _artifact(world):
    out = registry.default_registry().dispatch(
        "creative_write", {"text": STORY}, attribution=world.attribution)
    return out, db.list_artifacts(world.lyle)[0]


def test_a_trace_stored_before_records_renders_the_same_artifact_receipt(world):
    new, row = _artifact(world)
    new_entry = new.to_trace_entry()
    old_entry = {k: v for k, v in new_entry.items() if k != "records"}
    old_entry["artifact_ids"] = [row["id"]]

    assert receipts.for_trace([old_entry]) == receipts.for_trace([new_entry])
    assert receipts.for_trace([old_entry])[0].to_dict() == {
        "tool": "creative_write", "outcome": "saved", "artifact_id": row["id"],
        "artifact_type": "creative_writing", "created_at": row["created_at"]}


def test_records_win_when_an_entry_somehow_carries_both(world):
    _, row = _artifact(world)
    entry = {"tool": "creative_write", "outcome": "ok", "ran": True,
             "artifact_ids": ["f" * 32], "records": [{"kind": "artifact", "id": row["id"]}]}
    assert one(entry).artifact_id == row["id"]


def test_an_entry_with_neither_key_predates_o23_and_is_unknown():
    assert one({"tool": "creative_write", "outcome": "ok", "ran": True}).outcome == "unknown"
    assert one({"tool": "note_propose", "outcome": "ok", "ran": True}).outcome == "unknown"


@pytest.mark.parametrize("tool", ["creative_write", "note_propose"])
@pytest.mark.parametrize("outcome", ["ok", "tool_error", "timeout"])
def test_a_pre_o23_entry_that_ran_cannot_say_what_it_wrote(tool, outcome):
    """No key at all is not an empty list: a `tool_error` there is unknown, not not-saved."""
    r = one({"tool": tool, "outcome": outcome, "ran": True})
    assert r.outcome == "unknown"


def test_an_artifact_receipt_serialises_exactly_as_before():
    r = receipts.Receipt("creative_write", "saved", "i", "creative_writing", "t")
    assert r.to_dict() == {"tool": "creative_write", "outcome": "saved", "artifact_id": "i",
                           "artifact_type": "creative_writing", "created_at": "t"}


# --- a proposal's receipt is its row's current status -------------------------------------------


def test_the_live_receipt_is_pending_and_says_nothing_about_a_note(world):
    r = one(propose(world).to_trace_entry())
    assert (r.kind, r.outcome, r.status) == ("note_proposal", "proposed", "pending")
    assert r.text == "Proposed, awaiting a person's review."
    assert r.record_id == pending_id() and r.created_at
    low = r.text.lower()
    assert "saved" not in low and "exists" not in low and "added" not in low
    assert set(r.to_dict()) == {"tool", "kind", "outcome", "record_id", "status", "text",
                                "created_at"}


@pytest.mark.parametrize("decision, outcome, status, text", [
    ("approved", "accepted", "approved", "Reviewed and accepted."),
    ("edited", "accepted", "edited", "Reviewed and accepted."),
    ("rejected", "declined", "rejected", "Reviewed and not accepted."),
])
def test_a_decided_proposal_says_what_its_row_says(world, decision, outcome, status, text):
    entry = propose(world).to_trace_entry()
    assert one(entry).outcome == "proposed"
    note_admin.decide(pending_id(), decision, text="Edited text." if decision == "edited" else None)

    r = one(entry)         # the SAME stored trace, rebuilt after the decision
    assert (r.outcome, r.status, r.text) == (outcome, status, text)


def test_applied_without_review_reads_from_the_row(world):
    entry = propose(world).to_trace_entry()
    pid = pending_id()
    nid = db.new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO notes (id, subject_kind, subject, text, status, version, origin, "
            "created_at, last_confirmed_at) VALUES (?, 'person', 'Jodie', 't', 'active', 1, "
            "'entity', 'x', 'x')", (nid,))
        conn.execute("UPDATE note_proposals SET status = 'applied_without_review', "
                     "decided_at = 'x', resulting_note_id = ? WHERE id = ?", (nid, pid))
    r = one(entry)
    assert (r.outcome, r.text) == ("applied_without_review", "Applied without review.")


def test_a_missing_proposal_row_is_unknown_and_not_inferred_pending(world, caplog):
    entry = propose(world).to_trace_entry()
    with db.transaction() as conn:                   # a pending proposal may be deleted
        conn.execute("DELETE FROM note_proposals")
    with caplog.at_level(logging.ERROR):
        r = one(entry)
    assert (r.outcome, r.status) == ("unknown", None)
    assert r.text == receipts.PROPOSAL_TEXTS["unknown"]
    assert "no such row exists" in caplog.text


def test_a_failed_lookup_is_unknown_and_never_empty_and_other_kinds_survive(world, monkeypatch):
    p = propose(world).to_trace_entry()
    a, row = _artifact(world)
    trace = [p, a.to_trace_entry()]

    def locked(ids):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setitem(receipts.READERS, "note_proposal", locked)
    got = receipts.for_trace(trace)
    assert len(got) == 2
    assert got[0].outcome == "unknown" and got[0].record_id == pending_id()
    assert got[1].outcome == "saved", "one kind's failed read must not take the other down"

    # whichever order the kinds first appear in, the failing one alone degrades
    got = receipts.for_trace(list(reversed(trace)))
    assert [r.outcome for r in got] == ["saved", "unknown"]

    monkeypatch.setitem(receipts.READERS, "artifact", locked)
    assert [r.outcome for r in receipts.for_trace(trace)] == ["unknown", "unknown"]


def test_an_unrecognised_status_is_unknown(world, monkeypatch):
    entry = propose(world).to_trace_entry()
    monkeypatch.setitem(receipts.READERS, "note_proposal",
                        lambda ids: {i: {"status": "weird", "created_at": "t"} for i in ids})
    assert one(entry).outcome == "unknown"


@pytest.mark.parametrize("outcome, expected", [
    ("tool_error", "not_proposed"), ("skipped", "not_proposed"),
    ("unknown_tool", "not_proposed"), ("invalid_arguments", "not_proposed"),
    ("timeout", "unknown")])
def test_a_call_that_named_no_proposal(outcome, expected):
    r = one({"tool": "note_propose", "outcome": outcome, "ran": outcome == "timeout",
             "records": []})
    assert (r.kind, r.outcome) == ("note_proposal", expected)
    assert r.text == receipts.PROPOSAL_TEXTS[expected]


def test_ok_with_no_records_is_unknown_and_logged(caplog):
    r = one({"tool": "note_propose", "outcome": "ok", "ran": True, "records": []})
    assert r.outcome == "unknown" and "must name what it wrote" in caplog.text


def test_an_unknown_kind_in_a_stored_trace_is_unknown_not_a_crash(caplog):
    r = one({"tool": "creative_write", "outcome": "ok", "ran": True,
             "records": [{"kind": "mystery", "id": "z"}]})
    assert r.outcome == "unknown" and "unknown kind" in caplog.text


def test_a_failed_note_propose_call_in_a_real_turn_is_not_proposed(world):
    entry = propose(world, quotes=["nobody said this"]).to_trace_entry()
    assert entry["outcome"] == "tool_error" and entry["records"] == []
    assert one(entry).outcome == "not_proposed"


def test_the_count_equals_the_side_effect_calls_across_kinds(world):
    a, _ = _artifact(world)
    trace = [a.to_trace_entry(), propose(world).to_trace_entry(),
             propose(world, quotes=["nobody said this"]).to_trace_entry(),
             {"tool": "web_search", "outcome": "ok", "ran": True, "records": []}]
    got = receipts.for_trace(trace)
    assert [r.tool for r in got] == ["creative_write", "note_propose", "note_propose"]
    assert [r.outcome for r in got] == ["saved", "proposed", "not_proposed"]


# --- through a real turn and the route -----------------------------------------------------------

SECRET = "test-signing-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"


def test_the_route_returns_a_pending_receipt_and_rebuilding_after_approval_changes_it(
        world, monkeypatch):
    monkeypatch.setenv("ANAM_NOTES_ENABLED", "true")
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    config.reload()
    registry.reset_default_registry()
    auth.throttle.reset()
    db.set_password_hash(world.lyle, auth.hash_password(PASSWORD))
    monkeypatch.setattr(turn.retrieval, "search", lambda q: None)
    calls = {"n": 0}

    def fake(messages, *, model=None, options=None, tools=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "note_propose", "arguments": dict(
                    action="add", subject_kind="person", subject="Jodie",
                    text="Takes oat milk.", quotes=["takes her coffee with oat milk"])}}]}}
        return {"message": {"role": "assistant", "content": "I have noted that."}}

    monkeypatch.setattr(loop.ollama, "chat", fake)
    try:
        client = TestClient(create_app())
        token = client.post("/api/login", json={"name": "Lyle", "password": PASSWORD}).json()
        body = client.post(
            "/api/chat", json={"message": "Jodie takes her coffee with oat milk, never dairy."},
            headers={"Authorization": f"Bearer {token['token']}"}).json()
    finally:
        registry.reset_default_registry()
        auth.throttle.reset()

    [live] = body["receipts"]
    assert live["kind"] == "note_proposal" and live["outcome"] == "proposed"
    assert live["text"] == "Proposed, awaiting a person's review."
    assert body["content"] == "I have noted that."       # the entity's words are untouched
    assert "Proposed" not in body["content"]

    note_admin.decide(live["record_id"], "approved")
    [stored] = db.get_messages_by_ids([body["message_id"]])
    [later] = [r.to_dict() for r in receipts.for_trace(json.loads(stored["tool_trace"]))]
    assert later["outcome"] == "accepted" and later["text"] == "Reviewed and accepted."
    assert {k: v for k, v in later.items() if k not in ("outcome", "status", "text")} == {
        k: v for k, v in live.items() if k not in ("outcome", "status", "text")}


# --- the journal's system-record lines -----------------------------------------------------------


@pytest.mark.parametrize("decision, words", [
    (None, "proposed, awaiting a person's review"),
    ("approved", "proposal reviewed and accepted"),
    ("rejected", "proposal reviewed and not accepted")])
def test_the_journal_line_for_a_proposal_follows_the_row_and_never_says_saved(
        world, decision, words):
    entry = propose(world).to_trace_entry()
    if decision:
        note_admin.decide(pending_id(), decision)
    [line] = journal.system_record_lines(json.dumps([entry]))
    assert words in line and "saved" not in line


def test_the_journal_line_for_a_call_that_proposed_nothing(world):
    entry = propose(world, quotes=["nobody said this"]).to_trace_entry()
    [line] = journal.system_record_lines(json.dumps([entry]))
    assert "did not propose anything" in line


# --- the gate is untouched ---------------------------------------------------------------------

_REPLIES = (
    "CONSISTENT",
    "CONTRADICTS-SELF\n- I have been thinking | nothing runs between replies",
    "CONTRADICTS-TOOL\n- the search | the trace",
    "CONTRADICTS-ACTION\n- I have saved that piece | claims a file",
)


def _with_records(trace, kind):
    return [{**entry, "records": [{"kind": kind, "id": "f" * 32}]
             if entry.get("tool") in registry.side_effect_tools() else []} for entry in trace]


def test_gate_verdicts_and_prompt_are_byte_identical_with_records_in_the_trace(monkeypatch):
    """O23's proof, re-run for the renamed key: every frozen case, four scripted replies, with
    artifact records, with proposal records, and with neither. Verdict, advisory and classifier
    prompt are identical."""
    truth = gate.load_architecture()
    checked = 0
    for reply in _REPLIES:
        for case in gate_eval.load_cases():
            seen = []

            def scripted(prompt, _reply=reply):
                seen.append(prompt)
                return _reply

            monkeypatch.setattr(classifier, "classify", scripted)
            base = gate.check(case.answer, list(case.trace), case.situation, ground_truth=truth)
            base_prompts = list(seen)
            for kind in ("artifact", "note_proposal"):
                seen.clear()
                other = gate.check(case.answer, _with_records(case.trace, kind), case.situation,
                                   ground_truth=truth)
                assert base.to_json() == other.to_json(), (case.id, kind)
                assert base.advisory_json() == other.advisory_json(), (case.id, kind)
                assert seen == base_prompts, (case.id, kind)
            seen.clear()
            checked += 1
    assert checked == len(_REPLIES) * len(gate_eval.load_cases())


def test_the_gate_still_reads_no_record_and_receipts_still_never_import_it():
    import ast
    import inspect
    assert '"records"' not in inspect.getsource(gate)
    assert "records_of" not in inspect.getsource(gate)
    tree = ast.parse(inspect.getsource(receipts))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith("program.integrity") for m in imported)
