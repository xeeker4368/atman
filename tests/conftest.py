"""Test-session guards that keep the suite out of the real runtime store.

This file exists in Phase 0, before there is any store to protect, on purpose.
The reference build added its equivalent only after its suite had been writing
real records into the production store for about seven weeks with nothing
failing — the writes either succeeded into production or were swallowed by an
``except Exception`` downstream. Adding the guard alongside the first store is
too late; the guard has to predate it.

**The mechanism is a fingerprint, and it had to change.** Armed at task 1.4, the
guard asked "was this directory *created* while the suite ran?" — which is
answerable only while the directory does not exist. By 2026-09-22 all five existed
(`data/` from the first seeded store onward), so **four of the five checks could
never fire again**, and a test that forgot ``isolated_data_dir`` and wrote rows into
the real ``working.db`` passed silently. `workspace/` had already needed a different
check for the same reason — it is part of the tracked skeleton — and got a file-set
snapshot at Phase 4 B0.

A file-set snapshot alone would not have been enough either: **writing rows into an
existing database creates no new file**. So all five are now watched one way, by
``{path: (size, mtime_ns)}`` captured at import and compared at session end, which
catches creation and modification together. One mechanism instead of two with a gap
between them.

The violation type derives from ``BaseException`` deliberately: retrieval and
indexing paths wrap store access in ``except Exception``, and a guard those can
swallow is not a guard.

**Known limit, stated rather than implied:** the comparison cannot tell the suite's
writes from another process's. Running the suite while anything else is using the
real store will fail the session, and that is the right direction — a foreign write
is indistinguishable from a leak, and reporting it is safer than filtering it out.
"""

from pathlib import Path

import pytest

from program import config
from program.memory import vectors

# Captured at import — before any test can patch config.
REAL_DATA_DIR = str(config.data_dir())
REAL_CHROMA_DIR = vectors.chroma_path()
# Added with the backup CLI: it is a third real location the suite can write
# into, and isolating the data directory does not isolate this one. A backup
# test that forgot to repoint it wrote two real backup directories into the
# repo before this guard existed.
REAL_BACKUP_DIR = str(config.backup_dir())
# Added with file ingestion (task 2.6): a fourth real location, and the same
# trap as the backup directory — it resolves from its own config key, so
# repointing ANAM_DATA_DIR does not move it, even though its default sits inside
# `data/`. Uploaded files are the one thing here that cannot be regenerated.
REAL_ARTIFACT_DIR = str(config.artifact_dir())
# Added at Phase 4 B0 (2026-09-21), and it needs a DIFFERENT check from the four
# above. `workspace/` is part of the tracked repository skeleton — Phase 0 created
# `workspace/{generated,uploads,writing,research,journals}/` with a `.gitkeep` in
# each — so "was this directory created during the run?" can never fire for it. The
# question is whether the suite WROTE INTO it.
#
# The gap was real, not theoretical: `tests/test_image_generate.py` wrote 65 real
# PNGs into the repository's own `workspace/` before this existed, because
# `isolated_data_dir` repointed the data, backup and artifact directories and not
# this one. Same trap as the backup directory at task 1.14 and the artifact
# directory at 2.6, for the third time — it resolves from its own config key.
REAL_WORKSPACE_DIR = str(config.workspace_dir())

#: Every real runtime directory the suite must stay out of, keyed by the name of
#: the `config` accessor that resolves it. `tests/test_directories.py` asserts this
#: covers every accessor, by comparing resolved paths rather than by grepping this
#: file for a spelling.
REAL_DIRS: dict[str, str] = {
    "data_dir": REAL_DATA_DIR,
    "chroma": REAL_CHROMA_DIR,
    "backup_dir": REAL_BACKUP_DIR,
    "artifact_dir": REAL_ARTIFACT_DIR,
    "workspace_dir": REAL_WORKSPACE_DIR,
}

#: Written by the OS, not by the suite. Their presence or mtime says nothing about
#: isolation, and Finder touching one mid-run would otherwise fail the session.
_IGNORED_NAMES = {".DS_Store"}


