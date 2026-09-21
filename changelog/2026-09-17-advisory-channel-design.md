# 2026-09-17 — Advisory channel: design (revision 7)

**Tier 3 · Opus (design).** Design only. **No code**: `gate.py`,
`classifier.py`, `migrations.py`, `architecture.md` and `cases.toml` are
untouched. Design of record: `docs/FABRICATION_GATE_DESIGN.md` revision 7
(F30–F36, O16–O19). Nothing committed. Stops here for review.

## The finding that shapes the design

The obvious implementation — route what the O7 enforcement already discards into
a new field — **does not deliver the measured signal**, and pursuing it would
quietly weaken the guarantee it is supposed to preserve.

What the enforcement discards today is *leakage*: the classifier objecting to
tool claims despite a prompt telling it not to. F30's 50% recall was measured
with that exclusion **removed**. Recovering it means restoring the classifier's
tool remit in the prompt — and then:

> the exclusion bullet and the enforcement predicate are belt and braces. Remove
> the bullet and the guarantee rests entirely on `tool_outcome_sentences()`,
> whose coverage over the same 24 claims is **12/24**. An unrecognised shape
> would land in the authoritative findings list and flag.

That is the route revision 5 closed, re-opened from the other side.

## So: routing by label, not by inference

The reply grammar gains `CONTRADICTS-TOOL` alongside `CONTRADICTS-SELF`, so a
tool-claim objection arrives already labelled and routing is explicit rather than
attribution-dependent. The enforcement predicate stays, applied to self-claim
findings, as the backstop for a mislabelled objection.

**Cost:** the prompt and the shared grammar change, so both frozen sets need
re-measuring, and F13's deadlock policy applies — 3.3 inherits the grammar.

**Not a cost:** no extra model call, no extra latency, no floor change. The
signal comes from the call that already runs.

## Storage: a new column, and why

`messages.integrity_advisory` (nullable JSON, migration 4), rather than a key
inside `integrity_check`. It follows migration 3's own precedent: `integrity_check`
was kept out of `tool_trace` because *"a turn with no tools would otherwise carry
a tool trace describing an integrity check"* — the same argument against putting
a non-authoritative signal inside the authoritative verdict. Per `AGENTS.md`, the
column decision goes up before it is coded, which is what this is.

## The boundary, proven directly

Five tests specified, including a property assertion that `status` is unchanged
for arbitrary advisory content, a source-level check that `status`/`clean` never
reference the advisory attribute, and — the one that protects the measurement —
**`gate_eval`'s numbers must be byte-identical with and without advisory
content**, so the frozen set cannot move because of this channel.

Direct assertions rather than inferred ones: that standard is in the design
because this project has now twice watched an inferred guarantee hide a gap.

## The operator surface is not invented

Phase 9's admin panel is the natural consumer and **does not exist**. This ships
no viewer: the column is queryable by SQL and one `INFO` line fires when the
advisory speaks on an otherwise-clean turn. Building a surface now would be
building a consumer for a signal nobody has looked at; the dependency is recorded
so Phase 9 inherits it.

## Open questions

**O16** one labelled call versus a second dedicated call (+2 s/turn, floor stays
35; avoids touching the identity prompt at all) · **O17** whether the advisory
records everything or only what the rules missed · **O18** whether 3.3 should
inherit a verdict form it never emits · **O19** the bar for keeping a channel
that has no consumer — without one it becomes a field nobody reads and nobody
dares delete.
