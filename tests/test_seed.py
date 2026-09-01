"""The seed corpus: varied enough to be a real test of retrieval later."""

from __future__ import annotations

import hashlib

import pytest

from anam.engine import ollama
from anam.memory import chunking, db
from anam.ops import seed


@pytest.fixture
def deterministic_embeddings(monkeypatch):
    """Content-derived vectors, so the corpus can be built without Ollama.

    Not random: two chunks with the same text must embed identically, or a
    reconciliation test elsewhere would be meaningless. Similarity between
    different texts is not modelled — these tests are about the corpus's shape,
    not about retrieval quality, which is the Phase 1 checkpoint's job.
    """
    def fake_embed(text, **kwargs):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [(digest[i % len(digest)] / 255.0) for i in range(768)]

    monkeypatch.setattr(ollama, "embed", fake_embed)
    monkeypatch.setattr(chunking.ollama, "embed", fake_embed)


@pytest.fixture
def seeded(isolated_data_dir, deterministic_embeddings):
    return seed.seed()


# --- The corpus is declaratively coherent (no store needed) ------------------


def test_the_corpus_covers_more_than_one_user():
    users = {spec.user for spec in seed.CONVERSATIONS}
    assert len(users) > 1
    assert users <= {name for name, _ in seed.USERS}


def test_every_conversation_declares_a_topic_and_a_reason_for_existing():
    """A corpus entry with no stated purpose stops being maintainable."""
    for spec in seed.CONVERSATIONS:
        assert spec.topic, f"{spec.key} has no topic"
        assert spec.note, f"{spec.key} does not say what it is for"


def test_conversation_keys_are_unique():
    keys = [spec.key for spec in seed.CONVERSATIONS]
    assert len(keys) == len(set(keys))


def test_at_least_one_conversation_is_left_open():
    """The open trailing group is deliberately unindexed; the corpus shows that."""
    assert any(not spec.close for spec in seed.CONVERSATIONS)


def test_it_contains_both_short_and_long_conversations():
    sizes = [sum(len(c) for _, c in spec.turns) for spec in seed.CONVERSATIONS]
    assert min(sizes) < 500
    assert max(sizes) > 4_000


# --- What it actually produces in a store -----------------------------------


def test_it_creates_both_users_with_their_roles(seeded):
    lyle = db.get_user_by_name("Lyle")
    jodie = db.get_user_by_name("Jodie")
    assert lyle["role"] == "admin"
    assert jodie["role"] == "user"


def test_every_declared_conversation_reaches_the_store(seeded):
    assert len(seeded.conversations) == len(seed.CONVERSATIONS)
    for conversation_id in seeded.conversations.values():
        assert db.get_conversation(conversation_id) is not None


def test_messages_land_in_both_stores(seeded):
    archive_count, working_count = db.count_messages()
    assert archive_count == working_count == seeded.messages


def test_closed_conversations_are_chunked_and_the_open_one_is_not(seeded):
    for spec in seed.CONVERSATIONS:
        conversation_id = seeded.conversations[spec.key]
        row = db.get_conversation(conversation_id)
        chunks = db.get_conversation_chunks(conversation_id)
        if spec.close:
            assert row["chunked"] == 1, f"{spec.key} was not chunked"
            assert chunks, f"{spec.key} produced no chunks"
        else:
            assert row["ended_at"] is None
            assert row["chunked"] == 0
            assert not chunks, (
                f"{spec.key} is open; its trailing group must stay unindexed"
            )


def test_the_corpus_exercises_sub_chunk_splitting(seeded):
    """A message over the embedding budget must actually split, not just be long.

    Siblings share ``first_message_id`` — that is how they are discoverable
    without a dedicated column — so a repeated value is the evidence.
    """
    assert seeded.split_conversations, (
        "no conversation split. The long paste is no longer over the "
        "embedding budget, so the splitter is not being exercised at all."
    )
    conversation_id = seeded.conversations[seeded.split_conversations[0]]
    rows = db.get_conversation_chunks(conversation_id)
    firsts = [r["first_message_id"] for r in rows]
    assert len(firsts) != len(set(firsts))


