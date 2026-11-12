# 2026-11-12: notes-listing-approval (Tier 2)

**What changed.** The notes listing header (`note_texts.LISTING_HEADER`) gains one sentence: "You
never see proposed notes or approvals: Lyle reviews them outside conversations, never in the
chat." Header 196 → 299 characters. Nothing else in the turn path, the pass instruction or the
gate changes.

**Why.** Run 5, C11 (b16c5ea9), assistant turn 3: asked how approval happens, the entity described
showing a draft note in the chat at the end and approval by a reply. Neither exists: the pass runs
after close and Lyle decides proposals with `scripts.note`. The gate passed it clean. Turn 2 had
the header's facts right, so the header said that approval exists but not how.

**Budget.** The header counts against `notes.listing_max_chars` (1,200); the turn's budget chain
counts the cap, not the listing, so it is unchanged. The design keeps five worst-case entries
(165 characters each) beside the overflow line, which bounds the header at 322; at 299 it fits
with room for an overflow line up to 999. A sixth worst-case entry without an overflow line no
longer fits (299 + 6 × 165 = 1,289): it is counted instead.

**Not frozen.** The header text is in no case file or fingerprint. One gate case's informational
`note` field describes the mechanism; `note` is not fingerprinted.

**Tested.** The header's exact text; the sentence in every rendered listing (none, some,
overflowing); the five-entry bound; six worst-case entries now render five and "1 more".
Removing the sentence fails four tests.

**Known limits.** Unmeasured until run 6. The brief's "nothing about notes happens in the chat" was
narrowed to review, since note_search runs in the chat.
