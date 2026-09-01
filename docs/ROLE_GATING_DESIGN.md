# Multi-user attribution + role gating — spec for approval

**Tier 2 · Sonnet · spec only, no code written.**

Decisions numbered `R1`–`R8`.

---

## R1 — What exists today, measured

| Thing | State |
|---|---|
| `users` table, both stores | **built.** `role` CHECK-constrained `'admin'`\|`'user'`, `password_hash`, `last_seen_at` |
| Per-user attribution on data | **built.** `messages.user_id` and `chunks.user_id` are written on every row; `RetrievedChunk` carries `user_id` through retrieval |
| `create_user(name, role)` | **built**, validates role, writes both stores atomically |
| `settings.store` | **built.** Admin-only *by intent*; enforces nothing. Records `updated_by`, ignores it |
| HTTP surface | **one endpoint**: `GET /api/health`, deliberately public |
| Admin routes | **none** |
| Chat route | **none** (task 2.2) |
| Admin panel | **none** (Phase 9) |
| Authentication | **none.** `password_hash` is written by nothing and read by nothing |

**Attribution is already built.** What is missing is *enforcement*, and — the
finding that shapes this whole spec — there is almost nothing to enforce against
yet.

---

## R2 — Loopback gating: specify now, **do not build**

**Recommendation: this task does not build the HTTP loopback gate.**

The gate's job is to keep an admin surface off the LAN. There is no admin
surface. The only endpoint is `/api/health`, which is deliberately public and
must stay reachable — `start.sh` polls it to decide the backend came up.

Building a FastAPI dependency now would mean a gate mounted on nothing,
testable only against a route invented in the test file to give it something to
guard. That is precisely the pattern this project has twice rejected: task 1.4
**removed** `artifacts` and `research_candidates` for having no Phase 1
consumer, and decision #15 refuses a self-modification seam rather than leaving
a stub. An unmounted gate is worse than absent, because it reads as protection
that exists.

**Instead: the contract is specified here in full, and recorded in
`BUILD_PLAN.md` against the task that introduces the first admin route** — the
same cross-task obligation treatment idle-close got against the agent loop, and
the elapsed-time pairing got against the current-situation block.

### The contract Phase 9 must implement

So the reasoning is not re-derived later:

1. **Trust `request.client.host` only. Never a header.** `X-Forwarded-For`,
   `X-Real-IP` and friends are attacker-controlled on a direct-to-uvicorn
   deployment — anyone on the LAN can set them. There is no reverse proxy in
   this build, so there is never a legitimate reason to read them, and
   `uvicorn`'s `--proxy-headers` must stay off.
2. **Accept exactly `127.0.0.1`, `::1`, and `::ffff:127.0.0.1`.** Not the
   `127.0.0.0/8` block generally — parse to an address object and test
   `is_loopback`, rather than string-prefix matching, which `127.0.0.1.evil`
   style inputs defeat.
3. **A missing `request.client` denies.** It is `None` for some transports;
   absence of evidence is not evidence of loopback.
4. **Deny by default**, returning 404 rather than 403 — a 403 confirms the
   admin surface exists to a household device that should not learn that.
5. **This is a network-position check, not authentication.** It answers "is this
   request from this machine", not "is this Lyle". Both are needed for a real
   admin surface; see R6.

**Why this matters concretely today:** `start.sh --lan` already binds
`0.0.0.0`, so Jodie's device can reach the backend right now. Nothing sensitive
is exposed because nothing sensitive is mounted — which is exactly the state
this recommendation preserves.

---

## R3 — The permission model, as data

A frozen capability registry, mirroring the pattern `settings/store.py` already
uses for setting keys — not conditionals scattered across call sites.

```python
# program/settings/permissions.py   (name open — see question 2)

class Role(str, Enum):
    ADMIN = "admin"
    USER  = "user"

@dataclass(frozen=True)
class Capability:
    name: str
    minimum_role: Role
    description: str

CAPABILITIES: tuple[Capability, ...] = (
    Capability("settings.read",  Role.ADMIN, "Read effective settings values."),
    Capability("settings.write", Role.ADMIN, "Change a setting; takes effect immediately."),
)
```

**Only two capabilities are registered, because only two are enforceable
today.** Asking about an unregistered capability raises
`UnknownCapabilityError`, mirroring `store.UnknownSettingError` — so a future
task cannot silently check a capability nobody defined and get a permissive
default.

### What this can actually enforce today vs. what it cannot