def _fingerprint() -> dict[str, tuple[int, int]]:
    """Every file under every real runtime directory, as ``{path: (size, mtime_ns)}``.

    **Size and mtime, not just the path set, and that is the whole point.** The
    four original checks asked "was this directory *created* during the run?",
    which was answerable only while the directories did not exist. All five exist
    now — `data/` from the moment a store was seeded — so those checks could never
    fire again, and a test that forgot `isolated_data_dir` and wrote rows into the
    real `working.db` passed silently. A path-set snapshot (which is what
    `workspace/` used) would not have caught that either: **writing rows into an
    existing database creates no new file.** Comparing `(size, mtime_ns)` catches
    creation and modification with one mechanism, so the five directories are no
    longer watched two different ways with a gap between them.

    Nested roots are deduplicated by keying on the path: `data/chromadb` and
    `data/artifacts` sit inside `data/`, so a file under either is recorded once.
    """
    seen: dict[str, tuple[int, int]] = {}
    for root in REAL_DIRS.values():
        base = Path(root)
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.name in _IGNORED_NAMES:
                continue
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
            except OSError:  # vanished mid-walk, or unreadable — not evidence
                continue
            seen[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return seen


_FINGERPRINT_AT_IMPORT = _fingerprint()


class StoreIsolationViolation(BaseException):
    """Raised when the suite touched a real runtime store.

    Derives from ``BaseException`` deliberately: retrieval, indexing and tool
    dispatch all wrap work in ``except Exception``, and a guard those can swallow
    is not a guard. `registry.dispatch` and `turn._after_durable` both name it for
    that reason.
    """


@pytest.fixture(scope="session", autouse=True)
def _guard_runtime_store():
    """Fail the session if the suite created or modified anything real."""
    yield

    after = _fingerprint()
    created = sorted(set(after) - set(_FINGERPRINT_AT_IMPORT))
    modified = sorted(
        path for path in set(after) & set(_FINGERPRINT_AT_IMPORT)
        if after[path] != _FINGERPRINT_AT_IMPORT[path]
    )
    if not created and not modified:
        return

    def _listing(label: str, paths: list[str]) -> str:
        shown = "\n".join(f"      {p}" for p in paths[:5])
        more = f"\n      ... and {len(paths) - 5} more" if len(paths) > 5 else ""
        return f"  - {len(paths)} file(s) {label}:\n{shown}{more}"

    parts = []
    if created:
        parts.append(_listing("created in a real runtime directory", created))
    if modified:
        parts.append(_listing("MODIFIED in a real runtime directory", modified))

    raise StoreIsolationViolation(
        "the suite touched a real runtime store:\n"
        + "\n".join(parts)
        + "\n\n    A test that reads or writes a store must take `isolated_data_dir`, "
        "which repoints ANAM_DATA_DIR, ANAM_BACKUP_DIR, ANAM_ARTIFACT_DIR and "
        "ANAM_WORKSPACE_DIR at a temporary path. See AGENTS.md, 'Adding a runtime "
        "directory'.\n    Note this cannot tell the suite's writes from another "
        "process's: do not run the suite while anything else is using the real store."
    )


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Point the configured data directory at a temporary path for one test.

    Works because ``program.config`` resolves values through accessor functions at
    call time rather than binding module-level constants at import — see the
    module docstring in ``program/config.py`` for why that distinction matters.
    """
    monkeypatch.setenv("ANAM_DATA_DIR", str(tmp_path))
    # The backup directory resolves from its own config key, so repointing the
    # data directory alone leaves backups writing into the real one.
    monkeypatch.setenv("ANAM_BACKUP_DIR", str(tmp_path / "backups"))
    # Same reason as the backup directory: its own key, so isolating the data
    # directory leaves it pointing at the real one.
    monkeypatch.setenv("ANAM_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    # Third instance of the same trap, and the one that actually leaked: the
    # workspace resolves from its own key too, so isolating the data directory left
    # generated images writing into the repository.
    monkeypatch.setenv("ANAM_WORKSPACE_DIR", str(tmp_path / "workspace"))
    config.reload()
    # Stores are cached per resolved path, so clearing here means this test gets
    # its own vector store rather than one another test built for another path.
    vectors.reset_vector_store()
    yield tmp_path
    monkeypatch.delenv("ANAM_DATA_DIR", raising=False)
    monkeypatch.delenv("ANAM_BACKUP_DIR", raising=False)
    monkeypatch.delenv("ANAM_ARTIFACT_DIR", raising=False)
    monkeypatch.delenv("ANAM_WORKSPACE_DIR", raising=False)
    config.reload()
    vectors.reset_vector_store()
