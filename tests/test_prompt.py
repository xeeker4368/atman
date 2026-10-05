"""soul.md integrity and system-prompt assembly.

The design's whole premise is that a wrong soul.md fails *invisibly* — nothing
breaks until a behavioural probe runs weeks later. So these tests are weighted
toward proving the S9 constraints actually fire, not that they exist.
"""

from __future__ import annotations

import re

import pytest

from program.engine import history, prompt
from program.memory.retrieval import RetrievalResult, RetrievedChunk

REAL_SOUL = prompt.SOUL_PATH.read_text(encoding="utf-8")
REAL_OPERATIONAL = prompt.OPERATIONAL_PATH.read_text(encoding="utf-8")

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


def write_authored(tmp_path, soul=None, operational=None):
    """Both authored files in a temporary directory, defaulting to the real ones."""
    s = tmp_path / "soul.md"
    o = tmp_path / "operational.md"
    s.write_text(REAL_SOUL if soul is None else soul, encoding="utf-8")
    o.write_text(REAL_OPERATIONAL if operational is None else operational, encoding="utf-8")
    return s, o


def joined():
    """The authored text as the marker check sees it: the two files in assembly order."""
    return REAL_SOUL.strip() + "\n\n" + REAL_OPERATIONAL.strip()


def strip_markers(text, requirement):
    for alternative in prompt.REQUIRED_MARKERS[requirement]:
        text = re.sub(r"\s+".join(map(re.escape, alternative.split())), "", text,
                      flags=re.IGNORECASE)
    return text


# --- The file matches what the design approved ------------------------------


def test_soul_md_char_count_matches_the_design_document():
    """The design gives 1,175 characters. Drift means a transcription error.

    Asserted rather than eyeballed once, because the approved artefact is the
    design document and the file is supposed to be that text.

    Was 3,401 until the cross-user disclosure paragraph replaced S7's original
    single sentence (2026-09-02), then 3,963 until Phase 4's creative-work refusal
    clause was added after the general-discretion paragraph (2026-09-21, design
    revision 3), then 4,392 until B12's correction-description clause was added after
    the "You do not fabricate" paragraph (2026-09-30, design revision 4, D3), then
    4,749 when the naming and memory paragraphs were replaced (2026-10-04, design
    revision 5, decision #24) — the first step in this history that subtracts — then
    1,175 when the text was split into soul.md and operational.md (2026-10-05, design
    revision 6, decision #25). The design document was updated in the same change each
    time, so this still compares the file against the approved text.

    **Characters, not bytes.** Revision 6 has no em-dashes, so the two agree (1,175);
    revision 5 was 4,749 characters and 4,763 bytes. This assertion and
    `SOUL_MAX_CHARS` both measure characters.
    """
    assert len(REAL_SOUL) == 1175


def test_operational_md_char_count_matches_the_design_document():
    """Decision #25's second authored file: 696 characters in the design."""
    assert len(REAL_OPERATIONAL) == 696


def test_soul_md_token_estimate_stays_within_its_stated_share_of_the_window():
    tokens = history.estimate_tokens(REAL_SOUL)
    assert tokens == pytest.approx(294, abs=5)
    both = history.estimate_tokens(REAL_SOUL) + history.estimate_tokens(REAL_OPERATIONAL)
    assert both == pytest.approx(468, abs=5)
    # ~1.4% of the window for both. The ceilings that actually govern growth are
    # SOUL_MAX_CHARS and OPERATIONAL_MAX_CHARS; this bound only catches an
    # order-of-magnitude mistake.
    assert both / 32768 < 0.04


def test_the_real_soul_md_passes_every_check():
    """The shipped file must satisfy the constraints it is validated against."""
    assert prompt.load_soul().strip()


def test_soul_md_is_well_under_the_ceiling_with_headroom_for_later_phases():
    """Phase 10 rewords and needs room.

    The threshold was **> 2000** when this covered two future consumers: *"Phase 4
    adds a refusal clause and Phase 10 rewords. Both need room."* Phase 4's clause
    landed on 2026-09-21 and spent 429 characters of it, leaving 1,608 for Phase 10
    alone — which is a rewording pass rather than an addition, and may well shrink the
    file.

    So the threshold drops to 1500 rather than being removed: the check exists to stop
    `soul.md` creeping toward a ceiling that silently shrinks every future turn's
    history, and it still fails if another ~100 characters arrive unreviewed.

    Then B12's clause landed on 2026-09-30 (design revision 4, D3), a reviewed
    addition of 457 characters, approved with its remaining headroom (1,152) stated. So
    this dropped again, to 1000, on the same reasoning. It still fails if about 150 more
    characters arrive unreviewed, and a fifth addition of any size will meet it.
    Decision #24 subtracted 100 characters rather than adding any, so the headroom is
    1,251 and the threshold is unchanged.

    Decision #25 (2026-10-05) split the text in two and gave each file its own
    ceiling: soul.md 3,000 and operational.md 1,500. The thresholds are restated
    against those: soul.md has 1,825 characters of headroom and this fails if about
    325 more arrive unreviewed; operational.md has 804 and this fails if about 200 do.
    """
    assert len(REAL_SOUL) < prompt.SOUL_MAX_CHARS
    assert prompt.SOUL_MAX_CHARS - len(REAL_SOUL) > 1500
    assert len(REAL_OPERATIONAL) < prompt.OPERATIONAL_MAX_CHARS
    assert prompt.OPERATIONAL_MAX_CHARS - len(REAL_OPERATIONAL) > 600