| Capability | Built? | Enforceable now? |
|---|---|---|
| `settings.read` / `settings.write` | **yes** | **yes** — the one real consumer |
| Chat | no (task 2.2) | no |
| Creative writing | no (Phase 4) | no |
| Image generation | no (Phase 6) | no |
| Research triggering | no (Phase 5) | no |
| Moltbook posting | no (Phase 8) | no |
| Backup / restore | scripts only | operator-run, no request context to gate |

Most of decision #17's list for Jodie has **no built capability to gate**. Each
of those phases registers its own capability when it builds the thing, which
keeps the registry a description of reality rather than of intent.

**Note the two-axis model is untouched.** `GUIDANCE.md`'s `enabled` /
`approval_required` axes govern *capabilities of the entity*; this registry
governs *which human may invoke an operation*. They are different questions and
must not be merged — see question 3.

---

## R4 — Enforcement point: `settings.store`, the one real consumer

*(Revision 2 — Q4 resolved: explicit `Actor.operator()` sentinel replaces
`actor=None`.)*

```python
@dataclass(frozen=True)
class Actor:
    user_id: str
    name: str
    role: Role

    @classmethod
    def operator(cls) -> "Actor":
        """The person at a shell on this machine. Always allowed."""

    @property
    def is_operator(self) -> bool: ...

def require(actor: Actor, capability: str) -> None:
    """Raise PermissionDenied unless the actor holds the capability."""
```

`store.set()`, `store.clear()`, `store.get()`, `store.describe()` and
`store.describe_all()` take **`actor` as a required parameter — no default**.

Required, not defaulted, because a default of `Actor.operator()` would
reintroduce exactly what the sentinel exists to remove: a caller who forgot to
think about authorization would silently get the always-allowed path, and the
call site would look identical to one that meant it.

`updated_by` is **derived from the actor** rather than passed alongside it, so
the recorded attribution is the same thing that was checked and cannot disagree
with it.

### The operator sentinel

`Actor.operator()` marks a call as deliberately running with operator authority
— a script, a migration, CC at a shell. It is always allowed, and that is the
correct behaviour, not an exemption: `GUIDANCE.md`'s authorization rule is
explicit that the flags exist for unattended execution, *"except when a human is
directly driving the action in the moment (you ran the command, you approved a
queued item) — that always just works."* A person at a shell on the Mac mini is
that human.

The sentinel rather than `None` because **`actor=None` reads as "no check
happened"**, indistinguishable from a caller who simply forgot. `Actor.operator()`
reads as "deliberately running as the operator", is greppable, and makes every
unauthenticated call visible at the call site instead of implicit in an absent
argument.

It carries a reserved `user_id` of `"operator"`, which is not a real users-table
id, so an operator write is distinguishable from a write by any actual user when
reading `settings.updated_by` later.

### `resolve()` stays ungated, deliberately

`store.resolve()` is the seam `config._settings_first()` calls on **every**
settings-backed config read — `config.chat_model()`, `config.model_options()`
and so on. It is the system reading its own configuration in order to operate,
not a person reading settings.

It takes no actor and is not gated. Requiring one would mean the Ollama client
needed an `Actor` to discover which model to call, which is not a permission
question at all. The person-facing reads (`get`, `describe`, `describe_all`) are
the ones `settings.read` governs.

### The limitation this creates

`Actor.operator()` is an unauthenticated bypass by design, so gating is only as
strong as the fact that nothing untrusted can call Python in this process. That
is true today — no chat route, no HTTP write surface — and stops being true the
moment task 2.2 lands. **Task 2.2 must construct a real `Actor` from the request
rather than reaching for the sentinel**, and that obligation is recorded in
`BUILD_PLAN.md` alongside R2's.

## R5 — Role is **fixed at creation**. Not mutable in this task.

**Recommendation: no `set_role()`, no promote/demote path.**

1. **No caller.** The admin panel is Phase 9; there is no authenticated request
   context to authorize a promotion from. Building it means designing it without
   the surface it belongs to, then designing it again.
2. **Promotion is the highest-value operation in the system.** Whoever can
   change a role can grant themselves settings access. Designing that without a
   request context, an audit surface, or authentication is designing the weakest
   version of it.
3. **Both users already exist** at the roles they need — Lyle admin, Jodie user,
   created at seed time.
4. `create_user()` already validates role at creation, which is the whole
   lifecycle currently needed.

**What "fixed" does and does not mean.** It means *no code path changes a role*.
It does **not** mean the database forbids it — `UPDATE users SET role='admin'`
still works from `sqlite3`. Adding a trigger to block that is a schema change,
which is Tier 3 and out of scope here. The honest statement is: role changes are
an operator action performed deliberately against the database, not a feature.

