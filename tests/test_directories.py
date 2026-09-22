"""Every runtime directory is covered by the guards. Phase 4 B0's standing item.

This exists because the same gap has been found **three times** — `backup_dir()` at
task 1.14, `artifact_dir()` at 2.6, `workspace_dir()` at Phase 4 A2 — and each time
only after something leaked or nearly did. See `AGENTS.md`, "Adding a runtime
directory".

The mechanism is always the same: a new directory resolves from **its own config
key**, so it starts outside `isolated_data_dir` and outside `backup.py` by default,
and nothing fails until a test writes into the real one.

So this enumerates rather than remembers, the way
`test_every_write_in_db_carries_the_retry` does for `db.py`'s writers. A new directory
accessor fails these tests until it is either covered or **explicitly exempted with a
reason** — an exemption is a real answer, it just has to be written down.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from program import config
from tests import conftest

#: Directories that deliberately get no `isolated_data_dir` repointing, and why.
ISOLATION_EXEMPT = {
    "config_dir": (
        "Read-only, and part of the repository rather than runtime state: it holds "
        "defaults.toml and local.toml. Nothing writes here, so there is nothing to "
        "isolate — and repointing it would hide the real configuration from the "
        "suite, which is what most tests are reading."
    ),
}

#: Directories `backup.py` deliberately does not copy, and why.
BACKUP_EXEMPT = {
    "backup_dir": (
        "It is the destination. Copying a backup into itself is the one recursion "
        "this module must not have."
    ),
    "config_dir": (
        "Repository content, not runtime state, and it holds `local.toml` — which "
        "carries auth.session_secret. A backup that swept up the signing key would "
        "spread it to wherever backups are kept. `blocklist.py` refuses to ingest "
        "this directory for the same reason."
    ),
    "data_dir": (
        "Covered by its contents rather than as a directory: both databases are "
        "captured through SQLite's online backup API under one held read lock, and "
        "the vector store is copied separately. A directory copy would duplicate "
        "them and could capture torn pages — the exact failure `_snapshot_databases` "
        "exists to avoid."
    ),
}


def directory_accessors() -> dict[str, Path]:
    """Every zero-argument `config` function that resolves to a directory path."""
    found = {}
    for name, fn in vars(config).items():
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        if getattr(fn, "__module__", "") != "program.config":
            continue
        if inspect.signature(fn).parameters:
            continue
        try:
            value = fn()
        except Exception:  # noqa: BLE001 — an accessor that raises is not a directory
            continue
        if isinstance(value, Path):
            found[name] = value
    return found


def test_there_is_at_least_one_of_each_kind_to_check():
    """A guard on the guard: if the enumeration silently found nothing, every test
    below would pass by vacuity — the failure mode this whole file exists to avoid."""
    accessors = directory_accessors()

    assert len(accessors) >= 5, accessors
    assert "workspace_dir" in accessors and "artifact_dir" in accessors


def test_every_runtime_directory_is_repointed_by_the_isolation_fixture():
    """Otherwise a test writing there writes into the real one — which is how 65 real
    PNGs ended up in the repository's `workspace/`."""
    source = inspect.getsource(conftest.isolated_data_dir)

    missing = []
    for name in directory_accessors():
        if name in ISOLATION_EXEMPT:
            continue
        env_var = f"ANAM_{name.upper()}"
        if env_var not in source:
            missing.append(f"{name}() — expected {env_var} in isolated_data_dir")

    assert missing == [], (
        "runtime directories not isolated:\n  " + "\n  ".join(missing)
        + "\n\nAdd the setenv/delenv pair in tests/conftest.py, or add an entry to "
        "ISOLATION_EXEMPT here saying why it needs none. See AGENTS.md, "
        "'Adding a runtime directory'."
    )


def test_every_runtime_directory_is_watched_by_the_session_guard():
    """Repointing is not enough on its own: a test that forgets the fixture still
    writes into the real directory, and the session guard is what notices."""
    guard = inspect.getsource(conftest)

    missing = [
        name for name in directory_accessors()
        if name not in ISOLATION_EXEMPT and f"config.{name}()" not in guard
    ]

    assert missing == [], (
        f"runtime directories the session guard never looks at: {missing}. Capture "
        f"the real path at import and check it in `_guard_runtime_store`. For a "
        f"directory that is part of the tracked skeleton, 'was it created?' can never "
        f"fire — snapshot the file set instead, as workspace_dir does."
    )


def test_every_runtime_directory_is_either_backed_up_or_exempted_with_a_reason():
    """The question is what it holds that exists nowhere else."""
    from program.ops import backup

    source = inspect.getsource(backup)

    missing = [
        name for name in directory_accessors()
        if name not in BACKUP_EXEMPT and f"config.{name}()" not in source
    ]

    assert missing == [], (
        f"runtime directories `backup.py` neither copies nor exempts: {missing}. "
        f"Either copy it, or add an entry to BACKUP_EXEMPT here saying what it holds "
        f"that can be rebuilt from something else."
    )


@pytest.mark.parametrize("registry", [ISOLATION_EXEMPT, BACKUP_EXEMPT])
def test_the_exemptions_name_real_directories_and_carry_real_reasons(registry):
    """An exemption for a directory that no longer exists is stale, and a one-word
    reason is not a reason."""
    accessors = directory_accessors()

    for name, reason in registry.items():
        assert name in accessors, f"{name} is exempted but is not a directory accessor"
        assert len(reason.split()) >= 12, f"{name}'s exemption does not explain itself"
