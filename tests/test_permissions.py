"""Role gating: admin vs household user, against the real seed-corpus users.

Lyle (admin) and Jodie (user) are the concrete case throughout — they are the
two people this build actually has, created by `program/ops/seed.py`.
"""

from __future__ import annotations

import pytest

from program.memory import db
from program.settings import store
from program.settings.permissions import (
    CAPABILITIES,
    Actor,
    PermissionDenied,
    Role,
    UnknownCapabilityError,
    capability,
    require,
)


@pytest.fixture
def household(isolated_data_dir):
    """The two real users, created the way seed.py creates them."""
    db.init_databases()
    lyle_id = db.create_user("Lyle", role="admin")
    jodie_id = db.create_user("Jodie", role="user")
    store.reset_cache()
    yield {
        "lyle": db.get_actor(lyle_id),
        "jodie": db.get_actor(jodie_id),
        "operator": Actor.operator(),
    }
    store.reset_cache()


# --- Actors load from the real users table ----------------------------------


def test_actors_load_from_the_users_table_with_their_roles(household):
    assert household["lyle"].name == "Lyle"
    assert household["lyle"].role is Role.ADMIN
    assert household["jodie"].name == "Jodie"
    assert household["jodie"].role is Role.USER


def test_an_unknown_user_id_yields_no_actor_rather_than_a_default(isolated_data_dir):
    """An unrecognised user must not resolve to a default role."""
    db.init_databases()
    assert db.get_actor("no-such-user") is None


# --- Jodie is denied settings, Lyle is allowed ------------------------------


def test_jodie_cannot_write_settings(household):
    with pytest.raises(PermissionDenied, match="Jodie"):
        store.set("model_options.temperature", 0.9, household["jodie"])


def test_jodie_cannot_read_settings(household):
    """PROJECT.md: no settings access, ever. Reads included."""
    with pytest.raises(PermissionDenied):
        store.get("models.chat", household["jodie"])
    with pytest.raises(PermissionDenied):
        store.describe("models.chat", household["jodie"])
    with pytest.raises(PermissionDenied):
        store.describe_all(household["jodie"])
    with pytest.raises(PermissionDenied):
        store.has_row("models.chat", household["jodie"])


def test_lyle_can_read_and_write_settings(household):
    store.set("models.chat", "a-different-model", household["lyle"])
    assert store.get("models.chat", household["lyle"]) == "a-different-model"
    assert store.describe("models.chat", household["lyle"]).source == "settings"


def test_a_denied_write_changes_nothing(household):
    """The denial must be refusal, not a partial write."""
    before = store.describe("models.chat", household["lyle"])

    with pytest.raises(PermissionDenied):
        store.set("models.chat", "jodie-was-here", household["jodie"])

    after = store.describe("models.chat", household["lyle"])
    assert after.value == before.value
    assert after.source == before.source == "config"
    assert not store.has_row("models.chat", household["lyle"])


def test_a_denied_clear_changes_nothing(household):
    store.set("models.chat", "set-by-lyle", household["lyle"])

    with pytest.raises(PermissionDenied):
        store.clear("models.chat", household["jodie"])

    assert store.get("models.chat", household["lyle"]) == "set-by-lyle"


def test_the_denial_message_names_who_what_and_why(household):
    with pytest.raises(PermissionDenied) as excinfo:
        store.set("models.chat", "x", household["jodie"])
    message = str(excinfo.value)
    assert "Jodie" in message
    assert "user" in message
    assert "settings.write" in message
    assert "admin" in message


# --- The operator sentinel ---------------------------------------------------


def test_the_operator_sentinel_is_always_allowed(household):
    """Scripts, migrations and CC at a shell — GUIDANCE.md's carve-out."""
    operator = Actor.operator()
    store.set("models.chat", "operator-set", operator)
    assert store.get("models.chat", operator) == "operator-set"


def test_the_operator_is_distinguishable_from_a_real_user(household):
    operator = Actor.operator()
    assert operator.is_operator is True
    assert household["lyle"].is_operator is False
    # Not a real users-table id, so operator writes are traceable as such.
    assert db.get_actor(operator.user_id) is None


