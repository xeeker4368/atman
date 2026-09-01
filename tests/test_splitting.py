"""Text splitting. Pure logic, no stores — exhaustively testable in isolation."""

from __future__ import annotations

import pytest

from program.memory import splitting


def test_text_within_budget_is_one_piece():
    assert splitting.split_text("short", 100) == ["short"]


def test_every_piece_is_within_budget():
    text = "word " * 500
    for piece in splitting.split_text(text, 100):
        assert len(piece) <= 100


def test_split_is_lossless():
    """Concatenating the pieces must reproduce the input exactly.

    A chunk that silently dropped text would be a memory the system is wrong
    about, and nothing downstream would notice.
    """
    text = "Paragraph one.\n\nParagraph two is longer.\n\nAnd a third.\n" * 40
    assert "".join(splitting.split_text(text, 200)) == text


def test_prefers_paragraph_over_line_over_sentence():
    text = "A" * 40 + "\n\n" + "B" * 40 + "\n" + "C" * 40 + ". " + "D" * 40
    pieces = splitting.split_text(text, 100)
    # The widest boundary inside the window is the paragraph break.
    assert pieces[0].endswith("\n\n")


def test_falls_back_to_line_when_no_paragraph_break():
    text = "A" * 40 + "\n" + "B" * 40 + "\n" + "C" * 40
    pieces = splitting.split_text(text, 60)
    assert pieces[0] == "A" * 40 + "\n"


def test_falls_back_to_whitespace_when_no_sentence_end():
    text = " ".join("token" for _ in range(50))
    pieces = splitting.split_text(text, 60)
    assert all(len(p) <= 60 for p in pieces)
    assert "".join(pieces) == text


def test_hard_split_when_no_boundary_exists():
    text = "x" * 250
    pieces = splitting.split_text(text, 100)
    assert [len(p) for p in pieces] == [100, 100, 50]
    assert "".join(pieces) == text


def test_hard_split_never_corrupts_multibyte_characters():
    """Slicing must happen in str space, never bytes.

    Byte-slicing would cut a 4-byte emoji in half and produce invalid UTF-8 at
    the seam. This is the one constraint carried verbatim from the reference
    build, whose comment on it was correct.
    """
    text = "🌊" * 200  # no whitespace, so the hard-split path is forced
    pieces = splitting.split_text(text, 50)
    assert "".join(pieces) == text
    for piece in pieces:
        # Round-trips cleanly, so no character was severed.
        assert piece.encode("utf-8").decode("utf-8") == piece
        assert all(ch == "🌊" for ch in piece)


def test_zero_or_negative_budget_is_rejected():
    with pytest.raises(ValueError):
        splitting.split_text("anything", 0)


# ---------------------------------------------------------------------------
# pack_lines
# ---------------------------------------------------------------------------


def test_pack_lines_keeps_whole_lines_together():
    lines = [("m1", "a" * 30), ("m2", "b" * 30), ("m3", "c" * 30)]
    runs = splitting.pack_lines(lines, 70)
    assert [[mid for mid, _ in run] for run in runs] == [["m1", "m2"], ["m3"]]


def test_pack_lines_isolates_an_overlong_line():
    """A line that cannot fit any run gets its own, for the caller to split."""
    lines = [("m1", "a" * 10), ("m2", "b" * 200), ("m3", "c" * 10)]
    runs = splitting.pack_lines(lines, 50)
    assert [[mid for mid, _ in run] for run in runs] == [["m1"], ["m2"], ["m3"]]


def test_pack_lines_accounts_for_the_joining_newline():
    """Two 30-char lines joined by a newline are 61 chars, not 60."""
    lines = [("m1", "a" * 30), ("m2", "b" * 30)]
    runs = splitting.pack_lines(lines, 60)
    assert len(runs) == 2


def test_pack_lines_loses_nothing():
    lines = [(f"m{i}", f"line {i} " * 5) for i in range(20)]
    runs = splitting.pack_lines(lines, 100)
    flattened = [pair for run in runs for pair in run]
    assert flattened == lines
