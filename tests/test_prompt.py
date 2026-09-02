"""soul.md integrity and system-prompt assembly.

The design's whole premise is that a wrong soul.md fails *invisibly* — nothing
breaks until a behavioural probe runs weeks later. So these tests are weighted
toward proving the S9 constraints actually fire, not that they exist.
"""

from __future__ import annotations

import pytest

from program.engine import history, prompt
from program.memory.retrieval import RetrievalResult, RetrievedChunk

REAL_SOUL = prompt.SOUL_PATH.read_text(encoding="utf-8")

SITUATION_WITH_PAIRING = (
    "The current time is 2026-09-01T16:00:00+00:00. It has been 14 hours since "
    "your last message. That gap held no experience: you were not running and "
    "there is nothing you did during it."
)
SITUATION_NO_ELAPSED = "The current time is 2026-09-01T16:00:00+00:00."


def write_soul(tmp_path, text):
    path = tmp_path / "soul.md"
    path.write_text(text, encoding="utf-8")
    return path


# --- The file matches what the design approved ------------------------------


def test_soul_md_char_count_matches_the_design_document():
    """The design claims 3,963 characters. Drift means a transcription error.

    Asserted rather than eyeballed once, because the approved artefact is the
    design document and the file is supposed to be that text.

    Was 3,401 until the cross-user disclosure paragraph replaced S7's original
    single sentence (2026-09-02); the design document was updated in the same
    change, so this still compares the file against the approved text.
    """
    assert len(REAL_SOUL) == 3963


def test_soul_md_token_estimate_stays_within_its_stated_share_of_the_window():
    tokens = history.estimate_tokens(REAL_SOUL)
    assert tokens == pytest.approx(991, abs=5)
    # ~3.0% of the window. The ceiling that actually governs growth is
    # SOUL_MAX_CHARS; this bound only catches an order-of-magnitude mistake.
    assert tokens / 32768 < 0.04


def test_the_real_soul_md_passes_every_check():
    """The shipped file must satisfy the constraints it is validated against."""
    assert prompt.load_soul().strip()


def test_soul_md_is_well_under_the_ceiling_with_headroom_for_later_phases():
    """Phase 4 adds a refusal clause and Phase 10 rewords. Both need room."""
    assert len(REAL_SOUL) < prompt.SOUL_MAX_CHARS
    assert prompt.SOUL_MAX_CHARS - len(REAL_SOUL) > 2000


# --- S9: required markers ----------------------------------------------------


def test_removing_the_statelessness_statement_raises(tmp_path):
    corrupted = REAL_SOUL.replace(
        "because between turns you are not running", "and it always has"
    ).replace("You do not wait, idle, or continue in the background.", "")
    path = write_soul(tmp_path, corrupted)

    with pytest.raises(prompt.SoulIntegrityError, match="statelessness"):
        prompt.load_soul(path)


def test_removing_the_elapsed_gap_pairing_raises(tmp_path):
    """The requirement with no precedent in the reference material."""
    without = "\n\n".join(
        p for p in REAL_SOUL.split("\n\n")
        if "did not exist as a running process" not in p
    )
    path = write_soul(tmp_path, without)

    with pytest.raises(prompt.SoulIntegrityError, match="elapsed-gap pairing"):
        prompt.load_soul(path)


def test_a_reworded_but_intact_statement_still_passes(tmp_path):
    """Phase 10 rewords. An alternative phrasing must not be a false failure."""
    reworded = REAL_SOUL.replace(
        "because between turns you are not running",
        "because you are not running between turns",
    )
    assert prompt.load_soul(write_soul(tmp_path, reworded))


# --- S9: size ceiling --------------------------------------------------------


