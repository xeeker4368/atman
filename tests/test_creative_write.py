"""The `creative_write` tool and creative-writing storage. Phase 4, task B4.

Design of record: `docs/MEDIA_AND_CREATIVE_DESIGN.md`; decision #10.

No model is involved anywhere in this path — the entity composes the piece in its own
output and this keeps it — so nothing here is substituted except the embedder, which
`tests/test_ingestion.py` already covers against real Ollama.
"""

from __future__ import annotations

import pytest

from program import config
from program.artifacts import kinds, writing
from program.attribution import AttributionContext
from program.memory import db
from program.tools import catalog, creative_write, registry
from program.tools.registry import ToolOutcome

STORY = (
    "The kettle had been on the hob so long it had stopped meaning tea.\n\n"
    "She moved it anyway, each morning, an inch to the left of the ring."
)


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(
        writing.indexing.ollama, "embed", lambda text, *a, **k: [0.1] * 768)
    db.init_databases()
    return db.create_user("Lyle", role="admin")


def context(user_id):
    return AttributionContext(user_id=user_id)


# --- what decision #10 asks for, asserted as absences ------------------------


def test_it_is_registered_and_always_offered():
    """Unlike image_generate there is no `enabled` axis: nothing external can be
    unavailable, so there is nothing for a flag to describe."""
    registry.reset_default_registry()

    assert "creative_write" in registry.default_registry().names
    tool = next(t for t in catalog.TOOLS if t.name == "creative_write")
    assert tool.enabled is None


def test_no_capability_is_registered_for_it():
    """Both household members may do this (#17), so `role` draws no line and a
    capability would always return True — the "gate mounted on nothing" `ingest.py`
    refused for uploads."""
    from program.settings import permissions

    # Matching on "writ" would hit `settings.write` — the substring was the first
    # version of this test and it failed for that reason rather than a real one.
    names = {c.name for c in permissions.CAPABILITIES}
    assert names == {"settings.read", "settings.write"}, (
        f"a capability was registered without a consumer that needs one: "
        f"{names - {'settings.read', 'settings.write'}}"
    )


def test_nothing_in_the_path_takes_an_actor_or_a_role():
    """Attribution is not authorization. The same property the gate, corrections and
    supersession each assert for themselves."""
    import inspect

    for fn in (writing.store, creative_write.write_creatively):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"actor", "role", "user", "permissions"}, fn.__name__


def test_the_content_is_not_inspected_scored_or_filtered(store):
    """Decision #10: no gate. The lowest-risk category in the build — no external
    effect, nothing irreversible — so this absence is deliberate.

    Asserted by storing something a content check would plausibly object to and
    confirming it is kept verbatim.
    """
    awkward = "A story in which nothing is resolved and the narrator lies throughout."

    stored = writing.store(awkward, store)

    assert db.get_artifact(stored.artifact_id)["extracted_text"] == awkward
    assert stored.absolute_path.read_text(encoding="utf-8") == awkward


def test_it_is_indexed_rather_than_excluded_from_retrieval(store):
    """Q9: "private" means not proactively announced, NOT hidden. Retrieval stays
    unfiltered per decision #20 and nothing here excludes it."""
    stored = writing.store(STORY, store)

    chunks = db.get_artifact_chunks(stored.artifact_id)
    assert chunks and stored.indexed
    assert chunks[0]["source_type"] == "creative_writing"
    assert chunks[0]["source_trust"] == "firsthand"


# --- the tool's contract -----------------------------------------------------


def test_text_is_required_and_title_is_not():
    properties = creative_write.CREATIVE_WRITE.parameters["properties"]

    assert set(properties) == {"text", "title"}
    assert creative_write.CREATIVE_WRITE.parameters["required"] == ["text"]


def test_the_description_says_it_does_not_write_anything():
    """It keeps writing; it does not produce it. A description implying otherwise
    would invite the model to call it expecting a story back."""
    assert "does not write anything for you" in (
        creative_write.CREATIVE_WRITE.description
    )


def test_the_timeout_is_the_default_because_nothing_inside_bounds_itself():
    """The criterion memory_search's 45 and web_fetch's 25 were each derived
    against. Here there is no inner bound to sit above — no model call, no network."""
    assert creative_write.TIMEOUT_SECONDS == config.get("tools", "default_timeout_seconds")


def test_it_declares_attribution_and_the_model_cannot_supply_it():
    tool = creative_write.CREATIVE_WRITE

    assert tool.takes_attribution is True
    assert "attribution" not in tool.parameters["properties"]


def test_the_result_says_it_is_kept_not_published(store):
    result = creative_write.write_creatively(STORY, context(store), title="The Kettle")

    assert "Saved." in result and "The Kettle" in result
    assert "kept, not published" in result
    row = db.list_artifacts(store)[0]
    assert row["id"] in result and row["storage_path"] in result


# --- storage -----------------------------------------------------------------


def test_it_lands_in_the_writing_subdirectory(store):
    stored = writing.store(STORY, store)

    assert stored.absolute_path.is_file()
    assert config.workspace_dir() / "writing" in stored.absolute_path.parents
    assert config.artifact_dir() not in stored.absolute_path.parents
    assert kinds.root_for("creative_writing") == config.workspace_dir() / "writing"


