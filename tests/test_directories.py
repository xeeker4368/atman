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
import os
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


def test_the_isolation_fixture_actually_repoints_every_runtime_directory(
    isolated_data_dir,
):
    """The property, not the spelling.

    This used to grep `inspect.getsource(conftest.isolated_data_dir)` for the string
    `ANAM_<NAME>`, which passes on a mention — in a comment, in a docstring, in a
    line that sets the wrong value. It is the same weakness `BUILT.md` already
    records against `test_the_gate_takes_no_actor`: *a check that passes on spelling
    while the property weakens is worse than no check.*

    So resolve every accessor **from inside the fixture** and require the answer to
    be somewhere under the temporary directory. A new runtime directory that nobody
    repointed resolves to its real path and fails here, which is the whole point.
    """
    leaked = {}
    for name, path in directory_accessors().items():
        if name in ISOLATION_EXEMPT:
            continue
        if not str(Path(path).resolve()).startswith(str(Path(isolated_data_dir).resolve())):
            leaked[name] = str(path)

    assert leaked == {}, (
        "these runtime directories still resolve to their REAL paths inside "
        f"`isolated_data_dir`: {leaked}\n\nAdd the setenv/delenv pair in "
        "tests/conftest.py, or add an entry to ISOLATION_EXEMPT here saying why it "
        "needs none. See AGENTS.md, 'Adding a runtime directory'."
    )


def test_the_session_guard_watches_every_runtime_directory():
    """Repointing is not enough: a test that forgets the fixture still writes into
    the real directory, and the session guard is what notices.

    Also a property rather than a grep — `conftest.REAL_DIRS` is data, so this
    compares resolved paths. A directory nested inside a watched one counts as
    watched, which is why `data/chromadb` and `data/artifacts` need no separate
    entry to satisfy this.
    """
    watched = [str(Path(p).resolve()) for p in conftest.REAL_DIRS.values()]

    missing = []
    for name, path in directory_accessors().items():
        if name in ISOLATION_EXEMPT:
            continue
        resolved = str(Path(path).resolve())
        if not any(resolved == w or resolved.startswith(w + os.sep) for w in watched):
            missing.append(name)

    assert missing == [], (
        f"runtime directories the session guard never looks at: {missing}. Add the "
        f"real path to `conftest.REAL_DIRS`, which is what `_fingerprint()` walks."
    )


def test_the_session_guard_notices_a_write_into_a_watched_directory(tmp_path,
                                                                    monkeypatch):
    """Proven to bite, rather than assumed to.

    The guard's own failure mode is silence, so the thing worth testing is that it
    speaks. This drives `_fingerprint()` directly against a directory it is told to
    watch: a created file and a MODIFIED one must both show up, because the
    modification case is the one a file-set snapshot could not see.
    """
    watched = tmp_path / "pretend-real"
    watched.mkdir()
    existing = watched / "working.db"
    existing.write_text("rows")
    untouched = watched / "archive.db"
    untouched.write_text("frozen")

    monkeypatch.setattr(conftest, "REAL_DIRS", {"pretend": str(watched)})
    before = conftest._fingerprint()

    (watched / "leaked.png").write_bytes(b"new file")
    existing.write_text("rows and one more")      # same file, different content

    after = conftest._fingerprint()

    created = set(after) - set(before)
    modified = {p for p in set(after) & set(before) if after[p] != before[p]}

    assert created == {str(watched / "leaked.png")}
    assert modified == {str(existing)}, "a write into an existing store must be seen"
    assert str(untouched) not in created | modified


def test_backup_actually_captures_every_non_exempt_runtime_directory(
    isolated_data_dir,
):
    """The property, established by running a backup rather than by grepping.

    This used to check that the string `config.<name>()` appeared anywhere in
    `backup.py` — which a mention in the manifest's `source` dict satisfies just as
    well as a copy, so a directory could be *named* as a source with no bytes behind
    it. Instead: plant a canary in each non-exempt directory, take a real backup, and
    require the canary to come out the other side.
    """
    from program.memory import db
    from program.ops import backup

    db.init_databases()

    planted = {}
    for name in directory_accessors():
        if name in BACKUP_EXEMPT:
            continue
        directory = getattr(config, name)()
        directory.mkdir(parents=True, exist_ok=True)
        canary = directory / f"canary-{name}.txt"
        canary.write_text(f"planted for {name}")
        planted[name] = canary.name

    assert planted, "nothing to check — every directory was exempt, which is a bug here"

    result = backup.create_backup(include_vectors=False)
    captured = {p.name for p in Path(result.directory).rglob("*") if p.is_file()}

    missing = sorted(name for name, filename in planted.items()
                     if filename not in captured)

    assert missing == [], (
        f"`backup.py` does not actually copy: {missing}. Either copy it, or add an "
        f"entry to BACKUP_EXEMPT here saying what it holds that can be rebuilt from "
        f"something else."
    )


@pytest.mark.parametrize("registry", [ISOLATION_EXEMPT, BACKUP_EXEMPT])
def test_the_exemptions_name_real_directories_and_carry_real_reasons(registry):
    """An exemption for a directory that no longer exists is stale, and a one-word
    reason is not a reason."""
    accessors = directory_accessors()

    for name, reason in registry.items():
        assert name in accessors, f"{name} is exempted but is not a directory accessor"
        assert len(reason.split()) >= 12, f"{name}'s exemption does not explain itself"