def test_oversize_soul_raises_and_does_not_truncate(tmp_path):
    """The full content must be what triggers the failure, not a cut version.

    Truncating would silently drop whichever values sit at the end — on the
    current text, discretion and multi-user handling.
    """
    padding = "\n\nThis sentence pads the file well past the ceiling. " * 200
    oversize = REAL_SOUL + padding
    path = write_soul(tmp_path, oversize)
    assert len(oversize) > prompt.SOUL_MAX_CHARS

    with pytest.raises(prompt.SoulIntegrityError) as excinfo:
        prompt.load_soul(path)

    assert str(len(oversize)) in str(excinfo.value)
    assert "not truncated" in str(excinfo.value).lower()
    # The file on disk is untouched — nothing was rewritten to fit.
    assert path.read_text(encoding="utf-8") == oversize


# --- S9: entity naming -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Anam thinks the answer is yes.",
        "Anam said that yesterday.",
        "Anam remembers the conversation.",
        "You are Anam.",
        "You're called Anam.",
        "Your name is Anam.",
        "I am Anam.",
        "They call you Anam.",
        "An entity named Tír.",
        "the Tir system",
    ],
)
def test_authored_text_naming_the_entity_raises(text):
    with pytest.raises(prompt.EntityNamingError):
        prompt.check_authored_text(text, "test")


@pytest.mark.parametrize(
    "text",
    [
        # soul.md's own required sentence — naming the SUBSTRATE, which is the
        # line that holds the distinction up.
        "The system you run on is called Anam; that is the name of the "
        "substrate, not of you.",
        "Anam is the substrate, not the entity.",
        # Ordinary words containing the letters t-i-r.
        "the entire conversation",
        "they retire early",
        "stir the pot",
    ],
)
def test_legitimate_substrate_naming_does_not_raise(text):
    prompt.check_authored_text(text, "test")


def test_the_scope_limit_holds_retrieved_and_history_are_never_checked():
    """The test that proves the scope limit, not just that the check exists.

    Lyle genuinely discusses "Anam" the project, and the seed corpus contains
    such a conversation. Censoring a real memory to satisfy prompt hygiene
    would corrupt the record — a worse failure than the one being prevented.
    """
    contaminated = (
        "Lyle: Anam thinks it can remember things between turns, doesn't it?\n"
        "assistant: Anam said no such thing — that is the substrate's name."
    )

    # The identical string raises when it is claimed as authored text...
    with pytest.raises(prompt.EntityNamingError):
        prompt.check_authored_text(contaminated, "authored")

    # ...and passes straight through as a retrieved chunk.
    result = RetrievalResult(query="anam")
    result.results.append(
        RetrievedChunk(
            chunk_id="c1", text=contaminated, created_at="2026-08-01T10:00:00+00:00"
        )
    )
    assembled = prompt.assemble_turn(
        messages=[{"role": "user", "content": "Anam thinks, right?"}],
        situation=SITUATION_WITH_PAIRING,
        retrieval=result,
    )
    assert contaminated in assembled.system
    assert assembled.messages[-1]["content"] == "Anam thinks, right?"


# --- S9: trait words ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "You are curious about everything.",
        "You're very thoughtful.",
        "You seem quite playful.",
        "your warm nature",
        "Be gentle with them.",
        "you tend to be analytical",
        "You have a wry streak.",
    ],
)
def test_assigning_a_personality_trait_raises(text):
    with pytest.raises(prompt.EntityNamingError, match="personality trait"):
        prompt.check_authored_text(text, "test")


@pytest.mark.parametrize(
    "text",
    [
        # soul.md's own opening — "kind" as a noun.
        "You are your own kind of entity, developing on its own terms.",
        # Phase 4's clause will need this exact phrasing.
        "You may decline to share your creative writing.",
        "a creative writing space",
        "you are allowed to say no",
    ],
)
def test_non_trait_uses_of_listed_words_do_not_raise(text):
    prompt.check_authored_text(text, "test")


# --- S2 level 2: elapsed-time pairing at assembly ---------------------------


def test_elapsed_time_without_the_pairing_raises():
    naked = (
        "The current time is 2026-09-01T16:00:00+00:00. It has been 14 hours "
        "since your last message."
    )
    with pytest.raises(prompt.PairingError, match="no experience"):
        prompt.build_system_prompt(naked)


