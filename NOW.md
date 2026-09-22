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
      task 3.3 lands. **It opens a question 3.3's design must answer rather
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

**The entity's `users` row can be turned into an account** (Phase 4 P0,
2026-09-21). `db.entity_user_id()` creates a real row in `users` so that a write
with no person present has something to attribute to (artifacts.user_id is NOT
NULL). It is kept inert by `password_hash` being NULL — *a NULL hash never
authenticates* — and **not** by its role, which is `user` because
`CHECK (role IN ('admin', 'user'))` allows nothing else without recreating the
table.

**The gap:** `scripts/set_password.py` takes any user by name and sets a hash. Run
against `__entity__` it would produce a working login for a row that is not a
person, and every route behind `require_actor` would then accept it. Nothing
currently prevents that, and nothing detects it afterwards.

**Closing it means an auth-side guard** — refusing credential operations and token
issue for the reserved id — which falls under `AGENTS.md`'s authentication
checkpoint ("credential verification, session-token issue and expiry, and anything
that decides which `Actor` a request produces"), so it is its own Tier 3 change
rather than a patch alongside Phase 4. Tracked here so it is not left sitting in a
changelog.

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
