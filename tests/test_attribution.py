"""Attribution, the artifact-kind registry, and the entity's own row. Phase 4 P0.

Design of record: `docs/MEDIA_AND_CREATIVE_DESIGN.md` (Q1, Q2, Q3).

The load-bearing tests here are the two that protect properties rather than
behaviour: that the three Phase 2 tools are dispatched **exactly** as they were
before attribution existed, and that attribution can never be set by the model.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from program import config
from program.artifacts import ingest, kinds
from program.attribution import AttributionContext
from program.engine import loop
from program.memory import db
from program.tools import catalog, registry
from program.tools.registry import Tool, ToolOutcome, ToolRegistry


@pytest.fixture
def store(isolated_data_dir):
    db.init_databases()
    return db.create_user("Lyle", role="admin")


# --- AttributionContext is not an Actor --------------------------------------


def test_it_carries_a_user_id_and_nothing_else():
    """Attribution answers "whose record is this". Authorization answers "what may
    this person do". A Role here would make the second reachable wherever the
    first is needed, and several tests across the build assert the tool path has
    no authorization object in it."""
    fields = {f.name for f in dataclasses.fields(AttributionContext)}

    assert fields == {"user_id"}
    assert not fields & {"role", "actor", "name", "permissions"}


def test_an_empty_user_id_is_refused_where_the_caller_is_visible():
    """artifacts.user_id is NOT NULL, so an empty value fails at the write — far
    from whoever passed it. Failing here names the caller instead."""
    for bad in ("", "   "):
        with pytest.raises(ValueError, match="real users.id"):
            AttributionContext(user_id=bad)


def test_it_is_frozen():
    context = AttributionContext(user_id="u1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.user_id = "u2"


# --- the entity's own row (Q2c) ----------------------------------------------


def test_the_entity_row_is_created_once_and_reused(store):
    first = db.entity_user_id()
    second = db.entity_user_id()

    assert first == second
    assert AttributionContext.for_entity().user_id == first


def test_the_entity_row_can_never_authenticate(store):
    """`password_hash` stays NULL, and a NULL hash never authenticates. That —
    not the role — is what keeps this row from being an account."""
    row = db.get_user(db.entity_user_id())

    assert row["password_hash"] is None


def test_the_entity_row_takes_a_real_role_because_the_schema_requires_one(store):
    """`users.role` is CHECK (role IN ('admin', 'user')), so a third value would
    need the table recreated — and the reviewed decision was a real row in the
    existing table with no schema change. `user` over `admin` on least privilege.
    """
    row = db.get_user(db.entity_user_id())

    assert row["role"] == "user"
    with pytest.raises(ValueError, match="unknown role"):
        db.create_user("someone else", role="entity")


def test_the_entity_row_carries_an_unspeakable_sentinel_not_a_name(store):
    """CLAUDE.md: the entity has no name and must not be given one, by code or
    config. `users.name` is NOT NULL UNIQUE so the row needs *something* — and it
    is a sentinel nobody could mistake for a name meant to be spoken.

    `"the system"` was the first choice and was rejected on the reachability trace
    the next two tests pin: a phrase in the register of rendered prose is exactly
    the wrong thing to put somewhere that can reach rendered prose.
    """
    row = db.get_user(db.entity_user_id())

    assert row["name"] == db.ENTITY_USER_NAME == "__entity__"
    assert row["name"].startswith("__") and row["name"].endswith("__"), (
        "the marker has to be visibly a sentinel, not a phrase"
    )
    assert " " not in row["name"], "a spoken label has spaces; a sentinel does not"


def test_a_user_name_reaches_chunk_text_and_therefore_the_prompt(store):
    """WHY the sentinel matters — the reachability finding, pinned.

    `chunking._format_line` renders each user message as `"{name}: {content}"`,
    and chunk text is what reaches FTS5, the embedding and the retrieved-records
    block. A conversation owned by the entity row would bake its `users.name` into
    all three, permanently: re-chunking reproduces it and an embedding cannot be
    edited afterwards.

    If this test ever fails because names stopped reaching chunk text, the
    constraint on `ENTITY_USER_NAME` has loosened and the comment there should say
    so rather than staying as received wisdom.
    """
    from program.memory import chunking

    row = {"role": "user", "content": "hello"}
    line = chunking._format_line(row, "Lyle")

    assert line == "Lyle: hello"
    assert "Lyle" in line, "the user's name is embedded in indexed chunk text"


def test_the_entity_row_would_produce_an_actor_carrying_that_name(store):
    """The second path: `db.get_actor()` builds an Actor from the row, and
    `turn.py` passes `actor.name` into the correction classifier's prompt and the
    situation block — both model-facing. Unreachable today because this row cannot
    log in, but nothing structurally prevents a future caller."""
    actor = db.get_actor(db.entity_user_id())

    assert actor is not None
    assert actor.name == db.ENTITY_USER_NAME


def test_creating_the_entity_row_writes_to_both_stores(store):
    """`create_user` is atomic across both, and the archive's copy is what makes
    attribution survive in the append-only record."""
    entity = db.entity_user_id()

    with db.connection() as conn:
        archived = conn.execute(
            "SELECT name FROM archive.users WHERE id = ?", (entity,)
        ).fetchone()
    assert archived["name"] == db.ENTITY_USER_NAME


# --- the artifact-kind registry (Q1, Q3) -------------------------------------


def test_every_kind_declares_a_root_and_a_provenance_pair():
    for kind in kinds.KINDS:
        assert kind.artifact_type and kind.source_type and kind.source_trust
        assert callable(kind.root)
        assert kind.note.strip(), f"{kind.artifact_type} has no recorded reasoning"


def test_uploads_keep_the_artifact_dir_and_entity_output_goes_to_workspace(
    isolated_data_dir,
):
    """Q1. The split is uploads-versus-entity-output, not text-versus-binary —
    which is why a generated image goes under the workspace, where the writing does.

    And within the workspace it goes to its own subdirectory: Phase 0 created
    `workspace/{generated,uploads,writing,research,journals}/` deliberately and tracks
    each with a `.gitkeep`, so sharding flat under `workspace/` would scatter 256 hex
    directories across that structure.
    """
    assert kinds.root_for("upload") == config.artifact_dir()
    assert kinds.root_for("creative_writing") == config.workspace_dir() / "writing"
    assert kinds.root_for("generated_image") == config.workspace_dir() / "generated"
    for kind in kinds.KINDS:
        if kind.root is config.workspace_dir:
            assert kind.subdirectory, f"{kind.artifact_type} would shard flat"


def test_the_root_is_resolved_per_call_not_captured_at_import(
    isolated_data_dir, tmp_path, monkeypatch
):
    """config.py's own rule. If the root were captured at import, repointing the
    stores — which the whole test suite depends on — would not move it."""
    before = kinds.root_for("creative_writing")
    monkeypatch.setenv("ANAM_WORKSPACE_DIR", str(tmp_path / "elsewhere"))
    config.reload()

    assert kinds.root_for("creative_writing") != before
    assert kinds.root_for("creative_writing") == tmp_path / "elsewhere" / "writing"


def test_an_unregistered_kind_raises_rather_than_guessing_a_root():
    """A wrong root writes bytes somewhere the backup, the wipe and the governance
    blocklist do not expect. A default is how that happens without an error."""
    with pytest.raises(kinds.UnknownArtifactKindError, match="not a registered"):
        kinds.root_for("screenplay")


def test_entity_output_is_firsthand_and_uploads_are_not():
    assert kinds.kind("creative_writing").source_trust == "firsthand"
    assert kinds.kind("generated_image").source_trust == "firsthand"
    assert kinds.kind("upload").source_trust == "secondhand"


def test_each_kind_has_its_own_source_type(isolated_data_dir):
    """Decision #10 requires creative writing to be separable later if it ever
    competes for retrieval slots."""
    types = [kind.source_type for kind in kinds.KINDS]

    assert len(set(types)) == len(types)
    assert "creative_writing" in types and "generated_image" in types


def test_storage_path_shards_on_the_generated_id_only(isolated_data_dir):
    relative, absolute = kinds.storage_path("creative_writing", "abcdef0123456789")

    assert relative == "ab/abcdef0123456789"
    assert absolute == config.workspace_dir() / "writing" / relative
    assert relative == f"{'abcdef0123456789'[:2]}/abcdef0123456789", (
        "the stored path stays relative to the kind's root, so the row does not "
        "encode which subdirectory the root happened to be"
    )
    assert absolute.parent.is_dir()


def test_ingest_reads_its_vocabulary_and_root_from_the_registry():
    """One kind must not have its vocabulary in one file and its directory in
    another. The exported names stay; the values are the registry's."""
    assert ingest.SOURCE_TYPE == kinds.kind("upload").source_type
    assert ingest.SOURCE_TRUST == kinds.kind("upload").source_trust
    assert ingest.ARTIFACT_TYPE == "upload"


