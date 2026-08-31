"""Layered configuration: defaults.toml -> local.toml -> ANAM_* environment.

Resolution order, lowest precedence first:

1. ``config/defaults.toml``  — checked in, the documented baseline.
2. ``config/local.toml``     — gitignored, this machine's overrides.
3. ``ANAM_*`` env vars       — highest precedence, one variable per key.

**Values are read through accessor functions, resolved at call time.** There are
no module-level constants for callers to import. This is the single most
important property of this module and it is not stylistic.

The reference build exposed its configuration as module-level constants. A
caller writing ``from config import CHROMA_DIR`` bound a *separate* name in its
own module, so patching ``config.CHROMA_DIR`` in a test never reached it and
every default kept pointing at the real store. That is the direct cause of its
test suite writing into the production store for weeks without anything failing.
Call-time resolution closes that structurally rather than by convention.

**Scope: bootstrap defaults only.** Once the settings table exists (Phase 1),
it is authoritative at runtime for any key it holds a row for. This module then
serves two things: keys the settings table never owns — data paths, ports, the
Ollama host, anything needed before the database can be opened — and the seed
value for a settings-backed key that has no row yet. Nothing should read from
both this module and the settings store at request time.
"""

from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Baseline used when config/defaults.toml is missing or partial. Keeping it here
# means the application still starts from a checkout with no config directory,
# and gives defaults.toml something to be diffed against.
_FALLBACK: dict[str, Any] = {
    "paths": {
        "data_dir": "data",
        "workspace_dir": "workspace",
        "backup_dir": "backups",
    },
    "api": {
        "host": "127.0.0.1",
        "port": 8000,
    },
    "ollama": {
        "host": "http://localhost:11434",
        "timeout_seconds": 300,
    },
    "app": {
        "timezone": "America/New_York",
    },
}

# Env var -> (section, key, coercion). One variable per key, named explicitly
# rather than derived, so the full set of overrides is greppable from one place.
_ENV_MAP: dict[str, tuple[str, str, str]] = {
    "ANAM_DATA_DIR": ("paths", "data_dir", "str"),
    "ANAM_WORKSPACE_DIR": ("paths", "workspace_dir", "str"),
    "ANAM_BACKUP_DIR": ("paths", "backup_dir", "str"),
    "ANAM_API_HOST": ("api", "host", "str"),
    "ANAM_API_PORT": ("api", "port", "int"),
    "ANAM_OLLAMA_HOST": ("ollama", "host", "str"),
    "ANAM_OLLAMA_TIMEOUT_SECONDS": ("ollama", "timeout_seconds", "int"),
    "ANAM_TIMEZONE": ("app", "timezone", "str"),
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """Raised when a configuration value cannot be parsed."""


def config_dir() -> Path:
    """Directory holding defaults.toml and local.toml."""
    override = os.getenv("ANAM_CONFIG_DIR")
    return Path(override) if override else PROJECT_ROOT / "config"


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_bool(raw: str, source: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ConfigError(f"{source}: expected a boolean, got {raw!r}")


def _coerce(raw: str, kind: str, source: str) -> Any:
    if kind == "int":
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{source}: expected an integer, got {raw!r}") from exc
    if kind == "float":
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigError(f"{source}: expected a number, got {raw!r}") from exc
    if kind == "bool":
        return _parse_bool(raw, source)
    return raw


def _apply_env(config: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(config)
    for env_name, (section, key, kind) in _ENV_MAP.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        result.setdefault(section, {})[key] = _coerce(raw, kind, env_name)
    return result


def _load() -> dict[str, Any]:
    merged = deepcopy(_FALLBACK)
    merged = _deep_merge(merged, _read_toml(config_dir() / "defaults.toml"))
    merged = _deep_merge(merged, _read_toml(config_dir() / "local.toml"))
    return _apply_env(merged)


_cache: dict[str, Any] | None = None


def _config() -> dict[str, Any]:
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def reload() -> None:
    """Drop the cached configuration so the next read re-resolves all layers.

    Used by tests after changing the environment. Not a runtime hot-reload
    mechanism — that is the settings store's job from Phase 1 onward.
    """
    global _cache
    _cache = None


def get(section: str, key: str, default: Any = None) -> Any:
    """Return one configuration value, resolving all layers at call time."""
    return _config().get(section, {}).get(key, default)


def section(name: str) -> dict[str, Any]:
    """Return a copy of one configuration section."""
    value = _config().get(name, {})
    return deepcopy(value) if isinstance(value, dict) else {}


def as_dict() -> dict[str, Any]:
    """Return the whole resolved configuration. For diagnostics."""
    return deepcopy(_config())


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def data_dir() -> Path:
    """Runtime data directory. Bootstrap-only — never settings-backed."""
    return _resolve_path(get("paths", "data_dir", "data"))


def workspace_dir() -> Path:
    """Workspace root for artifacts. Bootstrap-only."""
    return _resolve_path(get("paths", "workspace_dir", "workspace"))


def backup_dir() -> Path:
    """Backup destination root. Bootstrap-only."""
    return _resolve_path(get("paths", "backup_dir", "backups"))


def api_host() -> str:
    return get("api", "host", "127.0.0.1")


def api_port() -> int:
    return int(get("api", "port", 8000))


def timezone() -> str:
    return get("app", "timezone", "America/New_York")
