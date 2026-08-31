"""Test-session guards that keep the suite out of the real runtime store.

This file exists in Phase 0, before there is any store to protect, on purpose.
The reference build added its equivalent only after its suite had been writing
real records into the production store for about seven weeks with nothing
failing — the writes either succeeded into production or were swallowed by an
``except Exception`` downstream. Adding the guard alongside the first store is
too late; the guard has to predate it.

Right now this captures the real paths and records violations. Phase 1 task 1.4
arms it against the vector store and working database once those exist.

The violation type derives from ``BaseException`` deliberately: retrieval and
indexing paths wrap store access in ``except Exception``, and a guard those can
swallow is not a guard. Violations are also recorded and re-reported at session
end, so one that does get swallowed still fails the run visibly.
"""

import pytest

from anam import config

# Captured at import — before any test can patch config.
REAL_DATA_DIR = str(config.data_dir())

_violations: list[str] = []


class StoreIsolationViolation(BaseException):
    """Raised when a test resolves a real runtime store path."""


def record_violation(message: str) -> StoreIsolationViolation:
    """Record a violation and return the exception to raise."""
    _violations.append(message)
    return StoreIsolationViolation(message)


@pytest.fixture(scope="session", autouse=True)
def _guard_runtime_store():
    """Fail the session if any test resolved a real runtime store path."""
    yield
    if _violations:
        raise StoreIsolationViolation(
            f"{len(_violations)} test(s) resolved the real runtime store:\n"
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
    config.reload()
    yield tmp_path
    monkeypatch.delenv("ANAM_DATA_DIR", raising=False)
    config.reload()
