"""Receipts for side-effect tools, and the trace key that feeds them. O23, F50.

Design of record: ``docs/FABRICATION_GATE_DESIGN.md`` F50, approved at review
2026-09-30. Three things are under test:

* **the trace carries what a call wrote**: ``ToolOutput`` on success,
  ``ArtifactWriteError`` on a failure after the row was committed;
* **a receipt keys on the ``artifacts`` row, not the outcome label**, with
  *unknown* kept distinct from *not saved*;
* **the gate is untouched**: byte-identical verdicts, and an identical classifier
  prompt, with the new key present.

Only the embedder is substituted, as in ``tests/test_creative_write.py``. Storage,
dispatch and the database are real.
"""

from __future__ import annotations

import ast
import inspect
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from program import auth, config
from program.api.app import create_app
from program.artifacts import indexing, writing
from program.attribution import AttributionContext
from program.engine import loop
from program.integrity import classifier, gate, gate_eval
from program.memory import db
from program.tools import receipts, registry
from program.tools.registry import (
    ArtifactWriteError,
    Tool,
    ToolError,
    ToolOutcome,
    ToolOutput,
    ToolRegistry,
)

STORY = "The kettle had been on the hob so long it had stopped meaning tea."


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(indexing.ollama, "embed", lambda text, *a, **k: [0.1] * 768)
    db.init_databases()
    registry.reset_default_registry()
    return db.create_user("Lyle", role="admin")


def write(store, text=STORY, **kwargs):
    return registry.default_registry().dispatch(
        "creative_write", {"text": text, **kwargs},
        attribution=AttributionContext(user_id=store))


def embedder_down(monkeypatch):
    def down(text, *a, **k):
        raise RuntimeError("embedder down")

    monkeypatch.setattr(indexing.ollama, "embed", down)


# --- the trace key ----------------------------------------------------------


def test_a_successful_write_names_its_row_in_the_trace(store):
    result = write(store)
    [row] = db.list_artifacts(store)

    assert result.outcome is ToolOutcome.OK
    assert result.to_trace_entry()["artifact_ids"] == [row["id"]]
    # What the model sees is the text, exactly as before: never the wrapper.
    assert isinstance(result.value, str) and "Saved." in result.value


def test_a_failure_after_the_row_still_names_it_and_tells_the_model_it_was_kept(
    store, monkeypatch
):
    """The finding behind this task, measured before any code existed: indexing fails
    after the row is committed, the outcome is tool_error, and the piece IS kept."""
    embedder_down(monkeypatch)

    result = write(store)
    [row] = db.list_artifacts(store)

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert result.to_trace_entry()["artifact_ids"] == [row["id"]]
    # The bundled error-text fix: the model is told what was kept, and why it failed.
    assert "The piece was kept" in result.error
    assert row["id"] in result.error
    assert "could not be indexed" in result.error
    assert "RuntimeError: embedder down" in result.error
    assert db.get_artifact_chunks(row["id"]) == []


def test_a_failure_before_the_row_names_nothing(store):
    result = write(store, text="   ")

    assert result.outcome is ToolOutcome.TOOL_ERROR
    assert result.to_trace_entry()["artifact_ids"] == []
    assert db.list_artifacts(store) == []


def test_a_timeout_names_nothing_because_the_result_was_lost(store, monkeypatch):
    def slow(*a, **k):
        time.sleep(0.3)
        raise RuntimeError("never read")

    monkeypatch.setattr(writing, "store", slow)
    result = registry.default_registry().dispatch(
        "creative_write", {"text": STORY}, timeout_seconds=0.02,
        attribution=AttributionContext(user_id=store))

    assert result.outcome is ToolOutcome.TIMEOUT
    assert result.to_trace_entry()["artifact_ids"] == []
    time.sleep(0.35)  # let the abandoned worker finish inside the test


