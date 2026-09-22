# 2026-09-21 — B5: writing with nobody present

**Tier 1.** The reduced scope, as reviewed: a **provable property**, not a wiring.
Nothing committed. **Stops here for review** — this closes Cluster B.

## Files

Created: `tests/test_no_person_present.py` (9). Modified: `BUILT.md`,
`docs/MEDIA_AND_CREATIVE_DESIGN.md`.

**Nothing in `program/` changed**, and that is the result rather than a shortcut.

**1,140 tests pass** (was 1,131), 2 skipped, `ruff` clean.

## What the task turned out to be

Decision #10 wants creative writing available in autonomous and background sessions.
**No such session mode exists** — no scheduler, no reflection cycle, no research
runner — so the reviewed scope was to prove the path works with nobody there, rather
than build a seam into something that cannot happen (R2: *"an unmounted gate is worse
than an absent one"*).

Run for real before writing any tests: with **no users, no conversation, no turn, no
actor**, `registry.dispatch("creative_write", …, attribution=AttributionContext.for_entity())`
returned `OK`, stored 67 characters to `workspace/writing/66/66d641de…`, indexed one
chunk with `conversation_id = None`, and `retrieval.search("house cooled unobserved")`
found it rendered as `[record 1 · creative writing · …]`.

**So nothing needed building.** The property already held, because P0 put attribution
in the tool path rather than an actor, and because `for_entity()` was built at P0 for
exactly this case. What was missing was evidence, which is now nine tests.

## The one thing that makes it work, and it is not obvious

`artifacts.user_id` and `chunks.user_id` **both** `REFERENCES users(id)`, and
`PRAGMA foreign_keys` is **on**. So a write attributed to a user id with no row fails
at the first insert — which means `for_entity()` creating the entity row lazily is
**load-bearing rather than tidy**.

Pinned two ways: a test asserts the foreign key really does reject an id with no row,
and **breaking the lazy creation fails 6 of the 9 tests** with
`FOREIGN KEY constraint failed`. Without that, the no-person path would have been one
schema detail away from not working at all, and nothing would have said so.

## What the tests hold

* **A guard on the fixture first.** The store is asserted to have no users and no
  conversations, because a fixture that quietly seeded one would make every test below
  it pass without touching the no-person case.
* Writing works with no person and no conversation; chunks carry
  `conversation_id = None`.
* The entity row is created **on demand by the first write**, with
  `password_hash = None`.
* The tool dispatches with **no turn, no loop and no `Actor`** — the call an autonomous
  session would make.
* Unattended work is **retrievable and labelled** like anything else: Q9's "private"
  means not proactively announced, and that does not change because nobody was there.
* Q2b and Q2c are pinned **as a pair** — a live turn files to the person, an unattended
  write files to the entity — so they cannot both pass by everything being attributed
  one way.
* **B5 built no session mode**, asserted: no `scheduler`, `reflection`, `autonomous`,
  `session` or `daemon` module anywhere under `program/`. If one appears it should
  arrive with its own task and its own tests, and this is the test that fails and
  points at it — the same idiom as `BUILT.md`'s tables-asserted-absent and the seed
  module's no-wipe-surface test.

## Known limitations

- **`image_generate` inherits the property but is not covered by these tests.** Its
  handler takes the same `AttributionContext`, so an entity-attributed generation
  should work identically — but it needs a running ComfyUI, so asserting it would put a
  service dependency in the unit suite. Untested rather than unsupported.
- **Nothing calls this yet.** The property is real and unused until Phase 5 or 6 builds
  something that wants it. That is the intended state, and the last test is what keeps
  it honest.
- **No entity-authored content exists in any real store**, so nothing has yet been
  retrieved months later and read back by the entity as its own past work. That is the
  interesting case and it needs time rather than a test.
- **The entity row remains one `set_password.py` away from being an account** — tracked
  in `NOW.md`'s backlog since P0, unchanged here.

## Cluster A and B are complete except the soul.md thread

Built: ComfyUI running outside the repo with its recreate record; the client; the
`image_generate` tool with storage and indexing; the `creative_write` tool with storage
and indexing; both findable by retrieval and both labelled so neither reads as
conversation; `workspace/` protected three ways with a standing item and an enumeration
guard against a fourth occurrence.

**Not built, and not this cluster's:** the `soul.md` entity-refusal clause — decision
#10's remaining piece, Tier 3 / Opus, and the one part of creative writing a tool
cannot provide.