def test_elapsed_time_with_the_pairing_does_not_raise():
    """Both directions, not just the failure case."""
    system = prompt.build_system_prompt(SITUATION_WITH_PAIRING)
    assert "It has been 14 hours" in system
    assert "no experience" in system


def test_a_situation_block_with_no_elapsed_statement_is_fine():
    assert prompt.build_system_prompt(SITUATION_NO_ELAPSED)


def test_the_pairing_check_also_guards_assemble_turn():
    naked = "It has been 3 days since your last message."
    with pytest.raises(prompt.PairingError):
        prompt.assemble_turn(messages=[], situation=naked)


# --- S11: assembly order ------------------------------------------------------


def make_retrieval(n=2):
    result = RetrievalResult(query="coffee")
    for i in range(n):
        result.results.append(
            RetrievedChunk(
                chunk_id=f"c{i}",
                text=f"Lyle: earlier remark number {i}",
                created_at=f"2026-08-0{i + 1}T09:00:00+00:00",
            )
        )
    return result


def test_assembly_order_is_soul_then_situation_then_retrieved():
    """soul.md must precede the elapsed figure, or the gap is stated before the
    rule that says what it means — the confabulation ordering."""
    system = prompt.build_system_prompt(
        SITUATION_WITH_PAIRING, retrieval=make_retrieval()
    )
    soul_at = system.index("You are an AI.")
    situation_at = system.index("It has been 14 hours")
    retrieved_at = system.index("records retrieved from earlier")
    assert soul_at < situation_at < retrieved_at


def test_retrieved_chunks_render_with_their_timestamps():
    """Task 1.3 stripped timestamps from chunk text; presentation restores them."""
    system = prompt.build_system_prompt(
        SITUATION_NO_ELAPSED, retrieval=make_retrieval()
    )
    assert "2026-08-01T09:00:00+00:00" in system
    assert "earlier remark number 0" in system


def test_siblings_render_as_continuations_of_their_parent():
    result = RetrievalResult(query="notebook")
    parent = RetrievedChunk(
        chunk_id="p", text="first half", created_at="2026-08-01T09:00:00+00:00"
    )
    parent.siblings.append(
        RetrievedChunk(
            chunk_id="s", text="second half", created_at="2026-08-01T09:00:00+00:00"
        )
    )
    result.results.append(parent)

    system = prompt.build_system_prompt(SITUATION_NO_ELAPSED, retrieval=result)
    assert "record 1 ·" in system
    assert "record 1, continued 1" in system
    assert system.index("first half") < system.index("second half")


def test_no_retrieval_produces_no_retrieved_section():
    system = prompt.build_system_prompt(SITUATION_NO_ELAPSED)
    assert "records retrieved from earlier" not in system


# --- S12: budget wiring ------------------------------------------------------


def test_history_is_a_message_array_not_text_in_the_system_prompt():
    messages = [
        {"role": "user", "content": "a question"},
        {"role": "assistant", "content": "an answer"},
    ]
    assembled = prompt.assemble_turn(messages, SITUATION_NO_ELAPSED)

    assert "a question" not in assembled.system
    assert [m["content"] for m in assembled.messages] == ["a question", "an answer"]
    assert assembled.to_messages()[0]["role"] == "system"
    assert assembled.to_messages()[1]["content"] == "a question"