# --- attribution reaches only the tools that ask for it ----------------------


def make_tool(**overrides):
    base = dict(
        name="writer",
        description="TEST-ONLY. Records what it was handed.",
        parameters={"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
        handler=lambda text, attribution=None: {"text": text, "attribution": attribution},
        takes_attribution=True,
    )
    return Tool(**{**base, **overrides})


def test_a_declaring_tool_receives_the_context():
    reg = ToolRegistry([make_tool()])
    context = AttributionContext(user_id="u1")

    result = reg.dispatch("writer", {"text": "hello"}, attribution=context)

    assert result.outcome is ToolOutcome.OK
    assert result.value["attribution"] is context


def test_the_context_never_appears_in_the_arguments_or_the_trace():
    """`ToolResult.arguments` is what reaches the tool trace the fabrication gate
    reasons over. A value the model never sent must not change that shape."""
    reg = ToolRegistry([make_tool()])

    result = reg.dispatch(
        "writer", {"text": "hello"}, attribution=AttributionContext(user_id="u1")
    )

    assert result.arguments == {"text": "hello"}
    assert "attribution" not in result.to_trace_entry()["arguments"]


def test_a_tool_may_not_declare_attribution_as_a_model_parameter():
    """The security invariant. A model that could set this could attribute a
    write to the other household member."""
    with pytest.raises(registry.ToolError, match="never from the model"):
        make_tool(parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}, "attribution": {"type": "string"}},
        })


