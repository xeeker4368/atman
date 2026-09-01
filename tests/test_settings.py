"""Settings persistence: DB authoritative, config the seed, cache dropped on write."""

from __future__ import annotations

import sqlite3

import pytest

from program import config
from program.memory import db
from program.settings import store


@pytest.fixture
def settings_store(isolated_data_dir):
    """An initialised store with an empty settings table and a clean cache."""
    db.init_databases()
    store.reset_cache()
    yield isolated_data_dir
    store.reset_cache()


# --- The read path: settings first, config only as fallback -----------------


def test_config_seed_is_used_when_no_row_exists(settings_store):
    assert not store.has_row("models.chat")
    assert store.get("models.chat") == config.get("models", "chat")
    assert store.describe("models.chat").source == "config"


def test_a_row_takes_precedence_over_config(settings_store):
    seed = config.get("models", "chat")
    store.set("models.chat", "some-other-model", updated_by="lyle")

    assert store.get("models.chat") == "some-other-model"
    assert store.get("models.chat") != seed
    assert store.describe("models.chat").source == "settings"


def test_the_existing_config_accessor_is_the_settings_first_read_path(settings_store):
    """The point of the task: config.chat_model() must honour the table.

    Not a parallel accessor callers have to remember to use — the one that
    already exists, so every existing caller becomes settings-first with no
    change. This is what turns config.py's scope comment from an intention
    into the actual behaviour.
    """
    assert config.chat_model() == config.get("models", "chat")

    store.set("models.chat", "swapped-model")

    assert config.chat_model() == "swapped-model"


def test_model_options_resolves_each_key_settings_first(settings_store):
    assert config.model_options()["temperature"] == 0.35

    store.set("model_options.temperature", 0.9)

    assert config.model_options()["temperature"] == 0.9
    # Untouched keys still come from config.
    assert config.model_options()["num_ctx"] == config.get("model_options", "num_ctx")


def test_clearing_a_row_falls_back_to_the_config_seed_again(settings_store):
    seed = config.get("models", "chat")
    store.set("models.chat", "temporary")
    assert config.chat_model() == "temporary"

    assert store.clear("models.chat") is True

    assert config.chat_model() == seed
    assert store.describe("models.chat").source == "config"
    assert store.clear("models.chat") is False


def test_get_and_section_stay_pure_config_reads(settings_store):
    """The store's own fallback calls get(); delegating there would recurse."""
    store.set("models.chat", "db-value")

    assert config.get("models", "chat") != "db-value"
    assert config.section("models")["chat"] != "db-value"
    assert config.chat_model() == "db-value"


# --- No restart required ----------------------------------------------------


def test_a_write_takes_effect_immediately_without_reload(settings_store):
    """Decision #8: no setting requires a restart.

    The read is taken first so the cache is definitely populated and stale if
    the write does not invalidate it.
    """
    assert config.model_options()["temperature"] == 0.35

    store.set("model_options.temperature", 0.11)

    # No config.reload(), no reset_cache(), no new process.
    assert config.model_options()["temperature"] == 0.11


def test_the_cache_is_actually_dropped_on_write_not_just_overwritten(settings_store):
    """Proven by writing behind the store's back after an invalidation.

    A read repopulates from the database rather than from anything the write
    path put in the cache directly.
    """
    store.set("models.chat", "first")
    assert store.get("models.chat") == "first"

    with db.transaction() as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", ("second", "models.chat"))
    # Cache is still warm and still holds the old value.
    assert store.get("models.chat") == "first"

    store.invalidate()
    assert store.get("models.chat") == "second"


def test_reads_do_not_hit_the_database_once_cached(settings_store, monkeypatch):
    """One query per invalidation, not one per accessor call.

    Making every settings-backed accessor DB-first is only reasonable if it is
    not a query per read.
    """
    store.set("models.chat", "cached-model")

    calls = {"n": 0}
    real = store._load_table

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(store, "_load_table", counting)
    store.invalidate()

    for _ in range(10):
        config.chat_model()
        config.model_options()

    assert calls["n"] == 1


def test_the_cache_is_keyed_by_store_path_not_shared_across_stores(
    isolated_data_dir, tmp_path, monkeypatch
):
    """Repointing the data directory must not serve another store's values."""
    db.init_databases()
    store.reset_cache()
    store.set("models.chat", "store-one-model")
    assert config.chat_model() == "store-one-model"

    other = tmp_path / "second-store"
    monkeypatch.setenv("ANAM_DATA_DIR", str(other))
    config.reload()
    db.init_databases()

    # No reset_cache() call — the path key alone must keep them apart.
    assert config.chat_model() == config.get("models", "chat")
    assert not store.has_row("models.chat")

    store.reset_cache()


# --- Typing -----------------------------------------------------------------


