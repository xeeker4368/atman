"""The chat page's server side: the three read routes, the static page and its headers.

Decision #31. The browser check is the operator's (the report lists the hostile inputs); these
pin what the server promises the page: each person reads only their own conversations, an unowned
id looks exactly like an unknown one, a stored reply comes back with the receipts the live turn
returned, and every response carries the security headers.

Only the model, the classifier and the embedder are substituted. Storage, auth, the turn and the
routes are real.
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from program import auth, config
from program.api import headers
from program.api.app import create_app
from program.api.routes import ui
from program.artifacts import indexing
from program.engine import loop
from program.integrity import classifier
from program.memory import db
from program.tools import registry

SECRET = "test-signing-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"
STORY = "The kettle had been on the hob so long it had stopped meaning tea."


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("ANAM_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ANAM_AUTH_SCRYPT_N", "4096")
    config.reload()
    auth.throttle.reset()
    monkeypatch.setattr(indexing.ollama, "embed", lambda text, *a, **k: [0.1] * 768)
    db.init_databases()
    registry.reset_default_registry()
    lyle = db.create_user("Lyle", role="admin")
    jodie = db.create_user("Jodie", role="user")
    db.set_password_hash(lyle, auth.hash_password(PASSWORD))
    db.set_password_hash(jodie, auth.hash_password(PASSWORD))
    from program.engine import turn

    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    monkeypatch.setattr(classifier, "classify", lambda prompt: "CONSISTENT")
    monkeypatch.setattr(loop.ollama, "chat", lambda *a, **k: {
        "message": {"role": "assistant", "content": "Answered."}})
    yield {"lyle": lyle, "jodie": jodie}
    auth.throttle.reset()


@pytest.fixture
def client(store):
    return TestClient(create_app())


def login(client, name):
    response = client.post("/api/login", json={"name": name, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def say(client, who, message, conversation_id=None):
    response = client.post(
        "/api/chat", json={"message": message, "conversation_id": conversation_id}, headers=who)
    assert response.status_code == 200
    return response.json()


def model_that_writes(monkeypatch):
    calls = {"n": 0}

    def fake(messages, *, model=None, options=None, tools=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "creative_write", "arguments": {"text": STORY}}}]}}
        return {"message": {"role": "assistant", "content": "Done."}}

    monkeypatch.setattr(loop.ollama, "chat", fake)


# --- GET /api/me ---------------------------------------------------------------


def test_me_names_the_logged_in_person_and_their_role(client):
    assert client.get("/api/me", headers=login(client, "Lyle")).json() == {
        "name": "Lyle", "role": "admin"}
    assert client.get("/api/me", headers=login(client, "Jodie")).json() == {
        "name": "Jodie", "role": "user"}


@pytest.mark.parametrize("path", ["/api/me", "/api/conversations",
                                  "/api/conversations/anything/messages"])
def test_every_read_route_needs_a_login(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "authentication failed"}


# --- GET /api/conversations ----------------------------------------------------


def test_the_list_is_the_callers_own_newest_first_with_closed_ones_included(client, store):
    lyle = login(client, "Lyle")
    first = say(client, lyle, "the first conversation")["conversation_id"]
    second = say(client, lyle, "the second conversation")["conversation_id"]
    assert db.end_conversation(first)
    say(client, login(client, "Jodie"), "a conversation of hers")

    listed = client.get("/api/conversations", headers=lyle).json()

    assert [c["id"] for c in listed] == [second, first]
    assert listed[1]["ended_at"] is not None, "a closed conversation is still listed"
    assert listed[0]["ended_at"] is None
    assert [c["preview"] for c in listed] == ["the second conversation", "the first conversation"]
    assert not any(c["preview_cut"] for c in listed)


def test_the_preview_is_the_first_user_message_cut_to_a_fixed_length(client):
    lyle = login(client, "Lyle")
    long = "x" * (db.CONVERSATION_PREVIEW_CHARS + 50)
    cid = say(client, lyle, long)["conversation_id"]
    say(client, lyle, "a later message", cid)

    [row] = client.get("/api/conversations", headers=lyle).json()
    assert row["preview"] == "x" * db.CONVERSATION_PREVIEW_CHARS
    assert row["preview_cut"] is True


def test_a_conversation_with_no_message_yet_has_no_preview(client, store):
    cid = db.start_conversation(store["lyle"])
    [row] = client.get("/api/conversations", headers=login(client, "Lyle")).json()
    assert row["id"] == cid and row["preview"] is None and row["preview_cut"] is False


# --- GET /api/conversations/{id}/messages ---------------------------------------


def test_a_conversation_comes_back_in_order_with_utc_times(client):
    lyle = login(client, "Lyle")
    cid = say(client, lyle, "hello")["conversation_id"]
    say(client, lyle, "and again", cid)

    body = client.get(f"/api/conversations/{cid}/messages", headers=lyle).json()

    assert body["id"] == cid and body["ended_at"] is None
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("user", "hello"), ("assistant", "Answered."),
        ("user", "and again"), ("assistant", "Answered.")]
    stored = db.get_conversation_messages(cid)
    assert [m["id"] for m in body["messages"]] == [r["id"] for r in stored]
    assert [m["timestamp"] for m in body["messages"]] == [r["timestamp"] for r in stored]
    assert all(m["timestamp"].endswith("+00:00") for m in body["messages"])


def test_another_users_conversation_and_an_unknown_one_are_the_same_404(client):
    lyles = say(client, login(client, "Lyle"), "mine")["conversation_id"]
    jodie = login(client, "Jodie")

    unowned = client.get(f"/api/conversations/{lyles}/messages", headers=jodie)
    unknown = client.get("/api/conversations/does-not-exist/messages", headers=jodie)

    assert unowned.status_code == unknown.status_code == 404
    assert unowned.json() == unknown.json() == {"detail": "conversation not found"}
    assert unowned.headers.get("content-type") == unknown.headers.get("content-type")
    # Word for word what POST /api/chat says for the same id.
    posted = client.post(
        "/api/chat", json={"message": "x", "conversation_id": lyles}, headers=jodie)
    assert posted.status_code == 404 and posted.json() == unowned.json()


def test_a_turn_with_no_tools_has_a_null_trace_and_no_receipts(client):
    lyle = login(client, "Lyle")
    cid = say(client, lyle, "hello")["conversation_id"]

    messages = client.get(f"/api/conversations/{cid}/messages", headers=lyle).json()["messages"]

    [reply] = [m for m in messages if m["role"] == "assistant"]
    assert reply["trace"] is None and reply["receipts"] == []
    [question] = [m for m in messages if m["role"] == "user"]
    assert question["trace"] is None and question["receipts"] is None


def test_receipts_and_trace_for_a_past_turn_equal_what_the_live_turn_returned(
    client, monkeypatch
):
    model_that_writes(monkeypatch)
    lyle = login(client, "Lyle")
    live = say(client, lyle, "Write and keep a line.")
    assert live["receipts"] and live["receipts"][0]["outcome"] == "saved", "a real receipt"

    messages = client.get(f"/api/conversations/{live['conversation_id']}/messages",
                          headers=lyle).json()["messages"]

    [past] = [m for m in messages if m["id"] == live["message_id"]]
    assert past["receipts"] == live["receipts"]
    assert past["trace"] == live["trace"]
    assert past["content"] == live["content"] == "Done."


def test_an_unreadable_stored_trace_is_logged_and_shown_as_null_never_a_500(
    client, store, caplog
):
    cid = db.start_conversation(store["lyle"])
    db.save_message(cid, store["lyle"], "user", "hello")
    bad = db.save_message(cid, store["lyle"], "assistant", "Answered.", tool_trace="{not json")
    odd = db.save_message(cid, store["lyle"], "assistant", "Again.", tool_trace='{"a": 1}')

    with caplog.at_level(logging.WARNING, logger="program.api.routes.conversations"):
        response = client.get(f"/api/conversations/{cid}/messages", headers=login(client, "Lyle"))

    assert response.status_code == 200
    by_id = {m["id"]: m for m in response.json()["messages"]}
    for message_id in (bad, odd):
        # NULL receipts, not []: an empty list would say no side-effect tool ran.
        assert by_id[message_id]["trace"] is None and by_id[message_id]["receipts"] is None
    assert sum("could not be read" in r.getMessage() for r in caplog.records) == 2


def test_a_closed_conversation_opens_and_says_it_is_closed(client):
    lyle = login(client, "Lyle")
    cid = say(client, lyle, "hello")["conversation_id"]
    assert db.end_conversation(cid)

    body = client.get(f"/api/conversations/{cid}/messages", headers=lyle).json()
    assert body["ended_at"] is not None and len(body["messages"]) == 2


# --- two people at once --------------------------------------------------------


def test_two_people_logged_in_at_once_each_see_only_their_own(client, store):
    lyle, jodie = login(client, "Lyle"), login(client, "Jodie")

    lyles = say(client, lyle, "from Lyle")
    jodies = say(client, jodie, "from Jodie")

    lyle_list = client.get("/api/conversations", headers=lyle).json()
    jodie_list = client.get("/api/conversations", headers=jodie).json()
    assert [c["id"] for c in lyle_list] == [lyles["conversation_id"]]
    assert [c["id"] for c in jodie_list] == [jodies["conversation_id"]]

    for who, sent, user_id in ((lyle, lyles, store["lyle"]), (jodie, jodies, store["jodie"])):
        rows = db.get_conversation_messages(sent["conversation_id"])
        assert {r["user_id"] for r in rows} == {user_id}, "working store"
        for row in rows:
            assert db.get_archive_message(row["id"])["user_id"] == user_id, "archive"
        assert db.get_conversation(sent["conversation_id"])["user_id"] == user_id
        own = client.get(f"/api/conversations/{sent['conversation_id']}/messages", headers=who)
        assert own.status_code == 200

    crossed = client.get(f"/api/conversations/{lyles['conversation_id']}/messages", headers=jodie)
    assert crossed.status_code == 404


# --- the page and its headers ----------------------------------------------------


def _assert_security_headers(response):
    assert response.headers["content-security-policy"] == headers.CONTENT_SECURITY_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_the_policy_is_the_reviewed_one():
    assert headers.CONTENT_SECURITY_POLICY == (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def test_the_page_is_served_at_the_root_with_the_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<script type="module" src="/static/app.js">' in response.text
    _assert_security_headers(response)


@pytest.mark.parametrize("asset, kind", [("app.js", "javascript"), ("render.js", "javascript"),
                                         ("app.css", "text/css")])
def test_assets_are_served_under_static_with_their_type_and_the_headers(client, asset, kind):
    response = client.get(f"/static/{asset}")
    assert response.status_code == 200
    assert kind in response.headers["content-type"], "nosniff needs the right type"
    _assert_security_headers(response)


def test_api_responses_carry_the_headers_too(client):
    _assert_security_headers(client.get("/api/health"))
    _assert_security_headers(client.get("/api/me"))  # a 401
    _assert_security_headers(client.get("/api/me", headers=login(client, "Lyle")))


def test_an_unknown_api_path_is_still_fastapis_json_404(client):
    response = client.get("/api/no-such-route")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert response.headers["content-type"] == "application/json"
    _assert_security_headers(response)


def test_nothing_is_mounted_at_the_root():
    """A root static mount would shadow /api paths; only /static is mounted."""
    app = create_app()
    mounts = [r.path for r in app.routes if type(r).__name__ == "Mount"]
    assert mounts == ["/static"]


# --- the static files: nothing inline, one renderer --------------------------------


def _static(name):
    return (ui.STATIC_DIR / name).read_text(encoding="utf-8")


def test_the_page_has_no_inline_script_style_or_handler():
    page = _static("index.html")
    scripts = re.findall(r"<script\b([^>]*)>", page)
    assert scripts and all('src="/static/' in attrs for attrs in scripts)
    assert "<style" not in page
    assert not re.search(r"\sstyle\s*=", page)
    assert not re.search(r"\son[a-z]+\s*=", page, re.IGNORECASE)


def test_the_scripts_never_set_style_attributes_and_have_one_innerhtml_fed_by_the_renderer():
    app_js, render_js = _static("app.js"), _static("render.js")
    assert "setAttribute(\"style\"" not in app_js and ".style." not in app_js
    assignments = re.findall(r"\.innerHTML\s*=\s*([^;]+);", app_js)
    assert assignments == ["renderReply(content)"]
    assert "innerHTML" not in render_js
    assert "export function renderReply" in render_js


def test_the_renderer_escapes_before_it_converts():
    """Read, not run (no JS toolchain): the function's first statement escapes the whole
    input, and every later step works on those escaped lines."""
    render_js = _static("render.js")
    body = render_js[render_js.index("export function renderReply"):]
    first_statement = body.splitlines()[1].strip()
    assert first_statement.startswith("const lines = escapeHtml(text")
    assert "/[&<>\"']/g" in render_js, "all five characters are escaped"
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert entity in render_js


def test_the_token_is_kept_per_tab_only():
    app_js = _static("app.js")
    assert "sessionStorage" in app_js
    assert "localStorage" not in app_js and "document.cookie" not in app_js


# --- the entity's own writing is not in an API trace (decision #10) ------------------

TITLE = "The Kettle Hour"


def model_that_writes_titled(monkeypatch):
    calls = {"n": 0}

    def fake(messages, *, model=None, options=None, tools=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "creative_write",
                              "arguments": {"text": STORY, "title": TITLE}}}]}}
        return {"message": {"role": "assistant", "content": "Done."}}

    monkeypatch.setattr(loop.ollama, "chat", fake)


def _slug_words(title):
    return title.lower().replace(" ", "-")


def test_a_creative_write_turns_api_trace_has_neither_text_nor_title(client, monkeypatch):
    model_that_writes_titled(monkeypatch)
    lyle = login(client, "Lyle")
    live = say(client, lyle, "Write and keep a line.")
    past = client.get(f"/api/conversations/{live['conversation_id']}/messages",
                      headers=lyle).json()
    [stored_reply] = [m for m in past["messages"] if m["id"] == live["message_id"]]

    for trace in (live["trace"], stored_reply["trace"]):
        shown = json.dumps(trace)
        assert STORY not in shown and TITLE not in shown
        assert _slug_words(TITLE) not in shown, "nor the file name made from the title"
        [entry] = [e for e in trace if e.get("tool") == "creative_write"]
        assert entry["arguments"]["text"] == f"[{len(STORY)} characters, not shown here]"
        assert entry["arguments"]["title"] == f"[{len(TITLE)} characters, not shown here]"
        assert entry["value"].endswith("characters, not shown here]")
        assert entry["outcome"] == "ok" and entry["records"], "the mechanical facts stay"

    # The stored trace is the record and is not changed; receipts still come from it.
    [row] = db.get_messages_by_ids([live["message_id"]])
    assert STORY in row["tool_trace"] and TITLE in row["tool_trace"]
    assert live["receipts"][0]["outcome"] == "saved"
    assert stored_reply["receipts"] == live["receipts"]


def _rendered_record(source_type, text):
    from program.engine import prompt
    from program.memory.retrieval import RetrievedChunk

    chunk = RetrievedChunk(chunk_id="c1", text=text, created_at="2026-10-01T10:00:00+00:00",
                           source_type=source_type)
    return prompt._render_chunk(chunk, "record 1")


def _search_entry(value):
    return {"call_id": "c", "tool": "memory_search", "arguments": {"query": "kettle"},
            "outcome": "ok", "ran": True, "value": value, "error": None,
            "duration_seconds": 0.1, "timeout_seconds": 30, "records": []}


@pytest.mark.parametrize("source_type", ["creative_writing", "reflection_journal"])
def test_a_memory_search_that_retrieved_its_own_writing_is_hidden(client, store, source_type):
    value = _rendered_record(source_type, STORY)
    cid = db.start_conversation(store["lyle"])
    db.save_message(cid, store["lyle"], "user", "what did you write?")
    reply = db.save_message(cid, store["lyle"], "assistant", "Something.",
                            tool_trace=json.dumps([_search_entry(value)]))

    body = client.get(f"/api/conversations/{cid}/messages", headers=login(client, "Lyle")).json()

    [m] = [m for m in body["messages"] if m["id"] == reply]
    assert STORY not in json.dumps(m["trace"])
    assert m["trace"][0]["value"] == f"[{len(value)} characters, not shown here]"
    assert m["trace"][0]["arguments"] == {"query": "kettle"}


@pytest.mark.parametrize("entry", [
    _search_entry("[record 1 · from a conversation with Lyle, today]\nLyle: the kettle\n"
                  "[end of record 1]"),
    {"tool": "image_generate", "arguments": {"prompt": "a kettle at dawn"}, "value": "made"},
    {"tool": "note_propose", "arguments": {"text": "Lyle likes tea"}, "value": "proposed"},
    {"tool": "note_search", "arguments": {"query": "tea"}, "value": "a note"},
    {"tool": "web_search", "arguments": {"query": "kettles"}, "value": "results"},
    {"tool": "web_fetch", "arguments": {"url": "http://example.test"}, "value": "page"},
])
def test_other_tools_traces_are_shown_as_stored(entry):
    """The list in trace_view's docstring: only the entity's own writing is hidden."""
    from program.api import trace_view

    assert trace_view.for_response([entry]) == [entry]


def test_hiding_never_modifies_the_trace_it_was_given():
    from program.api import trace_view

    entry = {"tool": "creative_write", "arguments": {"text": STORY}, "value": "Saved."}
    original = json.loads(json.dumps(entry))
    trace_view.for_response([entry])
    assert entry == original