# --- S9: required markers ----------------------------------------------------


@pytest.mark.parametrize("requirement", sorted(prompt.REQUIRED_MARKERS))
def test_removing_a_required_statement_from_the_authored_text_raises(tmp_path, requirement):
    """Derived from the marker set, applied to BOTH files, so the statement cannot
    survive in either one and a reworded file cannot leave the test measuring nothing."""
    soul = strip_markers(REAL_SOUL, requirement)
    operational = strip_markers(REAL_OPERATIONAL, requirement)
    assert (soul, operational) != (REAL_SOUL, REAL_OPERATIONAL), (
        "the authored text must carry at least one alternative")
    s, o = write_authored(tmp_path, soul, operational)

    with pytest.raises(prompt.SoulIntegrityError, match=requirement):
        prompt.load_authored(s, o)


def test_a_reworded_but_intact_statement_still_passes(tmp_path):
    """A rewording that keeps an accepted alternative must not be a false failure,
    whichever file the statement is in."""
    phrase = "You run when something starts you"
    reworded = "You are not running between turns except when something starts you"
    soul = REAL_SOUL.replace(phrase, reworded)
    operational = REAL_OPERATIONAL.replace(phrase, reworded)
    # Without this, a rewrite of a string neither file contains is a no-op and the
    # test passes while measuring nothing.
    assert (soul, operational) != (REAL_SOUL, REAL_OPERATIONAL), "nothing was rewritten"
    assert prompt.load_authored(*write_authored(tmp_path, soul, operational))


def test_the_gap_statement_about_the_record_is_an_accepted_pairing():
    """Decision #25's block sentence pairs the figure; the older phrases stay accepted."""
    assert prompt.has_pairing("Apart from any run your record shows, nothing was running "
                              "in that time, so there is nothing else from it to report.")
    assert prompt.has_pairing("You were not running during that time.")


def test_the_markers_are_checked_on_the_two_files_joined_not_on_each(tmp_path):
    """Decision #25: which file holds a required statement is layout. A statement
    moved from one file to the other loads; the same statement in neither raises."""
    soul, operational = REAL_SOUL, REAL_OPERATIONAL
    for requirement in prompt.REQUIRED_MARKERS:
        soul = strip_markers(soul, requirement)
        operational = strip_markers(operational, requirement)
    statelessness = prompt.REQUIRED_MARKERS["statelessness"][-1]
    pairing = prompt.REQUIRED_MARKERS["elapsed-gap pairing"][-1]

    split = write_authored(tmp_path, soul + "\n" + statelessness + ".\n",
                           operational + "\n" + pairing + ".\n")
    assert prompt.load_authored(*split)

    (tmp_path / "b").mkdir()
    swapped = write_authored(tmp_path / "b", soul + "\n" + pairing + ".\n",
                             operational + "\n" + statelessness + ".\n")
    assert prompt.load_authored(*swapped)

    (tmp_path / "c").mkdir()
    neither = write_authored(tmp_path / "c", soul, operational)
    with pytest.raises(prompt.SoulIntegrityError):
        prompt.load_authored(*neither)


def test_a_marker_split_by_a_line_wrap_still_matches(tmp_path):
    """Whitespace is collapsed on both sides. Revision 5's `there is nothing you have
    been up to` never matched, because soul.md broke the line inside it."""
    soul, operational = REAL_SOUL, REAL_OPERATIONAL
    for requirement in prompt.REQUIRED_MARKERS:
        soul = strip_markers(soul, requirement)
        operational = strip_markers(operational, requirement)
    wrapped = ("You run when something\n   starts you. There is nothing from\n"
               "that time  to report.\n")
    s, o = write_authored(tmp_path, soul, operational + "\n" + wrapped)
    assert prompt.load_authored(s, o)