def test_the_corpus_exercises_multi_chunk_conversations(seeded):
    """Chunk ordering within a conversation is only real above one chunk."""
    multi = [
        key for key, cid in seeded.conversations.items()
        if len(db.get_conversation_chunks(cid)) > 1
    ]
    assert len(multi) >= 2, f"only {multi} produced more than one chunk"


def test_chunk_indices_are_contiguous_from_zero(seeded):
    for key, conversation_id in seeded.conversations.items():
        rows = db.get_conversation_chunks(conversation_id)
        if not rows:
            continue
        indices = [r["chunk_index"] for r in rows]
        assert indices == list(range(len(indices))), f"{key}: {indices}"


def test_both_users_own_chunks_so_attribution_can_be_filtered(seeded):
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT u.name, COUNT(*) AS n FROM chunks c "
            "JOIN users u ON u.id = c.user_id GROUP BY u.name"
        ).fetchall()
    by_user = {r["name"]: r["n"] for r in rows}
    assert by_user.get("Lyle", 0) > 0
    assert by_user.get("Jodie", 0) > 0


def test_it_contains_two_deliberately_adjacent_topics(seeded):
    """Retrieval that cannot separate near neighbours looks fine on distinct
    topics alone, so the corpus has to contain a hard pair."""
    espresso = db.get_conversation_chunks(seeded.conversations["espresso"])
    pourover = db.get_conversation_chunks(seeded.conversations["pourover"])
    assert espresso and pourover
    assert "coffee" in " ".join(r["text"] for r in pourover).lower()
    assert "espresso" in " ".join(r["text"] for r in espresso).lower()


def test_chunks_are_indexed_for_lexical_search(seeded):
    """The FTS index has to be populated, or half of hybrid retrieval is dark."""
    with db.connection() as conn:
        hits = conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?", ("espresso",)
        ).fetchone()[0]
    assert hits > 0


# --- Provenance is the provisional single vocabulary ------------------------


def test_every_chunk_carries_the_provisional_conversation_provenance(seeded):
    """Task 1.7 owns the vocabulary and has not landed.

    The corpus is single-provenance on purpose: inventing a second source_type
    to look more varied would be writing 1.7's vocabulary ahead of its design.
    """
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT source_type, source_trust FROM chunks"
        ).fetchall()
    assert [tuple(r) for r in rows] == [("conversation", "firsthand")]
    assert chunking.SOURCE_TYPE == "conversation"


# --- Safety -----------------------------------------------------------------


def test_seeding_twice_is_refused_by_default(seeded):
    with pytest.raises(seed.SeedError, match="already holds"):
        seed.seed()


def test_the_refusal_changes_nothing(seeded):
    before = db.count_messages()
    with pytest.raises(seed.SeedError):
        seed.seed()
    assert db.count_messages() == before


def test_allow_existing_adds_rather_than_replacing(seeded):
    before_messages, _ = db.count_messages()
    before_conversations = len(seeded.conversations)

    again = seed.seed(allow_existing=True)

    after_messages, _ = db.count_messages()
    assert after_messages == before_messages * 2
    assert len(again.conversations) == before_conversations
    # The original conversations are still there.
    for conversation_id in seeded.conversations.values():
        assert db.get_conversation(conversation_id) is not None


def test_reseeding_reuses_existing_users_rather_than_duplicating_them(seeded):
    seed.seed(allow_existing=True)
    names = [u["name"] for u in db.list_users()]
    assert sorted(names) == ["Jodie", "Lyle"]


def test_there_is_no_wipe_or_reset_surface():
    """Seeding is additive. A reset here would be a second wipe implementation.

    Go-live wipe tooling is its own Tier 3 task and does not belong in a
    development-fixture module.
    """
    surface = {n for n in dir(seed) if not n.startswith("_")}
    for forbidden in ("wipe", "reset", "clear", "drop", "truncate", "delete"):
        assert forbidden not in surface