def test_values_round_trip_with_their_declared_type(settings_store):
    store.set("model_options.num_ctx", 16384)
    store.set("model_options.temperature", 0.7)
    store.set("model_options.think", True)
    store.set("models.embedding", "some-embedder")

    assert store.get("model_options.num_ctx") == 16384
    assert isinstance(store.get("model_options.num_ctx"), int)
    assert store.get("model_options.temperature") == 0.7
    assert isinstance(store.get("model_options.temperature"), float)
    assert store.get("model_options.think") is True
    assert store.get("models.embedding") == "some-embedder"


def test_a_wrong_type_is_refused_at_write_time(settings_store):
    """Fails at the write, not at some unrelated caller's next read."""
    with pytest.raises(store.SettingTypeError, match="declared int"):
        store.set("model_options.num_ctx", "not-a-number")
    with pytest.raises(store.SettingTypeError, match="declared bool"):
        store.set("model_options.think", "yes")
    assert not store.has_row("model_options.num_ctx")


def test_a_bool_is_not_accepted_as_an_int(settings_store):
    """True == 1 in Python; the settings table should not blur the two."""
    with pytest.raises(store.SettingTypeError):
        store.set("model_options.num_ctx", True)


def test_a_corrupt_row_raises_rather_than_silently_using_the_seed(settings_store):
    """Quietly falling back would show a panel value the system is not using."""
    store.set("model_options.num_ctx", 8192)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE settings SET value = ? WHERE key = ?", ("garbage", "model_options.num_ctx")
        )
    store.invalidate()

    with pytest.raises(store.SettingTypeError, match="not a valid int"):
        store.get("model_options.num_ctx")


def test_the_stored_value_type_matches_the_registry(settings_store):
    store.set("model_options.temperature", 0.5)
    with db.connection() as conn:
        row = conn.execute(
            "SELECT value_type FROM settings WHERE key = ?", ("model_options.temperature",)
        ).fetchone()
    assert row["value_type"] == "float"


def test_every_registered_type_is_one_the_schema_check_allows(settings_store):
    """The CHECK constraint is the real arbiter; the registry must agree with it."""
    for spec in store.SETTINGS:
        assert spec.value_type in store.VALUE_TYPES
    # And prove the constraint actually rejects something outside it.
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO settings (key, value, value_type, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ("bogus", "x", "datetime", db.now_iso()),
            )


# --- The registry boundary --------------------------------------------------


def test_bootstrap_keys_are_not_settable(settings_store):
    """Data paths and ports must stay config-only — they precede the database."""
    for name in ("paths.data_dir", "api.port", "ollama.host"):
        with pytest.raises(store.UnknownSettingError):
            store.set(name, "anything")


def test_bootstrap_accessors_are_unaffected_by_the_settings_table(settings_store):
    """Even a hand-written row must not change a bootstrap value."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, value_type, updated_at) VALUES (?, ?, ?, ?)",
            ("api.port", "9999", "int", db.now_iso()),
        )
    store.invalidate()

    assert config.api_port() == config.get("api", "port")
    assert config.api_port() != 9999


def test_resolve_passes_through_unregistered_keys_untouched(settings_store):
    sentinel = object()
    assert store.resolve("paths", "data_dir", sentinel) is sentinel


def test_unknown_key_names_what_is_known(settings_store):
    with pytest.raises(store.UnknownSettingError, match="models.chat"):
        store.get("models.nonexistent")


# --- Provenance / diagnostics ------------------------------------------------


def test_updated_by_and_updated_at_are_recorded(settings_store):
    store.set("models.chat", "attributed", updated_by="lyle")
    described = store.describe("models.chat")
    assert described.updated_by == "lyle"
    assert described.updated_at is not None
    assert described.source == "settings"


def test_describe_all_covers_the_whole_registry(settings_store):
    store.set("model_options.temperature", 0.42)
    described = {d.name: d for d in store.describe_all()}

    assert set(described) == {spec.name for spec in store.SETTINGS}
    assert described["model_options.temperature"].source == "settings"
    assert described["models.chat"].source == "config"


# --- Degradation before the database exists ---------------------------------


def test_reads_fall_back_to_config_when_there_is_no_database(isolated_data_dir):
    """A fresh checkout must still start. Nothing is created on this path."""
    store.reset_cache()
    # init_databases() deliberately not called.
    assert config.chat_model() == config.get("models", "chat")
    assert store.describe("models.chat").source == "config"
    assert not (isolated_data_dir / "working.db").exists()
    store.reset_cache()


def test_a_read_never_creates_a_database_file(isolated_data_dir):
    """sqlite3.connect() creates a missing file; the read path must not.

    Regression guard for a defect found building this task — a settings read on
    a data directory with no store left an empty working.db behind.
    """
    store.reset_cache()
    config.chat_model()
    config.model_options()
    store.describe_all()

    assert not (isolated_data_dir / "working.db").exists()
    assert not (isolated_data_dir / "archive.db").exists()
    store.reset_cache()
