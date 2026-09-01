"""Chunking pipeline.

Embedding is faked here rather than called for real: these tests are about
*when and how often* embedding happens, and counting calls is the entire point
of several of them. The live embedding path is covered by
``tests/test_ollama.py`` against the real instance.
"""

from __future__ import annotations

import itertools
import sqlite3

import pytest

from program.engine import ollama
from program.memory import chunking, db, vectors


class RecordingStore:
    """A vector store that keeps what it was given, so tests can assert on it."""

    indexes_vectors = True

    def __init__(self):
        self.vectors: dict[str, list[float]] = {}
        self.metadata: dict[str, dict] = {}

    def upsert(self, chunk_id, vector, metadata):
        self.vectors[chunk_id] = vector
        self.metadata[chunk_id] = metadata

    def delete(self, chunk_id):
        self.vectors.pop(chunk_id, None)

    def has(self, chunk_id):
        return chunk_id in self.vectors


@pytest.fixture
def embed_calls(monkeypatch):
    """Replace embedding with a counter. Returns the list of embedded texts."""
    calls: list[str] = []

    def fake_embed(text, **kwargs):
        calls.append(text)
        return [0.1] * 768

    monkeypatch.setattr(ollama, "embed", fake_embed)
    monkeypatch.setattr(chunking.ollama, "embed", fake_embed)
    return calls


@pytest.fixture
def store(monkeypatch):
    recording = RecordingStore()
    monkeypatch.setattr(vectors, "get_vector_store", lambda: recording)
    monkeypatch.setattr(chunking.vectors, "get_vector_store", lambda: recording)
    return recording


@pytest.fixture
def convo(isolated_data_dir):
    db.init_databases()
    user_id = db.create_user("Lyle", role="admin")
    conversation_id = db.start_conversation(user_id)
    return conversation_id, user_id


# Every helper emits distinct text. Identical content across turns would make
# "was anything embedded twice?" unanswerable — the first version of these tests
# generated the same string every call and the uniqueness assertion failed for
# that reason rather than for a real double-embed.
_counter = itertools.count()


def add_turn(conversation_id, user_id, user_text=None, assistant_text=None):
    n = next(_counter)
    db.save_message(conversation_id, user_id, "user", user_text or f"hello number {n}")
    db.save_message(
        conversation_id, user_id, "assistant", assistant_text or f"hi there {n}"
    )


def big_turn(conversation_id, user_id):
    """A turn large enough to seal a group on its own, small enough not to split.

    ~2800 characters: over the 2500 target, comfortably under the 5000 embedding
    budget, so it yields exactly one chunk.
    """
    n = next(_counter)
    db.save_message(conversation_id, user_id, "user", f"marker{n} " + "alpha " * 230)
    db.save_message(conversation_id, user_id, "assistant", f"reply{n} " + "beta " * 280)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_public_surface_is_exactly_two_functions():
    """The reference build grew a third entry point that nothing called.

    Pinned so adding one fails rather than quietly reading as live behaviour.
    """
    public = {
        name
        for name in dir(chunking)
        if not name.startswith("_") and callable(getattr(chunking, name))
    }
    entry_points = {n for n in public if n.endswith("_conversation")}
    assert entry_points == {"checkpoint_conversation", "finalise_conversation"}


# ---------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------


def test_checkpoint_writes_nothing_when_only_the_open_group_exists(convo, embed_calls, store):
    conversation_id, user_id = convo
    add_turn(conversation_id, user_id)

    result = chunking.checkpoint_conversation(conversation_id)

    assert result.chunks_written == 0
    assert embed_calls == []  # nothing embedded at all
    assert db.get_conversation_chunks(conversation_id) == []


def test_checkpoint_writes_a_group_once_it_seals(convo, embed_calls, store):
    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)  # fills the target on its own
    add_turn(conversation_id, user_id)  # forces the first group to seal

    result = chunking.checkpoint_conversation(conversation_id)

    assert result.chunks_written == 1
    assert len(embed_calls) == 1
    assert len(db.get_conversation_chunks(conversation_id)) == 1