def test_load_soul_alone_no_longer_checks_the_markers(tmp_path):
    """The markers moved to load_authored. load_soul keeps presence, ceiling and the
    naming and trait checks."""
    soul = REAL_SOUL
    for requirement in prompt.REQUIRED_MARKERS:
        soul = strip_markers(soul, requirement)
    assert prompt.load_soul(write_soul(tmp_path, soul)) == soul.strip()


def test_operational_md_is_validated_like_soul_md(tmp_path):
    """Missing raises, oversize raises without truncating, and the naming and trait
    tripwires cover it."""
    s, o = write_authored(tmp_path)
    o.unlink()
    with pytest.raises(prompt.SoulIntegrityError, match="operational.md not found"):
        prompt.load_authored(s, o)

    padded = REAL_OPERATIONAL + "\n\nPadding past the ceiling. " * 200
    o.write_text(padded, encoding="utf-8")
    with pytest.raises(prompt.SoulIntegrityError) as excinfo:
        prompt.load_authored(s, o)
    assert str(len(padded)) in str(excinfo.value)
    assert "not truncated" in str(excinfo.value).lower()
    assert o.read_text(encoding="utf-8") == padded

    o.write_text(REAL_OPERATIONAL + "\nYou are curious.\n", encoding="utf-8")
    with pytest.raises(prompt.EntityNamingError, match="trait"):
        prompt.load_authored(s, o)


def test_an_injected_soul_text_replaces_both_authored_files():
    """A test that injects its own text sends exactly that: no operational text is
    loaded beside it."""
    system = prompt.build_system_prompt(SITUATION_NO_ELAPSED, soul_text="TEST SOUL")
    assert system == "TEST SOUL\n\n" + SITUATION_NO_ELAPSED


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


def test_assembly_order_is_soul_then_operational_then_situation_then_retrieved():
    """The authored text must precede the elapsed figure, or the gap is stated before
    the statement that says what it means — the confabulation ordering. soul.md comes
    before operational.md (S32)."""
    system = prompt.build_system_prompt(
        SITUATION_WITH_PAIRING, retrieval=make_retrieval()
    )
    soul_at = system.index(REAL_SOUL.strip())
    situation_at = system.index("It has been 14 hours")
    retrieved_at = system.index("records retrieved from earlier")
    assert soul_at == 0
    if REAL_OPERATIONAL.strip():
        operational_at = system.index(REAL_OPERATIONAL.strip())
        assert soul_at < operational_at < situation_at
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

    def spy(system_prompt_chars=0, retrieved_chars=0, context_tokens=None,
            tool_schema_chars=0):
        captured.update(
            system_prompt_chars=system_prompt_chars,
            retrieved_chars=retrieved_chars,
            context_tokens=context_tokens,
            tool_schema_chars=tool_schema_chars,
        )
        return real(system_prompt_chars, retrieved_chars, context_tokens,
                    tool_schema_chars)

    monkeypatch.setattr(prompt.history, "plan_budget", spy)
    retrieval = make_retrieval()
    assembled = prompt.assemble_turn(
        [{"role": "user", "content": "hi"}],
        SITUATION_WITH_PAIRING,
        retrieval=retrieval,
        tool_schema_chars=1234,
    )

    soul = prompt.load_soul()
    # B20: the schemas are a third, separate figure, never folded into the others.
    assert captured["tool_schema_chars"] == 1234
    assert assembled.budget.tool_schema_tokens == history.estimate_tokens_from_chars(1234)
    rendered = prompt.render_retrieved(retrieval)

    assert captured["retrieved_chars"] == len(rendered)
    assert captured["system_prompt_chars"] == (
        len(soul) + assembled.operational_chars + len(SITUATION_WITH_PAIRING)
        + assembled.scaffolding_chars
    )
    # Separate, not summed into one figure.
    assert captured["system_prompt_chars"] != captured["retrieved_chars"] + len(soul)


def test_the_reported_parts_sum_to_the_system_string():
    assembled = prompt.assemble_turn(
        [], SITUATION_WITH_PAIRING, retrieval=make_retrieval()
    )
    assert (
        assembled.soul_chars
        + assembled.operational_chars
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

    # Both authored files are present, verbatim.
    assert "Nobody has given you a name" in assembled.system
    assert "You run when something starts you" in assembled.system
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
    assert assembled.operational_chars == len(REAL_OPERATIONAL.strip())


def test_a_caller_cannot_route_around_the_checks_with_injected_soul_text():
    with pytest.raises(prompt.EntityNamingError):
        prompt.build_system_prompt(
            SITUATION_NO_ELAPSED, soul_text="You are Anam and you are curious."
        )


def test_a_missing_soul_file_raises_rather_than_falling_back(tmp_path):
    with pytest.raises(prompt.SoulIntegrityError, match="not found"):
        prompt.load_soul(tmp_path / "absent.md")
