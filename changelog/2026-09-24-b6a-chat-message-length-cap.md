# B6a: a derived length cap on chat messages

Date: 2026-09-24 · merged-queue item 16, chat half · plan B6a · Tier 2. The approved
plan was: a new config value derived from the embedding and history budgets, not
guessed. B6b (login and password lengths, the throttle, `__entity__`) is separate, as
split at review.

## What was wrong

`ChatRequest.message` had only `min_length=1`, and `turn.handle_user_message` had no
cap. An arbitrarily long message would therefore be:

- persisted to both stores before generation, including the append-only archive, where
  it can never be removed;
- embedded in full at idle-close, one call per 5,000-character piece;
- sent to the model even if it alone exceeded the context window. `history.select_history`
  always sends the newest message, because dropping the turn being answered is worse,
  and the model server truncates an overflowing prompt without an error.

## The derivation

Recorded in `config/defaults.toml` and recomputed by a test from live config. All
figures use the budget's own 4.0 characters per token.

| term | tokens |
|---|---|
| `num_ctx` | 32,768 |
| output reserve | −2,048 |
| safety margin | −512 |
| per-message overhead | −4 |
| `soul.md` at its enforced 6,000-character ceiling | −1,500 |
| situation block, 600-character allowance (206 measured) | −150 |
| retrieval: 10 hits × 5,000 characters, + annotations (under 6,000, asserted), + headers (~1,000) = 57,000 characters | −14,250 |
| **left for the message** | **14,304 = 57,216 characters** |

Rounded down to **50,000**. That rounding is the only judgment in the chain. It also
leaves about 1,800 tokens of earlier history beside a maximal message.

## What changed

- **`chat.max_message_chars = 50,000`.**
  - In the built-in defaults, and in `defaults.toml` with the derivation.
  - `ANAM_CHAT_MAX_MESSAGE_CHARS` overrides it.
  - Read by `config.chat_max_message_chars()`, which raises `ConfigError` below 1.
- **`turn.MessageTooLongError`.**
  - Raised in `handle_user_message` right after the empty-message check.
  - That is before the conversation is resolved, before anything is saved, and before
    the model is called.
  - Measured on the stripped text, which is what gets stored.
- **`routes/chat.py`** maps it to 413 with the limit in the detail.
- **Why not pydantic `max_length`:** a pydantic field limit is fixed at import time and
  couldn't follow config. Putting the check in `turn.py` also covers any future caller
  of the turn.

## Tested

In `tests/test_turn.py` (4) and `tests/test_chat_route.py` (1):

- **Over the limit.** `MessageTooLongError` is raised, both stores' message counts are
  unchanged, no conversation row exists, and the model was never called.
- **Exactly at the limit.** The message is answered.
- **The limit is measured after stripping.** Surrounding whitespace doesn't count.
- **The derivation, recomputed from live config.** It asserts the configured cap is no
  larger than what fits beside a maximal turn. It currently computes 57,216.
- **Route.** Returns 413 with the limit in the detail, and nothing is stored.

**Proof it bites:**

- Removing the check fails the turn and route refusal tests.
- `ANAM_CHAT_MAX_MESSAGE_CHARS=60000` fails the derivation test with "exceeds the 57216
  characters that fit beside a maximal turn".

Full suite **1221 passed, 2 skipped**. `ruff check .` clean.

## Known limitations

- **The retrieved-records block has no cap. Tracked as B17.**
  - The derivation covers ranked hits only.
  - Split-sibling attachment can add up to 3 more 5,000-character pieces per hit, and
    nothing limits the rendered block. At the extreme that alone exceeds the window.
  - History already reports `overflowed` and logs a warning. Nothing prevents it.
- **Messages near the limit make the correction classifier miss.**
  - A message near the cap is about 12,000 tokens in that classifier's prompt.
  - At the measured 227 tokens/s of prompt evaluation, that is about 55 s, past the
    classifier's 45 s timeout. The correction check then records no link: a miss, which
    is the safe direction.
  - This is reasoned from measured rates, not measured on a real long message.
- **The situation allowance (600 characters) is a judgment value.** 206 was measured.
- **Nothing tells the entity the limit exists.** The refusal goes to the person as a
  413, and the message never reaches the model.