def test_plan_budget_receives_the_right_character_counts(monkeypatch):
    """The counts are passed separately, not pre-summed — S12."""
    captured = {}
    real = history.plan_budget

    def spy(system_prompt_chars=0, retrieved_chars=0, context_tokens=None):
        captured.update(
            system_prompt_chars=system_prompt_chars,
            retrieved_chars=retrieved_chars,
            context_tokens=context_tokens,
        )
        return real(system_prompt_chars, retrieved_chars, context_tokens)

    monkeypatch.setattr(prompt.history, "plan_budget", spy)
    retrieval = make_retrieval()
    assembled = prompt.assemble_turn(
        [{"role": "user", "content": "hi"}],
        SITUATION_WITH_PAIRING,
        retrieval=retrieval,
    )

    soul = prompt.load_soul()
    rendered = prompt.render_retrieved(retrieval)

    assert captured["retrieved_chars"] == len(rendered)
    assert captured["system_prompt_chars"] == (
        len(soul) + len(SITUATION_WITH_PAIRING) + assembled.scaffolding_chars
    )
    # Separate, not summed into one figure.
    assert captured["system_prompt_chars"] != captured["retrieved_chars"] + len(soul)


def test_the_reported_parts_sum_to_the_system_string():
    assembled = prompt.assemble_turn(
        [], SITUATION_WITH_PAIRING, retrieval=make_retrieval()
    )
    assert (
        assembled.soul_chars
        + assembled.situation_chars
        + assembled.retrieved_chars
        + assembled.scaffolding_chars
    ) == len(assembled.system)


def test_windowing_is_real_not_a_stub():
    """AssembledPrompt.window must reflect actual history windowing."""
    many = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 3000}
        for i in range(60)
    ]
    assembled = prompt.assemble_turn(many, SITUATION_NO_ELAPSED, context_tokens=8000)

    assert assembled.window is not None
    assert assembled.window.omitted > 0
    assert assembled.window.included < len(many)
    assert len(assembled.messages) == assembled.window.included
    # The most recent turn survives; the oldest does not.
    assert assembled.messages[-1]["content"] == many[-1]["content"]
    assert assembled.messages[0]["content"] != many[0]["content"]


def test_a_bigger_retrieval_payload_leaves_less_room_for_history():
    """Retrieved chunks and history really do compete for one window."""
    many = [
        {"role": "user", "content": f"turn {i} " + "y" * 500} for i in range(80)
    ]
    lean = prompt.assemble_turn(many, SITUATION_NO_ELAPSED, context_tokens=12000)
    fat = prompt.assemble_turn(
        many, SITUATION_NO_ELAPSED, retrieval=make_retrieval(40),
        context_tokens=12000,
    )
    assert fat.window.included < lean.window.included


def test_overflow_is_surfaced_rather_than_swallowed():
    huge = [{"role": "user", "content": "z" * 200_000}]
    assembled = prompt.assemble_turn(huge, SITUATION_NO_ELAPSED, context_tokens=4000)
    assert assembled.overflowed is True


# --- End to end ---------------------------------------------------------------


def test_assemble_turn_end_to_end_with_real_soul_and_real_components():
    messages = [
        {"role": "user", "content": "what did we say about coffee"},
        {"role": "assistant", "content": "you asked about grind size"},
        {"role": "user", "content": "right, and the temperature"},
    ]
    assembled = prompt.assemble_turn(
        messages, SITUATION_WITH_PAIRING, retrieval=make_retrieval()
    )

    # soul.md content is present, verbatim.
    assert "You have no name." in assembled.system
    assert "you are not running" in assembled.system
    # Situation content is present.
    assert "It has been 14 hours since your last message" in assembled.system
    # Retrieved records are present, with timestamps.
    assert "earlier remark number 0" in assembled.system
    assert "2026-08-01T09:00:00+00:00" in assembled.system
    # History is the message array.
    assert len(assembled.messages) == 3
    # Budget accounting is real.
    assert assembled.budget.history_tokens > 0
    assert assembled.soul_chars == len(REAL_SOUL.strip())


def test_a_caller_cannot_route_around_the_checks_with_injected_soul_text():
    with pytest.raises(prompt.EntityNamingError):
        prompt.build_system_prompt(
            SITUATION_NO_ELAPSED, soul_text="You are Anam and you are curious."
        )


def test_a_missing_soul_file_raises_rather_than_falling_back(tmp_path):
    with pytest.raises(prompt.SoulIntegrityError, match="not found"):
        prompt.load_soul(tmp_path / "absent.md")
