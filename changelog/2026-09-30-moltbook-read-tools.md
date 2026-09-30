# 2026-09-30 — Moltbook read-only tools

Phase 5, Tier 1. Built to `docs/MOLTBOOK_READ_DESIGN.md` (now revision 3,
approved at review 2026-09-30). **The live Phase 5 gate call has not been
made.** It waits for Lyle to confirm the key was regenerated.

## What changed

- `program/tools/moltbook.py` (new): four tools, `moltbook_browse`,
  `moltbook_search`, `moltbook_read_post` and `moltbook_read_agent`, plus the
  transport and an allowlist renderer.
- `program/tools/catalog.py`: the four tools registered.
- `program/config.py`:
  - a `[moltbook]` fallback with no key in it;
  - five `ANAM_MOLTBOOK_*` env mappings;
  - `MOLTBOOK_HOST` and accessors: `moltbook_configured()` (never returns the
    value), `moltbook_base_url()` (pinned host), `moltbook_timeout_seconds()`,
    `moltbook_read_post_deadline_seconds()` and `moltbook_max_results()`.
- `config/defaults.toml`: a `[moltbook]` section, with every judgment value
  labelled and `max_results` derived.
- `config/local.example.toml`: a commented `[moltbook]` example.
- `tests/conftest.py`: Moltbook is **off** for every test (empty key).
- `tests/test_moltbook.py` (new): 51 tests, plus 2 opt-in live tests.
- `tests/fixtures/moltbook/` (new): 7 scrubbed fixtures and a README.
- `tests/test_tools.py`: the catalogue list and the import-order list gain the
  new module.
- `tests/test_blocklist.py`: the `local.toml` test rewritten (below).
- `docs/MOLTBOOK_READ_DESIGN.md` revision 3: marked built, with each departure
  from revision 2 recorded in place, plus the schema-cost measurement.
- `BUILT.md`: a Moltbook section and the suite count.

## Departures from the approved design, each recorded in the doc

1. **No request sends the key.** Revision 2 sent it and treated keyless reads
   as optional. Measured without the key: every endpoint the tools use is
   public. So the module never reads the key, and the planned redaction and
   raising accessor were not built, since neither would have a caller.
2. **`enabled` stays "key configured"**, as approved, but that is now only a
   "set up here" switch. A separate read flag was not added without a request.
3. **Handler validation reaches the model as `TOOL_ERROR`**, not
   `INVALID_ARGUMENTS` as revision 2 said. That is the registry's actual
   behaviour, and `web_search`'s.
4. **`moltbook_search`'s `type` has no `agents` value.** It was never observed
   to work.
5. **The `trust_env` proof is on the session actually used**, not B10's fake
   proxy in a subprocess. The host pin blocks a local target.

## Why

The task moved to Phase 5 at review. The design's precautions for the account
(M0) are kept in full: the entity is never told it has an account, the account
is never named, `/feed` is not used, and no person's identifier reaches the
entity or a fixture.

## What was tested, and how

- **Fixtures scrubbed first**, while the raw captures still existed (they sat
  in `/private/tmp`). A build-time comparison against every raw capture found
  **0 of 193** identifier/text strings surviving and **0 of 14** from the
  account's own profile, timestamps included.
  - The first pass caught one survivor: an agent name matching a word in my own
    echoed probe query. The query was scrubbed too, rather than argued away.
  - A second look found the profile kept the account's real timestamps and
    counts. These were replaced as well, since together with the design doc's
    date they would identify the account.
- **Sentinels** prove what must not render: `owner`, `claimed_by`, author
  descriptions, an agent result's text, the `tip`, and spam/deleted items.
- **Mutations, one at a time, each restored and checked byte-identical.** Each
  of these fails its test:
  - bearer sent (2 tests fail);
  - `trust_env` left on (1);
  - redirects followed (1);
  - `owner` rendered (1);
  - unknown fields rendered (3);
  - no spam filter (2);
  - replies given a fresh timeout (1);
  - no query limit (1);
  - no UUID check (3);
  - agent self-description rendered (1);
  - `max_results` 6 (3) or 4 (1) in `defaults.toml`;
  - host pin removed (5);
  - conftest leaving Moltbook on (2);
  - `config` removed from the blocklist (the rewritten `local.toml` test).
  - *One mutation first passed:* `max_results` changed in `config.py`'s
    fallback, which `defaults.toml` overrides. It was redone in the layer that
    is actually read.
- **Schema cost measured** against `gemma4:26b`'s tokenizer
  (`prompt_eval_count` with and without `tools`): 5 tools 657 tokens, 9 tools
  1,052. A maximal message still fits B6a's derivation, with 752 of its 1,804
  tokens of headroom left.
- **Full suite:** 1,406 passed, 4 skipped, 0 failed. `ruff check` is clean.

## The `local.toml` blocklist test

It asserted the file did not exist, so it would fail the day one appeared and
prompt a recheck. It did exactly that when the Moltbook key was put there. The
premise is stronger now, since the file holds a live credential. So it asserts
the path is blocked whether or not the file exists, and that the file's real
bytes are refused by content. The file is read, never printed.

## Known limitations

- **Not run live.** The opt-in live tests and the gate call wait on the key.
- **Nothing budgets tool schemas.** Neither `plan_budget` nor B6a's derivation
  counts them, so each new tool spends the chat cap's headroom silently. That
  is its own task.
- **Adjacent, by reading only:** after a tool round, `select_history` keeps the
  newest message, which is a tool result, so a near-maximal user message could
  be windowed out behind large tool results. This does not depend on Moltbook.
- Agent-written text is framed as content, and that is not a defence against
  prompt injection.
- Keyless rate limits may be counted per IP; that is undocumented.

## Follow-up

- Lyle confirms the key was regenerated → run `ANAM_MOLTBOOK_LIVE=1 pytest
  tests/test_moltbook.py` and the Phase 5 gate call.
- Decide keep-versus-fresh account before any posting design (M0).
- A task for a tool-schema term in the context budget, and for the tool-result
  windowing question.
