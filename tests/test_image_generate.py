"""The `image_generate` tool and generated-image storage. Phase 4, task A2.

Design of record: `docs/MEDIA_AND_CREATIVE_DESIGN.md` (Q4, Q12, Q13, Q14, Q15).

ComfyUI is substituted throughout — the client has its own tests
(`tests/test_comfyui.py`), and what is checked here is the tool's contract: one
parameter, no negative prompt, attribution it cannot be handed by the model, a
result that does not invite describing an image nothing has seen, and a row that says
`metadata_only` because a PNG has no text to read.
"""

from __future__ import annotations

import json

import pytest

from program import config
from program.artifacts import generated, kinds
from program.attribution import AttributionContext
from program.media.comfyui import GeneratedImage
from program.memory import db
from program.tools import catalog, image_generate, registry
from program.tools.registry import ToolOutcome

PNG = bytes.fromhex("89504e470d0a1a0a") + b"x" * 4096


@pytest.fixture
def store(isolated_data_dir):
    db.init_databases()
    return db.create_user("Lyle", role="admin")


def fake_image(prompt="a copper kettle", **overrides):
    base = dict(
        image_bytes=PNG, content_type="image/png", prompt=prompt, seed=4129143294,
        checkpoint="sd_xl_base_1.0.safetensors",
        lora="sdxl_lightning_4step_lora.safetensors", steps=4, cfg=1.0,
        sampler="euler", scheduler="sgm_uniform", width=1024, height=1024,
        duration_seconds=13.2, source_filename="ComfyUI_temp_x_00001_.png",
    )
    return GeneratedImage(**{**base, **overrides})


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    """A3 made storage index, so every storing test now needs an embedder. Real
    embeddings are `tests/test_ingestion.py`'s business; what matters here is the
    tool's contract."""
    from program.artifacts import indexing

    monkeypatch.setattr(indexing.ollama, "embed", lambda text, *a, **k: [0.1] * 768)


