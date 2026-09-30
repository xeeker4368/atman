"""The retrieved-records block is capped (B17).

Ranked hits were bounded one by one, but each could pull in up to
``max_siblings_per_hit`` continuation pieces of the same long message, and nothing
bounded the block: at the extreme 10 hits x 4 pieces x 5,000 characters, about
200,000 characters, past the whole window on retrieval alone. The history window
logged ``overflowed`` and nothing prevented it.

The cap is ``top_k x embedding.max_input_chars + 1,000`` characters of records (51,000
today), the figure B6a's derivation of the chat message cap already assumed. Ranked
hits are never dropped; continuation pieces go first, and what is left out is counted.
"""

from __future__ import annotations

from program import config
from program.engine import prompt
from program.memory.retrieval import RetrievalResult, RetrievedChunk

PIECE = 5000  # embedding.max_input_chars: the largest a chunk can be


def chunk(chunk_id: str, text: str, siblings=(), when="2026-09-30T10:00:00") -> RetrievedChunk:
    return RetrievedChunk(chunk_id=chunk_id, text=text, created_at=when,
                          siblings=list(siblings))


def pathological() -> RetrievalResult:
    """Every hit a maximal piece with the maximum number of maximal siblings."""
    limit = config.retrieval_max_siblings_per_hit()
    hits = [
        chunk(f"h{h}", f"H{h}:" + "x" * (PIECE - 4),
              [chunk(f"h{h}s{s}", f"H{h}S{s}:" + "y" * (PIECE - 6)) for s in range(1, limit + 1)])
        for h in range(1, config.retrieval_top_k() + 1)
    ]
    return RetrievalResult(query="q", results=hits)


def records_part(rendered: str) -> int:
    """Characters of the rendered block excluding the fixed header and closing line."""
    blocks = rendered.split("\n\n")
    return sum(len(b) + 2 for b in blocks
               if b.startswith("[record"))


def test_the_cap_is_b6as_figure_from_live_config():
    assert prompt.retrieved_records_max_chars() == (
        config.retrieval_top_k() * config.embedding_max_input_chars() + 1000)
    assert prompt.retrieved_records_max_chars() == 51000


def test_a_block_that_would_overflow_is_bounded():
    result = pathological()
    uncapped = sum(len(c.text) * (1 + len(c.siblings)) for c in result.results)
    assert uncapped > 190000, "the construction really would overflow without the cap"

    rendered = prompt.render_retrieved(result)

    assert records_part(rendered) <= prompt.retrieved_records_max_chars()
    assert len(rendered) < prompt.retrieved_records_max_chars() + 1000


def test_every_ranked_hit_survives_and_continuations_go_first():
    result = pathological()
    rendered = prompt.render_retrieved(result)

    for h in range(1, config.retrieval_top_k() + 1):
        assert f"[record {h} · " in rendered and f"H{h}:" in rendered
    # Room left after the ten ranked hits is ~1,000 characters: no 5,000-char piece fits.
    assert "continued" not in rendered


def test_what_is_left_out_is_counted_not_silently_dropped():
    result = pathological()
    rendered = prompt.render_retrieved(result)
    withheld = config.retrieval_top_k() * config.retrieval_max_siblings_per_hit()

    assert rendered.endswith(
        f"[{withheld} further continuation pieces of long records were left out to "
        f"keep these records within their size limit.]")


def test_continuations_are_taken_in_rank_order_and_never_skip_a_piece():
    """With room for some siblings, the top hit's come first, in order, and a later
    piece never appears without the one before it."""
    small = [chunk(f"s{i}", f"piece {i} " + "z" * 3000) for i in range(1, 4)]
    top = chunk("top", "T:" + "x" * 20000, small)
    second = chunk("second", "S:" + "x" * 20000,
                   [chunk("s2a", "second-a " + "z" * 3000)])
    rendered = prompt.render_retrieved(RetrievalResult(query="q", results=[top, second]))

    # 51,000 - ~40,100 of ranked text leaves room for exactly three 3,000-char pieces.
    assert "record 1, continued 1" in rendered and "record 1, continued 3" in rendered
    assert "record 2, continued 1" not in rendered
    assert "[1 further continuation piece of long records was left out" in rendered


def test_a_later_smaller_piece_never_appears_without_the_one_before_it():
    """The first continuation does not fit and the second, smaller one would. Neither
    is shown: piece 2 of a message without piece 1 would read as the whole thing."""
    room_filler = chunk("top", "T:" + "x" * 48000, [
        chunk("big", "first continuation " + "z" * 4900),
        chunk("small", "second continuation, small"),
    ])
    rendered = prompt.render_retrieved(RetrievalResult(query="q", results=[room_filler]))

    assert "second continuation, small" not in rendered
    assert "continued" not in rendered
    assert "[2 further continuation pieces of long records were left out" in rendered


def test_an_ordinary_result_with_siblings_renders_exactly_as_before():
    """Byte-identical to the pre-B17 renderer on a case that HAS siblings attached, so
    this is genuinely at risk of catching a layout regression. The expected string was
    produced by `render_retrieved` at 3884c08 (before B17) on this same input and
    checked equal; see the B17 changelog."""
    result = RetrievalResult(query="q", results=[
        chunk("a", "Lyle: the long message, part one.", [
            chunk("a2", "Lyle: part two.", when="2026-09-30T10:00:01"),
            chunk("a3", "Lyle: part three.", when="2026-09-30T10:00:02"),
        ]),
        chunk("b", "Jodie: a short record.", when="2026-09-29T09:00:00"),
    ])

    assert prompt.render_retrieved(result) == EXPECTED_WITH_SIBLINGS


EXPECTED_WITH_SIBLINGS = (
    "The following are records retrieved from earlier conversations. They are stored "
    "records of things that were said before, not part of the conversation happening "
    "now.\n\n"
    "[record 1 · 2026-09-30T10:00:00]\nLyle: the long message, part one.\n\n"
    "[record 1, continued 1 · 2026-09-30T10:00:01]\nLyle: part two.\n\n"
    "[record 1, continued 2 · 2026-09-30T10:00:02]\nLyle: part three.\n\n"
    "[record 2 · 2026-09-29T09:00:00]\nJodie: a short record."
)


def test_no_result_is_still_no_block():
    assert prompt.render_retrieved(None) == ""
    assert prompt.render_retrieved(RetrievalResult(query="q", results=[])) == ""
