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
    "models": {
        "chat": "gemma4:26b",
        "embedding": "nomic-embed-text",
    },
    "model_options": {
        "num_ctx": 32768,
        "temperature": 0.35,
        "think": False,
    },
    "conversations": {
        "idle_close_minutes": 15,
        "in_flight_grace_minutes": 30,
    },
    "chunking": {
        "target_chars": 2500,
        "max_turns": 8,
    },
    "history": {
        "chars_per_token": 4.0,
        "message_overhead_tokens": 4,
        "output_reserve_tokens": 2048,
        "safety_margin_tokens": 512,
    },
    "embedding": {
        "expected_dimension": 768,
        "max_input_chars": 5000,
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
    "ANAM_CHAT_MODEL": ("models", "chat", "str"),
    "ANAM_EMBED_MODEL": ("models", "embedding", "str"),
    "ANAM_MODEL_NUM_CTX": ("model_options", "num_ctx", "int"),
    "ANAM_MODEL_TEMPERATURE": ("model_options", "temperature", "float"),
    "ANAM_MODEL_THINK": ("model_options", "think", "bool"),
    "ANAM_IDLE_CLOSE_MINUTES": ("conversations", "idle_close_minutes", "int"),
    "ANAM_IN_FLIGHT_GRACE_MINUTES": ("conversations", "in_flight_grace_minutes", "int"),
    "ANAM_CHUNK_TARGET_CHARS": ("chunking", "target_chars", "int"),
    "ANAM_CHUNK_MAX_TURNS": ("chunking", "max_turns", "int"),
    "ANAM_HISTORY_CHARS_PER_TOKEN": ("history", "chars_per_token", "float"),
    "ANAM_HISTORY_OUTPUT_RESERVE_TOKENS": (
        "history", "output_reserve_tokens", "int",
    ),
    "ANAM_HISTORY_SAFETY_MARGIN_TOKENS": (
        "history", "safety_margin_tokens", "int",
    ),
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


# --- Models -----------------------------------------------------------------
#
# Seed values for settings-backed keys. From Phase 1 task 1.11 the settings
# table is authoritative at runtime for any key it holds a row for; these are
# what the system uses until it does. See the module docstring.


def _settings_first(section_name: str, key: str, default: Any = None) -> Any:
    """Resolve one key settings-table-first, falling back to the layered config.

    **This is what makes this module's "bootstrap defaults only" scope real
    rather than aspirational.** Task 1.11 (decision #8) makes the settings table
    authoritative at runtime for any key it holds a row for; every accessor
    below goes through here, so an existing caller of ``chat_model()`` picks up
    a live setting change with no restart and no code change.

    Deliberately *not* wired into ``get()`` or ``section()``. Those stay pure
    layered-config reads: the settings store calls ``get()`` for its own
    fallback, so delegating there would recurse. It also keeps the boundary
    legible — a key is settings-backed because it appears in the store's
    registry, not because of which reader happened to be used.

    The import is function-local: ``anam.settings.store`` reaches
    ``anam.memory.db``, which imports this module.
    """
    from anam.settings import store

    return store.resolve(section_name, key, get(section_name, key, default))


def ollama_host() -> str:
    """Bootstrap-only, per this module's docstring — not settings-backed.

    Flagged rather than settled: ``NOW.md`` #9's Check/Verify button implies
    external-connection settings belong in the panel. See the settings
    registry's note and the task 1.11 changelog.
    """
    return get("ollama", "host", "http://localhost:11434")


def ollama_timeout_seconds() -> int:
    return int(_settings_first("ollama", "timeout_seconds", 300))


def chat_model() -> str:
    return _settings_first("models", "chat")


def embed_model() -> str:
    return _settings_first("models", "embedding")


def model_options() -> dict[str, Any]:
    """Options sent with a chat request.

    Each key resolves settings-table-first, so a temperature change in the admin
    panel reaches the next request without a restart.

    ``think`` is returned alongside the rest but belongs at the top level of the
    Ollama payload rather than inside ``options`` — the client separates them.
    """
    options = section("model_options")
    return {
        key: _settings_first("model_options", key, value)
        for key, value in options.items()
    }


#: Hard floor on the in-flight grace, in minutes. Derived from a measured
#: worst-case turn (see config/defaults.toml). Configuring below it raises
#: rather than clamping: a silently clamped value hides that the operator asked
#: for something unsafe, and this is the setting where unsafe means closing a
#: conversation while the model is still answering.
IN_FLIGHT_GRACE_FLOOR_MINUTES = 20


def idle_close_minutes() -> int:
    """Idle window for a conversation whose last turn completed."""
    return int(get("conversations", "idle_close_minutes", 15))


def in_flight_grace_minutes() -> int:
    """Idle window for a conversation whose last message is unanswered.

    Raises rather than clamping when configured below the floor.
    """
    value = int(get("conversations", "in_flight_grace_minutes", 30))
    if value < IN_FLIGHT_GRACE_FLOOR_MINUTES:
        raise ConfigError(
            f"conversations.in_flight_grace_minutes is {value}, below the "
            f"{IN_FLIGHT_GRACE_FLOOR_MINUTES}-minute floor. That floor is a "
            f"correctness constraint, not a preference: a worst-case turn on "
            f"this hardware takes minutes, and a shorter window would close a "
            f"conversation while the model was still answering it."
        )
    return value


# --- History windowing ------------------------------------------------------
#
# The divisor and the embedding-input budget are two answers to the same
# chars-per-token question, and they are deliberately different numbers. See
# config/defaults.toml under [history] for both margins and why they diverge.


def history_chars_per_token() -> float:
    """Characters per token, for the history-window estimate.

    Must be **at or below** the true ratio of the text: the estimate has to
    over-count tokens so the window under-fills rather than overflows.
    """
    value = float(get("history", "chars_per_token", 4.0))
    if value <= 0:
        raise ConfigError(
            f"history.chars_per_token is {value}; it must be greater than zero. "
            f"It is a divisor over character counts."
        )
    return value


def history_message_overhead_tokens() -> int:
    """Per-message cost of the chat template's role markers and delimiters."""
    value = int(get("history", "message_overhead_tokens", 4))
    if value < 0:
        raise ConfigError(
            f"history.message_overhead_tokens is {value}; it must not be "
            f"negative. A negative overhead would make the estimate under-count, "
            f"which is the overflow direction."
        )
    return value


def history_output_reserve_tokens() -> int:
    """Context space held back for the model's own reply."""
    value = int(get("history", "output_reserve_tokens", 2048))
    if value < 0:
        raise ConfigError(
            f"history.output_reserve_tokens is {value}; it must not be negative."
        )
    return value


def history_safety_margin_tokens() -> int:
    """Unallocated slack, absorbing estimator error on denser-than-prose text."""
    value = int(get("history", "safety_margin_tokens", 512))
    if value < 0:
        raise ConfigError(
            f"history.safety_margin_tokens is {value}; it must not be negative. "
            f"The margin exists to absorb estimator error toward overflow."
        )
    return value


def chunk_target_chars() -> int:
    return int(get("chunking", "target_chars", 2500))


def chunk_max_turns() -> int:
    return int(get("chunking", "max_turns", 8))


def expected_embedding_dimension() -> int:
    return int(get("embedding", "expected_dimension", 768))


def embedding_max_input_chars() -> int:
    return int(get("embedding", "max_input_chars", 5000))
