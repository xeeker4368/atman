"""Runtime settings: the working database is authoritative, config.py is the seed.

Decision #8. A settings table in the working DB, with an in-memory cache
invalidated on write. **No setting requires a restart to take effect.**

The rule this module enforces
-----------------------------
``program/config.py`` says of itself: *"Once the settings table exists (Phase 1),
it is authoritative at runtime for any key it holds a row for... Nothing should
read from both this module and the settings store at request time."*

Until this task that was a comment describing an intention. It is now the actual
read path: the settings-backed accessors in ``config.py`` delegate here, so a
caller writing ``config.chat_model()`` gets the database value if a row exists
and the TOML/env value only if one does not. There is deliberately **no second
accessor** for callers to choose between — choosing wrong is how a system ends
up reading from both.

Resolution order for a settings-backed key:

1. ``settings`` row in ``working.db``  — authoritative once written.
2. ``config.py``'s layered value       — the seed, used until a row exists.

Bootstrap keys — data paths, the API port, anything needed before the database
can be opened — are **not** in the registry and never resolve through here.
That boundary is the registry itself, ``SETTINGS``, and is checked by a test.

Caching
-------
A read fills the cache with the **whole table in one query**, not one row per
read, so making every settings-backed accessor DB-first costs one query per
invalidation rather than one per call. Any write drops the cache for that store.

The cache is keyed by the resolved path of ``working.db``, matching the pattern
``program/memory/vectors.py`` uses. Config resolves paths at call time, so a test
that repoints ``ANAM_DATA_DIR`` must not then read another store's cached
values — keying by path is what stops that, rather than remembering to clear.

Falling back when there is no database yet
------------------------------------------
A read before ``init_databases()`` — a fresh checkout, or a bootstrap path that
runs early — finds no file or no table. That resolves to the config seed rather
than raising: the application has to be able to start from a checkout with no
data directory. It does not create anything on that path, which is what keeps
it from tripping the test suite's store-isolation guard.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any

from program import config
from program.memory.db import retry_on_locked
from program.settings.permissions import Actor, require

logger = logging.getLogger(__name__)

VALUE_TYPES = ("str", "int", "float", "bool", "json")


@dataclass(frozen=True)
class SettingSpec:
    """One settings-backed key: where its seed lives and how it is typed.

    ``section``/``key`` name the ``config.py`` value that seeds it. The setting's
    own name is the ``settings.key`` column, kept flat and dotted so the admin
    panel and the database agree on one spelling.
    """

    name: str
    section: str
    key: str
    value_type: str
    description: str


#: The settings-backed keys. Everything not listed here is bootstrap-only and
#: resolves from config.py alone.
#:
#: Scope note: this registry holds exactly the keys ``config.py`` already marks
#: as settings-backed seeds under its "Models" heading. Chunking, history and
#: idle-close values are deliberately **not** here — making them live-editable
#: changes the behaviour of Tier 3 pipelines, and no task has asked for that.
#: Adding a key is a one-line change plus a test.
#:
#: ``ollama.host`` is deliberately ABSENT and this is flagged for review, not
#: settled. ``config.py``'s module docstring names "the Ollama host" among the
#: keys the settings table *never* owns, but ``docs/DECISIONS.md`` decision #9 gives every
#: setting representing a connection to an external system a Check/Verify
#: button — which presumes such settings live in the panel, and the Ollama host
#: is the obvious one. Left bootstrap-only because that is the reversible
#: choice: adding it later is one line, whereas a bad host committed to the
#: database is awkward to unpick. See the task 1.11 changelog.
SETTINGS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "ollama.timeout_seconds", "ollama", "timeout_seconds", "int",
        "Per-request timeout for model calls, in seconds.",
    ),
    SettingSpec(
        "models.chat", "models", "chat", "str",
        "Chat model name as Ollama knows it.",
    ),
    SettingSpec(
        "models.embedding", "models", "embedding", "str",
        "Embedding model name. Changing it invalidates the vector store.",
    ),
    SettingSpec(
        "model_options.num_ctx", "model_options", "num_ctx", "int",
        "Context window pinned on every chat request.",
    ),
    SettingSpec(
        "model_options.temperature", "model_options", "temperature", "float",
        "Sampling temperature for chat.",
    ),
    SettingSpec(
        "model_options.think", "model_options", "think", "bool",
        "Whether to request the model's thinking block.",
    ),
    SettingSpec(
        "notes.approval_required", "notes", "approval_required", "bool",
        "Whether a note proposal waits for a person. Written only by `scripts.note approval`.",
    ),
)

#: Settings whose ONLY writer is a purpose-built operator command that writes the setting and its
#: log row in one transaction (:func:`write_in_transaction`). ``set`` and ``clear`` refuse them,
#: so no other path (a panel, a script, a future caller) can change one without the log knowing.
LOGGED_WRITER_ONLY = frozenset({"notes.approval_required"})

_BY_NAME = {spec.name: spec for spec in SETTINGS}
_BY_CONFIG = {(spec.section, spec.key): spec for spec in SETTINGS}

_lock = threading.Lock()
#: resolved working.db path -> {setting name: raw stored value}
_cache: dict[str, dict[str, Any]] = {}


class UnknownSettingError(KeyError):
    """Raised for a key that is not in the registry."""


class SettingTypeError(ValueError):
    """Raised when a value cannot be stored or read as its declared type."""


class SettingsUnavailableError(RuntimeError):
    """The settings table could not be read, so which values are in force is unknown.

    **Deliberately distinct from "there is no row".** A missing row means the
    config seed genuinely applies and is the normal state of a fresh store; this
    means the question was not answered. Collapsing the two is what let a single
    transient ``database is locked`` present every seed as the operator's own
    setting — see :func:`_load_table`.
    """


def spec_for(name: str) -> SettingSpec:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise UnknownSettingError(
            f"{name!r} is not a settings-backed key. Known keys: "
            f"{', '.join(sorted(_BY_NAME))}. Bootstrap values (data paths, the "
            f"API port) are config-only by design and are not settable here."
        ) from None


def is_settings_backed(section: str, key: str) -> bool:
    return (section, key) in _BY_CONFIG


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _encode(value: Any, spec: SettingSpec) -> str:
    """Value -> the TEXT that goes in the row, validated against its type."""
    if spec.value_type == "json":
        return json.dumps(value)
    if spec.value_type == "bool":
        if not isinstance(value, bool):
            raise SettingTypeError(
                f"{spec.name} is declared bool; got {type(value).__name__} "
                f"({value!r})."
            )
        return "true" if value else "false"
    if spec.value_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingTypeError(
                f"{spec.name} is declared int; got {type(value).__name__} "
                f"({value!r})."
            )
        return str(value)
    if spec.value_type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingTypeError(
                f"{spec.name} is declared float; got {type(value).__name__} "
                f"({value!r})."
            )
        return repr(float(value))
    return str(value)


def decode(name: str, raw: Any) -> Any:
    """A stored value for a registered setting, typed. For a reader that holds a raw row and its
    own connection (the approval decision reads inside its transaction)."""
    return _decode(str(raw), spec_for(name))


def _decode(raw: str, spec: SettingSpec) -> Any:
    """The stored TEXT -> a typed value.

    A row that cannot be decoded raises rather than silently falling back to the
    config seed. A malformed row is a real problem, and quietly serving the old
    default would hide that the panel is showing a value the system is not using.
    """
    try:
        if spec.value_type == "json":
            return json.loads(raw)
        if spec.value_type == "bool":
            lowered = raw.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off"):
                return False
            raise ValueError(f"not a boolean: {raw!r}")
        if spec.value_type == "int":
            return int(raw)
        if spec.value_type == "float":
            return float(raw)
        return raw
    except (ValueError, json.JSONDecodeError) as exc:
        raise SettingTypeError(
            f"settings row {spec.name!r} holds {raw!r}, which is not a valid "
            f"{spec.value_type}. Fix or delete the row; it is not being "
            f"silently ignored because the admin panel would then show a value "
            f"the system is not using."
        ) from exc


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _store_key() -> str:
    from program.memory import db

    return str(db.working_path())


def reset_cache() -> None:
    """Drop every cached table. For tests and for an out-of-band DB change."""
    with _lock:
        _cache.clear()


def invalidate() -> None:
    """Drop the cache for the current store. Called on every write."""
    key = _store_key()
    with _lock:
        _cache.pop(key, None)


def _store_exists() -> bool:
    """Whether working.db is actually there.

    Checked before connecting, because ``sqlite3.connect()`` **creates** a
    missing database file. A read path that creates an empty store as a side
    effect is a real defect, not a harmless one: it is how a stray settings
    read leaves a database behind in a directory that had none, and the test
    suite's isolation guard exists because that class of accident already cost
    the reference build seven weeks of writes into production.
    """
    from program.memory import db

    return db.working_path().exists()


#: ``OperationalError`` texts that mean *the schema is not there yet*, as opposed
#: to *the read failed*. Matched on the message because SQLite offers no error
#: code for it through :mod:`sqlite3`; the same approach ``db._is_lock_error``
#: takes, for the same reason.
_NO_TABLE = ("no such table",)


@retry_on_locked
def _read_rows() -> list[sqlite3.Row]:
    """The raw read, retried on contention. Lock errors must escape to be retried.

    Separate from :func:`_load_table` for a reason found by a test: with the
    classification *inside* the retried function, the ``except`` caught the lock
    error before ``@retry_on_locked`` could ever see one, so the decorator was
    dead code. The retry has to sit **under** the classification, not around it.
    """
    from program.memory import db

    with db.connection() as conn:
        return conn.execute("SELECT key, value FROM settings").fetchall()


def _load_table() -> dict[str, Any]:
    """Read every settings row in one query.

    **Two outcomes, and keeping them apart is the whole point of this function.**
    A missing database or a database with no ``settings`` table returns ``{}`` —
    the config seed applies, which is the correct and normal state of a fresh
    store. **Anything else raises** :class:`SettingsUnavailableError`.

    It used to catch every ``sqlite3.OperationalError`` and return ``{}``, with a
    comment naming only the no-table case. But that class is also what SQLite
    raises for ``database is locked``, ``unable to open database file`` and
    ``disk I/O error`` — and :func:`_table` cached the result, so **one transient
    lock silently reverted every settings-backed value to its config seed for the
    lifetime of the process**, at DEBUG level, with ``describe()`` then reporting
    ``source="config"`` as though no row existed. An operator-set chat model or
    temperature would quietly stop being the one in use.

    :func:`_read_rows` carries ``@retry_on_locked`` because this is an ordinary
    contended read and the two writers in this module already carry it. The retry
    is what makes raising affordable: by the time an error reaches here, the lock
    has been waited out and retried, so it is a real failure rather than a blip.
    """
    if not _store_exists():
        return {}
    try:
        rows = _read_rows()
    except sqlite3.OperationalError as exc:
        if any(text in str(exc).lower() for text in _NO_TABLE):
            # The schema is not there yet. The config seed genuinely applies.
            logger.debug("no settings table yet, using config seeds (%s)", exc)
            return {}
        raise SettingsUnavailableError(
            f"the settings table could not be read ({exc}); which values are in "
            f"force is unknown, so the config seed is not being presented as one"
        ) from exc
    return {row["key"]: row["value"] for row in rows}


def _table() -> dict[str, Any]:
    """The cached settings rows, loading them on first use.

    **Only a successful load is cached.** A failed read leaves the cache empty so
    the next call retries, because the alternative — caching the failure — is how
    a momentary lock became permanent. ``invalidate()`` is called only by
    ``set()``/``clear()``, so nothing else would ever have cleared it.
    """
    key = _store_key()
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    loaded = _load_table()
    with _lock:
        _cache[key] = loaded
    return loaded


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def resolve(section: str, key: str, fallback: Any) -> Any:
    """Settings-table value for a config key, or ``fallback`` if no row exists.

    This is the seam ``config.py`` calls. A key that is not settings-backed
    returns the fallback untouched, so wiring it in is harmless for bootstrap
    keys.

    **Deliberately ungated** (task 1.12, R4). This runs on every settings-backed
    config read — ``config.chat_model()``, ``config.model_options()`` — and is
    the system reading its own configuration in order to operate, not a person
    reading settings. Requiring an ``Actor`` here would mean the Ollama client
    needed one to discover which model to call, which is not a permission
    question. ``get()`` / ``describe()`` are the person-facing reads that
    ``settings.read`` governs.
    """
    spec = _BY_CONFIG.get((section, key))
    if spec is None:
        return fallback
    try:
        raw = _table().get(spec.name)
    except SettingsUnavailableError as exc:
        # DEGRADE, on `prompt.py`'s recorded criterion: nothing here can be
        # corrupted, a person is waiting on the turn this read sits inside, and
        # retrying is free because the failure is not cached. WARNING rather than
        # DEBUG — the value in use is not the one an operator may have set, and
        # that is worth a line somebody will actually see.
        logger.warning(
            "settings unreadable; using the config seed for %s.%s on this call "
            "only (%s)", section, key, exc,
        )
        return fallback
    if raw is None:
        return fallback
    return _decode(str(raw), spec)


def get(name: str, actor: Actor) -> Any:
    """Effective value of one setting: the row if there is one, else the seed.

    ``actor`` is required and has no default (task 1.12, R4). A default of
    ``Actor.operator()`` would let a caller who never thought about
    authorization land silently on the always-allowed path, which is exactly
    what the explicit sentinel exists to prevent.

    **Raises** :class:`SettingsUnavailableError` rather than degrading, unlike
    :func:`resolve`. This is the person-facing read: answering "what is this set
    to" with the seed, when the row could not be read, is a wrong answer to the
    exact question asked, and nothing is waiting on it.
    """
    require(actor, "settings.read")
    spec = spec_for(name)
    raw = _table().get(spec.name)
    if raw is None:
        return config.get(spec.section, spec.key)
    return _decode(str(raw), spec)


def has_row(name: str, actor: Actor) -> bool:
    """Whether the settings table holds a row for this key."""
    require(actor, "settings.read")
    return spec_for(name).name in _table()


@dataclass(frozen=True)
class EffectiveSetting:
    """One setting's current value and where it came from.

    ``source`` is ``"settings"``, ``"config"`` or ``"unavailable"``. Diagnostics,
    and the thing that makes "is the read path really settings-first" answerable
    rather than assumed.

    ``"unavailable"`` means the settings table could not be read, so whether a row
    exists is unknown and ``value`` is the config seed shown **as a fallback, not
    as provenance**. It exists because this previously reported ``"config"`` in
    that case — positively asserting that no row existed, which the code had not
    established.
    """

    name: str
    value: Any
    source: str
    value_type: str
    description: str
    updated_at: str | None = None
    updated_by: str | None = None


def describe(name: str, actor: Actor) -> EffectiveSetting:
    require(actor, "settings.read")
    spec = spec_for(name)
    try:
        row = _row(spec.name)
    except SettingsUnavailableError:
        # Report the seed AND that provenance is unknown, rather than claiming
        # "config". describe_all() then still lists every setting instead of
        # failing whole because one read lost a race.
        return EffectiveSetting(
            name=spec.name,
            value=config.get(spec.section, spec.key),
            source="unavailable",
            value_type=spec.value_type,
            description=spec.description,
        )
    if row is None:
        return EffectiveSetting(
            name=spec.name,
            value=config.get(spec.section, spec.key),
            source="config",
            value_type=spec.value_type,
            description=spec.description,
        )
    return EffectiveSetting(
        name=spec.name,
        value=_decode(str(row["value"]), spec),
        source="settings",
        value_type=spec.value_type,
        description=spec.description,
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
    )


def _row(name: str) -> sqlite3.Row | None:
    from program.memory import db

    if not _store_exists():
        return None
    try:
        with db.connection() as conn:
            return conn.execute(
                "SELECT * FROM settings WHERE key = ?", (name,)
            ).fetchone()
    except sqlite3.OperationalError as exc:
        if any(text in str(exc).lower() for text in _NO_TABLE):
            return None
        raise SettingsUnavailableError(
            f"the settings table could not be read ({exc})"
        ) from exc


def describe_all(actor: Actor) -> list[EffectiveSetting]:
    """Every registered setting, effective value and source. For diagnostics."""
    require(actor, "settings.read")
    return [describe(spec.name, actor) for spec in SETTINGS]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


class LoggedWriterOnlyError(PermissionError):
    """A setting that is written only together with its approval-log row was written alone."""


def _refuse_if_logged_only(name: str) -> None:
    if name in LOGGED_WRITER_ONLY:
        raise LoggedWriterOnlyError(
            f"{name} is written only by `python -m scripts.note approval on|off`, which writes it "
            f"and an approval_log row in one transaction."
        )


def write_in_transaction(conn: sqlite3.Connection, name: str, value: Any, actor: Actor) -> None:
    """Write one setting **inside the caller's transaction**, so a caller can commit it together
    with another row (the approval log). Validates and authorizes like :func:`set`, and does NOT
    invalidate the cache: the caller must call :func:`invalidate` after its commit."""
    from program.memory import db

    require(actor, "settings.write")
    spec = spec_for(name)
    conn.execute(
        """INSERT INTO settings (key, value, value_type, updated_at, updated_by)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
               value = excluded.value,
               value_type = excluded.value_type,
               updated_at = excluded.updated_at,
               updated_by = excluded.updated_by""",
        (spec.name, _encode(value, spec), spec.value_type, db.now_iso(), actor.name),
    )


@retry_on_locked
def set(name: str, value: Any, actor: Actor) -> None:
    """Write one setting and invalidate the cache. Takes effect immediately.

    The value is validated against its declared type before the write, so a
    wrong type fails here rather than at the next read from an unrelated caller.

    ``updated_by`` is **derived from ``actor``** rather than passed alongside it,
    so the attribution recorded is the same thing that was authorized and the
    two cannot disagree.
    """
    from program.memory import db

    _refuse_if_logged_only(name)
    require(actor, "settings.write")
    spec = spec_for(name)
    encoded = _encode(value, spec)
    updated_by = actor.name

    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, value_type, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   value_type = excluded.value_type,
                   updated_at = excluded.updated_at,
                   updated_by = excluded.updated_by""",
            (spec.name, encoded, spec.value_type, db.now_iso(), updated_by),
        )
    invalidate()
    logger.info("setting %s updated by %s", spec.name, updated_by or "unknown")


@retry_on_locked
def clear(name: str, actor: Actor) -> bool:
    """Remove the row so the key falls back to its config seed again.

    Returns whether a row was actually removed. This is "revert to default" for
    the admin panel, not a data deletion in any provenance sense — the settings
    table holds current operational values, never experience.
    """
    from program.memory import db

    _refuse_if_logged_only(name)
    require(actor, "settings.write")
    spec = spec_for(name)
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM settings WHERE key = ?", (spec.name,))
        removed = cursor.rowcount > 0
    invalidate()
    return removed
