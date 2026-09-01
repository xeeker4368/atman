"""Test-session guards that keep the suite out of the real runtime store.

This file exists in Phase 0, before there is any store to protect, on purpose.
The reference build added its equivalent only after its suite had been writing
real records into the production store for about seven weeks with nothing
failing — the writes either succeeded into production or were swallowed by an
``except Exception`` downstream. Adding the guard alongside the first store is
too late; the guard has to predate it.

Armed as of task 1.4: the real data directory and the real ChromaDB path are
captured at import, and the session fails if either was created while the suite
ran. Capturing whether they existed *beforehand* rather than merely checking at
the end means a pre-existing development store is not mistaken for a violation.

The violation type derives from ``BaseException`` deliberately: retrieval and
indexing paths wrap store access in ``except Exception``, and a guard those can
swallow is not a guard. Violations are also recorded and re-reported at session
end, so one that does get swallowed still fails the run visibly.
"""

import os

import pytest

from anam import config
from anam.memory import vectors

# Captured at import — before any test can patch config.
REAL_DATA_DIR = str(config.data_dir())
REAL_CHROMA_DIR = vectors.chroma_path()
# Added with the backup CLI: it is a third real location the suite can write
# into, and isolating the data directory does not isolate this one. A backup
# test that forgot to repoint it wrote two real backup directories into the
# repo before this guard existed.
REAL_BACKUP_DIR = str(config.backup_dir())

# Whether they were already there. A store that predates the run is the
# operator's, not evidence of a leak.
_DATA_DIR_PREEXISTED = os.path.exists(REAL_DATA_DIR)
_CHROMA_DIR_PREEXISTED = os.path.exists(REAL_CHROMA_DIR)
_BACKUP_DIR_PREEXISTED = os.path.exists(REAL_BACKUP_DIR)

_violations: list[str] = []


class StoreIsolationViolation(BaseException):
    """Raised when a test resolves a real runtime store path."""


def record_violation(message: str) -> StoreIsolationViolation:
    """Record a violation and return the exception to raise."""
    _violations.append(message)
    return StoreIsolationViolation(message)


@pytest.fixture(scope="session", autouse=True)
def _guard_runtime_store():
    """Fail the session if the suite touched a real runtime store."""
    yield

    if not _DATA_DIR_PREEXISTED and os.path.exists(REAL_DATA_DIR):
        _violations.append(f"the real data directory was created: {REAL_DATA_DIR}")
    if not _CHROMA_DIR_PREEXISTED and os.path.exists(REAL_CHROMA_DIR):
        _violations.append(f"the real vector store was created: {REAL_CHROMA_DIR}")
    if not _BACKUP_DIR_PREEXISTED and os.path.exists(REAL_BACKUP_DIR):
        _violations.append(f"the real backup directory was created: {REAL_BACKUP_DIR}")

    if _violations:
        raise StoreIsolationViolation(
            f"{len(_violations)} isolation violation(s):\n"
            + "\n".join(f"  - {v}" for v in _violations)
        )


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Point the configured data directory at a temporary path for one test.

    Works because ``anam.config`` resolves values through accessor functions at
    call time rather than binding module-level constants at import — see the
    module docstring in ``anam/config.py`` for why that distinction matters.
    """
    monkeypatch.setenv("ANAM_DATA_DIR", str(tmp_path))
    # The backup directory resolves from its own config key, so repointing the
    # data directory alone leaves backups writing into the real one.
    monkeypatch.setenv("ANAM_BACKUP_DIR", str(tmp_path / "backups"))
    config.reload()
    # Stores are cached per resolved path, so clearing here means this test gets
    # its own vector store rather than one another test built for another path.
    vectors.reset_vector_store()
    yield tmp_path
    monkeypatch.delenv("ANAM_DATA_DIR", raising=False)
    monkeypatch.delenv("ANAM_BACKUP_DIR", raising=False)
    config.reload()
    vectors.reset_vector_store()
