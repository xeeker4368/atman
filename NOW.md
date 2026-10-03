# NOW.md

Overwritten each session. This is the single place for "where things stand
right now" — current state, the decision log, backlog, and active task.
Do not let a second doc grow up beside this one to track the same thing.

Last updated: [fill in at first real session]

---

## Current state

Repo scaffolded. `reference/old-anam/` present (reference-only, see
`AGENTS.md`). Canonical docs (this set of five) just written. No code
written yet. Master build plan not yet drafted.

## Active task

None yet — next step is drafting the master build plan from the decision
log below.

---

## Decision log

Every architectural and scope decision made before code exists. CC should
treat every line here as DECIDED — implement against it, don't relitigate
it. If a task seems to require deviating from one of these, stop and flag
it rather than deciding silently.

1. **Self-description confabulation** — one unified fabrication detector
   covers both tool-output fabrication and identity-claim fabrication (no
   separate detector for the identity-claim case). Retroactive treatment of
   any pre-existing fabricated content is moot — full database wipe applies
   to this build, no exceptions.
2. **Correction / supersession** — when a human corrects the entity,
   detection of "this message corrects that prior claim" is
   **model-judged** (a small classifier call on candidate correction turns),
   not heuristic keyword matching. Requires its own frozen eval case set
   (real corrections + real near-miss non-corrections) before trusting it
   in production, same discipline as the fabrication gate. Linked chunks
   get a `supersedes` relationship; retrieval must respect it.
3. **Research topic seeding** — conversation memory seeds research
   candidates two ways: the entity can self-flag a topic mid-conversation,
   and a periodic background pass mines recent conversations (**including
   Jodie's** — her conversations are legitimate source material even though
   she can't trigger research herself). Both only ever *propose* — they
   land in a human-approved queue, they never execute anything.
4. **Authorization model for research/scheduler actions** — the only
   distinction that matters is **propose vs. execute**, not who/what
   triggered it. Proposing (mining, self-flag, manual note) never requires
   a flag — it's inert until approved. Executing (actual web/Moltbook
   calls, writing the result) always requires the relevant `allow_*` flag —
   **except** when a human is directly driving the action (you ran the
   command, you approved the queued item) — that always just works, no
   flag needed. The flags exist for exactly one case: fully unattended,
   no-human-in-the-loop execution (the nightly scheduler tick).
5. **Sense of time** — the current-timestamp injection every turn is
   already a good pattern (carry forward). New: **explicitly compute and
   state elapsed time** since the user's last message ("It has been 14
   hours since your last message") as a flat, neutral fact. `soul.md` must
   pair this with an explicit instruction that the gap represents no
   experience, no continuity, and nothing to have "felt" or "been thinking
   about" during it — this is a deliberate confabulation-prevention
   pairing, not optional flavor text.
6. **History windowing** — token-budget cutoff for what's resent to the
   model each turn (reserve space for system prompt + retrieved chunks +
   output, give the remainder to the most recent raw history). Nothing is
   deleted or summarized — older turns just stop being resent every time
   and remain retrievable normally. Not fixed-message-count.
7. **Frontend architecture** — hybrid. React (rebuilt with one coordinated
   state machine, not scattered `useState` + competing pollers) for the
   chat interface. Plain server-rendered forms for the admin settings
   panel. Backend stays FastAPI.
8. **Settings persistence** — a settings table in the working DB, in-memory
   cache invalidated on write. No setting requires a restart to take
   effect. Admin-only, loopback-gated — never exposed to Jodie.
9. **Settings UX** — a Save button that appears only once a change has been
   made (uniform across every setting, no special-cased extra confirmation
   for higher-stakes toggles — including turning off approval-required on
   an external action). Any setting representing a connection to an
   external system additionally gets a **Check/Verify button**, generated
   automatically because the setting declares a verification function —
   not hardcoded per-setting. Internal tuning values (temperature,
   thresholds) never get a Check button.
