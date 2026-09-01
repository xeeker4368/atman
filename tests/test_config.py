"""Configuration layering tests.

The env-override case is the Phase 0 gate: all three layers merge in the right
order and the environment wins.
"""

from __future__ import annotations

import pytest

from program import config


@pytest.fixture
def layered(tmp_path, monkeypatch):
    """Point config at a temporary config directory and reset the cache."""
    monkeypatch.setenv("ANAM_CONFIG_DIR", str(tmp_path))
    config.reload()
    yield tmp_path
    config.reload()


def write(path, text):
    path.write_text(text.strip() + "\n", encoding="utf-8")


def test_fallback_applies_with_no_config_files(layered):
    assert config.get("api", "host") == "127.0.0.1"
    assert config.get("api", "port") == 8000


def test_defaults_toml_overrides_fallback(layered):
    write(layered / "defaults.toml", '[api]\nhost = "0.0.0.0"\nport = 9001')
    config.reload()
    assert config.get("api", "host") == "0.0.0.0"
    assert config.get("api", "port") == 9001


def test_local_toml_overrides_defaults(layered):
    write(layered / "defaults.toml", '[api]\nhost = "0.0.0.0"\nport = 9001')
    write(layered / "local.toml", "[api]\nport = 9002")
    config.reload()
    # local.toml wins on the key it sets...
    assert config.get("api", "port") == 9002
    # ...and leaves the rest of the section from defaults.toml intact.
    assert config.get("api", "host") == "0.0.0.0"


def test_env_overrides_both_files(layered, monkeypatch):
    """The Phase 0 gate: environment beats local.toml beats defaults.toml."""
    write(layered / "defaults.toml", '[api]\nhost = "0.0.0.0"\nport = 9001')
    write(layered / "local.toml", "[api]\nport = 9002")
    monkeypatch.setenv("ANAM_API_PORT", "9003")
    config.reload()
    assert config.get("api", "port") == 9003
    assert config.get("api", "host") == "0.0.0.0"


def test_merge_is_deep_not_wholesale_section_replacement(layered):
    """A partial section in a higher layer must not drop the lower layer's keys."""
    write(layered / "defaults.toml", '[ollama]\nhost = "http://a:1"\ntimeout_seconds = 300')
    write(layered / "local.toml", "[ollama]\ntimeout_seconds = 60")
    config.reload()
    assert config.get("ollama", "timeout_seconds") == 60
    assert config.get("ollama", "host") == "http://a:1"


def test_bad_integer_env_raises_rather_than_silently_defaulting(layered, monkeypatch):
    monkeypatch.setenv("ANAM_API_PORT", "not-a-number")
    config.reload()
    with pytest.raises(config.ConfigError) as exc:
        config.get("api", "port")
    assert "ANAM_API_PORT" in str(exc.value)


def test_relative_paths_resolve_against_project_root(layered):
    assert config.data_dir() == config.PROJECT_ROOT / "data"
    assert config.data_dir().is_absolute()


def test_absolute_path_is_left_alone(layered, monkeypatch):
    monkeypatch.setenv("ANAM_DATA_DIR", "/tmp/anam-test-data")
    config.reload()
    assert str(config.data_dir()) == "/tmp/anam-test-data"


def test_values_resolve_at_call_time_not_import_time(layered, monkeypatch):
    """The property that keeps tests out of the real store.

    A module-level constant captured at import would make this impossible, which
    is exactly how the reference build's suite ended up writing into production.
    """
    first = config.api_port()
    monkeypatch.setenv("ANAM_API_PORT", "9999")
    config.reload()
    second = config.api_port()
    assert first == 8000
    assert second == 9999


def test_section_returns_a_copy(layered):
    api = config.section("api")
    api["host"] = "mutated"
    assert config.get("api", "host") == "127.0.0.1"
