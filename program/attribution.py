"""Who a write belongs to. Phase 4 P0.

Design of record: ``docs/MEDIA_AND_CREATIVE_DESIGN.md`` (Q2).

A tool that *writes* needs to know whose record it is writing into: ``artifacts``
and ``chunks`` both carry a ``user_id``, and on ``artifacts`` it is ``NOT NULL``
because *"an artifact with nobody behind it is not a state this system should be
able to represent."* Tool handlers are called with **model-supplied arguments
only**, so that identity has to arrive by another route.

**This is not an ``Actor`` and must not become one.**
``settings.permissions.Actor`` answers *"what is this person allowed to do"* and
carries a ``Role``. This answers *"whose record is this"* and carries no role, no
permission and no authorization meaning at all. The separation is deliberate and
is the reason for the name: several tests assert that nothing in the tool path
takes an ``actor``, ``role`` or ``user`` parameter, and those tests are protecting
a real property — fabrication checks, retrieval and correction all treat both
household members identically, and an authorization object in the tool path is how
that would quietly stop being true.

Attribution and authorization answer different questions, and mixing them would
make the second reachable wherever the first is needed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttributionContext:
    """Whose record a tool's write belongs to.

    ``user_id`` is a real ``users.id``. In a live turn it is **the person
    present** — they asked for the thing, and the record of it belongs in their
    history (Q2b). With no person present it is the entity's own row, via
    :meth:`for_entity`.

    Deliberately minimal. It carries no ``conversation_id`` because an artifact
    row has no conversation column, and no ``Role`` because nothing here decides
    what anyone may do.
    """

    user_id: str

    def __post_init__(self) -> None:
        if not self.user_id or not str(self.user_id).strip():
            raise ValueError(
                "AttributionContext needs a real users.id. An empty id would "
                "reach artifacts.user_id, which is NOT NULL, and fail at the "
                "write instead of here where the caller is visible."
            )

    @classmethod
    def for_entity(cls) -> "AttributionContext":
        """The entity's own row, for a write with no person present.

        Imported lazily: this module is deliberately free of database imports so
        that anything in the tool path can depend on it without pulling in
        ``program.memory``.
        """
        from program.memory import db

        return cls(user_id=db.entity_user_id())