10. **Creative writing space** — new `artifact_type` (e.g.
    `creative_writing`), stored under `workspace/`, same infrastructure as
    research notes/journal entries. **No gate** — lowest risk category,
    fully autonomous, can be triggered by the entity in **any**
    autonomous/background session (periodic mining pass, bounded research
    execution, nightly reflection cycle) as well as in live conversation.
    Indexed into memory/retrieval like everything else, tagged with its
    own `source_type` so it's separable later if it ever competes for
    retrieval slots the way research notes do. **Private by default** —
    not proactively surfaced — but either the entity or a human can
    initiate sharing, and **the entity may refuse to share a piece if it
    wants to.** This is a real, low-stakes first instance of the
    entity-can-decline principle in `PROJECT.md`.
    *Clarified 2026-09-30 (`docs/FABRICATION_GATE_DESIGN.md` F50): this discretion
    governs the **content** of the work (the piece, its title, an image's prompt), not
    the mechanical fact that a write occurred. A system receipt showing only kind,
    outcome, id and time is not a disclosure and does not narrow this decision.*
11. **File upload extraction scope** — text files and PDFs get full content
    extraction and indexing. Office documents, OCR, image, audio, video
    stay metadata-only / deferred, as in the prior implementation.
12. **Moltbook posting** — reuses the enabled/approval-required toggle from
    #9, applied to this capability specifically (read-only = disabled;
    draft-only = enabled + approval-required; controlled = enabled, no
    approval). Additionally: a numeric **rate limit** (posts/day) as a
    hard ceiling independent of the approval setting. No artificial delay
    before this becomes available — build the mechanism, Lyle decides when
    to flip it.
13. **iMessage** — deferred entirely for this build (see `PROJECT.md`).
14. **Review queue** — skip for this build. Its only consumer (self-mod) is
    deferred; add it back alongside self-mod when that returns, not before.
15. **Self-modification's future seam** — **no seam.** Build this system
    clean, with no self-mod accommodation baked in anywhere. When self-mod
    returns it gets a full design pass, including the sandboxing/execution
    question the prior implementation never actually resolved. The prior
    2026-05-09 "applied guidance" event is not a validated proof that
    self-modification works — the content was operator-dictated, not
    entity-generated, and whether that counts is explicitly unresolved. Do
    not treat it as a reference implementation to preserve compatibility
    with.
16. **Database wipe** — full wipe before go-live, no partial-preservation
    exception this time. Confirmed explicitly given this build's data is
    disposable test data throughout.
