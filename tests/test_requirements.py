"""Piece 4a: the dependencies are pinned, and the pins are what is installed.

``requirements.txt`` used to list bare names, so a fresh install picked up whatever was newest
(chromadb's behaviour changed under B23, and the B23 recovery uses an internal API of one version).
"""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINE = re.compile(r"^([A-Za-z0-9_.\[\]-]+)\s*(==\s*[^\s#]+)?\s*(#.*)?$")


def _direct() -> list[tuple[str, str | None]]:
    found = []
    for raw in (ROOT / "requirements.txt").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE.match(line)
        assert match, f"cannot read the requirement {raw!r}"
        found.append((match.group(1), match.group(2).replace("==", "").strip()
                      if match.group(2) else None))
    return found


def test_every_direct_dependency_is_pinned_to_an_exact_version():
    unpinned = [name for name, version in _direct() if version is None]
    assert not unpinned, f"unpinned in requirements.txt: {unpinned}"


@pytest.mark.parametrize("name,pinned", [(n, v) for n, v in _direct() if v])
def test_each_pin_is_the_installed_version(name, pinned):
    assert metadata.version(name) == pinned, (
        f"{name} is pinned to {pinned} but {metadata.version(name)} is installed: refresh the "
        f"pin and requirements.lock together after re-running the suite")


def test_the_lock_names_every_direct_dependency_at_its_pin():
    lock = {}
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        if "==" in line:
            name, version = line.split("==", 1)
            lock[name.lower().replace("_", "-")] = version.strip()
    for name, pinned in _direct():
        assert lock.get(name.lower().replace("_", "-")) == pinned, (
            f"{name} is not locked at {pinned}")