def test_incomplete_trailing_turn_is_not_sealed_mid_conversation(convo, embed_calls, store):
    """Half an exchange must not become a chunk retrieval can surface."""
    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)
    db.save_message(conversation_id, user_id, "user", "a question with no answer yet")

    chunking.checkpoint_conversation(conversation_id)

    stored = " ".join(c["text"] for c in db.get_conversation_chunks(conversation_id))
    assert "no answer yet" not in stored


def test_finalise_includes_an_incomplete_trailing_turn(convo, embed_calls, store):
    """At close it did happen, so it must not vanish from memory."""
    conversation_id, user_id = convo
    add_turn(conversation_id, user_id)
    db.save_message(conversation_id, user_id, "user", "unanswered question")

    chunking.finalise_conversation(conversation_id)

    stored = " ".join(c["text"] for c in db.get_conversation_chunks(conversation_id))
    assert "unanswered question" in stored


# ---------------------------------------------------------------------------
# No double embedding — the headline property
# ---------------------------------------------------------------------------


def test_repeated_checkpoints_never_re_embed(convo, embed_calls, store):
    """The reference build re-embedded the tail on every turn, up to 5x.

    Here a checkpoint that finds nothing newly sealed does no embedding at all.
    """
    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)
    add_turn(conversation_id, user_id)

    chunking.checkpoint_conversation(conversation_id)
    first = len(embed_calls)
    for _ in range(5):
        chunking.checkpoint_conversation(conversation_id)

    assert len(embed_calls) == first


def test_checkpoint_then_finalise_embeds_each_chunk_exactly_once(convo, embed_calls, store):
    conversation_id, user_id = convo
    for _ in range(4):
        big_turn(conversation_id, user_id)
        chunking.checkpoint_conversation(conversation_id)
    chunking.finalise_conversation(conversation_id)

    # Distinct text per turn, so a repeat here would be a genuine double-embed.
    assert len(embed_calls) == len(set(embed_calls))
    assert len(embed_calls) == len(db.get_conversation_chunks(conversation_id))


def test_checkpointing_does_not_change_total_work(convo, embed_calls, store, isolated_data_dir):
    """A heavily checkpointed conversation costs the same as one closed cold."""
    conversation_id, user_id = convo
    for _ in range(3):
        big_turn(conversation_id, user_id)
        chunking.checkpoint_conversation(conversation_id)
    chunking.finalise_conversation(conversation_id)
    with_checkpoints = len(embed_calls)

    embed_calls.clear()
    other = db.start_conversation(user_id)
    for _ in range(3):
        big_turn(other, user_id)
    chunking.finalise_conversation(other)

    assert len(embed_calls) == with_checkpoints


def test_finalise_skips_chunks_a_checkpoint_already_wrote(convo, embed_calls, store):
    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)
    add_turn(conversation_id, user_id)
    chunking.checkpoint_conversation(conversation_id)
    before = len(embed_calls)

    result = chunking.finalise_conversation(conversation_id)

    assert result.chunks_skipped >= 1
    assert len(embed_calls) > before  # only the tail was new


# ---------------------------------------------------------------------------
# conversations.chunked
# ---------------------------------------------------------------------------


def test_checkpoint_never_marks_chunked(convo, embed_calls, store):
    conversation_id, user_id = convo
    for _ in range(3):
        big_turn(conversation_id, user_id)
    result = chunking.checkpoint_conversation(conversation_id)

    assert result.marked_chunked is False
    assert db.get_conversation(conversation_id)["chunked"] == 0


def test_finalise_marks_chunked(convo, embed_calls, store):
    conversation_id, user_id = convo
    add_turn(conversation_id, user_id)
    result = chunking.finalise_conversation(conversation_id)

    assert result.marked_chunked is True
    assert db.get_conversation(conversation_id)["chunked"] == 1


