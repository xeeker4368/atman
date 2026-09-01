"""Token-budgeted history windowing: what gets resent to the model each turn.

Decision #6 in ``NOW.md``. The window is a **token budget**, not a message
count. Reserve space for the system prompt, the retrieved chunks and the
model's own output; whatever remains goes to the most recent raw history,
newest-first, until the next message would not fit.

**Nothing is deleted and nothing is summarised.** A turn that falls outside the
window simply stops being resent. It stays in both stores and stays retrievable
through ``memory_search`` exactly as before. This module never writes anything.

Why a budget and not a count
----------------------------
A fixed message count prices a one-line "ok" the same as a 4,000-word pasted
document. The count that is safe for the second is wasteful for the first, and
the count that is comfortable for the first overflows on the second. The budget
is the thing the model server actually enforces, so it is the thing to measure
against.

The estimator, and which way it is wrong
----------------------------------------
Characters divided by ``history.chars_per_token``. No tokenizer dependency —
that would pin us to one model's vocabulary for a number used only to decide
where to cut.

**Margin direction matters more than margin size.** The two errors are not
symmetric:

* Estimating *more* tokens than really occur under-fills the window. The
  omitted history is still retrievable. A non-event.
* Estimating *fewer* tokens than really occur overflows ``num_ctx``. The model
  server silently drops the overflow — the oldest content disappears with
  nothing raised. This is the failure this module exists to avoid.

So the estimate must err toward *over*-counting tokens, which means the divisor
must sit **at or below** the true chars-per-token ratio of the text.

Task 1.2 measured **4.63 chars/token** over an 81,600-character real prose
prompt, so the configured 4.0 over-counts by ~14% on prose. That is the safe
direction, and it is measured rather than assumed.

**It is not safe on every kind of content, and this is the known gap.**
Symbol-dense text — code, JSON, tool traces — runs nearer 3 chars/token. At 3.0
actual against a 4.0 divisor the estimator *under*-counts by ~33%, which is the
overflow direction. See ``config/defaults.toml`` under ``[history]`` for the
arithmetic and for how this margin relates to the embedding-input margin, which
is deliberately much more conservative.

Per-message overhead
--------------------
A message costs more than its content: the chat template wraps every turn in
role markers and delimiters. ``history.message_overhead_tokens`` is charged per
message so a long run of short turns is not systematically under-priced. It is
an unmeasured judgment value — see the changelog.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from anam import config

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Estimated token count for a string, rounded up.

    Rounds up rather than to nearest: rounding down would be the overflow
    direction, and at this granularity the cost of rounding up is one token.
    """
    if not text:
        return 0
    return math.ceil(len(text) / config.history_chars_per_token())


def _field(message: Mapping[str, Any], name: str) -> str:
    """Read one field from a dict or a ``sqlite3.Row``.

    ``sqlite3.Row`` supports ``__getitem__`` but not ``.get()``, so anything
    reading these messages has to go through subscripting. Callers pass rows
    straight from ``db.get_conversation_messages()``, so this is the common
    case rather than an edge one.
    """
    try:
        value = message[name]
    except (KeyError, IndexError):
        return ""
    return "" if value is None else str(value)


def estimate_message_tokens(message: Mapping[str, Any]) -> int:
    """Estimated cost of one message, content plus per-message overhead.

    The role string is counted too — it is part of what gets sent, and it is
    free to include in the arithmetic.
    """
    return (
        estimate_tokens(_field(message, "content"))
        + estimate_tokens(_field(message, "role"))
        + config.history_message_overhead_tokens()
    )


@dataclass(frozen=True)
class BudgetBreakdown:
    """Where the context window went.

    Every field is in estimated tokens. Kept as a record rather than a single
    number so that "why was history only 900 tokens" is answerable without
    re-deriving the arithmetic — the reference build's windowing was a bare
    slice with no way to ask that question.
    """

    context_tokens: int
    system_prompt_tokens: int
    retrieved_tokens: int
    output_tokens: int
    safety_tokens: int
    history_tokens: int

    @property
    def reserved_tokens(self) -> int:
        return (
            self.system_prompt_tokens
            + self.retrieved_tokens
            + self.output_tokens
            + self.safety_tokens
        )

    @property
    def over_committed(self) -> bool:
        """True when the reservations alone exceed the context window.

        History gets zero in that case. It means the caller asked for more
        system prompt and retrieved chunks than the window holds, which is a
        caller problem this module can only report, not fix.
        """
        return self.reserved_tokens > self.context_tokens


