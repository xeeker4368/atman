"""The test fixture releases what each test's stores hold open.

Every test gets its own data directory, so every test that builds a Chroma store builds a
new one. Without the release in ``tests/conftest.py::release_test_stores`` each stays open
for the whole session (~10 descriptors each), and the suite fails at ``ulimit -n 256``.
"""

from __future__ import annotations

import os

from program import config
from program.memory import vectors
from program.ops import store_lock
from tests import conftest


def open_descriptors() -> int:
    return len(os.listdir("/dev/fd"))


def test_forty_isolated_stores_do_not_accumulate_descriptors(tmp_path, monkeypatch):
    conftest.release_test_stores()
    before = open_descriptors()
    for i in range(40):
        monkeypatch.setenv("ANAM_DATA_DIR", str(tmp_path / f"store-{i}"))
        config.reload()
        store = vectors.get_vector_store()
        store.count()  # builds the client and the collection on disk
        store_lock.hold_or_refuse("test fd hygiene")
        conftest.release_test_stores()
    growth = open_descriptors() - before
    # Unreleased, this is ~10 per store (about 400 here). A handful is interpreter noise.
    assert growth < 20, f"{growth} descriptors still open after 40 stores were released"
