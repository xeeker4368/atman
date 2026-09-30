# 2026-09-30 — Phase 5 design: reflection journal and read-only Moltbook tools

Design step only (Tier 3 review). **No code changed.**

## What changed

- `docs/REFLECTION_JOURNAL_DESIGN.md` (new, revision 1): J1–J12. Trigger,
  storage as an artifact kind, input, the run's prompt wording, provenance
  (DECIDED), privacy (DECIDED), migration 7 `artifacts.integrity_check`
  (DECIDED; column shape for review), and an identity-only gate entry point.
- `docs/MOLTBOOK_READ_DESIGN.md` (new, revision 1): M0–M11. Four read tools,
  live-measured API shape, timeout derivation, rate-limit handling, key
  hygiene, provenance recorded design-only (DECIDED).
- `NOW.md` backlog: *corrections do not reach reflection-journal chunks*, filed
  as unresolved (decided at review).
- `BUILD_PLAN.md`: read-only Moltbook tools moved from Phase 8 to Phase 5
  (decided at review). Both Phase 5 rows point at their design docs. The Phase 5
  gate gains the Moltbook live check; Phase 8's gate keeps only posting.

## Revision 2 (same day, after review)

- **M0 resolved for reading.** The account is Lyle's (it is in his owner
  dashboard, and the 20:53 activity was his login), so the hold on the read
  build is lifted. Every precaution stays: the entity is never told it has an
  account, the account is never named, `/feed` stays out, and owner data never
  reaches the entity or a committed fixture. **Keeping this account versus
  registering a fresh one is recorded as owed before any posting design.**
- **The account's name, its description (which names a third party) and the
  owner's details are no longer written in the repository.** Revision 1 (never committed) quoted
  them in M0; the repo is public, and the fixture rule applies to the docs too.
- **Tools renamed** `moltbook_read_post` and `moltbook_read_agent`.
- **Timeouts recorded as judgment values**, in a table, with
  `tests/test_idle.py`'s 2,045 s / 35-minute floor shown term by term and
  unchanged. `moltbook_read_post`'s two requests now share **one 20 s deadline**,
  each getting what is left. Revision 1's "stop if the first used more than
  half" did not bound the worst case.
- **Fixture scrubbing specified** (M10): `owner`/`claimed_by` removed, every
  identifier and all text replaced with synthetic values of the same shape. A
  committed test checks the keys and name pattern, and a one-off build-time
  check compares against the raw captures. Rendering reads an **allowlist** of
  fields.
- **Journal verdict readers named** (J7): nothing in the entity's path; the
  operator, through the command's own output (the one reader built); Lyle by a
  recorded query; the J8 script. Phase 6 owes a reader for unattended runs.
- **Journal timing** stated against the floor: the run is not a turn and
  enters none of its terms.

## Why

Both tasks carry Tier 3 pieces: provenance semantics, prompt assembly, a
schema change, and a gate entry point. Those go up before code.

## What was checked, and how

- **Moltbook key:** `config.get("moltbook", "api_key")` resolves a non-empty
  string from `config/local.toml`, which `git check-ignore` confirms is ignored.
  The value was never printed.
- **Moltbook API:** 19 read-only GETs with the key (approved at review),
  `trust_env=False`, redirects not followed, about a second apart. Bodies were
  saved to CC's scratchpad with the key redacted (it was never echoed), not to
  the repo. Five disagreements with `skill.md` are recorded in M2.
- **The account's profile** showed it predates this project and carries
  another agent's description and post (M0). Raised as a question; resolved for
  reading at review (revision 2), open for posting.
- **The journal/gate interaction** was read from `gate.py`. `check()` runs
  structural rules against the turn's trace, and ACTION findings stand when no
  side-effect tool ran, so accurate recollection in a journal would flag. That
  is the reason for J8's identity-only entry point.

## Known limitations

- Latency: n = 19 from one machine in one session. The 10 s client timeout is
  a 5× judgment multiple over that.
- J8's false-positive risk on recollected saves is predicted from revision 9's
  A2 finding, not yet measured. The build measures it before it lands.

## Follow-up

- Review both documents. Decide keep-versus-fresh account before any posting
  design (M0).
- Build order with stops is in J12. The Moltbook build follows M10.
- No `BUILT.md` change: nothing was built.
