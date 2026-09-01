"""Who may invoke what. Roles, capabilities, and the check between them.

Design of record: ``docs/ROLE_GATING_DESIGN.md`` (revision 2), cited as R1–R8.

This is **authorization, not authentication** (R6)
--------------------------------------------------
Authentication proves you are Jodie. Authorization decides what Jodie may do.
Only the second is built. ``users.password_hash`` exists as a column, is written
by nothing, and is read by nothing.

So an :class:`Actor` is whatever the caller says it is, and **role gating is
only as strong as the caller's honesty**. For a single-process backend with no
HTTP write surface, run by its operator, that is genuinely fine. It is not
security, must not be described as security, and stops being adequate the moment
an untrusted caller can construct an ``Actor`` — which is task 2.2.

Capabilities are data, not conditionals (R3)
--------------------------------------------
A frozen registry, the same shape ``program/settings/store.py`` uses for setting
keys. Two capabilities are registered because two are enforceable: settings
reads and settings writes. Everything else in decision #17's list for Jodie —
chat, creative writing, image generation, research triggering, Moltbook posting
— has **no built capability to gate**. Each phase registers its own capability
when it builds the thing, so this registry stays a description of what exists
rather than of what is planned.

Asking about an unregistered capability raises rather than defaulting, so a
future task cannot silently check a capability nobody defined and be quietly
allowed.

This is not the two-axis model
------------------------------
``GUIDANCE.md``'s ``enabled`` / ``approval_required`` axes govern *the entity's*
capabilities — whether it may post to Moltbook, whether a draft needs review.
This registry governs *which human* may invoke an operation. Different
questions, deliberately not merged (R3).

And it is not data visibility
-----------------------------
Whether Lyle's query may reach Jodie's chunks is a **separate axis** keyed on
``chunks.user_id``, and it is an open decision-log question, not something this
module answers (R7). Nothing here filters retrieval, and no capability like
``memory.read_all_users`` is registered — defining one would presume the answer
is role-based when it may land on per-conversation scoping, explicit sharing, or
entity discretion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Reserved ``user_id`` for the operator sentinel. Deliberately not a real
#: users-table id, so an operator-authored settings change is distinguishable
#: from one made by an actual user when reading ``settings.updated_by`` later.
OPERATOR_ID = "operator"
OPERATOR_NAME = "operator"


class Role(str, Enum):
    """The two roles the ``users`` table's CHECK constraint permits."""

    ADMIN = "admin"
    USER = "user"


class PermissionDenied(PermissionError):
    """An actor attempted something their role does not permit."""


class UnknownCapabilityError(KeyError):
    """A capability that is not in the registry was checked."""


@dataclass(frozen=True)
class Capability:
    name: str
    minimum_role: Role
    description: str


#: Registered capabilities. **Only what is built and enforceable today.**
#:
#: Adding an entry here is how a later phase gates its own feature; adding one
#: for a feature that does not exist would make this a list of intentions, which
#: is the thing task 1.4 removed the `artifacts` table to avoid.
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "settings.read",
        Role.ADMIN,
        "Read effective settings values and their provenance.",
    ),
    Capability(
        "settings.write",
        Role.ADMIN,
        "Change or reset a setting. Takes effect immediately, no restart.",
    ),
)

_BY_NAME = {capability.name: capability for capability in CAPABILITIES}


def capability(name: str) -> Capability:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise UnknownCapabilityError(
            f"{name!r} is not a registered capability. Known: "
            f"{', '.join(sorted(_BY_NAME))}. Capabilities are registered by the "
            f"task that builds the thing they gate — an unregistered name is a "
            f"missing registration, not a permissive default."
        ) from None


@dataclass(frozen=True)
class Actor:
    """Who is asking. Constructed from a users row, or the operator sentinel."""

    user_id: str
    name: str
    role: Role

    @classmethod
    def operator(cls) -> "Actor":
        """The person at a shell on this machine. Always allowed.

        This is the ``GUIDANCE.md`` carve-out, not an exemption from it: *"except
        when a human is directly driving the action in the moment (you ran the
        command, you approved a queued item) — that always just works."* A
        script, a migration, or CC at a prompt is that human.

        Spelled as an explicit sentinel rather than ``actor=None`` on purpose.
        ``None`` reads as "no check happened" and is indistinguishable from a
        caller who forgot to pass one; this is deliberate, greppable, and makes
        every unauthenticated call visible at the call site.
        """
        return cls(user_id=OPERATOR_ID, name=OPERATOR_NAME, role=Role.ADMIN)

    @property
    def is_operator(self) -> bool:
        return self.user_id == OPERATOR_ID

    def can(self, name: str) -> bool:
        """Whether this actor holds a capability. Raises on an unknown name."""
        required = capability(name).minimum_role
        if self.is_operator:
            return True
        if required is Role.ADMIN:
            return self.role is Role.ADMIN
        return True


def require(actor: Actor, name: str) -> None:
    """Raise :class:`PermissionDenied` unless ``actor`` holds the capability."""
    if not isinstance(actor, Actor):
        raise TypeError(
            f"expected an Actor, got {type(actor).__name__}. Operator-run "
            f"callers pass Actor.operator() explicitly — there is no implicit "
            f"unauthenticated path."
        )
    if actor.can(name):
        return
    spec = capability(name)
    raise PermissionDenied(
        f"{actor.name} ({actor.role.value}) may not {name}: it requires the "
        f"{spec.minimum_role.value} role. {spec.description}"
    )