# ---------------------------------------------------------------------------
# Failure leaves nothing behind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ollama.EmbeddingDimensionError("wrong width"),
        ollama.OllamaUnreachable("not running"),
        ollama.OllamaTimeout("too slow"),
    ],
)
def test_embedding_failure_writes_nothing_and_propagates(convo, store, monkeypatch, error):
    """Embedding happens before any write, so a failure leaves the store clean.

    The reference build deleted before writing, so a transient failure here
    destroyed content that had been retrievable a moment earlier.
    """
    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)
    add_turn(conversation_id, user_id)

    def boom(text, **kwargs):
        raise error

    monkeypatch.setattr(chunking.ollama, "embed", boom)

    with pytest.raises(type(error)):
        chunking.checkpoint_conversation(conversation_id)

    assert db.get_conversation_chunks(conversation_id) == []
    assert store.vectors == {}
    assert db.get_conversation(conversation_id)["chunked"] == 0


def test_failure_partway_keeps_earlier_chunks(convo, embed_calls, store, monkeypatch):
    """Already-written chunks stand; the run resumes from where it stopped."""
    conversation_id, user_id = convo
    for _ in range(4):
        big_turn(conversation_id, user_id)
    chunking.checkpoint_conversation(conversation_id)
    written_before = len(db.get_conversation_chunks(conversation_id))
    assert written_before >= 2

    add_turn(conversation_id, user_id)
    big_turn(conversation_id, user_id)

    def boom(text, **kwargs):
        raise ollama.OllamaUnreachable("down")

    monkeypatch.setattr(chunking.ollama, "embed", boom)
    with pytest.raises(ollama.OllamaUnreachable):
        chunking.finalise_conversation(conversation_id)

    assert len(db.get_conversation_chunks(conversation_id)) == written_before


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------


def test_stored_text_mismatch_raises_rather_than_overwriting(convo, embed_calls, store):
    """text_sha256 exists for exactly this check.

    Overwriting would erase the evidence that the boundary rule is unstable and
    silently re-embed content that was already indexed.
    """
    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)
    add_turn(conversation_id, user_id)
    chunking.checkpoint_conversation(conversation_id)

    with db.transaction() as conn:
        conn.execute(
            "UPDATE chunks SET text_sha256 = 'tampered' WHERE conversation_id = ?",
            (conversation_id,),
        )

    with pytest.raises(chunking.ChunkIntegrityError):
        chunking.checkpoint_conversation(conversation_id)


def test_packing_is_stable_under_append(convo, embed_calls, store):
    """Appending messages must not change how earlier ones grouped.

    The whole no-double-embedding argument rests on this. If it breaks, the
    integrity check above starts firing instead — which is the intended failure.
    """
    conversation_id, user_id = convo
    for _ in range(3):
        big_turn(conversation_id, user_id)
    chunking.checkpoint_conversation(conversation_id)
    rows = db.get_conversation_chunks(conversation_id)
    first = [(c["chunk_index"], c["text_sha256"]) for c in rows]

    for _ in range(3):
        big_turn(conversation_id, user_id)
    chunking.checkpoint_conversation(conversation_id)
    rows = db.get_conversation_chunks(conversation_id)
    after = [(c["chunk_index"], c["text_sha256"]) for c in rows]

    assert after[: len(first)] == first


# ---------------------------------------------------------------------------
# Provenance, splitting, and the vector-store seam
# ---------------------------------------------------------------------------


def test_every_chunk_carries_provenance(convo, embed_calls, store):
    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)
    add_turn(conversation_id, user_id)
    chunking.checkpoint_conversation(conversation_id)

    for chunk in db.get_conversation_chunks(conversation_id):
        assert chunk["source_type"] == "conversation"
        assert chunk["source_trust"] == "firsthand"
        assert chunk["user_id"] == user_id