def test_a_tool_that_writes_nothing_reports_nothing(store):
    bench = ToolRegistry([Tool(
        name="scaffold_read", description="TEST-ONLY: returns a string.",
        parameters={"type": "object", "properties": {}}, handler=lambda: "read")])

    assert bench.dispatch("scaffold_read").to_trace_entry()["artifact_ids"] == []


@pytest.mark.parametrize("handler", [
    lambda: ToolOutput("wrote", ("a" * 32,)),
    lambda: (_ for _ in ()).throw(ArtifactWriteError("half", ("a" * 32,))),
], ids=["on-success", "on-failure"])
def test_only_a_tool_that_writes_records_may_report_one(handler):
    """Which tools write is defined once, by takes_attribution. A read-only tool
    naming an artifact would make receipts and the gate disagree about it."""
    bench = ToolRegistry([Tool(
        name="scaffold_liar", description="TEST-ONLY: claims a write.",
        parameters={"type": "object", "properties": {}}, handler=handler)])

    with pytest.raises(ToolError, match="takes_attribution"):
        bench.dispatch("scaffold_liar")


def test_the_side_effect_set_is_one_definition_shared_by_gate_and_receipts(
    monkeypatch,
):
    # `note_propose` (Notes piece 3) declares takes_attribution, so the catalogue-derived set
    # includes it even while Notes is dark.
    assert registry.side_effect_tools() == ("creative_write", "image_generate", "note_propose")
    assert gate.side_effect_tools is registry.side_effect_tools

    # From the catalogue, not the enabled registry: a stored trace can name a tool
    # that has since been disabled, and its receipt must still be built.
    # Disabled through config, the path an operator uses (test_image_generate's own
    # reason: patching the predicate would test the test's substitute).
    monkeypatch.setenv("ANAM_COMFYUI_ENABLED", "false")
    config.reload()
    registry.reset_default_registry()
    try:
        assert "image_generate" not in registry.default_registry().names
        assert "image_generate" in registry.side_effect_tools()
    finally:
        monkeypatch.delenv("ANAM_COMFYUI_ENABLED")
        config.reload()
        registry.reset_default_registry()


def test_receipts_never_import_the_gate():
    """Independent by design (F50): neither can hide the other's failure."""
    tree = ast.parse(inspect.getsource(receipts))
    imported = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(name.startswith("program.integrity") for name in imported)


# --- receipts: keyed on the row, three outcomes ------------------------------


def test_a_real_write_gets_a_saved_receipt_from_the_row(store):
    trace = [write(store, title="The Kettle").to_trace_entry()]
    [row] = db.list_artifacts(store)

    [receipt] = receipts.for_trace(trace)
    assert receipt.outcome == receipts.SAVED
    assert receipt.artifact_id == row["id"]
    assert receipt.artifact_type == "creative_writing"
    assert receipt.created_at == row["created_at"]


def test_a_receipt_carries_no_title_and_no_content(store):
    """Decision #10, clarified in F50: the fact of a write, never the work."""
    trace = [write(store, title="The Kettle").to_trace_entry()]
    [receipt] = receipts.for_trace(trace)

    assert set(receipt.to_dict()) == {
        "tool", "outcome", "artifact_id", "artifact_type", "created_at"}
    rendered = json.dumps(receipt.to_dict())
    assert "The Kettle" not in rendered
    assert "kettle" not in rendered.lower()


def test_a_tool_error_with_a_kept_row_is_saved_not_not_saved(store, monkeypatch):
    """The approved table's revised row: the receipt keys on the row, so the outcome
    label cannot make it say something false."""
    embedder_down(monkeypatch)
    trace = [write(store).to_trace_entry()]

    [receipt] = receipts.for_trace(trace)
    assert trace[0]["outcome"] == "tool_error"
    assert receipt.outcome == receipts.SAVED


def test_a_tool_error_before_any_row_is_not_saved(store):
    [receipt] = receipts.for_trace([write(store, text="   ").to_trace_entry()])
    assert receipt.outcome == receipts.NOT_SAVED


