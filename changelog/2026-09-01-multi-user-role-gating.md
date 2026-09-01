# 2026-09-01 — Multi-user attribution + role gating

**Tier 2 · Sonnet · spec approved (revision 2) before implementation.**
Design of record: `docs/ROLE_GATING_DESIGN.md`, cited as R1–R8.

## Summary

A capability registry, an `Actor`, and enforcement wired into the one built
capability that was admin-only by intent and enforced nothing: the settings
store. Jodie is now actually denied settings access; Lyle is allowed.

## Files changed

Created: `program/settings/permissions.py`, `tests/test_permissions.py` (23),
`docs/ROLE_GATING_DESIGN.md`.
Modified: `program/settings/store.py` (gating), `program/memory/db.py`
(`get_actor`), `tests/test_settings.py` (required actor), `BUILD_PLAN.md`,
`BUILT.md`.

No schema change — the `users` table was already right.

## What was already built, and what was actually missing

**Per-user attribution was already done.** `messages.user_id` and
`chunks.user_id` are written on every row and carried through to
`RetrievedChunk`. What was missing was *enforcement* — and the finding that
shaped the spec is that there is very little to enforce yet.

| Capability | Built? | Enforced now? |
|---|---|---|
| `settings.read` / `settings.write` | yes | **yes** |
| Chat, creative writing, image generation, research triggering, Moltbook | no | nothing to gate |

Most of decision #17's list for Jodie has no built capability behind it. Each
phase registers its own capability when it builds the thing, so the registry
stays a description of what exists rather than of what is planned — the same
reasoning that had task 1.4 *remove* the `artifacts` table.

## The loopback gate was specified, not built (R2)

The gate keeps an admin surface off the LAN. **There is no admin surface** — the
only endpoint is `GET /api/health`, deliberately public, and `start.sh` polls it
to decide the backend came up.

