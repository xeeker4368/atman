"""The suite never reads the developer's ``config/local.toml`` (``conftest.pytest_configure``).

With a session secret in a real local.toml, the two missing-secret tests stopped raising: the
suite's result depended on a gitignored file on one machine.
"""

from __future__ import annotations

from pathlib import Path

from program import config
from tests import conftest


def test_the_suite_reads_a_copy_of_the_tracked_config_files_only():
    assert config.config_dir() == conftest.TRACKED_CONFIG_DIR
    assert config.config_dir() != Path(config.PROJECT_ROOT) / "config"
    assert sorted(p.name for p in config.config_dir().iterdir()) == [
        "defaults.toml", "local.example.toml"]


def test_the_copy_is_the_tracked_defaults_byte_for_byte():
    tracked = Path(config.PROJECT_ROOT) / "config" / "defaults.toml"
    assert (config.config_dir() / "defaults.toml").read_bytes() == tracked.read_bytes()