def test_chunk_text_carries_no_timestamp(convo, embed_calls, store):
    """Date strings in chunk text pollute both the vector and the lexical index."""
    conversation_id, user_id = convo
    add_turn(conversation_id, user_id)
    chunking.finalise_conversation(conversation_id)

    text = db.get_conversation_chunks(conversation_id)[0]["text"]
    assert "20" not in text.split(":")[0]  # no year in the speaker prefix
    assert "[" not in text


def test_oversized_turn_splits_into_consecutive_indices(convo, embed_calls, store):
    conversation_id, user_id = convo
    huge = "sentence. " * 900  # ~9000 chars, over the 5000 budget
    db.save_message(conversation_id, user_id, "user", huge)
    db.save_message(conversation_id, user_id, "assistant", "noted")
    chunking.finalise_conversation(conversation_id)

    chunks = db.get_conversation_chunks(conversation_id)
    assert len(chunks) > 1
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert len(chunk["text"]) <= 5000


def test_split_pieces_of_one_message_share_its_message_id(convo, embed_calls, store):
    """Siblings are discoverable by first_message_id — no extra column needed."""
    conversation_id, user_id = convo
    huge = "x" * 12000
    message_id = db.save_message(conversation_id, user_id, "user", huge)
    db.save_message(conversation_id, user_id, "assistant", "ok")
    chunking.finalise_conversation(conversation_id)

    chunks = db.get_conversation_chunks(conversation_id)
    pieces = [c for c in chunks if c["first_message_id"] == message_id]
    assert len(pieces) > 1
    for piece in pieces:
        assert piece["last_message_id"] == message_id


def test_vectors_reach_the_store_with_metadata(convo, embed_calls, store):
    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)
    add_turn(conversation_id, user_id)
    result = chunking.checkpoint_conversation(conversation_id)

    assert result.vectors_indexed == result.chunks_written
    for chunk_id in result.chunk_ids:
        assert store.has(chunk_id)
        assert store.metadata[chunk_id]["conversation_id"] == conversation_id


def test_null_store_reports_no_vectors_indexed(convo, embed_calls, monkeypatch):
    """Under the null store a chunk is written and lexically retrievable, and
    has no vector. That must be reported, not collapsed into a success count."""
    null = vectors.NullVectorStore()
    monkeypatch.setattr(chunking.vectors, "get_vector_store", lambda: null)

    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)
    add_turn(conversation_id, user_id)
    result = chunking.checkpoint_conversation(conversation_id)

    assert result.chunks_written >= 1
    assert result.vectors_indexed == 0
    # Search a term from the *sealed* group, not the dropped open tail.
    sealed_text = db.get_conversation_chunks(conversation_id)[0]["text"]
    marker = sealed_text.split()[1]
    with db.connection() as conn:
        hits = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (marker,)
        ).fetchall()
    assert hits  # lexically retrievable despite having no vector


def test_missing_conversation_raises(convo, embed_calls, store):
    with pytest.raises(ValueError):
        chunking.checkpoint_conversation("no-such-conversation")


def test_concurrent_write_of_the_same_index_is_resolved_not_duplicated(
    convo, embed_calls, store, monkeypatch
):
    """The unique index is the arbiter when two writers pass the existence check."""
    conversation_id, user_id = convo
    big_turn(conversation_id, user_id)
    add_turn(conversation_id, user_id)

    real_get = db.get_chunk_by_index
    calls = {"n": 0}

    def racy_get(cid, index):
        # Report "absent" the first time, so the writer proceeds to insert into
        # a row another writer has meanwhile created.
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_get(cid, index)

    chunking.finalise_conversation(conversation_id)
    monkeypatch.setattr(chunking.db, "get_chunk_by_index", racy_get)

    result = chunking.checkpoint_conversation(conversation_id)
    assert result.chunks_written == 0
    assert result.chunks_skipped >= 1
    assert isinstance(
        db.get_chunk_by_index(conversation_id, 0), sqlite3.Row
    )