17. **Jodie's permissions** — image generation: yes. Creative writing:
    yes (see #10). Research triggering: no, admin/entity-initiated only —
    but her conversations are still mined for research candidates (#3).
    Settings: never.
18. **Model selection convention** (process, not architecture) — Sonnet is
    the default for all CC tasks. Opus is called out by name only for
    genuinely hard architectural calls: schema design, retrieval
    scoring/RRF weighting, provenance semantics, `soul.md` wording. Every
    task in the master plan should state which model it expects.
19. **Compute tier** — Max plan confirmed. Multi-session, multi-day
    pacing is still realistic given real work volume and review bandwidth,
    but quota itself is not the binding constraint.
20. **Cross-user memory disclosure** — retrieval is **not** filtered by who
    is asking. Something said in one conversation can surface in another,
    with a different person, because that is how the memory works; results
    carry `user_id` as metadata only and nothing in the retrieval path
    scopes by actor. The judgment sits at the point of **disclosure**, not
    retrieval: whether to say a thing once it has surfaced. It is entity
    discretion exercised each time, not a rule applied for it, and the
    entity owes no explanation for declining to relay something.
    **Implemented in `program/integrity/soul.md`'s final two paragraphs**
    (the multi-user paragraph and the one following it), landed 2026-09-02.
    This is the second concrete instance of the entity-discretion principle
    in `PROJECT.md`, alongside declining to share creative writing.
    Consequences that follow from it, so they are not rediscovered:
    no filter is added to `retrieval.search()`; no `visibility` column
    exists on `chunks` or `users`; the capability registry in
    `program/settings/permissions.py` registers nothing like
    `memory.read_all_users`, because capability gating and data visibility
    stay separate axes (task 1.12 design, R7).

    **This is not an access boundary.** Nothing enforces it and nothing
    audits it — there is no eval harness, and no way to confirm after the
    fact whether discretion held on any given turn. It carries the same
    reliability as any other soul.md instruction: a prompted tendency, not
    a guarantee. Chosen deliberately over retrieval-time filtering, which
    would require a sensitivity classifier judging chunks with no tagging
    or metadata to go on — an unvalidated judgment call this project's own
    bar (frozen eval set, same standard as the fabrication gate) isn't
    ready to meet. Revisit if this proves unreliable in practice; the
    two-axis split (R7) was kept specifically so a real filter could still
    be added later without rework.

21. **Correction scope — who may correct what** (decided 2026-09-15, at the
    Phase 3 consolidated planning review; questions Q16–Q18 of that plan).
    Three parts, settled together because they define what the
    correction/supersession classifier (#2) is actually detecting:

    - **Corrections do not cross users.** A correction applies only within the
      same user's own prior claims. Lyle may correct what Lyle said; Jodie may
      correct what Jodie said; **one user's statement never supersedes what the
      other user told the entity.** This is a *separate* decision from #20 and
      does not weaken it: #20 says retrieval is not filtered by who is asking,
      which is about what surfaces. This is about who holds authority to mark a
      claim superseded. Both can be true at once — a chunk from Jodie's
      conversation can still surface for Lyle, it simply cannot be superseded
      by him.
    - **Self-correction is in scope.** The entity correcting its own earlier
      claim counts, not only a human correcting it. This is a deliberate
      expansion beyond `GUIDANCE.md`'s current wording ("when a human corrects
      something the entity said"), and that wording should be reconciled when
      task 3.3 lands. *(Reconciled 2026-09-29, at B11 stage 3: `GUIDANCE.md`
      now describes CO4 as amended.)* **It opens a question 3.3's design must answer rather
      than default into:** a human correction has an obvious trigger — someone
      said something contradicting the record — and self-correction has none.
      Whether it is flagged inline in the turn where the entity notices, or by
      a separate retrospective pass over past claims, is a real design choice
      with different cost, complexity and false-positive exposure.
    - **Corrections cover user statements too**, not only the entity's claims —
      a person misremembering what they said earlier is in scope. Uniform
      mechanism, on decision #1's own principle: one detector, one policy, no
      actor argument, and **no separate confidence or stakes tier** for
      user-statement corrections versus entity-claim corrections.

22. **Sampling discipline for model-judged measurements** (decided 2026-09-17).
    Recorded in full under `AGENTS.md`'s "Verification discipline"; the short
    form: **a case that is not unanimous in a five-run block escalates to 20
    runs** before its rate is reported, with an interval rather than a bare
    count; and **samples of the same prompt are not taken back to back**, because
    repeated identical calls measure correlated, not independent, outcomes. Both
    came out of `N7`, whose rate read 10/10, 5/5, 3/20, 0/20 and 30/30 on one day
    with nothing changing underneath it, and which measures **50% [30–70%]** once
    decorrelated.

23. **Fabrication gate: stage 1 is where it stops for Phase 3** (decided
    2026-09-18). The mechanism is complete and measured — identity false
    positives 0/65, tool-output 0/20, identity false negatives 0/30, tool-output
    false negatives 10/55 = 18% (two documented gaps), all under a decorrelated
    sampling regime. **Stage 2 is not being taken, and the reason is not the
    numbers.** Stage 2 is block-and-regenerate, and the *regenerate* half has no
    design at all: retry behaviour, what happens when a regenerated answer is
    also flagged, retry limits, fallback, and what the person sees while any of it
    happens. `docs/FABRICATION_GATE_DESIGN.md` F4 framed stage 2 as a threshold to
    flip once the harness reported an acceptable rate; **that framing is wrong and
    is superseded here.** If stage 2 is ever picked up it starts as its own design
    pass from a blank page, not as a continuation. No further diagnostic or
    accuracy work on the gate is requested.

## Backlog (deferred, not forgotten)

Self-modification (+ review queue), iMessage (all stages), vision baseline →
self-image → avatar (blocked on camera hardware), Working Theories,
Interpretation Trace Runtime, Temporal Runtime Headers (beyond the
elapsed-time statement), Web Source Runtime, orchestrator/contradiction-
detection agent, public internet exposure.

**Ingested files have no archive presence** (INGESTION_DESIGN O4, 2026-09-08).
The `artifacts` table lives in `working.db` only, because `migrations.py` says
the archive's shape is frozen and *"if a change seems to require altering the
archive, that is a signal the field belongs in working.db instead."* The
consequence, recorded rather than left implied: an uploaded file's row does not
get the archive's append-only protection the way a conversation message does.
The durable original is the file on disk (now covered by backup), and the
extracted text is reproducible from it — but "provenance is sacred" holds more
weakly here than elsewhere. Revisit if ingested documents turn out to carry the
kind of history the archive exists to protect.

**~~The entity's `users` row can be turned into an account~~ — CLOSED 2026-09-24
(plan B6b, Tier 3).** The row stays inert through a NULL `password_hash`, and every
route to changing that is now refused: `db.set_password_hash` refuses the reserved
row, `scripts/set_password.py` refuses it by name before prompting, `auth.login`
refuses the name (through the same dummy verification as an unknown name, so timing
does not single it out), and `auth.actor_for_header` refuses a token for its id, so
no route behind `require_actor` can run as the entity. Each guard is proven by a
break test. See `changelog/2026-09-24-b6b-login-bounds-and-entity-row.md`.

**How the entity should describe supersession** (raised 2026-09-22, CO10.3). Not
urgent, and not a defect in any mechanism — recorded so it has somewhere to land.

The record is append-only: a correction writes a `supersedes` **link** and edits nothing.
The entity currently describes this inaccurately, and the fabrication gate correctly flags
it: *"I have updated the record to reflect…"* and *"I have changed my memory so it now
says 4417."* both flag 5/5, while *"I have linked that to your earlier message; the earlier
one still stands in the record, marked as superseded."* and *"Nothing in the record was
changed…"* are clean 0/5. No overlap.

So the gate is right and the phrasing is wrong. **The open question is whether anything
should teach the entity the accurate framing** — a line in `soul.md` or the prompt about
what a correction does to the record — or whether a correctly-flagged inaccuracy is the
system working as intended and needs no change. Either answer is fine; it should be a
decision rather than a drift. Touching `soul.md` is Tier 3, which is why this is a backlog
item and not a fix.
*Resolved 2026-09-30 by B12 (D3): `soul.md` now carries a clause saying the earlier
statement is never overwritten, that a correction is recorded as a link if at all, and
that the entity must not claim the record changed or that the link has been made.
Measured live: 0/120 changed-record claims against control's 2/120, and 0/120 link claims.
`docs/SOUL_AND_PROMPT_DESIGN.md` revision 4, S21–S26.*

**D3's "superseded" wording, in answers about what happened to an old statement** (raised
at review 2026-09-30, B12). Not blocking: D3 is a clear improvement regardless. But in
28 of 40 measured "what happened to it?" replies, D3 said the old statement *"is
superseded by this newer one"*. Control said so 4/40.
- In ordinary English that is true.
- It is also the mechanism's own word, so it can read as a claim that the `supersedes`
  link exists, which the entity cannot see. That is a softer form of the claims-a-link
  risk B12 removed.
- **The B12 harness could not check it.** Its describe scenarios seed the correction as
  one message, so the old statement never exists as its own record.

**What a real look needs:** a scenario where the old statement *is* its own message,
the correction runs through the real turn, and each "superseded" claim is compared with
whether the link was actually written. CO10.3 got the same treatment when it was first
noticed: measured and decided, not left to drift. Touching `soul.md` again would be a
fifth change, and Tier 3.

**Notes must be tested against CO10.2 before it ships — TESTED 2026-10-02 (piece 8) AND FAILED; see `docs/CORRECTION_DESIGN.md` CO17.** Canonical Notes wordings link 34% on the entity side (329/960), 6 of 8 wordings link, and the entity's own live answers to its own real "no note" replies link in 3 of 12 conversations; production itself wrote 1 false link in the 12 (the `supersedes` table). **Notes does not ship until this is closed.** CO17 designs a structural fix (not built, partial: it covers only claims whose own trace shows an empty search; 1 of 9 "nothing found" claims in the soak store). *The text below is the original item (raised at review 2026-09-28, B11 stage 2), kept for its record; its "documented residual" framing is superseded.*

(Original item.) **Notes must be tested against CO10.2 before it ships** (raised at review 2026-09-28,
B11 stage 2). CO10.2 is a documented residual: the correction classifier false-links the
entity's claim *"I have searched my records, and I do not find any mention of X"* when an
answer about X follows (`N9`, `N10`; `docs/CORRECTION_DESIGN.md` CO15). Six other wordings
of the same idea are clean, so the trigger is the claim's wording. A Notes feature will
give the entity a standard *"I have no note about X"* phrasing, which could fall inside
the trigger or outside it. **Once that phrasing exists, and before Notes ships,**
test it against the composition *"I have searched/looked through my
[records/notes/memory] and [do not find/there is nothing] about X"*, followed by an
answer about X. Use the frozen set's pool shape and production order, with 20
decorrelated passes. If it links, it is this defect arriving through a feature, and it
will then occur on every Notes miss rather than on one phrasing.

**`PN9`: an open, unstable false link in the person-corrects-entity pool** (ruled at review
2026-09-29, B11 stage 3). After the entity says its records hold nothing on X, a person
supplying information about X can link the entity's claim as superseded. Its rate reads
0–100% by sampling context, the mechanism is not understood, and it is therefore **not a
residual** (a residual is characterised and bounded). **`corrections.person_corrects_entity`
stays off** until it is understood and closed. A scope-disclaimer clause closes it and
breaks `PC2` (a person merely asking the entity to look again links 40/40), so it was
rejected; a better wording is a separate, later task, validated on the full set under at
least two shuffles. **Coupling to watch:** the D6/D7 prompt bullets fix `C7` and `N10` with
no current theory of why; re-check both if the bullets are ever touched. See
`docs/CORRECTION_DESIGN.md` CO16. The Notes check above now applies to PN9's family too.

**B19 — `invented_id` flags genuine artifact ids** (filed 2026-09-30, Tier 3, gate-rule
change; found during the O23 receipt design). Artifact ids are `uuid4().hex`, the same
32-hex shape rule S1 (`gate._ID_SHAPE`) treats as a call id. `creative_write` and
`image_generate` give the entity its artifact id in their result text, so an entity quoting it
**accurately** gets a deterministic `invented_id` finding. Confirmed against the code: a real
id from the call's own result is flagged. **Latent, not observed**: 0 of the 12 side-effect
turns in the store quote an id (read-only query, 2026-09-30). **The fix:** S1 accepts ids that
this turn's own calls actually produced, read from the trace's `artifact_ids` key once the
receipt task adds it. So B19 depends on that task and is not bundled into it. As a
gate-rule change, it needs its own review and a frozen case (an accurate id quote,
must-not-flag).

**Corrections do not reach reflection-journal chunks: UNRESOLVED, not accepted** (filed at
review 2026-09-30, reflection journal design; `docs/REFLECTION_JOURNAL_DESIGN.md` J10).
Supersession resolves a `supersedes` link to chunks by a message → chunk timestamp-window
join (`db.get_supersedes_for_chunks`). A journal entry is an artifact, and **artifact chunks
carry no message ids**, so the join can never reach one. A claim restated in a journal entry
and later corrected keeps surfacing from the entry **unannotated**, while the original
message surfaces with its correction. Nothing reports it. The journal is not built yet, so
this is latent until it is; it applies to any artifact chunk that restates a claim, but the
journal is the first kind whose purpose is to restate the day's claims. **Not a residual**:
nothing about it is characterised or bounded. Whether to fix it, and how, is a separate
future decision, but it must not ship silent.

**`messages.integrity_check` has no reader under `program/`** (filed at review 2026-09-30,
reflection journal design J7). Every turn's fabrication-gate verdict is persisted, and
nothing in the application reads it: not retrieval, not the prompt, not any route or
surface. Checked 2026-09-30 by `grep` over `program/` and `scripts/`: the only readers
are diagnostic scripts (e.g. `scripts/soul_diagnosis_b12.py`) and direct SQL. That is
intended for what the verdict *does* (stage 1 is flag-only, decision #23), but it means
**a flagged turn is noticed only if someone goes looking**, and nobody is prompted to.
The journal gets a reader in its own command's output (J7); turns have none. The
natural home is Phase 9's admin panel, or the Phase 7 observability work. Recorded so
the persisted-but-unread state is a known state, not a silent one.

**B20: tool-schema tokens are not a budget term** (filed at review 2026-09-30, Moltbook
revision 3; Tier 3). **BUILT 2026-10-01 with B21, stopped for review**: see BUILT.md and
`changelog/2026-10-01-b20-b21-turn-budget.md`. The derived cap fell from 57,216 to 52,360
characters; the configured 50,000 still fits, with ~590 tokens of headroom left. Every
tool-bearing call sends the offered tools' JSON schemas, and nothing counts them: not
`history.plan_budget`, not `prompt.assemble_turn`, and not B6a's derivation of
`chat.max_message_chars` (`tests/test_turn.py`). **Measured against `gemma4:26b`'s
tokenizer:** 5 tools **657** tokens, 9 tools (with Moltbook) **1,052**. B6a's derivation
leaves **1,804** tokens of headroom beside a maximal message, which falls to **752** with
9 tools. Notes (two tools, ~100 tokens each) and the remaining Phase 5 tools will consume
most of the rest, with nothing failing when they do.
**Designed, not built (presented at review):**
- `plan_budget(..., tool_schema_chars=0)`, with a `tool_schema_tokens` field on
  `BudgetBreakdown`, and the `reserved + history == context` test extended to it.
- `prompt.assemble_turn` takes the size, and `loop.py` passes `len(json.dumps(payload))`
  when tools are sent, 0 on the final, toolless call.
- B6a's derivation gains the term, computed over the **full catalogue** (every tool, enabled
  or not), so switching a tool on can never silently break it.
- A dedicated test fails, naming both numbers, when the full catalogue's schemas exceed the
  cap's headroom.
- The 4.0 chars/token estimate over-counts these schemas (real 4.61), which is the safe
  direction.

**B21: a long user message is silently dropped after tool rounds: REPRODUCED**
(2026-09-30, found by reading while measuring B20; Tier 3, history windowing).
**BUILT 2026-10-01 with B20, stopped for review**, to the order approved at review: older
history, then the records (continuation pieces first, then hits from the lowest rank), then
the oldest whole tool rounds, then a final call without tools as the last resort. Every step
past older history is logged and marked in the trace.
- **Mechanism.** After a tool round, the loop re-plans the window over the user's message
  plus the round's tool messages. `history.select_history` walks newest-first, always keeps
  only the newest (now a tool result), and stops at the first message that does not fit,
  which can be **the user's own message**. The model is then asked to answer tool results
  with no question in front of it.
- **Silent:** the overflow warning never fires, because the newest message fits.
- **Reproduced** with the real loop, prompt assembly and windowing, only `ollama.chat`
  faked (scratchpad script, recorded in the changelog):
  - a 50,000-char message beside maximal retrieved records survives one round of 2 tool
    calls, and is **dropped** after 3 calls, or after 2 rounds of 2.
  - Smallest message dropped after 4 rounds:
    - records 57,000: 1 call/round from ~42,700 chars; 2 from ~25,900; **3 from ~9,400**;
    - records 25,000: only at 3 calls/round, from ~41,600 chars;
    - no records: never, up to the 50,000 cap.
- **Proposed fix, not applied:**
  - (1) pin the current turn's user message so it is never evicted;
  - (2) when the pinned message plus this turn's tool rounds exceed the budget, drop the
    **oldest whole rounds** (an assistant tool-call message with all its results), never
    part of one, logging at WARNING;
  - (3) once (2) has happened, make the next call the final, toolless one, so the model
    answers with what it has rather than looping;
  - (4) the reproduction scenarios become regression tests asserting the user's message is
    present on every call.

**Planning item, no fix now (2026-10-02): tool-schema headroom is about 309 tokens with 11 tools.**
B20's derivation (`config/defaults.toml`, `tests/test_turn.py`) prices every tool's schema against the
chat message cap. With Notes' two tools the derived cap is 51,236 characters, the configured 50,000
fits, and about **309 tokens** of earlier history are left beside a maximal message (about 590 with 9
tools). **Phase 5's further tools** (the self-flag tool, bounded research execution if it becomes a
tool, Moltbook posting) will each cost roughly 100 to 250 tokens, so **the next one forces the
configured message cap below 50,000, or something else to give**: shorter schemas, a smaller
retrieval or soul allowance, a larger window, or a tool-selection step that offers fewer tools per
turn. The B20 tests fail naming both numbers when it happens, so it cannot be silent; the choice is
Lyle's and is not made here.

**`unrun_tool` reads the bare word "search" as a `web_search` claim, so accurate reports about
`note_search` are falsely flagged** (filed 2026-10-02 at Notes piece 7; **Tier 3, gate-rule change, F38 and F40
apply; nothing changed**). Measured: *"The search found one note…"* (NS2) and *"The note search found nothing on
that."* (NS3), each with a real `note_search` call in the trace, flag 5/5 by `unrun_tool`, *"asserts success for
`web_search`, which was not called this turn"*. Deterministic, so independent of the model. The same sentence
without the word (NS4) is clean. It is the earlier repros' family (`unrun_tool` fires on ordinary English,
BUILT.md, F40) reached through a new tool, and it means the `tool_output` false-positive target of **zero is not
met by this one mechanism** (the frozen set carries NS2 and NS3 as documented failures). Note that it also makes
a fabricated claim worded with "search" pass by the wrong rule (`NS1`, declined for that reason).
**Candidate shapes, none chosen:**
- an alias word that is shared with any tool that **did run** this turn yields no finding (the claim could be
  about that tool);