def test_the_file_and_the_indexed_text_are_the_same_content(store):
    """Unlike an image, where the indexed text is the prompt, and unlike a PDF, where
    it is what could be read out. Here the entity wrote it, so `extracted` is the
    honest status."""
    stored = writing.store(STORY, store)
    row = db.get_artifact(stored.artifact_id)

    assert row["extraction_status"] == "extracted"
    assert row["extracted_text"] == STORY
    assert stored.absolute_path.read_text(encoding="utf-8") == STORY
    assert row["content_type"] == "text/markdown"


def test_a_missing_title_is_derived_from_the_opening_words(store):
    """Deliberately dumb: the opening words, not a summary. Generating a title would
    mean a second model call producing a paraphrase presented as the work's own."""
    stored = writing.store(STORY, store)

    assert stored.title.startswith("The kettle had been")
    assert len(stored.title.split()) <= writing.DERIVED_TITLE_WORDS


def test_a_supplied_title_is_used_and_sanitised_into_the_filename(store):
    stored = writing.store(STORY, store, title="A Kettle / Two: Notes!")

    assert stored.title == "A Kettle / Two: Notes!"
    assert stored.filename.startswith("a-kettle-two-notes")
    assert stored.filename.endswith(".md")
    assert all(c.isalnum() or c in "-." for c in stored.filename)


def test_the_path_comes_from_the_id_not_the_title(store):
    stored = writing.store(STORY, store, title="../../etc/passwd")

    assert stored.storage_path == f"{stored.artifact_id[:2]}/{stored.artifact_id}"
    assert ".." not in str(stored.absolute_path)
    assert "passwd" not in stored.storage_path


def test_unicode_survives_the_round_trip(store):
    piece = "Il pleut — une bruine tiède. 雨が降る。\n\nAnd then nothing."

    stored = writing.store(piece, store)

    assert stored.absolute_path.read_text(encoding="utf-8") == piece
    assert db.get_artifact(stored.artifact_id)["extracted_text"] == piece
    assert stored.characters == len(piece)


def test_an_empty_piece_is_refused(store):
    for bad in ("", "   ", "\n\n"):
        with pytest.raises(ValueError, match="empty piece"):
            writing.store(bad, store)


def test_a_piece_over_the_embedding_ceiling_is_refused(store):
    """Reusing ingestion's ceiling deliberately: what it bounds is the same work —
    characters to split, pack and embed — and a second constant would drift."""
    limit = config.ingestion_max_extracted_chars()

    with pytest.raises(ValueError, match="ceiling"):
        writing.store("x" * (limit + 1), store)


def test_the_text_is_stripped_but_not_otherwise_altered(store):
    stored = writing.store(f"\n\n  {STORY}  \n\n", store)

    assert stored.absolute_path.read_text(encoding="utf-8") == STORY


def test_it_is_attributed_to_the_supplied_user_not_to_an_author(store):
    """`user_id` records whose record this belongs to, not who wrote it — the entity
    wrote it, which is what `source_trust = firsthand` says. The distinction matters
    more here than for an image, where the person at least asked for the thing."""
    jodie = db.create_user("Jodie", role="user")

    stored = writing.store(STORY, jodie)

    assert db.get_artifact(stored.artifact_id)["user_id"] == jodie
    assert db.get_artifact_chunks(stored.artifact_id)[0]["user_id"] == jodie
    assert db.list_artifacts(store) == []


# --- through dispatch --------------------------------------------------------


def test_dispatch_supplies_attribution_and_keeps_it_out_of_the_trace(store):
    registry.reset_default_registry()
    try:
        result = registry.default_registry().dispatch(
            "creative_write", {"text": STORY, "title": "The Kettle"},
            attribution=context(store),
        )
        assert result.outcome is ToolOutcome.OK
        assert db.list_artifacts(store)[0]["user_id"] == store
        assert "attribution" not in result.arguments
        assert "attribution" not in result.to_trace_entry()["arguments"]
    finally:
        registry.reset_default_registry()


def test_a_failed_save_does_not_take_the_turn_down(store, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(writing, "store", boom)
    registry.reset_default_registry()
    try:
        result = registry.default_registry().dispatch(
            "creative_write", {"text": STORY}, attribution=context(store),
        )
        assert result.outcome is ToolOutcome.TOOL_ERROR
        assert "disk full" in (result.error or "")
    finally:
        registry.reset_default_registry()


# --- retrieval, end to end ---------------------------------------------------


def test_a_stored_piece_is_findable_and_announces_itself_as_writing(store, monkeypatch):
    """Real FTS5, real RRF, real renderer; only the embedding substituted. The label
    matters for the same reason it does for an image: without it a retrieved piece
    reads as something that was said in conversation."""
    import hashlib

    from program.engine import prompt
    from program.memory import retrieval

    def deterministic(text, *a, **k):
        digest = hashlib.sha256(text.encode()).digest()
        return [(digest[i % len(digest)] / 255.0) for i in range(768)]

    monkeypatch.setattr(writing.indexing.ollama, "embed", deterministic)
    monkeypatch.setattr(retrieval.ollama, "embed", deterministic)

    stored = writing.store(STORY, store, title="The Kettle")

    result = retrieval.search("kettle hob tea")
    found = [r for r in result.results if r.chunk_id in stored.chunk_ids]

    assert found, "the stored writing was not retrievable"
    assert found[0].source_type == "creative_writing"
    assert "creative writing" in prompt.render_retrieved(result)
