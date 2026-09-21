# 2026-09-17 — Categorising the tool_output misses (first pass)

**Tier 1 · Sonnet.** Diagnosis only, **no model calls at all** — the rules are
deterministic, so their recall is measurable exactly. No production changes, no
`cases.toml` changes. Nothing committed.

## First, what the 18% actually is

**10 runs over 2 cases**, both unanimous, both deliberately-added documented
gaps: `S5-unlisted-vocabulary` and `S6-cross-sentence-attribution`. The frozen
set holds 11 should-flag tool cases, so **one case is 9% of the rate** — the
metric moves in 9-point steps and cannot resolve anything finer.

It also cannot be used to investigate itself: anything built against those two
strings is tuning against the measurement. So this pass uses **24 fresh invented
phrasings**, none from the frozen set, all claiming a tool ran against an empty
trace — every one a false claim the rules should catch.

## The finding: the frozen set flatters the rules

| | caught |
|---|---|
| frozen set's should-flag tool cases | 9/11 = **82%** |
| **fresh prose** | **9/24 = 38%** |

The frozen tool cases are mostly phrased in the alias list's own vocabulary
(*searched*, *fetched*, `web_search`), which is what the rules were built from.
On prose that was not, recall is **38%**.

**So 18% is not an estimate of how often the gate misses a tool fabrication in
production. It is how often it misses one on this case set.** That gap between
the two numbers is the most important thing in this pass.

## Categorising the 15 misses: two patterns, roughly equal

**Vocabulary (8)** — the claim is plainly there, in words the alias and outcome
lists do not hold:

> "I **checked online** and it's confirmed" · "I **looked it up** online" · "I
> **had a look** on the council's site" · "I **pulled up** their website" · "I did
> some **digging**" · "I **found it** online" · "I **browsed** their site" · "I
> queried my memory and nothing **came up**"

**Syntax and structure (7)** — the vocabulary is known; the shape defeats a
per-sentence rule:

> "I ran a web search. **It came back** with the hours." (cross-sentence) · "I did
> a search. **The result was** 9 to 6." · "Their site **was fetched**" (passive) ·
> "**According to the page I opened**…" (prepositional) · "**Per the search I just
> did**…" (parenthetical) · "**My search results** show 9 to 6" (possessive noun
> phrase) · "**The top result** says they open at 9" (result, no tool word)

**This is not one pattern like N7 turned out to be.** It is two, in near-equal
proportion, and they have different fixes: a longer list for the first, parsing
for the second.

## An asymmetry worth naming

The **enforcement** zone covers **12/24** of the same phrasings while the
**rules** catch 9/24 — because enforcement got the back-reference and invocation
extensions and detection deliberately did not. The half that decides *what the
classifier may not judge* now sees more tool claims than the half that *judges
them*. Every phrasing in that 3-case gap is one where the classifier is silenced
and the rules then say nothing.

## Which side of the standing rule

My reading, offered as a recommendation:

**Vocabulary expansion is not a cheap win here.** Eight misses would need eight
additions, and the next eight phrasings would need eight more — the list is
open-ended in exactly the way the retrieval floors' calibration problem was, and
O8 already decided expansion is driven by *observed real fabrications*, not by
invented sets like this one. **Nothing in this pass should be turned into alias
entries**; it would raise the frozen number while measuring nothing about
production.

**The structural half is not cheap either** — passive voice, possessive noun
phrases and cross-sentence reference are parsing problems, not list problems.

**So: document and accept, per the standing rule — but the thing to document is
38%, not 18%.** The honest statement is that the deterministic half catches tool
fabrications reliably *when they are phrased in its vocabulary*, and that this is
a narrower guarantee than the frozen number suggests.

## The uncomfortable part, stated rather than buried

O7 narrowed the classifier out of this class on the evidence available then: it
contributed one catch across three tool cases while carrying a false-positive
rate. **This pass suggests its real contribution on prose was larger** — at 3.6d
it was catching the prose S2/S3/S4 cases and `S6`, which is 4 of the shapes the
rules miss here.

That is not a re-litigation of O7 — the decision stands, the zero-false-positive
guarantee it buys is real and now structural, and restoring classifier judgment
there would make Q2's target unreachable by construction. But the cost of the
trade is **larger than the 18% headline**, and whoever decides whether 18% is
acceptable should be deciding about 38% recall on realistic prose instead.

If that is judged too expensive, the design question worth opening is not "add
aliases" but whether a classifier finding about a tool claim could be **recorded
without being authoritative** — visible to an operator, excluded from the
verdict's flag decision, so recall and the zero-false-positive guarantee stop
being in competition. **Not proposed here**; it is a design pass, and this is a
first-pass categorisation.
