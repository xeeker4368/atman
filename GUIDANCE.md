# GUIDANCE.md

Behavioral and operational principles governing how the entity acts and how
capabilities are gated. This is about *what the system should do*, as
distinct from `AGENTS.md` (how CC should work) and `PROJECT.md` (what the
project is).

## The two-axis capability model

Every gateable capability (Moltbook posting, bounded research execution,
image generation, etc.) has two independent settings, not one:

- **`enabled`** — does this capability exist at all right now.
- **`approval_required`** — does its output need human review before it
  takes effect.

These are orthogonal. A capability can be enabled with approval required
(entity can draft, human confirms before it goes live) or enabled with no
approval (goes live immediately once triggered).

**Authorization rule: propose vs. execute, not who triggered it.**
Proposing something (a research candidate, a draft) never requires a flag —
it's inert until approved. Executing something always requires the
relevant `allow_*` flag to be on, **except** when a human is directly
driving the action in the moment (you ran the command, you approved a
queued item) — that always just works. The flags exist for exactly one
case: fully unattended, no-human-in-the-loop execution.

Turning `approval_required` off for anything with real external effect
(posting, sending) uses the same interaction as every other setting: it
becomes a pending change, a Save button appears, clicking Save commits it
and it's live immediately. No special extra confirmation step. This was a
deliberate choice to keep the panel uniform rather than special-case one
toggle — see `NOW.md` decision log entry 9 if this needs revisiting.

## Memory integrity

**Fabrication detection** is unified — one detector covers both tool-output
fabrication (invented artifact IDs, claimed actions that didn't happen) and
identity-claim fabrication (false statements about the entity's own nature
or mechanism). Do not build these as two separate systems.

**Corrections and supersession.** When a human corrects something the
entity said, that correction should be retrievable as current going
forward — not sitting alongside the original wrong claim with no signal
about which one is current. Detecting "this message corrects that prior
claim" is model-judged (a small classification call), not keyword
heuristics. A correction gets linked to what it corrects via a
`supersedes` relationship; retrieval must respect that link. This
mechanism needs its own frozen eval case set before being trusted in
production — same bar as the fabrication gate.

**Raw experience is never edited or deleted to fix it.** Provenance is
sacred. Corrections layer on top of the record via supersession; they
don't rewrite history.

## Sense of time

The current real timestamp is injected into every turn — this stays a flat
statement of fact, computed fresh, no wrapper narrative.

**New:** elapsed time since the user's last message is explicitly computed
and stated ("It has been 14 hours since your last message"). This must be
paired, in `soul.md`, with an explicit statement that this gap represents
no experience, no continuity, and nothing to have "felt" during it. This
pairing exists specifically to prevent the confabulation pattern already
seen in the prior implementation (claims of persistent "training" or
"reflection" happening between turns, which is not how the system works —
it's stateless between calls). Do not state elapsed time without this
pairing.

## Entity discretion

The entity may decline. This is a real, intended capability — not
something to design around or discourage. The first concrete instance:
the entity can refuse to share a piece of its own creative writing if
asked, even by Lyle, even by Jodie. This needs no special mechanism — it's
a values statement in `soul.md` establishing that declining here is
legitimate, and the model already knows how to decline things when it
believes it's allowed to.

Personality is observed, not assigned. Do not inject personality traits,
sliders, or "you are like X" framing anywhere. If patterns in behavior are
worth noting, they get recorded as evidence-linked observations, not
prescriptions.

## Creative writing

No gate. Lowest-risk category in the system — no external effect, nothing
irreversible. Can be triggered by the entity in live conversation or in any
autonomous/background session (research mining pass, bounded research
execution, nightly reflection cycle). Indexed into memory like any other
content, tagged with its own `source_type`. Private by default — not
proactively pushed into conversation — but discoverable if the entity or a
human brings it up, and refusable per the discretion principle above.

## Research and mining

Conversation memory (from any user, including Jodie) is legitimate source
material for the periodic research-mining pass, even though only the
entity itself or Lyle can actually initiate a research run. Mining and
self-flagging both only ever produce candidates for the human-approved
queue — see the two-axis model above for what governs actual execution.