@pytest.fixture
def no_comfyui(monkeypatch):
    """Substitute the client. Records the prompt it was given."""
    seen = {}

    def fake_generate(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return fake_image(prompt)

    monkeypatch.setattr(image_generate.comfyui, "generate", fake_generate)
    return seen


# --- the tool's contract -----------------------------------------------------


def test_it_is_registered_and_offered():
    registry.reset_default_registry()
    assert "image_generate" in registry.default_registry().names


def test_only_prompt_is_exposed():
    """The generation settings are a matched set with a measured story — Lightning
    needs cfg 1.0 with euler/sgm_uniform, and 4 steps was chosen on a visual
    comparison. A model that could change steps could triple the turn's cost."""
    assert set(image_generate.IMAGE_GENERATE.parameters["properties"]) == {"prompt"}
    assert image_generate.IMAGE_GENERATE.parameters["required"] == ["prompt"]


def test_there_is_no_negative_prompt_parameter():
    """Q15, locked at review. At cfg 1.0 the sampler skips the unconditional branch,
    so a negative prompt would be accepted and silently ignored — worse than absent,
    because the model could not notice its instruction had no effect."""
    properties = image_generate.IMAGE_GENERATE.parameters["properties"]

    assert "negative_prompt" not in properties
    assert not any("negative" in name for name in properties)
    assert config.comfyui_generation()["cfg"] == 1.0, (
        "if cfg ever rises above 1.0 the negative prompt becomes functional and "
        "exposing it should be reconsidered — that is the trigger, not a phase"
    )


def test_the_description_tells_the_model_it_will_not_see_the_image():
    description = image_generate.IMAGE_GENERATE.description.lower()
    assert "not the image itself" in description or "cannot see" in description


#: The worst case actually observed, 2026-09-21: a COLD ComfyUI with both gemma4:26b
#: and nomic-embed-text resident. See config/defaults.toml for the full table.
MEASURED_WORST_CASE_SECONDS = 87.0


def test_the_timeouts_clear_the_measured_worst_case():
    """This is the test the first derivation needed and did not have.

    `comfyui.timeout_seconds` was 90, derived from an *estimated* "cold or contended"
    of 70-75 s. The real worst case is cold AND contended and measured 87.0 s, so a
    live turn was observed both passing at 83.8 s and failing past 90 s — a constant
    sitting inside its own measurement's variance band, which turns a slow generation
    into a lost one non-deterministically.

    Pinned against the measurement rather than against the number, so lowering it
    back fails here with the reason attached.
    """
    assert config.comfyui_timeout_seconds() > MEASURED_WORST_CASE_SECONDS * 1.2, (
        f"the client timeout must clear the measured {MEASURED_WORST_CASE_SECONDS}s "
        f"worst case with real margin, not sit on top of it"
    )
    assert image_generate.TIMEOUT_SECONDS > config.comfyui_timeout_seconds()


def test_the_timeout_sits_above_the_clients_own_bound():
    """memory_search's and web_fetch's rule: above what the inside enforces, so a
    real failure surfaces as its own named error rather than an opaque TIMEOUT."""
    assert image_generate.TIMEOUT_SECONDS > config.comfyui_timeout_seconds()
    assert image_generate.TIMEOUT_SECONDS < config.agent_tool_budget_seconds(), (
        "a tool timeout above the turn's aggregate budget is clipped on every call"
    )


def test_it_declares_attribution_and_the_model_cannot_supply_it():
    assert image_generate.IMAGE_GENERATE.takes_attribution is True
    assert "attribution" not in image_generate.IMAGE_GENERATE.parameters["properties"]


# --- the `enabled` axis ------------------------------------------------------


def test_a_disabled_capability_is_not_offered_to_the_model(monkeypatch):
    """Decision #12's first axis means "does not exist", so the schema never reaches
    the model — rather than being offered and refused, which burns a turn on the
    discovery.

    Disabled through **config**, not by patching the predicate: the `Tool` holds a
    reference to `config.image_generation_enabled`, which reads the value at call
    time. Patching the function would test the test's own substitute; changing the
    value exercises the path an operator would actually use.
    """
    monkeypatch.setenv("ANAM_COMFYUI_ENABLED", "false")
    config.reload()
    assert config.image_generation_enabled() is False
    registry.reset_default_registry()
    try:
        active = registry.default_registry()
        assert "image_generate" not in active.names
        assert "memory_search" in active.names, "only the disabled tool is withheld"
        assert active.dispatch("image_generate", {"prompt": "x"}).outcome is (
            ToolOutcome.UNKNOWN_TOOL
        )
    finally:
        monkeypatch.delenv("ANAM_COMFYUI_ENABLED", raising=False)
        config.reload()
        registry.reset_default_registry()


def test_it_stays_in_the_catalogue_even_when_disabled(monkeypatch):
    """The catalogue is the full set, not the active set — task 2.1's requirement was
    that the tools be greppable from one place."""
    monkeypatch.setenv("ANAM_COMFYUI_ENABLED", "false")
    config.reload()
    try:
        assert "image_generate" in {tool.name for tool in catalog.TOOLS}
    finally:
        monkeypatch.delenv("ANAM_COMFYUI_ENABLED", raising=False)
        config.reload()


def test_there_is_no_approval_required_axis():
    """Q4. A generated image has no external effect, and GUIDANCE.md reserves those
    flags for unattended execution — so a second axis would gate nothing, which is
    the "gate mounted on nothing" shape `ingest.py` refused."""
    from program.settings import permissions

    assert not any("image" in c.name for c in permissions.CAPABILITIES)
    assert "approval_required" not in config.section("comfyui")
    assert not any("approval" in key for key in config.section("comfyui"))


# --- what the model is told (Q13) --------------------------------------------


def test_the_result_carries_the_path_and_the_id(store, no_comfyui):
    result = image_generate.generate_image(
        "a copper kettle", AttributionContext(user_id=store))

    row = db.list_artifacts(store)[0]
    assert row["id"] in result
    assert row["storage_path"] in result
    assert "1024x1024" in result and "4,104 bytes" in result


def test_the_result_says_nothing_has_seen_the_image(store, no_comfyui):
    """The entity has no vision. A result reading "here is your image" would invite
    the model to describe something it has not seen — the exact fabrication the
    integrity gate exists to catch. Cheaper to make the tool truthful than to catch
    the consequence downstream."""
    result = image_generate.generate_image(
        "a copper kettle", AttributionContext(user_id=store))

    assert "has seen this image" in result
    assert "fabrication" in result


def test_the_prompt_reaches_the_client_unchanged(store, no_comfyui):
    image_generate.generate_image(
        "  a copper kettle on slate  ", AttributionContext(user_id=store))

    assert no_comfyui["prompt"] == "  a copper kettle on slate  "


# --- storage (Q1, Q14) -------------------------------------------------------


def test_it_lands_in_workspace_not_the_upload_directory(store):
    stored = generated.store(fake_image(), store)

    assert stored.absolute_path.is_file()
    assert stored.absolute_path.read_bytes() == PNG
    assert config.workspace_dir() in stored.absolute_path.parents
    assert config.workspace_dir() / "generated" in stored.absolute_path.parents, (
        "it must land in the tracked `generated/` subdirectory, not flat"
    )
    assert config.artifact_dir() not in stored.absolute_path.parents


def test_the_path_is_sharded_from_the_id_not_the_prompt(store):
    stored = generated.store(fake_image(prompt="../../etc/passwd"), store)

    assert stored.storage_path == f"{stored.artifact_id[:2]}/{stored.artifact_id}"
    assert ".." not in str(stored.absolute_path)
    assert "passwd" not in stored.storage_path


def test_the_filename_is_readable_but_sanitised(store):
    stored = generated.store(fake_image(prompt="A Copper Kettle! (on slate)"), store)

    assert stored.filename.startswith("a-copper-kettle-on-slate")
    assert stored.filename.endswith(".png")
    assert all(c.isalnum() or c in "-." for c in stored.filename)


def test_the_row_records_metadata_only_not_extracted(store):
    """A PNG has no text to read. `extracted` would claim the opposite of the truth —
    task 2.6's scanned-PDF precedent, where extracted-but-empty would make a file
    that was never read look read."""
    stored = generated.store(fake_image(), store)
    row = db.get_artifact(stored.artifact_id)

    assert row["extraction_status"] == "metadata_only"
    assert row["artifact_type"] == "generated_image"


def test_the_prompt_is_the_indexable_text(store):
    """Q14: an image has no text of its own, so the prompt is what makes it findable
    by what was asked for. Stored in the column A3 will index from."""
    stored = generated.store(fake_image(prompt="a copper kettle"), store)
    row = db.get_artifact(stored.artifact_id)

    assert row["extracted_text"] == "a copper kettle"


def test_the_generation_settings_are_recorded_for_provenance(store):
    stored = generated.store(fake_image(), store)
    row = db.get_artifact(stored.artifact_id)

    note = json.loads(row["extraction_note"])
    assert note["seed"] == 4129143294 and note["steps"] == 4
    assert note["lora"] == "sdxl_lightning_4step_lora.safetensors"
    assert "image_bytes" not in note


def test_the_sha256_is_of_the_stored_bytes(store):
    import hashlib

    stored = generated.store(fake_image(), store)
    row = db.get_artifact(stored.artifact_id)

    assert row["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert stored.sha256 == row["sha256"]


def test_it_is_attributed_to_the_supplied_user(store):
    other = db.create_user("Jodie", role="user")

    stored = generated.store(fake_image(), other)

    assert db.get_artifact(stored.artifact_id)["user_id"] == other
    assert db.list_artifacts(store) == []


def test_an_empty_image_is_refused(store):
    with pytest.raises(ValueError, match="empty image"):
        generated.store(fake_image(image_bytes=b""), store)


def test_the_prompt_is_indexed(store, monkeypatch):
    """A3. This test previously asserted **nothing** was indexed, with a note saying
    it would fail and point at the change when A3 landed. It did, and this is the
    change: the prompt is chunked and embedded through the same
    `indexing.index_text` uploads use."""
    from program.artifacts import indexing

    monkeypatch.setattr(indexing.ollama, "embed", lambda text, *a, **k: [0.1] * 768)
    stored = generated.store(fake_image(prompt="a copper kettle"), store)

    chunks = db.get_artifact_chunks(stored.artifact_id)
    assert len(chunks) == 1 and stored.chunks_written == 1 and stored.indexed
    assert chunks[0]["text"] == "a copper kettle"
    assert chunks[0]["source_type"] == "generated_image"
    assert chunks[0]["source_trust"] == "firsthand"
    assert chunks[0]["conversation_id"] is None


def test_the_provenance_comes_from_the_registry_not_the_caller(store, monkeypatch):
    """An artifact must not be indexed under provenance that disagrees with its row."""
    from program.artifacts import indexing, kinds

    monkeypatch.setattr(indexing.ollama, "embed", lambda text, *a, **k: [0.1] * 768)
    stored = generated.store(fake_image(), store)

    kind = kinds.kind("generated_image")
    chunk = db.get_artifact_chunks(stored.artifact_id)[0]
    assert (chunk["source_type"], chunk["source_trust"]) == (
        kind.source_type, kind.source_trust)


def test_an_unreachable_embedder_leaves_the_image_stored_but_unindexed(store, monkeypatch):
    """Embedding precedes every chunk write, so a failure leaves no half-indexed
    artifact. The file and row survive — they are the record; chunks are derived and
    rebuildable by re-indexing."""
    from program.artifacts import indexing
    from program.engine import ollama as ollama_module

    def unreachable(text, *a, **k):
        raise ollama_module.OllamaUnreachable("no model")

    monkeypatch.setattr(indexing.ollama, "embed", unreachable)

    with pytest.raises(ollama_module.OllamaUnreachable):
        generated.store(fake_image(), store)

    [row] = db.list_artifacts(store)
    assert row["artifact_type"] == "generated_image"
    assert db.get_artifact_chunks(row["id"]) == [], "no half-indexed artifact"


def test_the_registered_kind_decides_the_root_not_this_module(store):
    """If the registry ever says something else, storage must follow it rather than
    carry its own copy of the answer."""
    assert kinds.root_for("generated_image") == config.workspace_dir() / "generated"
    assert generated.ARTIFACT_TYPE == "generated_image"


# --- through dispatch, the way a turn reaches it ------------------------------


def test_dispatch_supplies_attribution_and_the_handler_writes_that_user(store, no_comfyui):
    registry.reset_default_registry()
    try:
        result = registry.default_registry().dispatch(
            "image_generate", {"prompt": "a copper kettle"},
            attribution=AttributionContext(user_id=store),
        )
        assert result.outcome is ToolOutcome.OK
        assert db.list_artifacts(store)[0]["user_id"] == store
        assert "attribution" not in result.arguments
    finally:
        registry.reset_default_registry()


def test_a_generation_failure_becomes_tool_error_not_a_dead_turn(store, monkeypatch):
    from program.media import comfyui as client

    def boom(prompt, **kwargs):
        raise client.ComfyUIUnreachable("ComfyUI is not running")

    monkeypatch.setattr(image_generate.comfyui, "generate", boom)
    registry.reset_default_registry()
    try:
        result = registry.default_registry().dispatch(
            "image_generate", {"prompt": "x"},
            attribution=AttributionContext(user_id=store),
        )
        assert result.outcome is ToolOutcome.TOOL_ERROR
        assert "not running" in (result.error or "")
        assert db.list_artifacts(store) == [], "nothing stored on failure"
    finally:
        registry.reset_default_registry()


# --- A3: how a retrieved image renders ----------------------------------------


def test_a_retrieved_image_announces_itself_as_an_image(store):
    """Without a label the model is handed a description and no way to know it
    describes a picture nothing has looked at. The entity has no vision, so the
    rendering is what stops the prompt being read as an observation."""
    from program.engine import prompt
    from program.memory import retrieval

    chunk = retrieval.RetrievedChunk(
        chunk_id="c1", text="a copper kettle on a slate worktop",
        created_at="2026-09-21T16:57:00", source_type="generated_image",
        source_trust="firsthand",
    )
    rendered = prompt._render_chunk(chunk, "record 1")

    assert "generated image" in rendered
    assert "prompt only" in rendered, (
        "the label has to say the text IS the prompt, not a description of what the "
        "image looks like"
    )


def test_a_conversation_chunk_renders_byte_identically_to_before(store):
    """The label is additive. A conversation record must render exactly as it did
    before A3, or every existing prompt test is measuring something new."""
    from program.engine import prompt
    from program.memory import retrieval

    for source_type in ("conversation", None):
        chunk = retrieval.RetrievedChunk(
            chunk_id="c1", text="we talked about kettles",
            created_at="2026-09-21T16:57:00", source_type=source_type,
        )
        assert prompt._render_chunk(chunk, "record 1") == (
            "[record 1 · 2026-09-21T16:57:00]\nwe talked about kettles"
        )


def test_the_label_is_not_in_the_indexed_text(store, monkeypatch):
    """Task 1.3's rule: metadata in chunk text pollutes the embedding and the BM25
    index. Every generated image would otherwise match a query mentioning images."""
    stored = generated.store(fake_image(prompt="a copper kettle"), store)

    [chunk] = db.get_artifact_chunks(stored.artifact_id)
    assert "generated image" not in chunk["text"]
    assert chunk["text"] == "a copper kettle"


def test_a_generated_image_is_findable_by_its_prompt(store, monkeypatch):
    """The point of indexing it at all: retrieval finds the image by what was asked
    for. Real FTS5 and real RRF; only the embedding is substituted."""
    import hashlib

    from program.memory import retrieval

    def deterministic(text, *a, **k):
        digest = hashlib.sha256(text.encode()).digest()
        return [(digest[i % len(digest)] / 255.0) for i in range(768)]

    from program.artifacts import indexing

    monkeypatch.setattr(indexing.ollama, "embed", deterministic)
    monkeypatch.setattr(retrieval.ollama, "embed", deterministic)

    stored = generated.store(
        fake_image(prompt="a copper kettle on a slate worktop"), store)

    result = retrieval.search("copper kettle slate")
    found = [r for r in result.results if r.chunk_id in stored.chunk_ids]

    assert found, "the generated image was not retrievable by its prompt"
    assert found[0].source_type == "generated_image"
    assert "generated image" in prompt_render(result)


def prompt_render(result):
    from program.engine import prompt

    return prompt.render_retrieved(result)