def test_a_declaring_tool_with_no_context_supplied_is_a_wiring_bug():
    """There is no honest value to substitute: the caller knows whose record the
    write belongs to and the model does not."""
    reg = ToolRegistry([make_tool()])

    with pytest.raises(registry.ToolError, match="wiring bug"):
        reg.dispatch("writer", {"text": "hello"})


def test_a_model_supplied_attribution_argument_is_rejected_as_unexpected():
    """Belt and braces: even if a tool somehow declared it, the argument
    validator refuses keys outside the schema."""
    reg = ToolRegistry([make_tool()])

    result = reg.dispatch(
        "writer",
        {"text": "hello", "attribution": {"user_id": "someone-else"}},
        attribution=AttributionContext(user_id="u1"),
    )

    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS


# --- REQUIRED regression proof: the Phase 2 tools are unchanged --------------


#: The tools that existed before attribution did. Named rather than "every tool",
#: because the property being protected is *these three are unchanged* — not "nothing
#: takes attribution", which stopped being true the moment `image_generate` landed and
#: would have made this test something to widen rather than something to trust.
PHASE_TWO_TOOLS = ("memory_search", "web_search", "web_fetch")


def test_the_phase_two_tools_do_not_take_attribution():
    """If one of them ever starts declaring it, that is a deliberate change and this
    test is where it gets noticed."""
    for tool in catalog.TOOLS:
        if tool.name in PHASE_TWO_TOOLS:
            assert tool.takes_attribution is False, tool.name


def test_no_phase_two_handler_could_accept_attribution_even_by_accident():
    for tool in catalog.TOOLS:
        if tool.name in PHASE_TWO_TOOLS:
            params = inspect.signature(tool.handler).parameters
            assert "attribution" not in params, tool.name


