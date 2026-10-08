"""`OriginContext`: which exchange produced a proposal. Notes piece 2 (`docs/NOTES_BUILD_PLAN.md`).

Design of record: `docs/NOTES_DESIGN.md` N3 option (b). Attribution says *whose record* a write
belongs to; origin says *which exchange produced it*, and only a tool that declares it receives it,
with the same four guards attribution has. **Nothing about any existing tool changes**, and the
digest test below is the proof: it was taken on the code before any edit to the registry, the loop
or the turn, and pinned.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re

import pytest

from program import config
from program.artifacts import indexing
from program.engine import loop, turn
from program.integrity import classifier
from program.memory import db, retrieval
from program.settings.permissions import Actor, Role
from program.tools.creative_write import CREATIVE_WRITE
from program.tools.registry import Tool, ToolRegistry

HEX32 = re.compile(r"[0-9a-f]{32}")
#: Any ISO-8601 time the code prints, to the second, to the minute (the correction prompt shows
#: `2026-10-02T16:00`, which first made this digest change every hour), with or without an offset.
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\+00:00)?")


def scrub(text):
    """Random ids and clock times out; everything else stays in the digest."""
    text = re.sub(r"\b[0-9a-f]{2}/(?=<id>)", "<shard>/", HEX32.sub("<id>", text))
    return ISO.sub("<time>", text)


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    config.reload()
    db.init_databases()
    uid = db.create_user("Lyle", role="admin")
    monkeypatch.setattr(indexing.ollama, "embed", lambda text, *a, **k: [0.1] * 768)
    monkeypatch.setattr(turn.retrieval, "search", lambda query: None)
    return Actor(user_id=uid, name="Lyle", role=Role.ADMIN)


def tool_call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


def scripted(monkeypatch, rounds, sent):
    """A model that makes the given tool-call rounds, then answers. Records every call."""

    def fake(messages, model=None, options=None, tools=None, timeout=None):
        sent.append({"messages": messages, "tools": tools, "options": options})
        if tools is not None and len(sent) <= len(rounds):
            return {"message": {"role": "assistant", "content": "",
                                "tool_calls": rounds[len(sent) - 1]}, "done_reason": "stop"}
        return {"message": {"role": "assistant", "content": "Answered."}, "done_reason": "stop"}

    monkeypatch.setattr(loop.ollama, "chat", fake)


# --- the proof that nothing existing changed ----------------------------------


def _normalised_trace(raw):
    """The stored trace, minus what is random or timed: call ids, artifact ids, durations."""
    out = []
    for entry in json.loads(raw):
        entry = {k: v for k, v in entry.items() if k not in ("duration_seconds",)}
        entry["call_id"] = "<call>" if entry.get("call_id") else entry.get("call_id")
        # Piece 5 replaced the trace key `artifact_ids` with `records`. The digest pinned at
        # piece 2 predates that, so the scrub reads the new key back into the old shape: the pin
        # then proves everything ELSE a turn observes is unchanged, and that no unexpected key
        # appeared.
        entry["artifact_ids"] = ["<id>" for r in entry.pop("records", [])
                                 if r["kind"] == "artifact"]
        entry["value"] = HEX32.sub("<id>", str(entry.get("value")))
        out.append(entry)
    return out


def existing_behaviour_digest(monkeypatch, actor):
    """sha256 over everything an existing tool or turn can observe, for three real turns:

    * every model call (messages, tools, options);
    * the stored tool trace of each assistant message, normalised;
    * every prompt the integrity gate's classifier was shown (so the gate is unchanged too);
    * the keyword names each handler was called with, **for tools that do not declare origin**.

    The tools are a plain one, one declaring `takes_attribution`, and the real `creative_write`.
    """
    seen_kwargs: list = []

    def echo(text, **kw):
        seen_kwargs.append(("probe_echo", sorted(kw)))
        return f"echo: {text}"

    def attr(attribution, **kw):
        seen_kwargs.append(("probe_attr", sorted(kw), attribution.user_id == actor.user_id))
        return "attributed"

    def real_creative(*args, **kw):
        seen_kwargs.append(("creative_write", sorted(kw)))
        return CREATIVE_WRITE.handler(*args, **kw)

    registry = ToolRegistry([
        Tool(name="probe_echo", description="TEST-ONLY echo.",
             parameters={"type": "object", "properties": {"text": {"type": "string"}},
                         "required": ["text"]}, handler=echo),
        Tool(name="probe_attr", description="TEST-ONLY attribution taker.",
             parameters={"type": "object", "properties": {}, "required": []},
             handler=attr, takes_attribution=True),
        dataclasses.replace(CREATIVE_WRITE, handler=real_creative),
    ])
    digest = hashlib.sha256()
    classified: list = []
    monkeypatch.setattr(classifier, "classify",
                        lambda prompt: classified.append(HEX32.sub("<id>", prompt)) or "CONSISTENT")
    conversation = db.start_conversation(actor.user_id)
    turns = [
        ("Hello there.", []),
        ("Use your tools.", [[tool_call("probe_echo", text="a"), tool_call("probe_attr")]]),
        ("Keep a short piece.", [[tool_call("creative_write", text="A kettle, cooling.",
                                           title="Kettle")]]),
    ]
    for text, rounds in turns:
        sent: list = []
        scripted(monkeypatch, rounds, sent)
        turn.handle_user_message(actor, text, conversation, situation="", registry=registry)
        digest.update(scrub(json.dumps(sent, sort_keys=True, default=str)).encode())
    for row in db.get_conversation_messages(conversation):
        if row["role"] == "assistant":
            trace = _normalised_trace(row["tool_trace"]) if row["tool_trace"] else None
            digest.update(scrub(json.dumps([row["content"], trace], sort_keys=True)).encode())
    digest.update(json.dumps(seen_kwargs, sort_keys=True).encode())
    digest.update(scrub(json.dumps(classified, sort_keys=True)).encode())   # the gate's own prompts
    return digest.hexdigest(), seen_kwargs


#: Taken by running this function on the HEAD production files (registry.py, loop.py, turn.py
#: as committed, before any piece-2 edit; `git stash` of the three files) and stable across 4 runs
#: there and 3 after the edit. **The first pin of this digest was wrong and is not this one**: it
#: changed every hour because the correction classifier's prompt prints a minute-granular time
#: (`2026-10-02T16:00`); the scrubber now covers that form.
#:
#: **Re-taken 2026-10-04 (decision #24)**, e5c92a42... -> this: it covers every model call, whose
#: system prompt holds soul.md, AND every gate prompt, which holds architecture.md — so both
#: replaced texts move it. Nothing about the tool path or the turn changed. Re-taken in a commit
#: that touched only the three pinned digests, and confirmed equal to the design pass's value.
#:
#: **Re-taken 2026-10-05 (decision #25)**, 107544e1... -> this: every model call's system prompt
#: holds soul.md and operational.md, both rewritten. architecture.md is unchanged, so the gate
#: prompts did not move it, and the turns pass no situation block. Re-taken in a commit that
#: touched only the two digests that moved, stable across two runs.
#:
#: **Re-taken 2026-10-05 (batch 1)**, fd3b4753... -> this: a later turn's history now carries the
#: earlier turn's one-line tool record (3.3, option B). The gate prompts did not move (the gate's
#: own digest holds). Stable across two runs.
#:
#: **Re-taken 2026-10-06 (chat page, decision #31)**, 8f209f8c... -> this: creative_write's
#: description and its result's last line say that the person is shown a note that a piece was
#: saved, because the page now renders receipts. Both are in every model call (the tool schema)
#: or tool message. Confirmed by restoring only creative_write.py, which gives the old value.
#: Stable across two runs.
BEFORE_PIECE_2 = "a4cf7c4824b4f3ffd62d9ff0558f3d07af38a62715644159f11b0fbe26c03a5f"


def test_existing_tools_and_turns_are_byte_identical_to_before_piece_2(store, monkeypatch):
    value, seen = existing_behaviour_digest(monkeypatch, store)
    assert value == BEFORE_PIECE_2


# --- the four guards, each proven to bite (mutations in the changelog) --------

import ast  # noqa: E402
import inspect  # noqa: E402
import logging  # noqa: E402

from program.attribution import AttributionContext  # noqa: E402
from program.origin import OriginContext  # noqa: E402
from program.tools import registry as registry_module  # noqa: E402
from program.tools.registry import ToolError, ToolOutcome  # noqa: E402

ORIGIN = OriginContext(conversation_id="conv-1", user_message_id="msg-1",
                       context_message_ids=frozenset({"msg-0", "msg-1"}))


def origin_tool(received, name="probe_ctx", parameters=None, **flags):
    def handler(origin, **kw):
        received.append((origin, kw))
        return "saw context"
    return Tool(name=name, description="TEST-ONLY context taker.",
                parameters=parameters or {"type": "object", "properties": {}, "required": []},
                handler=handler, takes_origin=True, **flags)


def plain_tool(received, name="probe_plain"):
    def handler(**kw):
        received.append(kw)
        return "plain"
    return Tool(name=name, description="TEST-ONLY plain tool.",
                parameters={"type": "object", "properties": {"text": {"type": "string"}},
                            "required": []}, handler=handler)


# 1. DECLARED, not inferred


def test_origin_reaches_only_a_tool_that_declares_it():
    got, plain = [], []
    reg = ToolRegistry([origin_tool(got), plain_tool(plain)])

    assert reg.dispatch("probe_ctx", {}, origin=ORIGIN).outcome is ToolOutcome.OK
    assert reg.dispatch("probe_plain", {"text": "x"}, origin=ORIGIN).outcome is ToolOutcome.OK

    assert got[0][0].conversation_id == "conv-1" and got[0][0].user_message_id == "msg-1"
    assert got[0][0].context_message_ids == ORIGIN.context_message_ids
    assert plain == [{"text": "x"}], "a tool that did not declare origin was handed it"


def test_a_declaring_tool_with_no_origin_raises_rather_than_degrading():
    """A wiring bug, as for attribution: there is no honest value to substitute."""
    reg = ToolRegistry([origin_tool([])])
    with pytest.raises(ToolError, match="takes origin and none was supplied"):
        reg.dispatch("probe_ctx", {})


def test_origin_is_not_an_actor_or_an_attribution_and_carries_no_role():
    names = {f.name for f in dataclasses.fields(OriginContext)}
    assert names == {"conversation_id", "user_message_id", "context_message_ids", "call_id"}
    assert not names & {"user_id", "role", "actor", "permissions"}
    assert {f.name for f in dataclasses.fields(AttributionContext)} == {"user_id"}  # untouched


def test_an_origin_that_names_no_exchange_is_refused():
    for bad in (dict(conversation_id="", user_message_id="m"),
                dict(conversation_id="c", user_message_id="  ")):
        with pytest.raises(ValueError):
            OriginContext(**bad)


# 2. NEVER MODEL-SETTABLE


def test_a_tool_that_takes_origin_may_not_declare_it_as_a_parameter():
    with pytest.raises(ToolError, match="never from the model"):
        origin_tool([], parameters={"type": "object", "required": [],
                                    "properties": {"origin": {"type": "string"}}})


def test_a_model_supplied_origin_argument_is_refused_and_the_handler_does_not_run():
    got = []
    reg = ToolRegistry([origin_tool(got)])

    result = reg.dispatch("probe_ctx", {"origin": "forged"}, origin=ORIGIN)

    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS and got == []


def test_the_schema_the_model_is_shown_never_mentions_origin():
    reg = ToolRegistry([origin_tool([]), plain_tool([])])
    assert "origin" not in json.dumps(reg.ollama_schema())


def test_a_tool_that_does_not_take_origin_may_still_have_a_parameter_so_named():
    """The guard is on declaring tools; an unrelated parameter called `origin` (a place of
    origin, say) is not this mechanism and is not refused."""
    got = []
    tool = Tool(name="probe_geo", description="TEST-ONLY.",
                parameters={"type": "object", "required": [],
                            "properties": {"origin": {"type": "string"}}},
                handler=lambda origin: got.append(origin) or "ok")
    ToolRegistry([tool]).dispatch("probe_geo", {"origin": "Peru"}, origin=ORIGIN)
    assert got == ["Peru"]


# 3. NEVER IN THE RECORDED ARGUMENTS OR THE TRACE


def test_origin_is_in_neither_the_recorded_arguments_nor_the_trace_entry():
    reg = ToolRegistry([origin_tool([], parameters={
        "type": "object", "required": [], "properties": {"note": {"type": "string"}}})])

    result = reg.dispatch("probe_ctx", {"note": "n"}, origin=ORIGIN)

    assert result.arguments == {"note": "n"}
    entry = result.to_trace_entry()
    assert "origin" not in entry and "origin" not in json.dumps(entry)
    assert "conv-1" not in json.dumps(entry) and "msg-1" not in json.dumps(entry)


def test_dispatch_fills_the_call_id_into_a_copy_and_leaves_the_callers_origin_alone():
    got = []
    reg = ToolRegistry([origin_tool(got)])

    result = reg.dispatch("probe_ctx", {}, origin=ORIGIN)

    assert got[0][0].call_id == result.call_id and result.call_id
    assert ORIGIN.call_id is None, "the caller's object was changed"
    assert got[0][0] is not ORIGIN


# 4. THE LOOP PASSES IT THROUGH UNREAD


def test_the_loop_hands_the_origin_to_dispatch_and_reads_nothing_in_it(monkeypatch):
    got, sent = [], []
    scripted(monkeypatch, [[tool_call("probe_ctx")]], sent)

    result = loop.run_turn([{"role": "user", "content": "hi"}], registry=ToolRegistry(
        [origin_tool(got)]), origin=ORIGIN)

    assert result.text == "Answered."
    assert got[0][0].conversation_id == "conv-1" and got[0][0].call_id
    assert "origin" not in json.dumps(result.trace) and "msg-1" not in json.dumps(result.trace)


def test_the_loop_module_never_reads_an_attribute_of_the_origin():
    """Passed through unread, checked on the code: no attribute of the origin appears in loop.py
    (a docstring naming one is not code)."""
    tree = ast.parse(inspect.getsource(loop))
    read = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not read & {"conversation_id", "user_message_id", "context_message_ids"}, (
        "loop.py reads the origin; it must pass it on untouched")


def test_registry_never_puts_origin_into_the_arguments_it_validates():
    source = inspect.getsource(registry_module.ToolRegistry.dispatch)
    assert "supplied[ORIGIN_ARGUMENT]" not in source and "supplied['origin']" not in source


# --- turn.py builds it ---------------------------------------------------------


@pytest.fixture
def turn_world(store, monkeypatch):
    """A real store with: this conversation, and another person's conversation holding a chunk
    the passive retrieval 'finds' and a message nothing finds."""
    jodie = db.create_user("Jodie", role="user")
    other = db.start_conversation(jodie)
    first = db.save_message(other, jodie, "user", "Jodie takes oat milk in her coffee.")
    last = db.save_message(other, jodie, "assistant", "Noted that she takes oat milk.")
    unfound = db.save_message(other, jodie, "user", "A message no retrieval returns.")
    db.insert_chunk(chunk_id="chunk-oat", conversation_id=other, user_id=jodie,
                    text="oat milk", source_type="conversation", source_trust="firsthand",
                    text_sha256="x", chunk_index=0, first_message_id=first, last_message_id=last)
    result = retrieval.RetrievalResult(
        query="q", results=[retrieval.RetrievedChunk(chunk_id="chunk-oat", text="oat milk")])
    monkeypatch.setattr(turn.retrieval, "search", lambda query: result)
    return type("W", (), {"actor": store, "first": first, "last": last, "unfound": unfound})


def run_turn_with(world, monkeypatch, rounds, tools):
    sent = []
    scripted(monkeypatch, rounds, sent)
    conversation = db.start_conversation(world.actor.user_id)
    earlier = db.save_message(conversation, world.actor.user_id, "user", "An earlier question.")
    turn.handle_user_message(world.actor, "Is oat milk right for Jodie?", conversation,
                             situation="", registry=ToolRegistry(tools))
    return conversation, earlier


def test_the_turn_builds_the_origin_from_the_conversation_and_the_passive_retrieval(
        turn_world, monkeypatch):
    got = []
    conversation, earlier = run_turn_with(
        turn_world, monkeypatch, [[tool_call("probe_ctx")]], [origin_tool(got)])

    origin = got[0][0]
    ids = {r["id"]: r["role"] for r in db.get_conversation_messages(conversation)}
    user_message = next(i for i, role in ids.items()
                        if role == "user" and i != earlier)
    assert origin.conversation_id == conversation
    assert origin.user_message_id == user_message           # the message that triggered the turn
    assert earlier in origin.context_message_ids            # the conversation's own messages
    assert user_message in origin.context_message_ids
    assert {turn_world.first, turn_world.last} <= origin.context_message_ids   # via the chunk
    assert turn_world.unfound not in origin.context_message_ids, (
        "a message nothing retrieved is in the context")
    assert origin.call_id


def test_a_message_a_tool_surfaces_during_the_turn_is_not_in_the_context(
        turn_world, monkeypatch):
    """The recorded gap (N18): `context_message_ids` is built before the loop, so what a
    `memory_search` call returns mid-turn is not in it. Here a tool in round 1 'surfaces' a
    message, and the origin a later tool receives still lacks it."""
    got = []
    searcher = Tool(name="probe_search", description="TEST-ONLY memory_search stand-in.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    handler=lambda: "A message no retrieval returns.")

    run_turn_with(turn_world, monkeypatch,
                  [[tool_call("probe_search")], [tool_call("probe_ctx")]],
                  [searcher, origin_tool(got)])

    assert turn_world.unfound not in got[0][0].context_message_ids


def test_unreadable_chunk_messages_degrade_to_the_conversation_alone_and_do_not_fail_the_turn(
        turn_world, monkeypatch, caplog):
    got = []

    def broken(chunk_ids):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(turn.db, "get_messages_in_chunks", broken)
    with caplog.at_level(logging.WARNING, logger="program.engine.turn"):
        conversation, earlier = run_turn_with(
            turn_world, monkeypatch, [[tool_call("probe_ctx")]], [origin_tool(got)])

    assert earlier in got[0][0].context_message_ids
    assert turn_world.first not in got[0][0].context_message_ids
    assert any("origin: could not read the messages" in r.getMessage() for r in caplog.records)


def test_the_stored_trace_of_a_real_turn_carries_no_origin_and_its_call_id_matches(
        turn_world, monkeypatch):
    got = []
    conversation, _ = run_turn_with(
        turn_world, monkeypatch, [[tool_call("probe_ctx")]], [origin_tool(got)])

    stored = next(r["tool_trace"] for r in db.get_conversation_messages(conversation)
                  if r["tool_trace"])
    entry = json.loads(stored)[0]
    assert entry["call_id"] == got[0][0].call_id
    assert "origin" not in stored and conversation not in stored


# --- built only when an offered tool declares it (review, 2026-10-02) ---------


def _spy_origin_work(monkeypatch):
    """Count what building an origin does: the builder itself, and chunk-message reads **made by
    the builder**. (The correction path also reads those messages, on every turn, so a bare
    count of reads would not isolate the origin's.)"""
    import traceback

    calls = {"chunk_read_by_origin": 0, "built": 0}
    real_read, real_build = turn.db.get_messages_in_chunks, turn._build_origin

    def read(chunk_ids):
        if any(frame.name == "_build_origin" for frame in traceback.extract_stack()):
            calls["chunk_read_by_origin"] += 1
        return real_read(chunk_ids)

    def build(*a, **k):
        calls["built"] += 1
        return real_build(*a, **k)

    monkeypatch.setattr(turn.db, "get_messages_in_chunks", read)
    monkeypatch.setattr(turn, "_build_origin", build)
    return calls


def test_a_turn_whose_tools_none_declare_origin_does_none_of_the_work(turn_world, monkeypatch):
    """Notes dark: nothing offered takes origin, so the origin never reads the messages behind
    the retrieved chunk and none is built. (Retrieval does return a chunk, so a read would show.)"""
    calls = _spy_origin_work(monkeypatch)

    run_turn_with(turn_world, monkeypatch, [[tool_call("probe_plain")]], [plain_tool([])])

    assert calls == {"chunk_read_by_origin": 0, "built": 0}


def test_the_default_registry_is_checked_when_none_is_passed(turn_world, monkeypatch):
    """`handle_user_message(registry=None)` runs on the default registry, as the loop does. With
    one that declares origin the origin is built and reaches the tool; with the real default
    (nothing declares it today) it is not."""
    from program.tools import registry as registry_module

    assert not any(t.takes_origin for t in registry_module.default_registry()), (
        "a tool in the default catalogue declares origin; Notes is meant to be dark")
    calls = _spy_origin_work(monkeypatch)
    sent = []
    scripted(monkeypatch, [], sent)
    conversation = db.start_conversation(turn_world.actor.user_id)
    turn.handle_user_message(turn_world.actor, "Plain turn on the real default registry.",
                             conversation, situation="")
    assert calls == {"chunk_read_by_origin": 0, "built": 0}

    got = []
    declaring = ToolRegistry([origin_tool(got)])
    monkeypatch.setattr(registry_module, "default_registry", lambda: declaring)
    scripted(monkeypatch, [[tool_call("probe_ctx")]], [])
    turn.handle_user_message(turn_world.actor, "Now with a default that declares it.",
                             conversation, situation="")

    assert calls["built"] == 1 and calls["chunk_read_by_origin"] == 1
    assert got[0][0].conversation_id == conversation