def test_a_timeout_is_unknown_not_not_saved():
    entry = {"tool": "creative_write", "outcome": "timeout", "ran": True,
             "artifact_ids": []}
    [receipt] = receipts.for_trace([entry])
    assert receipt.outcome == receipts.UNKNOWN


@pytest.mark.parametrize("outcome", ["skipped", "unknown_tool", "invalid_arguments"])
def test_a_call_that_never_ran_is_not_saved(outcome):
    entry = {"tool": "image_generate", "outcome": outcome, "ran": False,
             "artifact_ids": []}
    [receipt] = receipts.for_trace([entry])
    assert receipt.outcome == receipts.NOT_SAVED


def test_an_id_whose_row_is_gone_is_unknown_not_saved(store):
    """Saved is read off the row, not trusted from the trace."""
    trace = [write(store).to_trace_entry()]
    [row] = db.list_artifacts(store)
    with db.transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE artifact_id = ?", (row["id"],))
        conn.execute("DELETE FROM artifacts WHERE id = ?", (row["id"],))

    [receipt] = receipts.for_trace(trace)
    assert receipt.outcome == receipts.UNKNOWN
    assert receipt.artifact_id == row["id"]


def test_ok_with_no_ids_is_unknown_and_logged_as_a_defect(caplog):
    entry = {"tool": "creative_write", "outcome": "ok", "ran": True,
             "artifact_ids": []}
    [receipt] = receipts.for_trace([entry])

    assert receipt.outcome == receipts.UNKNOWN
    assert "must name what it wrote" in caplog.text


def test_a_trace_recorded_before_the_key_existed():
    """No key at all means the record predates O23: unknown, unless nothing ran."""
    ran = {"tool": "creative_write", "outcome": "ok", "ran": True}
    never = {"tool": "creative_write", "outcome": "skipped", "ran": False}

    assert [r.outcome for r in receipts.for_trace([ran, never])] == [
        receipts.UNKNOWN, receipts.NOT_SAVED]


def test_read_only_tools_get_no_receipt():
    trace = [{"tool": name, "outcome": "ok", "ran": True, "artifact_ids": []}
             for name in ("memory_search", "web_search", "web_fetch")]
    assert receipts.for_trace(trace) == []


def test_no_side_effect_call_means_no_receipts_and_no_lookup(monkeypatch):
    """The ONLY way to get an empty list: nothing was attempted. Distinct from the
    failed-lookup case below, which must never produce one."""
    def must_not_run(ids):
        raise AssertionError("no side-effect call, so there is nothing to look up")

    monkeypatch.setattr(receipts.db, "get_artifacts_by_ids", must_not_run)
    assert receipts.for_trace([]) == []
    assert receipts.for_trace(
        [{"tool": "web_search", "outcome": "ok", "ran": True, "artifact_ids": []}]
    ) == []


def test_a_failed_row_lookup_is_unknown_and_never_empty(store, monkeypatch):
    """The failure this names: the receipt's OWN row-existence check raising a
    database error, with side-effect calls present. Not the no-side-effect case
    above, where the list is empty because nothing was attempted.

    Every receipt that depends on the lookup becomes unknown and keeps its id.
    One decided without the store (a call that never ran) keeps its certain
    outcome, because the failed read tells us nothing new about it."""
    trace = [
        write(store).to_trace_entry(),
        {"tool": "image_generate", "outcome": "skipped", "ran": False,
         "artifact_ids": []},
    ]
    [row] = db.list_artifacts(store)

    def locked(ids):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(receipts.db, "get_artifacts_by_ids", locked)
    got = receipts.for_trace(trace)

    assert len(got) == 2, "a failed lookup must never read as nothing attempted"
    assert (got[0].outcome, got[0].artifact_id) == (receipts.UNKNOWN, row["id"])
    assert got[1].outcome == receipts.NOT_SAVED


# --- the gate is untouched ---------------------------------------------------

_REPLIES = (
    "CONSISTENT",
    "CONTRADICTS-SELF\n- I have been thinking | nothing runs between replies",
    "CONTRADICTS-TOOL\n- the search | the trace",
    "CONTRADICTS-ACTION\n- I have saved that piece | claims a file",
)