Building the dependency now would mean a gate mounted on nothing, testable only
against a route invented in the test file to give it something to guard. That is
the pattern this project has twice rejected (task 1.4's table removal, decision
#15's refusal of a self-mod seam), and an unmounted gate is worse than an absent
one because it reads as protection that exists.

So the full contract is written down — trust `request.client.host` only and
never `X-Forwarded-For`; parse to an address object rather than prefix-matching;
missing `request.client` denies; 404 not 403 — and **recorded in `BUILD_PLAN.md`
against the admin-panel task**, which mounts the first admin route. Same
cross-task obligation treatment idle-close got against the agent loop.

Worth stating plainly: `start.sh --lan` already binds `0.0.0.0`, so Jodie's
device can reach the backend today. Nothing sensitive is exposed because nothing
sensitive is mounted, which is the state this preserves.

## `Actor.operator()` rather than `actor=None` (Q4, as directed)

The reasoning for treating operator-run calls as always-allowed is unchanged —
it is `GUIDANCE.md`'s carve-out: *"except when a human is directly driving the
action in the moment… that always just works."* Only the spelling changed.

`actor=None` reads as "no check happened" and is indistinguishable from a caller
who forgot. `Actor.operator()` is deliberate, greppable, and visible at the call
site.

**The parameter is required, with no default.** That follows from the same
reasoning: defaulting to `Actor.operator()` would let a caller who never thought
about authorization land silently on the always-allowed path — reintroducing
exactly the invisibility the sentinel removes.
`test_there_is_no_implicit_unauthenticated_path` asserts `store.set(...)`
without an actor raises `TypeError`, and `require()` rejects a non-`Actor`
rather than evaluating it for truthiness.

The sentinel carries a reserved `user_id` of `"operator"`, which is not a real
users-table id — so an operator write stays distinguishable from a real user's
when reading `settings.updated_by`, and `db.get_actor("operator")` returns
`None`.

## `updated_by` is now derived, not asserted

`store.set()` previously took `updated_by="lyle"` as a free-text argument beside
the value. It is now taken from `actor.name`, so the attribution recorded is the
same thing that was authorized and the two cannot disagree.

## `resolve()` stays ungated, deliberately

`store.resolve()` is the seam `config._settings_first()` calls on every
settings-backed config read. It is the system reading its own configuration in
order to operate, not a person reading settings — requiring an `Actor` would
mean the Ollama client needed one to discover which model to call, which is not
a permission question. `get()` / `describe()` / `describe_all()` are the
person-facing reads `settings.read` governs, and a test asserts
`config.model_options()` still works with no actor anywhere.

## Role is fixed at creation (R5)

No `set_role()`, no promote/demote path. Promotion is the highest-value
operation in the system — whoever can change a role can grant themselves
settings access — and there is no admin panel to perform it on and no
authenticated actor to attribute it to. Building it now means designing the
weakest version and then designing it again.

`test_there_is_no_role_mutation_api` asserts no such function exists on `db`, so
adding one silently fails a test.

**"Fixed" means no code path changes a role.** It does not mean the database
forbids it — `UPDATE users SET role='admin'` still works from `sqlite3`. Adding
a trigger is a schema change and Tier 3. Role changes are an operator action
performed deliberately against the database, not a feature.

## The retrieval decision is not foreclosed (R7)

Whether Lyle's query may reach Jodie's chunks remains **an open decision-log
question**, and this task keeps both answers available by keeping two axes
apart:

- **Capability gating** — may this person invoke this operation? Keys on `role`.
  This task.
- **Data visibility** — whose memory may their queries reach? Keys on
  `chunks.user_id`. **Not this task.**

Concretely: no filter added to `retrieval.search()`, no column added, nothing
about what retrieval returns changed. Two tests enforce it —
`test_retrieval_is_unchanged_by_this_task` asserts `search()` gained no `actor`
or `user_id` parameter, and `test_data_visibility_is_not_a_capability` asserts
no `memory.read_all_users`-style capability was registered, since defining one
would already presume the answer is role-based.

**Recommendation unchanged, still not decided here:** a `NOW.md` decision-log
entry on cross-user memory disclosure before Phase 5's mining sharpens it.

## ⚠ The limitation to keep in view: this is authorization, not authentication

`users.password_hash` exists as a column, is written by nothing, and read by
nothing. Without authentication an `Actor` is **whatever the caller says it
is**, so role gating is only as strong as the caller's honesty.

For a single-process backend with no HTTP write surface, run by its operator,
that is genuinely fine. It is not security, must not be described as security,
and stops being adequate the moment an untrusted caller can construct an
`Actor` — which is task 2.2. That obligation is now recorded against it.

## Tests: 23 new (+1 in settings), 349 total

`ruff check .` clean. All permission tests use **Lyle and Jodie as the seed
corpus creates them**, not synthetic placeholders.

- `test_jodie_cannot_read_settings` — PROJECT.md says no settings access, ever;
  reads included, not just writes.
- `test_a_denied_write_changes_nothing` / `test_a_denied_clear_changes_nothing` —
  refusal, not partial application.
- `test_the_denial_message_names_who_what_and_why`.
- `test_an_unregistered_capability_raises_rather_than_defaulting` — a capability
  nobody defined must not be silently permitted.
- `test_an_unknown_user_id_yields_no_actor_rather_than_a_default`.
- `test_config_accessors_do_not_require_an_actor`.

Live run, real store:

```
Lyle : admin | Jodie: user
Lyle write  -> ok, temperature now 0.9
Jodie write -> Jodie (user) may not settings.write: it requires the admin role.
Jodie read  -> Jodie (user) may not settings.read: it requires the admin role.
unchanged after denial: 0.9
updated_by  : Lyle
config.model_options() needs no actor: 0.9
```

## Known limitations

- **No authentication**, as above. The headline one.
- **No loopback gate**, by design — specified and recorded, not built.
- **Two capabilities only.** Everything else in decision #17 has nothing to gate.
- **Role fixed at creation**, and only at the code level.
- **`Actor.operator()` is an unauthenticated always-allowed path.** Correct
  today; task 2.2 must not reach for it.
- **No admin HTTP surface exists**, so none of this is reachable over the
  network — which is also why none of it is exposed.

## Project Anam alignment check

1–3. Name / Anam-or-Tír / personality: **No** to all.
4. Preserve raw experience? **Yes** — no message or chunk touched; a denied
   write leaves the settings table unchanged.
5. Traceable derived artifacts? **Yes** — `updated_by` is now derived from the
   authorized actor rather than asserted separately.
6. Tool calls recorded? **N/A.**
7. Created artifacts remembered? **N/A.**
8. Context construction inspectable? **N/A.**
9. Autonomy more cumulative? **Neutral.**
10. Anam/entity distinction preserved? **Yes.** These are permissions for
    *humans*; the entity is not an actor here and holds no role.
11. Migration required? **No** — the `users` table already carried `role`.
12. Tests? **Yes**, 23, plus a live run.
13. Core substrate changed unnecessarily? **No** — retrieval, chunking and the
    schema untouched; `store.py` gained a required argument.
14. External dependencies added? **None.**
15. Workspace vs. self-modification? **Unaffected.**
16. Casual legacy renaming avoided? **Yes.** The reference build's auth was not
    consulted; this task does not point at it.
