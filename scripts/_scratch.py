"""Point every runtime directory at scratch before anything under ``program`` is imported.

    from scripts._scratch import scratch_env
    scratch_env("p10")          # FIRST, before any `from program ...`
    from program import config  # now reads ~/anam-measurements/p10/...

Why it must come first: ``program.config`` resolves its directories from ``ANAM_*`` at call time,
and the settings-backed accessors (``chat_model()``, ``model_options()``, ...) read the settings
table in ``working.db`` under ``ANAM_DATA_DIR``. A measurement script that imports ``program``
with the default directories therefore reads the real store (2026-10-03: a token-count script
read the real settings table; it was empty and the fingerprint was unchanged).

**Which variables.** Not a hand-kept list: the ``ANAM_*`` names whose config key is in the
``[paths]`` section are read from ``program/config.py``'s env table **by parsing the source**
(``ast``), so nothing under ``program`` is imported to find them. A new ``[paths]`` key with an
``ANAM_*`` name is picked up with no change here. A directory resolved some other way would not
be, so after setting the variables the helper imports ``program.config`` and requires every
``*_dir()`` accessor that returns a path (except ``config_dir``, the read-only repository config)
to resolve under the scratch root; one that does not raises. That check is what fails if a new
directory is added outside ``[paths]``.

Operator scripts that are meant to use the real store (``scripts/note.py``, ``backup.py``, ...)
do not use this; ``tests/test_scratch_helper.py`` lists them.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path.home() / "anam-measurements"


def path_variables() -> dict[str, str]:
    """``{ANAM_NAME: key}`` for every ``[paths]`` entry in ``program/config.py``'s env table,
    read from the source without importing it."""
    tree = ast.parse((PROJECT_ROOT / "program" / "config.py").read_text())
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                    and k.value.startswith("ANAM_")
                    and isinstance(v, ast.Tuple) and len(v.elts) >= 2
                    and isinstance(v.elts[0], ast.Constant) and v.elts[0].value == "paths"):
                found[k.value] = v.elts[1].value
    if not found:
        raise RuntimeError(
            "no [paths] ANAM_* variables found in program/config.py; the env table moved")
    return found


def scratch_env(name: str, *, root: Path | None = None) -> Path:
    """Set every ``[paths]`` ``ANAM_*`` variable under ``<root>/<name>/`` and verify it took.

    Refuses if ``program`` is already imported (too late to be sure nothing read a default).
    Returns the scratch base directory. Creates nothing; the store is created by whoever opens it.
    """
    if any(m == "program" or m.startswith("program.") for m in sys.modules):
        raise RuntimeError("scratch_env must run before anything under program is imported")
    base = (root or DEFAULT_ROOT) / name
    for var, key in path_variables().items():
        os.environ[var] = str(base / key.removesuffix("_dir"))

    from program import config  # only now

    config.reload()
    for attr in dir(config):
        fn = getattr(config, attr)
        if not attr.endswith("_dir") or attr == "config_dir" or not callable(fn):
            continue
        value = fn()
        if isinstance(value, Path) and not str(value.resolve()).startswith(str(base.resolve())):
            raise RuntimeError(
                f"config.{attr}() resolves to {value}, outside the scratch root {base}")
    return base
