# 2026-09-21 — Phase 4 P0: attribution, artifact kinds, and the entity's own row

**Tier 1 · the shared floor both clusters stand on.** Design of record:
`docs/MEDIA_AND_CREATIVE_DESIGN.md`, which records all fourteen review decisions.
Nothing committed. **No tool is built** — no image is generated and nothing is
written to `workspace/` yet.

## Files

Created: `program/attribution.py`, `program/artifacts/kinds.py`,
`tests/test_attribution.py` (28), `docs/MEDIA_AND_CREATIVE_DESIGN.md`.
Modified: `program/tools/registry.py`, `program/engine/loop.py`,
`program/engine/turn.py`, `program/memory/db.py`,
`program/artifacts/ingest.py`, `BUILT.md`.

**1,042 tests pass**, `ruff` clean.

## What landed

**`AttributionContext` — deliberately not an `Actor`.** It carries a `user_id` and
nothing else: no role, no permission, no authorization meaning. The naming is
load-bearing, because several tests across this build assert that nothing in the
tool path takes an `actor`, `role` or `user` parameter, and they protect a real
property — fabrication checks, retrieval and corrections all treat both household
members identically. An authorization object in the tool path is how that would
quietly stop being true.

**Three invariants, each tested, two proven by breaking them:**

- **Declared, not inferred.** `Tool.takes_attribution` — the contract is the thing
  written down, the same reason `parameters` is declared rather than derived from
  the handler's signature.
- **Never model-settable.** `Tool.__post_init__` refuses a tool that puts
  `attribution` in its `parameters`. A model that could set it could attribute a
  write to the other household member. *Removing the guard fails the test.*
- **Never in the recorded arguments.** `ToolResult.arguments` is what reaches the
  tool trace the fabrication gate reasons over, so a value the model never sent
  must not change that shape. *Leaking it into `supplied` fails two tests.*

A declaring tool dispatched with no context **raises** rather than degrading —
there is no honest value to substitute, and it is a wiring bug of the same class
as a duplicate registration. `turn.py` always supplies it, asserted from inside
the dispatch rather than by reading two lines.

**`program/artifacts/kinds.py`** — a table of artifact kinds carrying storage root,
`source_type` and `source_trust`, the same data-not-conditionals shape
`permissions.CAPABILITIES` and `store.SETTINGS` use. `upload` is registered
alongside the two new kinds and `ingest.py` now reads its root and vocabulary from
there, so one kind cannot have its directory in one file and its provenance in
another. An unregistered type **raises**: a wrong root writes bytes somewhere the
backup, the wipe and the governance blocklist do not expect.

## The entity's row was not trivial, exactly as the review anticipated

Two collisions, both found by reading the schema rather than assuming:

**`users.role` is `CHECK (role IN ('admin', 'user'))`** — and `create_user`
validates the same list in Python. A third value would need the table recreated,
against a decision that specified no schema change. So the row takes **`user`**, on
least privilege. **The role is not what keeps it from being an account:
`password_hash` stays `NULL`, and a NULL hash never authenticates.**

**`users.name` is `NOT NULL UNIQUE`**, so the row needs a string — and CLAUDE.md
says the entity *"has no name and must not be given one — not by code, prompt,
config, or docs."* The row carries the sentinel **`__entity__`**.

It was `"the system"` first, and **a reachability trace requested at review killed
that** — see "The reachability answer" below. The short version: a phrase in the
register of rendered prose is exactly the wrong thing to put somewhere that can
reach rendered prose.

Created **lazily** on first use, not in `init_databases()`: every test run builds a
store, and a user row appearing in all of them would change what existing tests
see. `UNIQUE` on `name` makes two concurrent first writes safe — the loser re-reads
instead of both inserting.

**Residual, recorded not closed:** an operator could set a password on that row
with `scripts/set_password.py`, after which it could authenticate. Closing it needs
an auth-side guard, and authentication is its own Tier 3 category.

## The reachability answer

The review asked directly whether anything reads `users.name` back into something
entity-facing. **It does, by two routes, and the first is irreversible:**

1. **`chunking._format_line()` renders every user message as
   `f"{user_name}: {content}"`**, with `user_name` from
   `db.get_user(conversation["user_id"])["name"]`. Chunk text is what reaches FTS5,
   the embedding vector and the retrieved-records block of the prompt. A
   conversation owned by the entity row would bake its name into all three —
   permanently, since re-chunking reproduces it and an embedding cannot be edited
   afterwards.
2. **`turn.py` passes `actor.name` into the correction classifier's prompt**
   (`corrections._render`) and into the situation block's speaker field, both
   model-facing; `db.get_actor()` builds a valid `Actor` from this row.

**Neither is reachable today** — the row owns no conversation and cannot log in.
But a reflection journal or a research run owning its own conversation is one
ordinary Phase 5 task away, and the chunk-text path cannot be un-taken. So the
review's second branch applies: the sentinel, not the phrase.

Three tests pin the finding rather than the conclusion — the sentinel's shape, that
a user name really does reach chunk text, and that this row really would produce an
`Actor` carrying it. If either path ever closes, the reasoning fails loudly instead
of becoming received wisdom.

## The required regression proof

`memory_search`, `web_search` and `web_fetch` dispatch identically with and without
attribution, compared on the **envelope** — outcome, recorded arguments, `ran`,
`timeout_seconds`, trace key set — plus a registry-wide check that no existing tool
declares `takes_attribution` and no existing handler could accept it even by
accident.

**The tools' own `value` and `error` are excluded from that comparison, and the
reason is not laziness:** two live calls to a search engine legitimately return
different results. The first version of this test compared them and failed on
`web_search` — not a regression, just two different result sets. A test that fails
for that reason is worse than no test, because it trains the next reader to ignore
it.

## A correction to the published plan

The plan reported the **Docker daemon not running** and SearXNG therefore
unreachable, measured on 2026-09-21. **That is no longer true** — Docker is up,
`searxng-core` and `searxng-valkey` are running, and `web_search` returned HTTP 200
during this work. The finding was accurate when taken and is stale now; the
artifact has been corrected rather than left standing.

## Known limitations

- **No tool uses attribution yet.** The plumbing is proven by a TEST-ONLY tool and
  by the loop/turn tests; its first real consumer is A3 or B4.
- **`workspace/` is still unprotected** — not gitignored, not backed up, not
  isolated in tests. That is B0, and nothing writes there until it lands.
- **The entity row exists but nothing writes as it.** `AttributionContext.for_entity()`
  is reachable and tested; B5 is its first caller.
- The lazy-creation race is handled by the `UNIQUE` constraint rather than by a
  lock, which is correct but means the losing writer does one extra read.

## Next

**A1a — installing ComfyUI — needs Lyle's hands.** Native rather than Docker (no
MPS passthrough on Apple Silicon), its own venv on 3.13 or 3.11 rather than the
repo's 3.14, and SDXL base downloaded. A1b's measurement then answers Q5 and Q6
before any client code is written.
