"""Retrieval resolving `supersedes` links. Task 3.5, design
`docs/RETRIEVAL_SUPERSESSION_DESIGN.md` R1–R12.

Real store, real chunking, real FTS5 and Chroma, real RRF — only the embedding is
substituted, following `tests/test_memory_search.py`. Nothing here is model-judged:
this task adds no classifier call, so its correctness is deterministic and needs no
frozen eval set of its own. That is worth saying rather than leaving a reader to
wonder whether a harness was skipped.
"""

from __future__ import annotations

import hashlib

import pytest

from program.engine import prompt
from program.memory import chunking, db, retrieval, supersession
from program.memory.supersession import CONTRADICTED, REPLACED


def _deterministic_embedding(text: str, *_args, **_kwargs):
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [(digest[i % len(digest)] / 255.0) for i in range(768)]


@pytest.fixture
def store(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(chunking.ollama, "embed", _deterministic_embedding)
    monkeypatch.setattr(retrieval.ollama, "embed", _deterministic_embedding)
    db.init_databases()
    return db.create_user("Lyle", role="admin")


def write(user_id, *turns):
    """A closed, fully chunked conversation. Returns (conversation_id, [ids])."""
    conversation_id = db.start_conversation(user_id)
    ids = [db.save_message(conversation_id, user_id, role, text) for role, text in turns]
    db.end_conversation(conversation_id)
    chunking.finalise_conversation(conversation_id)
    return conversation_id, ids


def link(superseding, superseded, replacement=REPLACED):
    return db.create_supersedes_link(superseding, superseded, replacement)


def chunk_ids_of(conversation_id):
    return [row["id"] for row in db.get_conversation_chunks(conversation_id)]


# --- resolution --------------------------------------------------------------


def test_a_correction_inside_a_chunk_is_resolved_to_the_chunk(store):
    conversation, ids = write(store, ("user", "The dentist is on Tuesday at 3."),
                              ("assistant", "Noted."))
    _later, correction = write(store, ("user", "Actually the dentist is Wednesday."))
    link(correction[0], ids[0])

    by_chunk, report = supersession.resolve_for_chunks(chunk_ids_of(conversation))

    [item] = next(iter(by_chunk.values()))
    assert item.superseded_message_id == ids[0]
    assert item.superseded_text == "The dentist is on Tuesday at 3."
    assert "Wednesday" in item.superseding_text
    assert item.replacement == REPLACED and item.depth == 1
    assert report.resolved and report.annotations == 1


def test_a_chunk_with_no_corrections_costs_one_query_and_returns_nothing(store, monkeypatch):
    conversation, _ = write(store, ("user", "The dentist is on Tuesday."))
    calls = {"n": 0}
    real = db.get_supersedes_for_chunks

    def counted(ids):
        calls["n"] += 1
        return real(ids)

    monkeypatch.setattr(db, "get_supersedes_for_chunks", counted)
    by_chunk, report = supersession.resolve_for_chunks(chunk_ids_of(conversation))

    assert by_chunk == {} and report.annotations == 0
    assert calls["n"] == 1, "the ordinary case must not walk chains it has no links for"


def test_no_chunk_ids_touches_the_database_at_all(monkeypatch):
    monkeypatch.setattr(db, "get_supersedes_for_chunks", lambda ids: pytest.fail("queried"))
    assert supersession.resolve_for_chunks([]) == ({}, supersession.SupersessionReport())


def test_a_document_chunk_is_skipped_rather_than_guarded(store):
    """Ingested files have NULL message-id columns, so the join drops them: a
    document has no messages to correct."""
    db.insert_chunk(
        chunk_id="doc-1", conversation_id=None, user_id=store, text="a manual",
        source_type="file", source_trust="secondhand", text_sha256="x",
        artifact_id=None,
    )
    by_chunk, report = supersession.resolve_for_chunks(["doc-1"])
    assert by_chunk == {} and report.resolved


# --- following the chain (R3) ------------------------------------------------


def test_a_chain_resolves_to_its_tip_not_its_first_link(store):
    """`A <- B <- C`: C is the current statement, and surfacing B would annotate a
    record with a correction that has itself been corrected."""
    conversation, ids = write(store, ("user", "The code is 1111."))
    _, b = write(store, ("user", "No, 2222."))
    _, c = write(store, ("user", "Sorry, 3333."))
    link(b[0], ids[0])
    link(c[0], b[0])

    by_chunk, report = supersession.resolve_for_chunks(chunk_ids_of(conversation))

    [item] = next(iter(by_chunk.values()))
    assert "3333" in item.superseding_text and item.depth == 2
    assert report.links_followed == 2


def test_the_chain_state_comes_from_the_last_link(store):
    """`A <-(replaced) B <-(contradicted) C`. B supplied a value and C withdrew it,
    so there is no current value — which is what the reader needs to know. The first
    link's state would answer a question nobody asked."""
    conversation, ids = write(store, ("user", "The code is 1111."))
    _, b = write(store, ("user", "No, 2222."))
    _, c = write(store, ("user", "That's not right either."))
    link(b[0], ids[0], REPLACED)
    link(c[0], b[0], CONTRADICTED)

    by_chunk, _ = supersession.resolve_for_chunks(chunk_ids_of(conversation))

    [item] = next(iter(by_chunk.values()))
    assert item.replacement == CONTRADICTED


def test_two_corrections_of_one_claim_both_surface(store):
    """`UNIQUE` is on the pair, not on the superseded side, so a branch is
    representable. Both tips are carried rather than newest-wins: picking would be an
    unrecorded judgment about a disagreement between two things one person said."""
    conversation, ids = write(store, ("user", "The code is 1111."))
    _, b = write(store, ("user", "No, 2222."))
    _, c = write(store, ("user", "No, 3333."))
    link(b[0], ids[0])
    link(c[0], ids[0])

    by_chunk, report = supersession.resolve_for_chunks(chunk_ids_of(conversation))

    items = next(iter(by_chunk.values()))
    assert len(items) == 2
    assert {"2222" in i.superseding_text for i in items} == {True, False}
    assert report.annotations == 2


def test_a_cycle_terminates_and_is_recorded(store, monkeypatch):
    """The schema's triggers keep loops out of the table, so a loop arriving here
    means the guard was bypassed — a restore, a hand-edited row, a future bug. It
    must not hang mid-turn, and it must not pass silently."""
    conversation, ids = write(store, ("user", "The code is 1111."))
    _, b = write(store, ("user", "No, 2222."))
    link(b[0], ids[0])

    # The schema's trigger refuses `A <- B` plus `B <- A`, so the loop is injected at
    # the read boundary — which is exactly where a restored or hand-edited store
    # would present one.
    original = db.get_supersedes_from

    def looping(message_ids):
        if b[0] in message_ids:
            return [{
                "from_id": b[0],
                "replacement": REPLACED,
                "superseding_id": ids[0],          # back to the origin: a 2-cycle
                "superseding_content": "The code is 1111.",
                "superseding_timestamp": "2026-09-19T09:00:00",
                "superseding_role": "user",
            }]
        return original(message_ids)

    monkeypatch.setattr(db, "get_supersedes_from", looping)
    by_chunk, report = supersession.resolve_for_chunks(chunk_ids_of(conversation))

    assert report.cycles_detected == 1
    [item] = next(iter(by_chunk.values()))
    assert "2222" in item.superseding_text, (
        "the walk should stop at the last message outside the loop, not follow it"
    )


def test_the_depth_bound_stops_a_long_chain_and_annotates_the_deepest_reached(
    store, monkeypatch
):
    """The visited set stops a loop; the bound stops a pathologically long chain from
    spending the turn. They fail differently and neither implies the other."""
    monkeypatch.setattr(supersession, "MAX_DEPTH", 3)
    conversation, ids = write(store, ("user", "step 0"))
    previous = ids[0]
    for n in range(1, 6):
        _, nxt = write(store, ("user", f"step {n}"))
        link(nxt[0], previous)
        previous = nxt[0]

    by_chunk, report = supersession.resolve_for_chunks(chunk_ids_of(conversation))

    [item] = next(iter(by_chunk.values()))
    assert report.depth_limit_hit == 1
    assert item.depth == 3 and item.superseding_text == "step 3"


# --- what must not change (R1, R12, D6) --------------------------------------


def test_links_do_not_change_ranking(store):
    """D6's own pattern, applied to supersession: provenance is returned but never
    scored. A chunk's position must not depend on whether something in it was later
    corrected."""
    conversation, ids = write(store, ("user", "The espresso machine needs descaling."),
                              ("assistant", "Every two months."))
    write(store, ("user", "Pour-over needs a gooseneck kettle."))
    _, correction = write(store, ("user", "Actually descaling is monthly."))

    # Every chunk exists before the baseline, so the ONLY difference between the two
    # searches is the link itself. Writing the correction in between would change the
    # corpus and prove nothing about ranking.
    before = retrieval.search("espresso descaling kettle")
    link(correction[0], ids[0])
    after = retrieval.search("espresso descaling kettle")

    assert [r.chunk_id for r in before.results] == [r.chunk_id for r in after.results]
    assert [r.rrf_score for r in before.results] == [r.rrf_score for r in after.results]
    assert [r.bm25_rank for r in before.results] == [r.bm25_rank for r in after.results]
    assert any(r.supersessions for r in after.results), "the annotation did not attach"


def test_nothing_is_suppressed_and_the_record_is_not_edited(store):
    """CO7 and `PROJECT.md`: the corrected content still surfaces, unchanged, and
    the chunk text is untouched — so nothing reaches FTS5 or the embedding either."""
    conversation, ids = write(store, ("user", "The dentist is on Tuesday at 3."))
    text_before = db.get_conversation_chunks(conversation)[0]["text"]
    _, correction = write(store, ("user", "Actually Wednesday."))
    link(correction[0], ids[0])

    result = retrieval.search("dentist Tuesday")
    [hit] = [r for r in result.results if r.chunk_id in chunk_ids_of(conversation)]

    assert "Tuesday at 3" in hit.text, "the corrected claim was suppressed"
    assert db.get_conversation_chunks(conversation)[0]["text"] == text_before
    assert hit.supersessions


def test_nothing_on_this_path_takes_an_actor(store):
    """R8. Links exist only within one user's own claims, so annotation needs no
    actor to be correct — and decision #20 forbids scoping retrieval by who asks.
    Once an actor parameter exists, someone will filter on it."""
    import inspect

    for module in (supersession,):
        for name, fn in vars(module).items():
            if name.startswith("_") or not callable(fn) or not inspect.isfunction(fn):
                continue
            params = set(inspect.signature(fn).parameters)
            assert not params & {"actor", "user_id", "role", "user"}, name

    assert not set(inspect.signature(db.get_supersedes_for_chunks).parameters) & {
        "actor", "user_id", "role", "user"}


def test_a_resolution_failure_degrades_and_says_so(store, monkeypatch):
    """R7. Nothing is corrupted by an unannotated result and a person is waiting —
    but the degraded state presents a corrected claim as current, so it is recorded
    rather than only logged. The gate's "unavailable is never clean" shape."""
    conversation, ids = write(store, ("user", "The dentist is on Tuesday at 3."))
    _, correction = write(store, ("user", "Actually Wednesday."))
    link(correction[0], ids[0])

    def boom(_ids):
        raise RuntimeError("supersedes table is unreadable")

    monkeypatch.setattr(retrieval, "resolve_for_chunks", boom)
    result = retrieval.search("dentist Tuesday")

    assert result.results, "retrieval itself must still answer"
    assert result.supersession.resolved is False
    assert "unreadable" in result.supersession.skip_reason
    assert all(not r.supersessions for r in result.results)


def test_siblings_are_annotated_too(store):
    """A split sibling is a different piece of the *same* long message, so a
    correction to that message applies to whichever piece is rendered."""
    long_text = "The boiler pressure should sit around 1.4 bar. " * 200
    conversation, ids = write(store, ("user", long_text))
    chunks = db.get_conversation_chunks(conversation)
    assert len(chunks) > 1, "this test needs a split message to be meaningful"

    _, correction = write(store, ("user", "Actually 1.2 bar."))
    link(correction[0], ids[0])

    by_chunk, _ = supersession.resolve_for_chunks([c["id"] for c in chunks])
    assert len(by_chunk) == len(chunks), "every piece of the corrected message"


# --- rendering (R5) and the budget (RO4) -------------------------------------


def make_chunk(chunk_id="c1", text="a record", **kwargs):
    return retrieval.RetrievedChunk(chunk_id=chunk_id, text=text, **kwargs)


def make_item(replacement=REPLACED, superseded="It was Tuesday.",
              superseding="Actually Wednesday.", depth=1):
    return supersession.Supersession(
        superseded_message_id="m-old", superseded_text=superseded,
        superseding_message_id="m-new", superseding_text=superseding,
        superseding_timestamp="2026-09-19T11:00:00", replacement=replacement, depth=depth)


def render_one(*items, chunk_text="The dentist is on Tuesday at 3."):
    chunk = make_chunk(text=chunk_text)
    chunk.supersessions = list(items)
    result = retrieval.RetrievalResult(query="q", results=[chunk])
    return prompt.render_retrieved(result)


def test_the_two_states_render_differently(store):
    replaced = render_one(make_item(REPLACED))
    contradicted = render_one(make_item(REPLACED if False else CONTRADICTED))

    assert "Later corrected." in replaced and "superseded on 2026-09-19" in replaced
    assert "no replacement given" in contradicted
    assert "contradicted on 2026-09-19" in contradicted
    assert "no replacement given" not in replaced


def test_the_state_is_stated_positively_not_by_omission():
    """The gate's "No tools were used this turn" rule: an absent clause reads as no
    information, so the missing value is said out loud."""
    text = render_one(make_item(CONTRADICTED))
    assert "with no replacement given" in text


def test_both_messages_are_quoted_and_the_corrected_one_locates_itself():
    """A chunk packs up to eight turns into one opaque block, so the annotation has
    to quote the line it points at rather than describe it by position."""
    text = render_one(make_item())
    assert '"It was Tuesday."' in text
    assert '"Actually Wednesday."' in text


def test_a_long_quote_is_truncated_but_the_locator_is_always_present():
    item = make_item(superseded="x" * 500, superseding="y" * 500)
    text = render_one(item)
    assert "x" * prompt.SUPERSEDED_QUOTE_CHARS + "…" in text
    assert "y" * prompt.SUPERSEDING_QUOTE_CHARS + "…" in text


def test_the_rationale_is_never_rendered(store):
    """It is classifier output about a judgment, not something either party said.
    The prompt is not the place to argue for the link; the row stays queryable."""
    conversation, ids = write(store, ("user", "The dentist is on Tuesday at 3."))
    _, correction = write(store, ("user", "Actually Wednesday."))
    db.create_supersedes_link(correction[0], ids[0], REPLACED,
                              rationale="SENTINEL-RATIONALE-TEXT")

    result = retrieval.search("dentist Tuesday")
    text = prompt.render_retrieved(result)

    assert text and "SENTINEL-RATIONALE-TEXT" not in text


def test_the_per_chunk_cap_counts_the_remainder_rather_than_dropping_it():
    items = [make_item(superseding=f"correction {n}") for n in range(6)]
    text = render_one(*items)

    assert text.count("Later corrected.") == prompt.SUPERSEDING_MAX_PER_CHUNK
    extra = 6 - prompt.SUPERSEDING_MAX_PER_CHUNK
    assert f"And {extra} further corrections to this record, not shown." in text


def test_the_quote_budget_shortens_annotations_before_dropping_them():
    """RO4's degradation order. A dropped annotation presents a corrected claim as
    current — the failure this mechanism exists to prevent — so the quote goes first
    and the annotation's existence goes last."""
    chunks = []
    for n in range(4):
        chunk = make_chunk(chunk_id=f"c{n}", text=f"record {n}")
        chunk.supersessions = [make_item(superseding="z" * 400) for _ in range(3)]
        chunks.append(chunk)
    text = prompt.render_retrieved(retrieval.RetrievalResult(query="q", results=chunks))

    assert text.count("Later corrected.") == prompt.SUPERSESSION_MAX_ANNOTATIONS
    quoted = text.count('by: "')
    assert 0 < quoted < prompt.SUPERSESSION_MAX_ANNOTATIONS, (
        "the budget should have run out part-way, leaving later annotations unquoted"
    )
    # The unquoted ones still say what happened and to which line.
    assert text.count("superseded on 2026-09-19.") == (
        prompt.SUPERSESSION_MAX_ANNOTATIONS - quoted)


def test_the_global_cap_counts_what_it_withholds():
    chunks = []
    for n in range(8):
        chunk = make_chunk(chunk_id=f"c{n}", text=f"record {n}")
        chunk.supersessions = [make_item() for _ in range(3)]
        chunks.append(chunk)
    text = prompt.render_retrieved(retrieval.RetrievalResult(query="q", results=chunks))

    assert text.count("Later corrected.") == prompt.SUPERSESSION_MAX_ANNOTATIONS
    assert "further corrections apply to" in text and "not shown." in text


def test_the_worst_case_render_is_bounded():
    """RO4's arithmetic, asserted rather than asserted-in-a-comment."""
    chunks = []
    for n in range(config_top_k()):
        chunk = make_chunk(chunk_id=f"c{n}", text="")
        chunk.supersessions = [
            make_item(superseded="x" * 900, superseding="y" * 900) for _ in range(8)
        ]
        chunks.append(chunk)
    text = prompt.render_retrieved(retrieval.RetrievalResult(query="q", results=chunks))

    annotation_chars = len(text) - len(prompt._RETRIEVED_HEADER)
    assert annotation_chars < 6000, annotation_chars


def config_top_k():
    from program import config
    return config.retrieval_top_k()


def test_an_unannotated_result_renders_exactly_as_before(store):
    """The annotation is additive. A chunk with no corrections must render byte-for-
    byte as it did before task 3.5, or every existing prompt test is measuring
    something new."""
    chunk = make_chunk(text="nothing was corrected here", created_at="2026-09-19T10:00:00")
    text = prompt.render_retrieved(retrieval.RetrievalResult(query="q", results=[chunk]))

    assert text == (
        f"{prompt._RETRIEVED_HEADER}\n\n"
        f"[record 1 · 2026-09-19T10:00:00]\nnothing was corrected here"
    )


# --- memory_search inherits this, and must stay identical to passive rendering ---


def test_the_tool_and_the_passive_context_render_a_correction_identically(store):
    """`memory_search` reuses `prompt.render_retrieved()`, and a test in
    `test_memory_search.py` already pins that its output is *identical* to the
    passive rendering. Annotations therefore arrive in the tool for free — and that
    identity is the point: one chunk must not read two ways depending on how it was
    retrieved."""
    from program.tools import memory_search

    conversation, ids = write(store, ("user", "The dentist is on Tuesday at 3."))
    _, correction = write(store, ("user", "Actually the dentist is Wednesday."))
    link(correction[0], ids[0])

    rendered = memory_search.MEMORY_SEARCH.handler("dentist Tuesday")
    passive = prompt.render_retrieved(retrieval.search("dentist Tuesday"))

    assert "Later corrected." in passive
    assert passive in rendered, "the tool must not render a correction differently"


# --- RO3, reversed at review: the model is told when the check did not run -----


def test_a_failed_check_is_surfaced_to_the_model(store):
    """RO3 as decided (reversing my own lean). `memory_search`'s precedent: nothing
    found with the vector leg down is a different claim from nothing found. Here the
    absence of annotations carries no information when the check did not run."""
    chunk = make_chunk(text="The dentist is on Tuesday at 3.")
    result = retrieval.RetrievalResult(query="q", results=[chunk])
    result.supersession = supersession.SupersessionReport(
        resolved=False, skip_reason="OperationalError: database is locked")

    text = prompt.render_retrieved(result)

    assert "did not complete" in text
    assert text.index("did not complete") < text.index("The dentist"), (
        "a caveat that arrives after the thing it qualifies has been read is the "
        "wrong way round — the same ordering rule as the elapsed-time figure"
    )


def test_a_successful_check_says_nothing(store):
    chunk = make_chunk(text="The dentist is on Tuesday at 3.")
    text = prompt.render_retrieved(retrieval.RetrievalResult(query="q", results=[chunk]))

    assert "did not complete" not in text


def test_no_results_means_no_note_either():
    """There is nothing to qualify, and a caveat about an empty section would be
    noise — the same reason the first-message situation block carries no pairing."""
    result = retrieval.RetrievalResult(query="q", results=[])
    result.supersession = supersession.SupersessionReport(resolved=False)

    assert prompt.render_retrieved(result) == ""


def test_the_note_survives_prompt_assembly(store):
    """It is authored text, so the naming and trait tripwires must cover it — the
    same treatment `_RETRIEVED_HEADER` gets."""
    chunk = make_chunk(text="The dentist is on Tuesday at 3.")
    result = retrieval.RetrievalResult(query="q", results=[chunk])
    result.supersession = supersession.SupersessionReport(resolved=False)

    assembled = prompt.build_system_prompt("", retrieval=result)

    assert "did not complete" in assembled


def test_the_failure_path_produces_the_note_end_to_end(store, monkeypatch):
    conversation, ids = write(store, ("user", "The dentist is on Tuesday at 3."))
    _, correction = write(store, ("user", "Actually Wednesday."))
    link(correction[0], ids[0])

    monkeypatch.setattr(
        retrieval, "resolve_for_chunks",
        lambda _ids: (_ for _ in ()).throw(RuntimeError("supersedes unreadable")))
    text = prompt.render_retrieved(retrieval.search("dentist Tuesday"))

    assert "did not complete" in text
    assert "Later corrected." not in text, "nothing resolved, so nothing to show"