def test_a_tool_that_writes_declares_attribution():
    """The other direction, so the pair cannot both pass by nothing declaring it.
    `image_generate` writes an artifact row whose user_id is NOT NULL."""
    image = next(t for t in catalog.TOOLS if t.name == "image_generate")

    assert image.takes_attribution is True
    assert "attribution" in inspect.signature(image.handler).parameters
    assert "attribution" not in image.parameters["properties"], (
        "attribution must never be model-settable"
    )


@pytest.mark.parametrize("name, arguments", [
    ("memory_search", {"query": "espresso"}),
    ("web_search", {"query": "espresso"}),
    ("web_fetch", {"url": "https://example.invalid/page"}),
])
def test_the_phase_two_tools_dispatch_identically_with_and_without_attribution(
    name, arguments, isolated_data_dir, monkeypatch
):
    """The regression the review required, asserted on the **result**, not on the
    code path. Shared infrastructure changed underneath three working tools, so
    "it should be fine" is not evidence.

    What is compared is the dispatch **envelope** — outcome, recorded arguments,
    the `ran` flag and the trace's key set. The tool's own `value` and `error` are
    deliberately excluded and the reason is not laziness: two live calls to a
    search engine legitimately return different results, and a `web_fetch` against
    an unreachable host can word its failure differently between attempts. A test
    that compared those would fail for reasons that have nothing to do with
    attribution, which is worse than no test — it would train the next person to
    ignore it.
    """
    reg = registry.default_registry()

    without = reg.dispatch(name, arguments)
    with_context = reg.dispatch(
        name, arguments, attribution=AttributionContext(user_id="u1")
    )

    def envelope(result):
        return {
            "outcome": result.outcome,
            "arguments": result.arguments,
            "ran": result.ran,
            "timeout_seconds": result.timeout_seconds,
            "trace_keys": sorted(result.to_trace_entry()),
        }

    assert envelope(without) == envelope(with_context), (
        f"{name} was dispatched differently once attribution existed"
    )
    assert "attribution" not in with_context.arguments
    assert "attribution" not in with_context.to_trace_entry()["arguments"]


def test_the_loop_passes_attribution_straight_through(monkeypatch):
    """`loop.py` must not read it, interpret it, or log it — it is plumbing."""
    seen = {}
    recorder = make_tool(handler=lambda text, attribution=None: seen.setdefault(
        "attribution", attribution) or "written")
    reg = ToolRegistry([recorder])
    context = AttributionContext(user_id="u1")

    calls = {"n": 0}

    def fake_chat(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "writer", "arguments": {"text": "a story"}}}
            ]}}
        return {"message": {"role": "assistant", "content": "Done."}}

    monkeypatch.setattr(loop.ollama, "chat", fake_chat)
    loop.run_turn(
        [{"role": "user", "content": "write something"}],
        registry=reg,
        soul_text="TEST SOUL",
        attribution=context,
    )

    assert seen["attribution"] is context


def test_turn_supplies_the_person_present(monkeypatch, store):
    """Q2b, asserted from inside the dispatch rather than by reading two lines."""
    from program.engine import turn
    from program.settings.permissions import Actor, Role

    seen = {}
    recorder = make_tool(handler=lambda text, attribution=None: seen.setdefault(
        "user_id", attribution.user_id) or "written")
    reg = ToolRegistry([recorder])

    calls = {"n": 0}

    def fake_chat(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "writer", "arguments": {"text": "a story"}}}
            ]}}
        return {"message": {"role": "assistant", "content": "Done."}}

    monkeypatch.setattr(loop.ollama, "chat", fake_chat)
    monkeypatch.setattr(turn.gate, "check", lambda *a, **k: turn.gate.GateVerdict())
    actor = Actor(user_id=store, name="Lyle", role=Role.ADMIN)

    turn.handle_user_message(actor, "write me something", situation="", registry=reg)

    assert seen["user_id"] == store, "the write was not attributed to the person present"
