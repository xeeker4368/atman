"""ChromaVectorStore and reconciliation.

**These run against a real ChromaDB instance**, on disk, in a temporary
directory — not a mock. The store is small and local, so there is no reason to
fake it, and the reference build's lesson is that an integration whose tests all
pass can still have never worked against the real thing.

The embedding model is faked in the reconciliation tests, because those are
about *which* chunks get repaired rather than about embedding. One test at the
end runs the whole path with the real model and the real store.
"""

from __future__ import annotations

import pytest

from program.engine import ollama
from program.memory import chunking, db, reconcile, vectors

live_only = pytest.mark.skipif(
    not ollama.is_available(),
    reason="Ollama is not reachable; live-embedding tests skipped",
)


@pytest.fixture
def store(isolated_data_dir):
    """A real ChromaVectorStore in a temporary directory."""
    return vectors.ChromaVectorStore(vectors.chroma_path())


@pytest.fixture
def fake_embed(monkeypatch):
    """Deterministic 768-wide vectors, distinct per text."""
    calls: list[str] = []

    def embed(text, **kwargs):
        calls.append(text)
        seed = (hash(text) % 1000) / 1000.0
        return [seed] * 768

    monkeypatch.setattr(chunking.ollama, "embed", embed)
    monkeypatch.setattr(reconcile.ollama, "embed", embed)
    return calls


# ---------------------------------------------------------------------------
# ChromaVectorStore against a real collection
# ---------------------------------------------------------------------------


def test_declares_that_it_indexes_vectors(store):
    assert store.indexes_vectors is True
    assert isinstance(store, vectors.VectorStore)


def test_upsert_then_has(store):
    assert store.has("c1") is False
    store.upsert("c1", [0.1] * 768, {"conversation_id": "conv1"})
    assert store.has("c1") is True


def test_delete_removes_it(store):
    store.upsert("c1", [0.1] * 768, {})
    store.delete("c1")
    assert store.has("c1") is False


def test_deleting_an_absent_id_is_not_an_error(store):
    store.delete("never-existed")


def test_upsert_replaces_rather_than_duplicating(store):
    store.upsert("c1", [0.1] * 768, {"v": 1})
    store.upsert("c1", [0.2] * 768, {"v": 2})
    assert store.count() == 1


def test_round_trip_retrieval_finds_the_nearest_vector(store):
    """The point of the store: a query vector comes back with its match."""
    store.upsert("near", [0.9] * 768, {"conversation_id": "a"})
    store.upsert("far", [-0.9] * 768, {"conversation_id": "b"})

    results = store.query([0.9] * 768, n_results=2)
    assert results["ids"][0][0] == "near"


def test_metadata_round_trips(store):
    store.upsert("c1", [0.1] * 768, {"conversation_id": "conv1", "chunk_index": 3})
    got = store._collection.get(ids=["c1"], include=["metadatas"])
    assert got["metadatas"][0]["conversation_id"] == "conv1"
    assert got["metadatas"][0]["chunk_index"] == 3


def test_none_metadata_values_are_dropped_not_sent(store):
    """Chroma rejects None values; absence is not a value to index on."""
    store.upsert("c1", [0.1] * 768, {"conversation_id": "conv1", "user_id": None})
    got = store._collection.get(ids=["c1"], include=["metadatas"])
    assert "user_id" not in got["metadatas"][0]


def test_persists_across_client_instances(store, isolated_data_dir):
    store.upsert("c1", [0.1] * 768, {})
    reopened = vectors.ChromaVectorStore(vectors.chroma_path())
    assert reopened.has("c1") is True


# ---------------------------------------------------------------------------
# Dimension
# ---------------------------------------------------------------------------


def test_wrong_dimension_is_rejected_before_chroma_sees_it(store):
    """Chroma infers a collection's width from the first vector it receives.

    So a wrong-width vector arriving first would silently define the collection
    wrongly and reject every correct vector afterwards. The guard here means the
    collection can only ever be defined by a vector of the configured width.
    """
    with pytest.raises(vectors.VectorDimensionError) as exc:
        store.upsert("c1", [0.1] * 512, {})
    message = str(exc.value)
    assert "512" in message
    assert "768" in message
    assert "expected_dimension" in message
    assert store.count() == 0  # nothing was written


def test_chroma_itself_also_rejects_a_mismatch(store):
    """Belt and braces: even bypassing our guard, the store does not accept it.

    Verified directly against the library rather than assumed — Chroma raises
    rather than truncating or silently dropping the vector.
    """
    import chromadb.errors

    store.upsert("c1", [0.1] * 768, {})
    with pytest.raises(chromadb.errors.InvalidArgumentError) as exc:
        store._collection.upsert(ids=["c2"], embeddings=[[0.1] * 512])
    assert "768" in str(exc.value)


def test_guard_reads_the_configured_dimension_at_call_time(store, monkeypatch):
    monkeypatch.setattr(vectors.config, "expected_embedding_dimension", lambda: 4)
    store.upsert("c1", [0.1] * 4, {})  # now accepted
    assert store.has("c1")


# ---------------------------------------------------------------------------
# Store selection
# ---------------------------------------------------------------------------


def test_default_store_is_chroma_not_null(isolated_data_dir):
    vectors.reset_vector_store()
    assert isinstance(vectors.get_vector_store(), vectors.ChromaVectorStore)
    assert vectors.get_vector_store().indexes_vectors is True


def test_stores_are_cached_per_path(isolated_data_dir):
    vectors.reset_vector_store()
    assert vectors.get_vector_store() is vectors.get_vector_store()


