# 2026-11-05: note-quote-check (Tier 2; amended 2026-11-06)

**What changed.** The note pass's quote check (`program/tools/note_quotes.py`). A quote is stripped
of surrounding whitespace and wrapping quote marks and split into fragments on ellipses (two or
more dots, or `…`), line breaks and sentence ends (`.`, `!` or `?` then whitespace, closing quote
marks allowed between); each fragment loses trailing `.!?,`. Every fragment must appear,
normalised (whitespace, curly quotes, case), inside one message a person wrote in this
conversation, and at least one must have `notes.min_fragment_words` (3) words or more. Evidence is
one entry per fragment, each carrying the quote as the model gave it; `user_message_id` is the first
fragment's message. A refusal names the failing fragment, or all of them when all are short.
`scripts.note passes` shows `none (N refused)` and lists a pass with refusals in its latest attempt
in the default view (display only); `review` shows a fragment line.

**Why.** Run 5: 6 ADDs in 10 passes, 5 refused by the check, 1 accepted. Of the 5, one joined
messages with ellipses, one was wrapped in quote marks, three joined messages as sentences (one
of these also changed a word). Four are now accepted; the changed word stays refused. The
refusals read as outcome `none` and were hidden in `passes`.

**Also changed by the rule** (agreed in review). No other conversation is searched (the store tier
was `note_propose`'s); a fragment in several of the person's messages resolves to the newest.
`notes.min_quote_chars` and `notes.messages_containing` are gone (unread). `notes.min_quote_words`
(4) still renders into the pass instruction, unchanged. `ARCHITECTURE.md`'s quote invariant updated.

**Tested.** Every joining shape, wrapping marks, curly/case/space, end punctuation not typed, short
beside long, only short, a changed word in one sentence, reply and invented fragments, another
conversation, a pass writing two evidence entries, `passes` default and `--all`. Mutations (each
split, trailing strip, the minimum both ways, mark stripping, the display and the default view)
each fail tests. Replayed on a copy of run 5: rows 1, 2, 5, 7 accepted, 8 refused.

**Known limits.** Whether a quote supports its note is not checked (C9).
