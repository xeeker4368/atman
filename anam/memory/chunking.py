"""Conversation messages into retrievable chunks.

Two public functions, and deliberately only two:

* ``checkpoint_conversation()`` — called after a completed assistant turn.
  Writes any chunk that has *sealed* since last time.
* ``finalise_conversation()``   — called at close. Seals and writes the
  trailing group, then marks the conversation chunked.

Everything else here is private. The reference build accumulated a third entry
point that nothing ever called and that read as live behaviour to anyone
skimming; ``test_public_surface_is_exactly_two_functions`` makes adding one
fail.

Sealed and open
---------------
Messages pack left-to-right into groups. All but the last group are **sealed**:
their boundary is final and their content can never change again. The last is
**open**, still accumulating.

Only sealed groups are embedded and written. That single distinction is what
removes the reference build's worst behaviour, where the trailing group was
re-embedded on every turn — up to five times for the same text.

The property this rests on is that **greedy left-to-right packing is stable
under append**: appending messages cannot change how earlier messages grouped,
because the packer's decision at group *i* depends only on messages before it.
If the boundary rule is ever changed, re-check that property first — a rule that
looked backward from the end, or balanced sizes globally, would destroy it.

The open tail is not indexed
----------------------------
It is the most recent few turns of a live conversation, which task 1.10's
history windowing already puts verbatim into the prompt. Embedding it
repeatedly buys retrievability of text that is already in front of the model.

The real cost is that another conversation cannot retrieve this one's last few
turns while it is still open. That window is bounded by the chunk target and
closes when the tail seals or the conversation is closed — which is why
idle-close exists as its own task rather than being assumed.

Ordering and failure
--------------------
Per chunk: embed, then insert the row, then hand the vector to the store.
Embedding first is what makes failure harmless — a raise leaves nothing written
at all. Nothing is ever deleted, so a failure cannot remove content that was
retrievable a moment ago, which is how the reference build lost chunks to
transient errors.

No exception is caught in the write loop. A failure aborts the run and
propagates; already-written chunks stand and the rest are retried by the next
checkpoint or a recovery pass.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass, field

from anam import config
from anam.engine import ollama
from anam.memory import db, splitting, vectors

# Conversation chunks are firsthand: the entity was present for them. Task 1.7
# owns the vocabulary and validates these; see the design's D4.
SOURCE_TYPE = "conversation"
SOURCE_TRUST = "firsthand"

# One in-process lock per conversation, so a checkpoint and a close cannot both
# embed the same group. Not a distributed guarantee — the unique index on
# (conversation_id, chunk_index) is the actual arbiter, and this only stops the
# wasted work. Single-process backend, so it is sufficient in practice.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _conversation_lock(conversation_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(conversation_id, threading.Lock())


class ChunkIntegrityError(RuntimeError):
    """A stored chunk disagrees with what the packer just produced for it.

    Means the boundary rule is not stable, or a constant changed underneath an
    existing store. Raised rather than resolved by overwriting: overwriting would
    erase the evidence and re-embed content that was already indexed, which is
    how this class of problem stays invisible.
    """


@dataclass
class ChunkingResult:
    """What a run did.

    ``vectors_indexed`` is reported separately from ``chunks_written`` on
    purpose: under the null vector store a chunk is written and lexically
    retrievable but has no vector, and collapsing those into one number would
    make a partial success read as a complete one.
    """

    chunks_written: int = 0
    chunks_skipped: int = 0
    vectors_indexed: int = 0
    marked_chunked: bool = False
    chunk_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


def _to_turns(messages: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    """Group messages into turns.

    A turn is one or more consecutive user messages followed by an assistant
    message. A trailing run with no assistant reply is an incomplete turn and is
    returned as its own group — callers decide whether to include it.
    """
    turns: list[list[sqlite3.Row]] = []
    current: list[sqlite3.Row] = []
    for message in messages:
        current.append(message)
        if message["role"] == "assistant":
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def _is_complete(turn: list[sqlite3.Row]) -> bool:
    return bool(turn) and turn[-1]["role"] == "assistant"


def _format_line(message: sqlite3.Row, user_name: str) -> str:
    """One message as a transcript line.

    No timestamp. The reference build formatted a local date into every line,
    which put date strings into both the embedding and the lexical index — a
    query mentioning a month matched every chunk from that month. Timestamps live
    on the row and are rendered when a chunk is presented (task 1.8). Structured
    time filtering is task 1.5's, recorded in BUILD_PLAN's Phase 1 notes.
    """
    speaker = user_name if message["role"] == "user" else "assistant"
    return f"{speaker}: {message['content']}"


def _turn_text(turn: list[sqlite3.Row], user_name: str) -> str:
    return "\n".join(_format_line(m, user_name) for m in turn)


def _pack(
    turns: list[list[sqlite3.Row]],
    user_name: str,
    target_chars: int,
    max_turns: int,
) -> list[list[list[sqlite3.Row]]]:
    """Pack turns into groups, greedily, left to right.

    Stops adding to a group when the next turn would push it past
    ``target_chars`` or when it already holds ``max_turns``. A single turn larger
    than the target becomes its own group — the splitter deals with it later if
    it also exceeds the embedding ceiling.
    """
    groups: list[list[list[sqlite3.Row]]] = []
    current: list[list[sqlite3.Row]] = []
    current_chars = 0

    for turn in turns:
        size = len(_turn_text(turn, user_name))
        if current and (current_chars + size > target_chars or len(current) >= max_turns):
            groups.append(current)
            current, current_chars = [], 0
        current.append(turn)
        current_chars += size

    if current:
        groups.append(current)
    return groups


def _group_messages(group: list[list[sqlite3.Row]]) -> list[sqlite3.Row]:
    return [message for turn in group for message in turn]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _sub_units(
    messages: list[sqlite3.Row], user_name: str, budget: int
) -> list[tuple[str, str, str]]:
    """Split a group's messages into embeddable units.

    Returns ``(text, first_message_id, last_message_id)`` per unit. A group
    within budget yields exactly one unit covering the whole group.

    Two shapes of split, both keeping first/last honest: a multi-message group
    splits at message boundaries, so each unit names the messages it actually
    contains; a single over-long message hard-splits, and every piece names that
    one message, because that is genuinely where all of them came from.
    """
    lines = [(m["id"], _format_line(m, user_name)) for m in messages]
    whole = "\n".join(line for _, line in lines)
    if len(whole) <= budget:
        return [(whole, messages[0]["id"], messages[-1]["id"])]

    units: list[tuple[str, str, str]] = []
    for run in splitting.pack_lines(lines, budget):
        run_text = "\n".join(line for _, line in run)
        if len(run_text) <= budget:
            units.append((run_text, run[0][0], run[-1][0]))
            continue
        # A single line over budget: split it, every piece attributed to it.
        message_id = run[0][0]
        for piece in splitting.split_text(run_text, budget):
            units.append((piece, message_id, message_id))
    return units


def _write_group(
    conversation_id: str,
    user_id: str,
    chunk_index: int,
    group: list[list[sqlite3.Row]],
    user_name: str,
    budget: int,
    result: ChunkingResult,
) -> int:
    """Write one sealed group, splitting if needed. Returns indices consumed.

    A group that splits into three pieces consumes three consecutive indices —
    ``chunk_index`` is the ordinal of a retrievable unit, not of a turn group.
    """
    store = vectors.get_vector_store()
    messages = _group_messages(group)
    units = _sub_units(messages, user_name, budget)

    for offset, (text, first_id, last_id) in enumerate(units):
        index = chunk_index + offset
        digest = _sha(text)

        existing = db.get_chunk_by_index(conversation_id, index)
        if existing is not None:
            if existing["text_sha256"] != digest:
                raise ChunkIntegrityError(
                    f"chunk {conversation_id}/{index} is stored with a different "
                    f"text than the packer just produced. The boundary rule is "
                    f"not stable, or a chunking constant changed under an "
                    f"existing store. Stored sha {existing['text_sha256'][:12]}, "
                    f"computed {digest[:12]}."
                )
            result.chunks_skipped += 1
            continue

        # Embed BEFORE writing anything. A raise here leaves the store untouched.
        vector = ollama.embed(text)

        chunk_id = db.new_id()
        try:
            db.insert_chunk(
                chunk_id=chunk_id,
                conversation_id=conversation_id,
                user_id=user_id,
                text=text,
                source_type=SOURCE_TYPE,
                source_trust=SOURCE_TRUST,
                text_sha256=digest,
                chunk_index=index,
                first_message_id=first_id,
                last_message_id=last_id,
            )
        except sqlite3.IntegrityError:
            # Another writer won the race for this index. Confirm it wrote the
            # same thing, then treat it as done rather than as a failure.
            winner = db.get_chunk_by_index(conversation_id, index)
            if winner is None or winner["text_sha256"] != digest:
                raise
            result.chunks_skipped += 1
            continue

        store.upsert(
            chunk_id,
            vector,
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "chunk_index": index,
                "source_type": SOURCE_TYPE,
                "source_trust": SOURCE_TRUST,
            },
        )
        result.chunks_written += 1
        result.chunk_ids.append(chunk_id)
        if store.indexes_vectors:
            result.vectors_indexed += 1

    return len(units)


def _run(conversation_id: str, *, include_open_tail: bool) -> ChunkingResult:
    """Shared body of both public entry points."""
    conversation = db.get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"no such conversation: {conversation_id}")

    user_id = conversation["user_id"]
    user = db.get_user(user_id)
    user_name = user["name"] if user else "user"

    messages = db.get_conversation_messages(conversation_id)
    result = ChunkingResult()
    if not messages:
        return result

    turns = _to_turns(messages)
    if not include_open_tail and turns and not _is_complete(turns[-1]):
        # An incomplete turn is never sealed mid-conversation: indexing half an
        # exchange produces a chunk retrieval can surface as a whole thought.
        turns = turns[:-1]
    if not turns:
        return result

    groups = _pack(
        turns,
        user_name,
        config.chunk_target_chars(),
        config.chunk_max_turns(),
    )
    if not include_open_tail:
        groups = groups[:-1]  # the trailing group is still open

    budget = config.embedding_max_input_chars()
    index = 0
    for group in groups:
        index += _write_group(
            conversation_id, user_id, index, group, user_name, budget, result
        )
    return result


# ---------------------------------------------------------------------------
# Public surface — exactly two functions
# ---------------------------------------------------------------------------


def checkpoint_conversation(conversation_id: str) -> ChunkingResult:
    """Write any chunks that have sealed since the last call.

    Called after a completed assistant turn. Never touches the open trailing
    group and never marks the conversation chunked. Cheap when nothing sealed:
    one packing pass and one indexed lookup.
    """
    with _conversation_lock(conversation_id):
        return _run(conversation_id, include_open_tail=False)


def finalise_conversation(conversation_id: str) -> ChunkingResult:
    """Chunk a conversation in full and mark it chunked.

    Called at close. Seals and writes the trailing group, including a final
    incomplete turn — a question the entity never answered still happened, and
    dropping it would lose it from memory entirely.

    Marks ``chunked`` only after every group has a row. If this aborts part way,
    the conversation stays unmarked and a recovery pass can resume; because
    writes are additive, resuming costs only what was missing.
    """
    with _conversation_lock(conversation_id):
        result = _run(conversation_id, include_open_tail=True)
        db.mark_conversation_chunked(conversation_id)
        result.marked_chunked = True
        return result