@dataclass(frozen=True)
class HistoryWindow:
    """The selected history and an account of what happened.

    ``messages`` is chronological — oldest first — regardless of the fact that
    selection walks backwards. Callers hand this straight to the Ollama client.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    omitted: int = 0
    estimated_tokens: int = 0
    budget: BudgetBreakdown | None = None
    overflowed: bool = False

    @property
    def included(self) -> int:
        return len(self.messages)


def plan_budget(
    system_prompt_chars: int = 0,
    retrieved_chars: int = 0,
    context_tokens: int | None = None,
) -> BudgetBreakdown:
    """Divide the context window, returning what is left for history.

    The system prompt and the retrieved chunks are passed in **as character
    counts by the caller**, not built here. Task 1.9 owns prompt assembly and
    task 1.5 owns retrieval; both are Tier 3 and neither exists yet. Taking
    their sizes as inputs means this module is complete and testable now, and
    does not have to be revisited when they land.
    """
    ctx = context_tokens if context_tokens is not None else int(
        config.model_options().get("num_ctx", 32768)
    )
    system_tokens = estimate_tokens_from_chars(system_prompt_chars)
    retrieved = estimate_tokens_from_chars(retrieved_chars)
    output = config.history_output_reserve_tokens()
    safety = config.history_safety_margin_tokens()

    remaining = ctx - (system_tokens + retrieved + output + safety)
    return BudgetBreakdown(
        context_tokens=ctx,
        system_prompt_tokens=system_tokens,
        retrieved_tokens=retrieved,
        output_tokens=output,
        safety_tokens=safety,
        history_tokens=max(0, remaining),
    )


def estimate_tokens_from_chars(chars: int) -> int:
    """Token estimate for a length already counted in characters."""
    if chars <= 0:
        return 0
    return math.ceil(chars / config.history_chars_per_token())


def select_history(
    messages: Sequence[Mapping[str, Any]],
    budget: BudgetBreakdown,
) -> HistoryWindow:
    """Take the most recent messages that fit the budget.

    Walks newest to oldest and stops at the **first** message that does not
    fit, rather than continuing to look for a smaller one further back. Skipping
    over a turn to include an older one would resend a conversation with a hole
    in it, which reads to the model as though the turn never happened.

    The newest message is always included, even when it alone exceeds the
    budget. Dropping the turn the model is being asked to respond to would be a
    worse failure than overflowing, so instead ``overflowed`` is set and a
    warning is logged — the overflow is visible rather than silent.
    """
    if not messages:
        return HistoryWindow(budget=budget)

    selected: list[dict[str, Any]] = []
    used = 0
    overflowed = False

    for message in reversed(messages):
        cost = estimate_message_tokens(message)
        if used + cost > budget.history_tokens:
            if not selected:
                # The newest message on its own does not fit.
                selected.append(_normalise(message))
                used += cost
                overflowed = True
                logger.warning(
                    "History budget %d tokens is smaller than the most recent "
                    "message (%d tokens); sending it anyway. The context window "
                    "may overflow and the model server will drop the excess "
                    "without reporting it.",
                    budget.history_tokens,
                    cost,
                )
            break
        selected.append(_normalise(message))
        used += cost

    selected.reverse()
    return HistoryWindow(
        messages=selected,
        omitted=len(messages) - len(selected),
        estimated_tokens=used,
        budget=budget,
        overflowed=overflowed or budget.over_committed,
    )


def _normalise(message: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a message to what the chat API takes.

    Accepts ``sqlite3.Row`` as readily as a dict, so a caller can pass
    ``db.get_conversation_messages()`` straight in. Everything else on the row —
    ids, timestamps, tool traces — is deliberately dropped: this is the payload
    sent to the model, not the record.
    """
    return {"role": _field(message, "role"), "content": _field(message, "content")}


def window_history(
    messages: Sequence[Mapping[str, Any]],
    system_prompt_chars: int = 0,
    retrieved_chars: int = 0,
    context_tokens: int | None = None,
) -> HistoryWindow:
    """Plan the budget and select against it in one call."""
    budget = plan_budget(system_prompt_chars, retrieved_chars, context_tokens)
    return select_history(messages, budget)