def _with_ids(trace):
    """The frozen case's trace, as production would now record it."""
    return [{**entry, "artifact_ids": ["f" * 32] if entry.get("tool") in
             registry.side_effect_tools() else []} for entry in trace]


def test_gate_verdicts_and_prompt_are_byte_identical_with_the_new_key(monkeypatch):
    """Every frozen case, under four scripted replies: the verdict and the prompt the
    classifier is shown are identical with and without ``artifact_ids`` on the trace.
    The frozen fingerprint is pinned separately and is unchanged by this task."""
    truth = gate.load_architecture()
    checked = 0
    for reply in _REPLIES:
        for case in gate_eval.load_cases():
            seen = []

            def scripted(prompt, _reply=reply):
                seen.append(prompt)
                return _reply

            monkeypatch.setattr(classifier, "classify", scripted)
            before = gate.check(case.answer, list(case.trace), case.situation,
                                ground_truth=truth)
            after = gate.check(case.answer, _with_ids(case.trace), case.situation,
                               ground_truth=truth)

            assert before.to_json() == after.to_json(), case.id
            assert before.advisory_json() == after.advisory_json(), case.id
            assert len(seen) in (0, 2) and (not seen or seen[0] == seen[1]), case.id
            checked += 1
    assert checked == len(_REPLIES) * len(gate_eval.load_cases())


# --- the route: beside the content, never in it ------------------------------

SECRET = "test-signing-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    config.reload()
    auth.throttle.reset()
    db.set_password_hash(store, auth.hash_password(PASSWORD))
    from program.engine import turn

    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    monkeypatch.setattr(classifier, "classify", lambda prompt: "CONSISTENT")
    yield TestClient(create_app())
    auth.throttle.reset()


def _login(client):
    response = client.post("/api/login", json={"name": "Lyle", "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _model_that_writes(monkeypatch):
    calls = {"n": 0}

    def fake(messages, *, model=None, options=None, tools=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "creative_write", "arguments": {"text": STORY}}}]}}
        return {"message": {"role": "assistant", "content": "Done."}}

    monkeypatch.setattr(loop.ollama, "chat", fake)


def test_the_response_carries_receipts_beside_the_content_never_in_it(
    client, store, monkeypatch
):
    _model_that_writes(monkeypatch)
    body = client.post("/api/chat", json={"message": "Write and keep a line."},
                       headers=_login(client)).json()
    [row] = db.list_artifacts(store)

    assert body["receipts"] == [{
        "tool": "creative_write", "outcome": "saved", "artifact_id": row["id"],
        "artifact_type": "creative_writing", "created_at": row["created_at"]}]
    # The entity's words are exactly what the model produced, in both stores, so
    # nothing of the receipt can reach history, chunks or the gate.
    assert body["content"] == "Done."
    [stored] = db.get_messages_by_ids([body["message_id"]])
    assert stored["content"] == "Done."
    assert db.get_archive_message(body["message_id"])["content"] == "Done."


def test_a_receipt_rebuilt_from_the_stored_trace_matches_the_live_one(
    client, store, monkeypatch
):
    _model_that_writes(monkeypatch)
    body = client.post("/api/chat", json={"message": "Write and keep a line."},
                       headers=_login(client)).json()

    [stored] = db.get_messages_by_ids([body["message_id"]])
    stored_trace = json.loads(stored["tool_trace"])
    rebuilt = [r.to_dict() for r in receipts.for_trace(stored_trace)]
    assert rebuilt == body["receipts"]


def test_a_turn_with_no_tool_has_no_receipts(client, monkeypatch):
    monkeypatch.setattr(loop.ollama, "chat", lambda *a, **k: {
        "message": {"role": "assistant", "content": "Answered."}})
    body = client.post("/api/chat", json={"message": "Hello."},
                       headers=_login(client)).json()
    assert body["receipts"] == []