def test_override_wins_and_can_be_cleared(isolated_data_dir):
    null = vectors.NullVectorStore()
    vectors.set_vector_store(null)
    assert vectors.get_vector_store() is null
    vectors.set_vector_store(None)
    assert isinstance(vectors.get_vector_store(), vectors.ChromaVectorStore)


def test_null_store_still_available_and_holds_nothing():
    null = vectors.NullVectorStore()
    null.upsert("c1", [0.1] * 768, {})
    assert null.has("c1") is False
    assert null.indexes_vectors is False


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_with_null_store(isolated_data_dir, fake_embed):
    """A conversation chunked while the null store was active.

    Reproduces the exact backlog this task inherits: rows written, no vectors.

    Uses ``set_vector_store`` rather than monkeypatch on purpose. ``monkeypatch``
    is shared across every fixture in a test, so ``monkeypatch.undo()`` here
    would also revert the ``fake_embed`` patches — which it silently did in the
    first version of this file, sending reconciliation at the *real* embedding
    model and making the call counts meaningless.
    """
    db.init_databases()
    vectors.set_vector_store(vectors.NullVectorStore())
    try:
        user_id = db.create_user("Lyle", role="admin")
        conversation_id = db.start_conversation(user_id)
        for n in range(3):
            db.save_message(
                conversation_id, user_id, "user", f"question {n} " + "alpha " * 230
            )
            db.save_message(
                conversation_id, user_id, "assistant", f"answer {n} " + "beta " * 280
            )
        chunking.finalise_conversation(conversation_id)
    finally:
        vectors.set_vector_store(None)
    return conversation_id


def test_finds_chunks_written_without_vectors(seeded_with_null_store, store, fake_embed):
    missing = reconcile.find_chunks_without_vectors(store=store)
    assert len(missing) == len(db.get_conversation_chunks(seeded_with_null_store))


def test_reconcile_repairs_every_missing_vector(seeded_with_null_store, store, fake_embed):
    before = store.count()
    result = reconcile.reconcile_vectors(store=store)

    assert before == 0
    assert result.missing == result.repaired > 0
    assert store.count() == result.repaired
    for chunk_id in result.repaired_ids:
        assert store.has(chunk_id)


def test_reconcile_is_idempotent(seeded_with_null_store, store, fake_embed):
    reconcile.reconcile_vectors(store=store)
    embeds_after_first = len(fake_embed)

    second = reconcile.reconcile_vectors(store=store)

    assert second.missing == 0
    assert second.repaired == 0
    assert len(fake_embed) == embeds_after_first  # nothing re-embedded


def test_dry_run_reports_without_changing_anything(seeded_with_null_store, store, fake_embed):
    result = reconcile.reconcile_vectors(store=store, dry_run=True)
    assert result.missing > 0
    assert result.repaired == 0
    assert result.dry_run is True
    assert store.count() == 0


def test_limit_repairs_only_that_many(seeded_with_null_store, store, fake_embed):
    result = reconcile.reconcile_vectors(store=store, limit=1)
    assert result.repaired == 1
    assert store.count() == 1
    # The rest are still outstanding and a later pass picks them up.
    assert reconcile.reconcile_vectors(store=store, dry_run=True).missing == result.missing - 1


def test_reconcile_leaves_already_present_vectors_alone(seeded_with_null_store, store, fake_embed):
    reconcile.reconcile_vectors(store=store, limit=1)
    calls_before = len(fake_embed)

    result = reconcile.reconcile_vectors(store=store)

    assert result.already_present == 1
    assert len(fake_embed) - calls_before == result.repaired


def test_embedding_failure_aborts_rather_than_reporting_partial_success(
    seeded_with_null_store, store, monkeypatch
):
    """Swallowing here would report a repair that did not happen."""

    def boom(text, **kwargs):
        raise ollama.OllamaUnreachable("down")

    monkeypatch.setattr(reconcile.ollama, "embed", boom)
    with pytest.raises(ollama.OllamaUnreachable):
        reconcile.reconcile_vectors(store=store)
    assert store.count() == 0


# ---------------------------------------------------------------------------
# End to end with the real store and the real model
# ---------------------------------------------------------------------------


@live_only
def test_live_chunking_writes_real_vectors_end_to_end(isolated_data_dir):
    """The whole path: real chunking, real embeddings, real ChromaDB.

    The unit tests above use a recording fake for the store or a fake for the
    model. This one uses neither, so it is the test that proves the seam is
    connected to something that actually works.
    """
    vectors.reset_vector_store()
    db.init_databases()

    user_id = db.create_user("Lyle", role="admin")
    conversation_id = db.start_conversation(user_id)
    db.save_message(
        conversation_id, user_id, "user", "The harbour was full of small boats. " * 60
    )
    db.save_message(
        conversation_id, user_id, "assistant", "The tide moved against the quay. " * 60
    )
    db.save_message(conversation_id, user_id, "user", "And afterwards?")
    db.save_message(conversation_id, user_id, "assistant", "It went quiet.")

    result = chunking.finalise_conversation(conversation_id)
    store = vectors.get_vector_store()

    assert result.chunks_written > 0
    assert result.vectors_indexed == result.chunks_written
    assert store.count() == result.chunks_written
    for chunk_id in result.chunk_ids:
        assert store.has(chunk_id)

    # A real semantic query returns a real chunk id.
    hits = store.query(ollama.embed("boats in the harbour"), n_results=1)
    assert hits["ids"][0][0] in result.chunk_ids

    # And reconciliation agrees there is nothing left to repair.
    assert reconcile.reconcile_vectors(dry_run=True).missing == 0