def test_operator_writes_are_attributed_to_the_operator(household):
    store.set("models.chat", "x", Actor.operator())
    described = store.describe("models.chat", household["lyle"])
    assert described.updated_by == "operator"


def test_there_is_no_implicit_unauthenticated_path(household):
    """actor is required with no default — a forgetful caller cannot slip past.

    This is the whole reason the sentinel replaced `actor=None`.
    """
    with pytest.raises(TypeError):
        store.set("models.chat", "x")
    with pytest.raises(TypeError):
        store.get("models.chat")


def test_passing_a_non_actor_raises_rather_than_being_truthy(household):
    with pytest.raises(TypeError, match="expected an Actor"):
        require("lyle", "settings.write")
    with pytest.raises(TypeError):
        require(None, "settings.write")


# --- updated_by is derived, not asserted by the caller ----------------------


def test_updated_by_is_derived_from_the_actor(household):
    """The recorded attribution and the thing authorized cannot disagree."""
    store.set("models.chat", "by-lyle", household["lyle"])
    assert store.describe("models.chat", household["lyle"]).updated_by == "Lyle"


# --- The registry ------------------------------------------------------------


def test_an_unregistered_capability_raises_rather_than_defaulting(household):
    """A capability nobody defined must not be silently permitted."""
    with pytest.raises(UnknownCapabilityError, match="settings.write"):
        require(household["lyle"], "moltbook.post")
    with pytest.raises(UnknownCapabilityError):
        household["jodie"].can("research.trigger")


def test_only_built_and_enforceable_capabilities_are_registered():
    """The registry describes what exists, not what is planned.

    Unbuilt features — chat, creative writing, image generation, research
    triggering, Moltbook posting — register their own capability when the task
    that builds them lands.
    """
    assert {c.name for c in CAPABILITIES} == {"settings.read", "settings.write"}


def test_every_registered_capability_is_well_formed():
    for spec in CAPABILITIES:
        assert isinstance(spec.minimum_role, Role)
        assert spec.description
        assert capability(spec.name) is spec


def test_data_visibility_is_not_a_capability():
    """R7: whether Lyle's query may reach Jodie's chunks is a separate axis.

    Registering something like `memory.read_all_users` here would presume the
    answer is role-based, foreclosing the open NOW.md decision.
    """
    names = {c.name for c in CAPABILITIES}
    for foreclosing in (
        "memory.read_all_users", "memory.read", "retrieval.cross_user",
    ):
        assert foreclosing not in names


# --- Role is fixed at creation (R5) -----------------------------------------


def test_there_is_no_role_mutation_api(household):
    """R5: promotion is the highest-value operation and has no surface yet.

    Fixed at creation until the admin panel exists to perform it and an
    authenticated actor exists to attribute it to.
    """
    surface = {n for n in dir(db) if not n.startswith("_")}
    for forbidden in ("set_role", "promote_user", "demote_user", "update_user_role"):
        assert forbidden not in surface


def test_create_user_still_validates_role_at_creation(household):
    with pytest.raises(ValueError, match="unknown role"):
        db.create_user("Someone", role="superuser")


# --- Not foreclosing the retrieval decision (R7) ----------------------------


def test_retrieval_is_unchanged_by_this_task(household):
    """No filter was added, and no actor parameter appeared on search()."""
    import inspect

    from program.memory import retrieval

    params = set(inspect.signature(retrieval.search).parameters)
    assert "actor" not in params
    assert "user_id" not in params


def test_chunks_still_carry_user_id_for_whichever_answer_lands(household):
    """The hook any future visibility rule would need is already present."""
    from program.memory.retrieval import RetrievedChunk

    assert "user_id" in RetrievedChunk.__dataclass_fields__


# --- config's own reads stay ungated ----------------------------------------


def test_config_accessors_do_not_require_an_actor(household):
    """resolve() is the system reading its own configuration, not a person.

    Requiring an Actor here would mean the Ollama client needed one to discover
    which model to call.
    """
    from program import config

    store.set("models.chat", "configured-model", household["lyle"])

    assert config.chat_model() == "configured-model"
    assert config.model_options()["temperature"] == 0.35