Revisit alongside the admin panel, when there is a surface to perform it on and
an authenticated actor to attribute it to.

---

## R6 — Authorization only. **Authentication is a separate, unbuilt thing.**

This distinction is the most important limitation in the spec.

- **Authentication** = proving you are Jodie. `password_hash` exists as a
  column, is written by nothing, and read by nothing.
- **Authorization** = deciding what Jodie may do. That is this task.

Without authentication, an `Actor` is whatever the caller says it is. **Role
gating is therefore only as strong as the caller's honesty.** For a
single-process backend with no HTTP write surface, run by its operator, that is
genuinely fine. It must not be described as security, and it must not be relied
on the moment an untrusted caller can construct an `Actor`.

Authentication needs its own task. This spec does not build it, stub it, or
assume its shape.

---

## R7 — Not foreclosing the cross-user retrieval decision

Task 1.5's retrieval can surface Jodie's chunks into Lyle's conversation.
`soul.md` acknowledges this and it is recorded as **an open gap awaiting a
`NOW.md` decision-log entry**, not something to settle in a schema task.

**This design keeps both answers open, by keeping two axes separate:**

- **Capability gating** — *may this person invoke this operation?* Keys on
  `role`. This task.
- **Data visibility** — *whose memory may this person's queries reach?* Keys on
  `chunks.user_id`. **Not this task.**

Conflating them would foreclose the decision. If visibility were folded into the
capability registry — say a `memory.read_all_users` capability — the model would
already presume the answer is "role-based", when the decision may land on
per-conversation scoping, an explicit sharing gesture, entity discretion, or no
restriction at all.

Concretely, this task:

- **adds no filter** to `retrieval.search()`;
- **adds no column** to `chunks` or `users` (no `visibility`, no `private`);
- **changes nothing** about what retrieval returns.

And it leaves the hooks any answer would need already present: `user_id` is on
every message and chunk, is carried through to `RetrievedChunk`, and
`retrieval.search()` can take an `actor=` parameter later without disturbing
anything here.

**Recommendation restated, not decided:** Lyle adds the `NOW.md` entry before
Phase 5's cross-user mining makes the question sharper.

---

## R8 — What this task actually delivers

**Builds:**

1. `Role` enum and `Capability` registry — two capabilities, both real.
2. `Actor` dataclass and `require()` / `PermissionDenied`.
3. `actor=` on `settings.store`'s read and write functions, enforced when
   supplied, with `updated_by` derived from the actor rather than passed
   alongside it.
4. `db.get_actor(user_id)` — loads a user row into an `Actor`.
5. Tests using **Lyle and Jodie from the seed corpus**: Jodie is denied
   `settings.write` and `settings.read`; Lyle is allowed both; an unregistered
   capability raises; `actor=None` still works for operator-run callers; a
   denial leaves the settings table unchanged.

**Does not build:** the loopback dependency, any admin route, authentication,
role mutation, retrieval filtering, or capabilities for unbuilt features.

**Records in `BUILD_PLAN.md`:** the R2 loopback contract against the first
admin-route task, and R4's obligation that task 2.2 pass a real actor.

---

## Open questions for approval

1. **Is R2 right?** The alternative is to build `is_loopback()` now as a pure
   predicate, unmounted, so Phase 9 inherits tested code rather than a written
   contract. I recommend against it on this project's own precedent, but it is a
   genuine trade and the security reasoning is the valuable part either way.
2. **Module placement.** `program/settings/permissions.py` puts it beside its
   only current consumer; `program/security/` or `program/api/permissions.py`
   would anticipate the HTTP surface. I lean to the first — it goes where its
   consumer is, and moving it later is mechanical.
3. **Confirm the two-axis model stays separate.** `GUIDANCE.md`'s
   `enabled`/`approval_required` governs the *entity's* capabilities; this
   registry governs *which human* may invoke an operation. I have kept them
   apart deliberately. If they are meant to be one system, this spec is shaped
   wrongly and should be reworked before implementation.
4. **Operator bypass spelling — RESOLVED: explicit `Actor.operator()`.**
   The reasoning for treating operator-run calls as always-allowed stands
   unchanged; only the spelling at the call site changed. `actor=None` reads as
   "no check happened" and is indistinguishable from a caller who forgot;
   `Actor.operator()` is deliberate, greppable, and visible. R4 updated, and the
   parameter is **required** rather than defaulted so a forgetful caller cannot
   silently land on the always-allowed path.
