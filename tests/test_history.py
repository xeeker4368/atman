"""History windowing: the token budget that decides what gets resent."""

from __future__ import annotations

import pytest

from anam import config
from anam.engine import history


@pytest.fixture(autouse=True)
def _clean_config(monkeypatch):
    """Each test resolves configuration fresh, with no env left over."""
    for name in (
        "ANAM_HISTORY_CHARS_PER_TOKEN",
        "ANAM_HISTORY_OUTPUT_RESERVE_TOKENS",
        "ANAM_HISTORY_SAFETY_MARGIN_TOKENS",
        "ANAM_MODEL_NUM_CTX",
    ):
        monkeypatch.delenv(name, raising=False)
    config.reload()
    yield
    config.reload()


def msg(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def exchange(n: int, chars: int = 100) -> list[dict[str, str]]:
    """n user/assistant pairs, each message `chars` long, oldest first."""
    out: list[dict[str, str]] = []
    for i in range(n):
        out.append(msg("user", f"u{i}" + "x" * (chars - 2)))
        out.append(msg("assistant", f"a{i}" + "y" * (chars - 2)))
    return out


# --- The estimator ----------------------------------------------------------


def test_estimate_rounds_up_never_down():
    """Rounding down is the overflow direction; one token is the cost of not."""
    # 9 chars / 4.0 = 2.25 -> 3, not 2.
    assert history.estimate_tokens("x" * 9) == 3
    assert history.estimate_tokens("x" * 8) == 2
    assert history.estimate_tokens("") == 0


def test_estimate_over_counts_at_the_measured_prose_ratio():
    """The whole point of the 4.0 divisor: over-count real prose, never under.

    Task 1.2 measured 4.63 chars/token over 81,600 chars. Estimating at 4.0
    must predict MORE tokens than that measurement observed.
    """
    chars = 81_600
    measured_tokens = 17_626  # what Ollama actually reported for that prompt
    estimated = history.estimate_tokens_from_chars(chars)
    assert estimated > measured_tokens
    assert estimated == 20_400


def test_dense_content_would_under_count_which_is_the_known_gap():
    """Pins the failure the config comment warns about, so it cannot be forgotten.

    At 3.0 chars/token actual (code, JSON), the 4.0 divisor predicts fewer
    tokens than really occur — the overflow direction.
    """
    chars = 30_000
    actual_tokens_if_dense = chars / 3.0
    estimated = history.estimate_tokens_from_chars(chars)
    assert estimated < actual_tokens_if_dense


def test_message_cost_includes_per_message_overhead():
    """A short turn costs more than its content, or short turns are under-priced."""
    content_only = history.estimate_tokens("hello")
    whole = history.estimate_message_tokens(msg("user", "hello"))
    assert whole > content_only
    assert whole == content_only + history.estimate_tokens("user") + 4


def test_chars_per_token_is_configurable_and_rejects_zero(monkeypatch):
    monkeypatch.setenv("ANAM_HISTORY_CHARS_PER_TOKEN", "3.0")
    config.reload()
    assert history.estimate_tokens_from_chars(30) == 10

    monkeypatch.setenv("ANAM_HISTORY_CHARS_PER_TOKEN", "0")
    config.reload()
    with pytest.raises(config.ConfigError, match="greater than zero"):
        history.estimate_tokens_from_chars(30)


# --- The budget -------------------------------------------------------------


def test_budget_reserves_before_giving_the_remainder_to_history():
    budget = history.plan_budget(
        system_prompt_chars=4_000,   # 1000 tokens
        retrieved_chars=8_000,       # 2000 tokens
        context_tokens=32_768,
    )
    assert budget.system_prompt_tokens == 1_000
    assert budget.retrieved_tokens == 2_000
    assert budget.output_tokens == 2_048
    assert budget.safety_tokens == 512
    assert budget.history_tokens == 32_768 - (1_000 + 2_000 + 2_048 + 512)
    assert not budget.over_committed


def test_budget_defaults_to_the_configured_num_ctx():
    """The window being divided is the one actually pinned on the model."""
    budget = history.plan_budget()
    assert budget.context_tokens == config.model_options()["num_ctx"] == 32_768


def test_over_committed_reservations_give_history_zero_not_negative():
    budget = history.plan_budget(
        system_prompt_chars=200_000, retrieved_chars=0, context_tokens=8_000
    )
    assert budget.history_tokens == 0
    assert budget.over_committed
    assert budget.reserved_tokens > budget.context_tokens


# --- Selection --------------------------------------------------------------


def test_it_is_a_token_budget_not_a_message_count():
    """The same budget holds many short turns and few long ones.

    This is the property that distinguishes decision #6 from the fixed-count
    approach it rejected.
    """
    budget = history.plan_budget(context_tokens=32_768)

    short = history.select_history(exchange(60, chars=40), budget)
    long = history.select_history(exchange(60, chars=4_000), budget)

    assert short.included > long.included
    assert short.omitted == 0
    assert long.omitted > 0


def test_it_keeps_the_most_recent_turns_and_drops_the_oldest():
    messages = exchange(50, chars=2_000)
    window = history.select_history(
        messages, history.plan_budget(context_tokens=8_000)
    )

    assert window.omitted > 0
    # The newest message survives; the oldest does not.
    assert window.messages[-1]["content"] == messages[-1]["content"]
    assert window.messages[0]["content"] != messages[0]["content"]
    # And what survived is the contiguous tail, in order.
    assert window.messages == [
        {"role": m["role"], "content": m["content"]}
        for m in messages[-window.included:]
    ]


def test_selection_returns_chronological_order_despite_walking_backwards():
    messages = exchange(3, chars=50)
    window = history.select_history(
        messages, history.plan_budget(context_tokens=32_768)
    )
    assert [m["content"] for m in window.messages] == [
        m["content"] for m in messages
    ]


def test_it_stops_at_the_first_message_that_does_not_fit():
    """No hole-punching: it must not skip a big turn to fit an older small one.

    A resent history with a gap reads to the model as though the turn never
    happened.
    """
    messages = [
        msg("user", "a" * 40),        # oldest, small — would fit if skipped to
        msg("assistant", "b" * 40),
        msg("user", "c" * 20_000),    # too big for the remaining budget
        msg("assistant", "d" * 40),   # newest, small
    ]
    budget = history.plan_budget(context_tokens=2_800)
    window = history.select_history(messages, budget)

    contents = [m["content"] for m in window.messages]
    assert contents == ["d" * 40]
    assert window.omitted == 3
    assert "a" * 40 not in contents


def test_estimated_tokens_never_exceeds_the_budget_when_it_fits():
    for chars in (10, 500, 3_000):
        budget = history.plan_budget(context_tokens=16_000)
        window = history.select_history(exchange(40, chars=chars), budget)
        if not window.overflowed:
            assert window.estimated_tokens <= budget.history_tokens


def test_nothing_is_summarised_or_truncated_only_omitted():
    """Every message that survives comes through byte-for-byte."""
    messages = exchange(40, chars=1_500)
    window = history.select_history(
        messages, history.plan_budget(context_tokens=8_000)
    )
    originals = {m["content"] for m in messages}
    for kept in window.messages:
        assert kept["content"] in originals


def test_the_newest_message_is_sent_even_when_it_alone_overflows(caplog):
    """Dropping the turn being answered would be worse than overflowing.

    The overflow is flagged and logged rather than silently accepted.
    """
    messages = [msg("user", "old"), msg("user", "z" * 100_000)]
    budget = history.plan_budget(context_tokens=4_000)

    with caplog.at_level("WARNING"):
        window = history.select_history(messages, budget)

    assert window.included == 1
    assert window.messages[0]["content"] == "z" * 100_000
    assert window.overflowed is True
    assert "overflow" in caplog.text.lower()


def test_empty_history_is_not_an_error():
    window = history.select_history([], history.plan_budget())
    assert window.messages == []
    assert window.included == 0
    assert window.omitted == 0
    assert window.estimated_tokens == 0


def test_over_committed_budget_is_reported_as_overflowed():
    budget = history.plan_budget(system_prompt_chars=200_000, context_tokens=8_000)
    window = history.select_history([msg("user", "hi")], budget)
    assert window.overflowed is True


def test_window_carries_the_breakdown_for_inspection():
    """"Why was history only N tokens" must be answerable without re-deriving it."""
    window = history.window_history(
        exchange(5), system_prompt_chars=4_000, retrieved_chars=4_000
    )
    assert window.budget is not None
    assert window.budget.system_prompt_tokens == 1_000
    assert window.budget.retrieved_tokens == 1_000
    assert (
        window.budget.reserved_tokens + window.budget.history_tokens
        == window.budget.context_tokens
    )


def test_it_accepts_sqlite_rows_from_the_message_store(isolated_data_dir):
    """A caller can pass db.get_conversation_messages() straight in."""
    from anam.memory import db

    db.init_databases()
    uid = db.create_user("Lyle", role="admin")
    cid = db.start_conversation(uid)
    db.save_message(cid, uid, "user", "first question")
    db.save_message(cid, uid, "assistant", "first answer")

    rows = db.get_conversation_messages(cid)
    window = history.select_history(rows, history.plan_budget())

    assert [m["content"] for m in window.messages] == [
        "first question",
        "first answer",
    ]
    # Row extras are dropped — this is the payload, not the record.
    assert set(window.messages[0]) == {"role", "content"}


def test_reserving_more_leaves_less_for_history():
    """The reservation actually competes with history rather than being cosmetic."""
    messages = exchange(60, chars=800)
    lean = history.window_history(messages, system_prompt_chars=0, retrieved_chars=0)
    fat = history.window_history(
        messages, system_prompt_chars=20_000, retrieved_chars=40_000
    )
    assert fat.included < lean.included
    assert fat.omitted > lean.omitted