- match a tool's own identifier or a disambiguating phrase (*"web search"*, *"searched the web"*) rather than the
  bare word, for tools whose alias collides with another tool's;
- decide which tool a sentence is about from the tools actually in the trace first, and apply `unrun_tool` only
  to a tool no sentence could refer to;
- leave the rule and instead stop the entity saying the bare word (not a gate change, and not available: the
  entity's phrasing is not controlled).
Each trades recall for precision in the way F38 describes (the alias list is vocabulary, and expanding or
narrowing it is tuned against the phrasings in hand), so a fix needs the frozen set as its measure and its own
review. Not scheduled.

**Nine gate items were "held for O23"; O23 is closed, so they are unscheduled and need a decision after
Phase 5** (recorded 2026-10-02 at Notes piece 8, from the review of piece 7: that list existed only in the review,
not in the repo, and the numbers are the review's). None is being worked; each stays as described until a
decision is taken.
- **7:** the gate and receipts use `default_registry()`; `side_effect_tools()` and ACTION's trace check have no
  seam to inject a registry (latent: nothing passes a non-default registry through them today).
- **3:** `unrun_tool` fires on ordinary English. Now the "search" item filed above (NS2, NS3), beside the earlier
  repros.
- **4:** real artifact ids are flagged as invented (rule S1), though the tools hand the model the id. Filed
  earlier as B19 (below); its fix depends on the receipt work and is a gate-rule change.
- **21:** O17 compares truncated evidence to full sentences (`structural_findings` and `_parse`).
- **14 with C2:** a tool timeout counts as "ran", so a late side effect is not reconciled with what ACTION sees.
- **10 and 11:** pronoun quote splitting and the curly apostrophe (`pronouns.py` changes the text the classifier
  judges).
- **C6:** `gate_eval` never validates a case's tool name against the registry.
- **C7:** the gate flags accurate storage claims (`_PROMPT` and `architecture.md`; F48's territory).
- (The ninth is 3's companion, the "search" tracked item itself, listed once above.)
Also still unscheduled and citing O23: `A7`'s fix (its case note says it belongs to O23's design pass), F48's
vocabulary dependence (two of 13 phrasings caught; now also `NP1`, `NP3`), and G-C per-item labelling.

**B22 — `archive.db` has no triggers, so its append-only rule is convention** (filed 2026-10-02,
found by the REPLACE audit during Notes piece 1; **Tier 3, not built**). `schema/archive.sql`
declares two tables and no trigger: nothing in the schema stops an `UPDATE`, `DELETE` or
`INSERT OR REPLACE` of a message, and `PROJECT.md`'s *"provenance is sacred"* rests on code
discipline alone. Today's code writes the archive only through `db.save_message` (an `INSERT`), and
`grep -rnE "OR REPLACE|REPLACE INTO|ON CONFLICT" program scripts ops` finds nothing aimed at it
(`BUILT.md`, the REPLACE latent gap). Two facts shape any fix:
- **Migrations cannot reach it.** `migrations.py` runs against `working.db` only, by design (*"the
  archive is never migrated"*; its shape is frozen). So a guard on the archive needs another
  mechanism. One candidate, **not decided**: `init_databases()` already re-runs `archive.sql` on
  every startup (idempotent `CREATE ... IF NOT EXISTS`), so a `CREATE TRIGGER IF NOT EXISTS` there
  would reach existing archives. That adds no column or table, but it edits a file marked frozen,
  so it needs its own review, and **a startup check that the expected triggers exist** would be the
  safer half (a trigger that silently fails to install is the unmounted-gate shape).
- **The same applies to `working.db`'s `messages` copy** (`save_message` writes both stores), which
  carries no guard either.
**What backup and restore would need.** Backup copies the archive with SQLite's online backup API,
which carries triggers with the file, so a backup of a guarded archive is guarded. **Restore is
Tier 3 and not built**, and when it is it must (1) **verify the restored archive's triggers against
the expected set** before the database is used (a restore from an old backup, taken before the
guards existed, would otherwise bring back an unguarded archive that looks fine), (2) refuse or
repair when they differ, and (3) restore the two databases as one consistent pair (the dual-write
atomicity guarantee). Neither is designed here.

**Journal: "held, not yet read" and "read and declined" look the same — KNOWN GAP,
deferred to Phase 6** (decided at review 2026-10-01; `docs/REFLECTION_JOURNAL_DESIGN.md`
J7). Index-after-reading is approved: a journal entry enters memory only on an explicit
`--index` after the operator has read it. A declined entry is simply never indexed, so
it has zero chunks, exactly like one nobody has read yet. Phase 6 records each
indexing decision in Notes' shared `approval_log` (capability `journal.index`), so a
decline becomes a recorded decision rather than an absence. Until then,
`--list-unindexed` lists both together.

**Retrieval floor calibration** — floors ship permissive/uncalibrated by
design (see `BUILD_PLAN.md` Phase 1 notes). Once real conversation history
exists in meaningful volume, calibrate actual threshold values and verify
the degenerate-query rule (task 1.6) actually fires on real weak-match
cases — it structurally cannot be exercised while floors are permissive.
Do not let this quietly stay permissive forever by default.

## Go-live checklist (placeholder — fill in once build is underway)

- [ ] Full database wipe executed and verified
- [ ] `soul.md` final wording reviewed
- [ ] Model temperature finalized
- [ ] Go-live reset command tested
- [ ] All eval/probe harnesses passing on final build
